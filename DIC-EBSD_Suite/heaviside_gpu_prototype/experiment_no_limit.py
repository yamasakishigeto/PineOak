"""
実験: grain登録・Hough(theta_center±5°)の制限を外して計算する。

  - theta範囲: 全域(-90°~+90°、1°刻み、181通り) ※theta_center=0,range=90として実現
  - 対象位置: 5grain限定をやめ、coord_to_grain に grain_id>0 が登録されている
    全ピクセル(=georef上の全27grain)を対象に、gamma_max > COMMON_THRESHOLD で抽出
  - 閾値は全grain共通で COMMON_THRESHOLD を使う(grainごとの個別校正は行わない)

  theta範囲がgrain非依存になるため、マスク行列は「grainごとに1回」ではなく
  「全体で1回」だけ構築すればよい(stage2/3の設計の自然な帰結)。

golden(5grain・theta_center±5°限定, 476件)と同じ座標での選択結果を突き合わせ、
Houghによる絞り込みを外した場合にどれだけ結果が変わるかを確認する。

heaviside_dic_v81.py は一切変更しない。
"""
import os
import sys
import time

import numpy as np
import openpyxl
import torch

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import heaviside_dic_v81 as hdic
from stage3_batch import GpuMaskCache, process_grain, DEVICE
from stage1_shift_gpu import load_ref_deformed  # ref/deformed 捕獲(grain設定に依存しない)

torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
SCRATCH = os.environ.get("CLAUDE_SCRATCHPAD", os.path.dirname(os.path.abspath(__file__)))
GOLDEN_NPZ = os.path.join(SCRATCH, "golden_1100MPa.npz")

COMMON_THRESHOLD = 0.09
LABEL = "1100MPa"


def main():
    print(f"device: {DEVICE}")
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    coord_to_grain = hdic.load_grain_assignment(xlsx_path)
    all_grain_ids = sorted(set(coord_to_grain.values()))
    print(f"georef上の全grain数: {len(all_grain_ids)}")

    ref, deformed = load_ref_deformed()

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    x, y, u_cols = hdic.load_sheet(wb["u"])
    _, _, v_cols = hdic.load_sheet(wb["v"])
    _, _, disc_cols = hdic.load_sheet(wb["gamma_max"])
    wb.close()
    u_grid, xs, ys = hdic.to_grid(x, y, u_cols[LABEL])
    v_grid, _, _ = hdic.to_grid(x, y, v_cols[LABEL])
    disc_grid, _, _ = hdic.to_grid(x, y, disc_cols[LABEL])

    subset_list = []
    for iy, cy_i in enumerate(ys):
        for ix, cx_i in enumerate(xs):
            gid = coord_to_grain.get((int(cx_i), int(cy_i)))
            if gid is None or gid <= 0:
                continue
            val = disc_grid[iy, ix]
            if np.isnan(val) or val <= COMMON_THRESHOLD:
                continue
            u0, v0 = u_grid[iy, ix], v_grid[iy, ix]
            if np.isnan(u0) or np.isnan(v0):
                continue
            subset_list.append((int(cx_i), int(cy_i), float(u0), float(v0), gid))
    print(f"候補点数(全grain・閾値{COMMON_THRESHOLD}): {len(subset_list)}")

    # theta全域: theta_center=0, theta_range=90 とすることで -90~+90 全域をカバー
    t0 = time.perf_counter()
    global_cache = GpuMaskCache(theta_center=0.0, theta_range=90.0, theta_step=1.0)
    print(f"グローバルマスク行列: {global_cache.combined_mask.shape} "
          f"構築時間={time.perf_counter()-t0:.1f}s")

    coords_only = [(cx, cy, u0, v0) for cx, cy, u0, v0, gid in subset_list]
    t0 = time.perf_counter()
    results, n_full, n_boundary, batch_size = process_grain(
        ref, deformed, coords_only, global_cache)
    elapsed = time.perf_counter() - t0

    valid_results = []
    for (cx, cy, u0, v0, gid), r in zip(subset_list, results):
        if r is not None:
            r["grain_id"] = gid
            valid_results.append(r)
    print(f"有効結果: {len(valid_results)}/{len(subset_list)}  "
          f"実行時間={elapsed:.2f}s ({elapsed/len(subset_list)*1000:.3f} ms/subset)")

    filtered = hdic.filter_by_neighbors(valid_results)
    print(f"filter_by_neighbors後: {len(filtered)}件")

    # ---- golden(5grain・theta_center±5°限定)との突き合わせ ----
    d = np.load(GOLDEN_NPZ)
    valid_idx = np.where(d["valid"])[0]
    loc_to_result = {(r["cx"], r["cy"]): r for r in valid_results}

    found, theta_within_old_range, theta_far = 0, 0, 0
    theta_diffs = []
    for i in valid_idx:
        cx, cy = int(d["cx"][i]), int(d["cy"][i])
        r = loc_to_result.get((cx, cy))
        if r is None:
            continue
        found += 1
        theta_center_old = float(d["theta_center"][i])
        diff = abs(r["theta"] - theta_center_old)
        diff = min(diff, 180 - diff)  # 対称性を考慮
        theta_diffs.append(diff)
        if diff <= hdic.THETA_RANGE + 1e-6:
            theta_within_old_range += 1
        else:
            theta_far += 1

    print("\n" + "=" * 60)
    print(f"golden 476件中、grain制限なし版でも同じ座標が検出された数: {found}/{len(valid_idx)}")
    print(f"  旧theta_center±{hdic.THETA_RANGE}°の範囲内に収まった: "
          f"{theta_within_old_range}/{found} ({theta_within_old_range/max(found,1)*100:.1f}%)")
    print(f"  旧範囲を外れて別のthetaを選んだ: {theta_far}/{found} "
          f"({theta_far/max(found,1)*100:.1f}%)")
    if theta_diffs:
        print(f"  theta差分: mean={np.mean(theta_diffs):.2f}°  max={np.max(theta_diffs):.2f}°")
    print(f"\n[全体件数比較] grain限定版: 候補476件→有効475件→filter後389件")
    print(f"              制限なし版: 候補{len(subset_list)}件→有効{len(valid_results)}件"
          f"→filter後{len(filtered)}件")
    print("=" * 60)

    out_dummy_path = os.path.join(SCRATCH, "gpu_heaviside_results_1100MPa_nolimit.xlsx")
    hdic._save_png(filtered, deformed, out_dummy_path,
                    label=f"1100MPa (GPU, no grain/theta limit, thr={COMMON_THRESHOLD})")


if __name__ == "__main__":
    main()
