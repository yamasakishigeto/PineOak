"""
Stage3(GPU, バッチ化済み)の結果からdelta_s不連続マップを生成する。

heaviside_dic_v81.py の _build_disc_line_map / _save_png をそのまま流用する
(可視化ロジックの再実装はしない、本体は無改変)。
"""
import os
import sys

import numpy as np
import torch

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import heaviside_dic_v81 as hdic
from stage3_batch import (
    GpuMaskCache, process_grain, load_ref_deformed,
    GRAIN_THETA_MAP, GRAIN_THR_MAP, DEVICE,
)

torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
SCRATCH = os.environ.get("CLAUDE_SCRATCHPAD", os.path.dirname(os.path.abspath(__file__)))


def main():
    print(f"device: {DEVICE}")
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    coord_to_grain = hdic.load_grain_assignment(xlsx_path)

    ref, deformed = load_ref_deformed()
    print(f"deformed shape: {deformed.shape}")

    mask_caches = {gid: GpuMaskCache(tc) for gid, tc in GRAIN_THETA_MAP.items()}

    # disc_grid_run のしきい値超えピクセルからサブセット中心一覧を再構成
    # (run_heaviside_dic 内部と同じロジック: grain_thr_mapで粒ごとの閾値)
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    x, y, u_cols = hdic.load_sheet(wb['u'])
    _, _, v_cols = hdic.load_sheet(wb['v'])
    _, _, disc_cols = hdic.load_sheet(wb['gamma_max'])
    wb.close()
    label = "1100MPa"
    u_grid, xs, ys = hdic.to_grid(x, y, u_cols[label])
    v_grid, _, _ = hdic.to_grid(x, y, v_cols[label])
    disc_grid, _, _ = hdic.to_grid(x, y, disc_cols[label])

    target_gids = set(GRAIN_THETA_MAP.keys())
    by_grain = {gid: [] for gid in target_gids}
    for iy, cy_i in enumerate(ys):
        for ix, cx_i in enumerate(xs):
            gid = coord_to_grain.get((int(cx_i), int(cy_i)))
            if gid not in target_gids:
                continue
            thr = GRAIN_THR_MAP[gid]
            val = disc_grid[iy, ix]
            if np.isnan(val) or val <= thr:
                continue
            u0, v0 = u_grid[iy, ix], v_grid[iy, ix]
            if np.isnan(u0) or np.isnan(v0):
                continue
            by_grain[gid].append((int(cx_i), int(cy_i), float(u0), float(v0)))

    all_results = []
    for gid, subset_list in by_grain.items():
        if not subset_list:
            continue
        results, n_full, n_boundary, batch_size = process_grain(
            ref, deformed, subset_list, mask_caches[gid])
        for (cx_i, cy_i, u0, v0), r in zip(subset_list, results):
            if r is not None:
                r['grain_id'] = gid
                all_results.append(r)
        print(f"  Grain {gid}: {len(subset_list)}件中 {sum(r is not None for r in results)}件 有効")

    print(f"GPU有効結果: {len(all_results)}件")
    filtered = hdic.filter_by_neighbors(all_results)
    print(f"filter_by_neighbors後: {len(filtered)}件")

    out_dummy_path = os.path.join(SCRATCH, "gpu_heaviside_results_1100MPa.xlsx")
    hdic._save_png(filtered, deformed, out_dummy_path, label="1100MPa (GPU/Stage3)")


if __name__ == "__main__":
    main()
