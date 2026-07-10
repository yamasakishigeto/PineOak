"""
Step2 / Stage1: shift探索(49通り)のみをGPU化する。

スコープ:
  固定の (theta, offset) マスク1枚に対して、u_init/v_init ± SEARCH_RANGE の
  49通りのshift探索を PyTorch のベクトル化演算(GPU)に置き換える。
  theta×offset の探索(Stage2)やマスク行列のGEMM分解(Stage2)、複数サブセット
  のバッチ化(Stage3)はまだ行わない。

  サブピクセルフィットは heaviside_dic_v81.subpixel_fit をそのまま流用する
  (CPU、変更なし)。

検証:
  golden_1100MPa.npz の各サブセットについて、golden側が選んだ (theta, offset)
  でマスクを再構成し、
    - CPU: heaviside_dic_v81.ncc_masked_search (無改変)
    - GPU: 本ファイルの gpu_ncc_masked_search
  を同一マスク・同一パッチに対して実行し、(ncc, u, v) を突き合わせる。

heaviside_dic_v81.py は一切変更しない。
"""
import os
import sys
import time

import numpy as np
import torch

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)

import heaviside_dic_v81 as hdic

# RTX50xx世代はデフォルトでTF32が有効なため、精度劣化を避けるため明示的に無効化する
torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
SCRATCH = os.environ.get("CLAUDE_SCRATCHPAD", os.path.dirname(os.path.abspath(__file__)))
GOLDEN_NPZ = os.path.join(SCRATCH, "golden_1100MPa.npz")

GRAIN_THETA_MAP = {1: 74.0, 7: 73.5, 14: -45.0, 15: 73.0, 18: -63.0}
GRAIN_THR_MAP = {1: 0.1007, 7: 0.1007, 14: 0.0909, 15: 0.0909, 18: 0.1291}

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class _StopEarly(Exception):
    pass


def load_ref_deformed():
    """golden_1100MPa.npz と同一の前処理(トリミング+アライメント補正済み)の
    ref/deformed 配列を、run_heaviside_dic の最初の1呼び出し時点で捕獲する。
    フルNCC探索(476サブセット・約100秒)は実行しない。"""
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    ref_path = os.path.join(DATA_DIR, "SEM_images", "0MPa.bmp")
    def_path = os.path.join(DATA_DIR, "SEM_images", "1100MPa.bmp")
    alignment_json_path = os.path.join(DATA_DIR, "sem_alignment.json")
    coord_to_grain = hdic.load_grain_assignment(xlsx_path)

    captured = {}
    orig = hdic.process_one_subset

    def capture_and_stop(ref, deformed, cx, cy, u_init, v_init, theta_center,
                          offset_max=None, min_side_px=None, _mask_cache=None):
        captured["ref"] = ref
        captured["deformed"] = deformed
        raise _StopEarly()

    hdic.process_one_subset = capture_and_stop
    try:
        hdic.run_heaviside_dic(
            xlsx_path=xlsx_path, ref_path=ref_path, def_path=def_path,
            label="1100MPa", grain_theta_map=GRAIN_THETA_MAP,
            coord_to_grain=coord_to_grain,
            out_path=os.path.join(SCRATCH, "_stage1_dummy_out.xlsx"),
            n_jobs=1, alignment_json_path=alignment_json_path,
            grain_thr_map=GRAIN_THR_MAP,
        )
    except _StopEarly:
        pass
    finally:
        hdic.process_one_subset = orig

    return captured["ref"], captured["deformed"]


