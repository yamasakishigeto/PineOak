"""
Step3 / Stage2: theta×offset のマスク行列を導入し、process_one_subset 全体を
GEMM(GPU)化する。

設計:
  マスク集合(theta×offset×2面)は grain(theta_center) ごとに定数なので、
  grainごとに1回だけ (n_combo*2, 961) のマスク行列をGPUに構築して使い回す。
  1サブセットあたり:
    sumF, sumF2   : (n_combo*2, 961) @ (961,)        -- 小さいGEMV
    sumG, sumG2   : (49, 961) @ (961, n_combo*2)      -- GEMM
    sumFG         : (49, 961) @ (961, n_combo*2)      -- GEMM (FマスクをGでまとめて評価)
  から共分散展開公式でNCCを一括計算し、(theta,offset)ごとに49shiftの最良値を
  argmaxで求める。min_side_px / NCC_THRESHOLD のフィルタもCPU版と同じ条件で
  ベクトル化して適用する。

  サブピクセルフィットは heaviside_dic_v81.subpixel_fit をそのまま流用(CPU)。

  THETA_RANGE はパラメータ化してあり、Hough有無(狭域探索 vs 全域探索)の比較
  実験にそのまま使える。

検証:
  golden_1100MPa.npz の全476サブセットについて、grainごとに1回だけマスク行列
  を構築し、GPU版 process_one_subset 相当の出力(theta, offset, ncc_A, ncc_B,
  delta_s, delta_n)を golden と突き合わせる。
  (1) (theta, offset) の選択一致率
  (2) ncc / delta_s / delta_n の誤差

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
            out_path=os.path.join(SCRATCH, "_stage2_dummy_out.xlsx"),
            n_jobs=1, alignment_json_path=alignment_json_path,
            grain_thr_map=GRAIN_THR_MAP,
        )
    except _StopEarly:
        pass
    finally:
        hdic.process_one_subset = orig

    return captured["ref"], captured["deformed"]


class GpuMaskCache:
    """grainごとに1回だけ構築してGPUに保持するマスク行列。

    combined_mask: (2*n_combo, subset_size*subset_size) float32
      行 0..n_combo-1        = mask_A (thetas×offsets の各組み合わせ)
      行 n_combo..2n_combo-1 = mask_B (対応するmask_Aの補集合)
    """

    def __init__(self, theta_center, subset_size=None, theta_range=None,
                 theta_step=None, offset_max=None, offset_step=None,
                 device=DEVICE):
        subset_size = subset_size or hdic.SUBSET_SIZE
        theta_range = theta_range if theta_range is not None else hdic.THETA_RANGE
        theta_step = theta_step or hdic.THETA_STEP
        offset_max = offset_max if offset_max is not None else hdic.OFFSET_MAX
        offset_step = offset_step or hdic.OFFSET_STEP

        self.thetas = np.arange(theta_center - theta_range,
                                 theta_center + theta_range + theta_step * 0.5,
                                 theta_step)
        offset_max_int = int(np.floor(offset_max))
        self.offsets = np.arange(-offset_max_int, offset_max_int + 1, offset_step)

        combos = [(float(t), int(o)) for t in self.thetas for o in self.offsets]
        self.combos = combos
        n_combo = len(combos)
        self.n_combo = n_combo

        mask_A_list = np.empty((n_combo, subset_size * subset_size), dtype=np.float32)
        for i, (theta, offset) in enumerate(combos):
            mA, _ = hdic.make_heaviside_mask(subset_size, theta, offset)
            mask_A_list[i] = mA.flatten().astype(np.float32)

        mask_A_t = torch.from_numpy(mask_A_list).to(device)      # (n_combo, 961)
        mask_B_t = 1.0 - mask_A_t                                 # 補集合(makeがA,~Aで返すため厳密に一致)
        self.combined_mask = torch.cat([mask_A_t, mask_B_t], dim=0)  # (2*n_combo, 961)
        self.N = self.combined_mask.sum(dim=1)                    # (2*n_combo,)
        self.device = device
        self.subset_size = subset_size


def gpu_process_one_subset(ref, deformed, cx, cy, u_init, v_init,
                            mask_cache, min_side_px=None, search_range=3,
                            device=DEVICE):
    if min_side_px is None:
        min_side_px = hdic.MIN_SIDE_PX
    subset_size = mask_cache.subset_size
    half = subset_size // 2
    if (cy - half < 0 or cy + half >= ref.shape[0] or
            cx - half < 0 or cx + half >= ref.shape[1]):
        return None

    du_init_int = int(round(u_init))
    dv_init_int = int(round(v_init))
    patches, offsets_list = [], []
    for dv in range(dv_init_int - search_range, dv_init_int + search_range + 1):
        for du in range(du_init_int - search_range, du_init_int + search_range + 1):
            cy_def, cx_def = cy + dv, cx + du
            if (cy_def - half < 0 or cy_def + half >= deformed.shape[0] or
                    cx_def - half < 0 or cx_def + half >= deformed.shape[1]):
                continue
            patches.append(deformed[cy_def - half:cy_def + half + 1,
                                     cx_def - half:cx_def + half + 1])
            offsets_list.append((du, dv))
    if not patches:
        return None

    ref_patch = ref[cy - half:cy + half + 1, cx - half:cx + half + 1].astype(np.float32)
    F = torch.from_numpy(ref_patch.flatten()).to(device)          # (961,)
    G = torch.from_numpy(np.stack(patches).reshape(len(patches), -1)
                          .astype(np.float32)).to(device)         # (K,961)

    M = mask_cache.combined_mask     # (2C, 961)
    N = mask_cache.N                 # (2C,)

    sumF = M @ F                     # (2C,)
    sumF2 = M @ (F * F)              # (2C,)
    sumG = G @ M.T                   # (K,2C)
    sumG2 = (G * G) @ M.T            # (K,2C)
    FM = F.unsqueeze(0) * M          # (2C,961)
    sumFG = G @ FM.T                 # (K,2C)

    numer = sumFG - sumF.unsqueeze(0) * sumG / N.unsqueeze(0)
    denomF = (sumF2 - sumF * sumF / N).clamp(min=0.0)             # (2C,)
    denomG = (sumG2 - sumG * sumG / N.unsqueeze(0)).clamp(min=0.0)  # (K,2C)
    denom = torch.sqrt(denomF.unsqueeze(0) * denomG)

    eps = 1e-7
    valid_shift = denom > eps
    ncc = torch.where(valid_shift, numer / denom.clamp(min=eps),
                       torch.full_like(numer, float("-inf")))

    # マスク単位(theta,offset,side)でf_norm(=sqrt(denomF))が小さすぎる場合は全shift無効
    mask_degenerate = denomF < eps * eps
    ncc = torch.where(mask_degenerate.unsqueeze(0),
                       torch.full_like(ncc, float("-inf")), ncc)

    best_ncc, best_shift_idx = ncc.max(dim=0)     # (2C,), (2C,)

    n_combo = mask_cache.n_combo
    ncc_A, ncc_B = best_ncc[:n_combo], best_ncc[n_combo:]
    N_A, N_B = N[:n_combo], N[n_combo:]

    valid_combo = ((N_A >= min_side_px) & (N_B >= min_side_px) &
                   (ncc_A >= hdic.NCC_THRESHOLD) & (ncc_B >= hdic.NCC_THRESHOLD) &
                   torch.isfinite(ncc_A) & torch.isfinite(ncc_B))

    score = torch.where(valid_combo, ncc_A + ncc_B, torch.full_like(ncc_A, float("-inf")))
    if not torch.isfinite(score.max()):
        return None
    best_i = int(torch.argmax(score).item())

    theta_best, offset_best = mask_cache.combos[best_i]
    shift_idx_A = int(best_shift_idx[best_i].item())
    shift_idx_B = int(best_shift_idx[n_combo + best_i].item())
    du_A, dv_A = offsets_list[shift_idx_A]
    du_B, dv_B = offsets_list[shift_idx_B]

    mask_A, mask_B = hdic.make_heaviside_mask(subset_size, theta_best, offset_best)

    ref_masked_A = ref_patch[mask_A]
    f_A = ref_masked_A - ref_masked_A.mean()
    f_norm_A = float(np.sqrt(np.sum(f_A ** 2)))
    u_A, v_A = hdic.subpixel_fit(ref, deformed, cx, cy, subset_size, mask_A,
                                  f_A, f_norm_A, du_A, dv_A)

    ref_masked_B = ref_patch[mask_B]
    f_B = ref_masked_B - ref_masked_B.mean()
    f_norm_B = float(np.sqrt(np.sum(f_B ** 2)))
    u_B, v_B = hdic.subpixel_fit(ref, deformed, cx, cy, subset_size, mask_B,
                                  f_B, f_norm_B, du_B, dv_B)

    theta_rad = np.radians(theta_best)
    du, dv = u_A - u_B, v_A - v_B
    result = dict(
        cx=cx, cy=cy, theta=theta_best, offset=offset_best,
        u_A=u_A, v_A=v_A, u_B=u_B, v_B=v_B,
        ncc_A=float(ncc_A[best_i].item()), ncc_B=float(ncc_B[best_i].item()),
        delta_u=du, delta_v=dv,
        delta_s=du * np.cos(theta_rad) + dv * np.sin(theta_rad),
        delta_n=du * (-np.sin(theta_rad)) + dv * np.cos(theta_rad),
    )
    return result


def main():
    print(f"device: {DEVICE}")
    print("ref/deformed を捕獲中(フルNCC探索はスキップ)...")
    ref, deformed = load_ref_deformed()

    d = np.load(GOLDEN_NPZ)
    valid_idx = np.where(d["valid"])[0]
    print(f"golden件数: {len(d['cx'])} (valid={len(valid_idx)})")

    # grainごとにマスク行列を1回だけ構築
    mask_caches = {}
    for gid, theta_center in GRAIN_THETA_MAP.items():
        mask_caches[gid] = GpuMaskCache(theta_center)
        print(f"  Grain {gid}: mask matrix {mask_caches[gid].combined_mask.shape} 構築完了")

    theta_match, offset_match = 0, 0
    ncc_a_diffs, ncc_b_diffs, s_diffs, n_diffs = [], [], [], []
    fail_records = []

    t0 = time.perf_counter()
    for i in valid_idx:
        cx, cy = int(d["cx"][i]), int(d["cy"][i])
        u_init, v_init = float(d["u_init"][i]), float(d["v_init"][i])
        gid = int(d["grain_id"][i])
        mc = mask_caches[gid]

        r = gpu_process_one_subset(ref, deformed, cx, cy, u_init, v_init, mc)
        if r is None:
            fail_records.append((int(i), "gpu_returned_none"))
            continue

        theta_g, offset_g = float(d["theta"][i]), float(d["offset"][i])
        if abs(r["theta"] - theta_g) < 1e-6:
            theta_match += 1
        else:
            fail_records.append((int(i), "theta_mismatch", r["theta"], theta_g))
        if abs(r["offset"] - offset_g) < 1e-6:
            offset_match += 1
        else:
            fail_records.append((int(i), "offset_mismatch", r["offset"], offset_g))

        ncc_a_diffs.append(abs(r["ncc_A"] - float(d["ncc_A"][i])))
        ncc_b_diffs.append(abs(r["ncc_B"] - float(d["ncc_B"][i])))
        s_diffs.append(abs(r["delta_s"] - float(d["delta_s"][i])))
        n_diffs.append(abs(r["delta_n"] - float(d["delta_n"][i])))
    elapsed = time.perf_counter() - t0

    n = len(valid_idx)
    print("\n" + "=" * 60)
    print(f"検証件数: {n}  実行時間: {elapsed:.1f}s ({elapsed/n*1000:.2f} ms/subset)")
    print(f"[1段階目] theta一致: {theta_match}/{n} ({theta_match/n*100:.1f}%)  "
          f"offset一致: {offset_match}/{n} ({offset_match/n*100:.1f}%)")
    print(f"[2段階目] ncc_A差分  max={max(ncc_a_diffs):.3e}  mean={np.mean(ncc_a_diffs):.3e}")
    print(f"          ncc_B差分  max={max(ncc_b_diffs):.3e}  mean={np.mean(ncc_b_diffs):.3e}")
    print(f"          delta_s差分 max={max(s_diffs):.3e}  mean={np.mean(s_diffs):.3e}")
    print(f"          delta_n差分 max={max(n_diffs):.3e}  mean={np.mean(n_diffs):.3e}")
    print(f"失敗/不一致件数: {len(fail_records)}")
    for f in fail_records[:20]:
        print("  ", f)
    print("=" * 60)


if __name__ == "__main__":
    main()