def gpu_ncc_masked_search(ref, deformed, cx, cy, subset_size, u_init, v_init,
                           mask_np, min_side_px, search_range=3, device=DEVICE):
    """CPUのncc_masked_searchと同じ入出力仕様のGPU版(shift探索のみGPU化)。"""
    half = subset_size // 2
    n_mask = int(mask_np.sum())
    if n_mask < min_side_px:
        return np.nan, np.nan, np.nan

    ref_patch = ref[cy - half:cy + half + 1, cx - half:cx + half + 1].astype(np.float32)
    ref_masked = ref_patch[mask_np]
    f_mean = ref_masked.mean()
    f_np = ref_masked - f_mean
    f_norm_np = np.sqrt(np.sum(f_np ** 2))
    if f_norm_np < 1e-7:
        return np.nan, np.nan, np.nan

    du_init_int = int(round(u_init))
    dv_init_int = int(round(v_init))

    # 49通りのshift候補パッチをまとめてスタック(範囲外はスキップ)
    patches = []
    offsets_list = []
    for dv in range(dv_init_int - search_range, dv_init_int + search_range + 1):
        for du in range(du_init_int - search_range, du_init_int + search_range + 1):
            cy_def = cy + dv
            cx_def = cx + du
            if (cy_def - half < 0 or cy_def + half >= deformed.shape[0] or
                    cx_def - half < 0 or cx_def + half >= deformed.shape[1]):
                continue
            patches.append(deformed[cy_def - half:cy_def + half + 1,
                                     cx_def - half:cx_def + half + 1])
            offsets_list.append((du, dv))

    if not patches:
        return np.nan, np.nan, np.nan

    mask_t = torch.from_numpy(mask_np).to(device)
    G = torch.from_numpy(np.stack(patches).astype(np.float32)).to(device)  # (K,31,31)
    Gm = G[:, mask_t]  # (K, n_mask)

    g_mean = Gm.mean(dim=1, keepdim=True)
    g = Gm - g_mean
    g_norm = torch.linalg.norm(g, dim=1)  # (K,)

    f_t = torch.from_numpy(f_np.astype(np.float32)).to(device)  # (n_mask,)
    f_norm_t = torch.tensor(float(f_norm_np), device=device)

    denom = f_norm_t * g_norm
    numer = g @ f_t  # (K,)  -- GEMV (matmul形式)
    ncc = torch.where(denom > 1e-7, numer / denom, torch.full_like(numer, float("-inf")))

    best_idx = int(torch.argmax(ncc).item())
    best_ncc = float(ncc[best_idx].item())
    best_du, best_dv = offsets_list[best_idx]

    if best_ncc < hdic.NCC_THRESHOLD:
        return np.nan, np.nan, np.nan

    # サブピクセルフィットはCPU実装をそのまま流用(計算量が無視できるため)
    du_sub, dv_sub = hdic.subpixel_fit(
        ref, deformed, cx, cy, subset_size, mask_np, f_np, f_norm_np,
        best_du, best_dv)
    return best_ncc, du_sub, dv_sub


def main():
    print(f"device: {DEVICE}")
    print("ref/deformed を捕獲中(フルNCC探索はスキップ)...")
    ref, deformed = load_ref_deformed()
    print(f"  ref shape={ref.shape}, deformed shape={deformed.shape}")

    d = np.load(GOLDEN_NPZ)
    n = len(d["cx"])
    valid_idx = np.where(d["valid"])[0]
    print(f"golden件数: {n} (valid={len(valid_idx)})")

    ncc_diffs, u_diffs, v_diffs = [], [], []
    mismatches = []
    t0 = time.perf_counter()
    for i in valid_idx:
        cx, cy = int(d["cx"][i]), int(d["cy"][i])
        u_init, v_init = float(d["u_init"][i]), float(d["v_init"][i])
        theta, offset = float(d["theta"][i]), float(d["offset"][i])
        mask_A, mask_B = hdic.make_heaviside_mask(hdic.SUBSET_SIZE, theta, offset)

        for side, mask_np, ncc_gold, u_gold, v_gold in [
            ("A", mask_A, d["ncc_A"][i], d["u_A"][i], d["v_A"][i]),
            ("B", mask_B, d["ncc_B"][i], d["u_B"][i], d["v_B"][i]),
        ]:
            ncc_cpu, u_cpu, v_cpu = hdic.ncc_masked_search(
                ref, deformed, cx, cy, hdic.SUBSET_SIZE, u_init, v_init,
                mask_np, min_side_px=hdic.MIN_SIDE_PX)
            ncc_gpu, u_gpu, v_gpu = gpu_ncc_masked_search(
                ref, deformed, cx, cy, hdic.SUBSET_SIZE, u_init, v_init,
                mask_np, min_side_px=hdic.MIN_SIDE_PX)

            # CPU再計算(ncc_cpu)がgolden格納値と一致することも確認(整合性チェック)
            if not np.isnan(ncc_gold) and abs(ncc_cpu - ncc_gold) > 1e-5:
                mismatches.append((int(i), side, "cpu_vs_golden", ncc_cpu, ncc_gold))

            if np.isnan(ncc_gpu) != np.isnan(ncc_cpu):
                mismatches.append((int(i), side, "nan_mismatch", ncc_gpu, ncc_cpu))
                continue
            if np.isnan(ncc_gpu):
                continue

            ncc_diffs.append(abs(ncc_gpu - ncc_cpu))
            u_diffs.append(abs(u_gpu - u_cpu))
            v_diffs.append(abs(v_gpu - v_cpu))

    elapsed = time.perf_counter() - t0
    ncc_diffs = np.array(ncc_diffs)
    u_diffs = np.array(u_diffs)
    v_diffs = np.array(v_diffs)

    print("\n" + "=" * 60)
    print(f"比較件数(A/B合計): {len(ncc_diffs)}  実行時間: {elapsed:.1f}s")
    print(f"ncc差分  max={ncc_diffs.max():.3e}  mean={ncc_diffs.mean():.3e}")
    print(f"u差分    max={u_diffs.max():.3e}  mean={u_diffs.mean():.3e}")
    print(f"v差分    max={v_diffs.max():.3e}  mean={v_diffs.mean():.3e}")
    print(f"不一致(nanミスマッチ/golden不整合): {len(mismatches)}")
    for m in mismatches[:20]:
        print("  ", m)
    print("=" * 60)


if __name__ == "__main__":
    main()
