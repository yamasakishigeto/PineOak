"""
視野全面(grain登録なし、全27grain、Hough無し・全域theta探索)に、
これまでの調査で見つけた最良設定を適用してdelta_sマップを生成する。

設定:
  SUBSET_SIZE=41, MIN_SIDE_PX=40%(672px)
  theta全域探索(-90~+90, grain非依存の共通閾値0.09)
  事後フィルタ: delta(不連続量)>=0.8px かつ
                theta と 主ひずみ方向の法線 の角度差<=30度

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
from stage1_shift_gpu import load_ref_deformed

torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
SCRATCH = os.environ.get("CLAUDE_SCRATCHPAD", os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

SUBSET_SIZE = 41
MIN_SIDE_FRAC = 0.40
COMMON_THRESHOLD = 0.09
DELTA_THR = 0.8
PNORM_THR = 30.0
LABEL = "1100MPa"


def ang_dist(a, b):
    diff = abs(a - b) % 180
    return min(diff, 180 - diff)


def _build_disc_line_map_fixed(results, H, W, subset_size):
    """hdic._build_disc_line_map のsubset_sizeパラメータ化版。
    本体はグローバル定数SUBSET_SIZE(=31)を内部で使うため、
    41pxで検出した結果を渡すと線がほとんど描画されないバグがあった。"""
    half = subset_size // 2
    disc_line_map = np.full((H, W), np.nan)
    for r in results:
        cx_def = int(round(r['cx'] + (r['u_A'] + r['u_B']) / 2.0))
        cy_def = int(round(r['cy'] + (r['v_A'] + r['v_B']) / 2.0))
        theta_rad = np.radians(float(r['theta']))
        offset = float(r['offset'])
        ds = float(r['delta_s'])
        cc, rr = np.meshgrid(np.arange(-half, half + 1), np.arange(-half, half + 1))
        proj = cc * (-np.sin(theta_rad)) + rr * np.cos(theta_rad)
        on_line = np.abs(proj - offset) < 1.5
        local_rows, local_cols = np.where(on_line)
        py = cy_def - half + local_rows
        px = cx_def - half + local_cols
        valid = (py >= 0) & (py < H) & (px >= 0) & (px < W)
        disc_line_map[py[valid], px[valid]] = ds
    return disc_line_map


def _save_png_fixed(results, sem_img, out_path, label, subset_size, vlim=None):
    import matplotlib.pyplot as plt
    H, W = sem_img.shape
    disc_line_map = _build_disc_line_map_fixed(results, H, W, subset_size)
    valid_ds = disc_line_map[~np.isnan(disc_line_map)]
    if len(valid_ds) == 0:
        print("Warning: No valid delta_s values, skipping PNG output.")
        return
    if vlim is None:
        p2, p98 = np.percentile(valid_ds, [2, 98])
        vlim = float(max(abs(p2), abs(p98)))
    cmap_rdbu = plt.cm.RdBu_r.copy()
    cmap_rdbu.set_bad(alpha=0.0)
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(sem_img, cmap='gray', extent=[-0.5, W - 0.5, H - 0.5, -0.5], alpha=0.6)
    im = ax.imshow(disc_line_map, cmap=cmap_rdbu, extent=[-0.5, W - 0.5, H - 0.5, -0.5],
                    vmin=-vlim, vmax=vlim, alpha=1.0)
    plt.colorbar(im, ax=ax, label='delta_s [px]')
    ax.set_title(f'Heaviside DIC: {label}  delta_s (±{vlim:.2f} px), n={len(results)}')
    ax.set_xlabel('x [px]')
    ax.set_ylabel('y [px]')
    plt.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)
    print(f"Saved PNG: {out_path}")


def main():
    print(f"device: {DEVICE}")
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    coord_to_grain = hdic.load_grain_assignment(xlsx_path)
    print("画像読込中...")
    import io, contextlib
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        ref, deformed = load_ref_deformed()
    print("画像読込完了")

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    x, y, u_cols = hdic.load_sheet(wb["u"])
    _, _, v_cols = hdic.load_sheet(wb["v"])
    _, _, disc_cols = hdic.load_sheet(wb["gamma_max"])
    _, _, exx_cols = hdic.load_sheet(wb["exx"])
    _, _, eyy_cols = hdic.load_sheet(wb["eyy"])
    _, _, exy_cols = hdic.load_sheet(wb["exy"])
    wb.close()
    u_grid, xs, ys = hdic.to_grid(x, y, u_cols[LABEL])
    v_grid, _, _ = hdic.to_grid(x, y, v_cols[LABEL])
    disc_grid, _, _ = hdic.to_grid(x, y, disc_cols[LABEL])
    exx_grid, _, _ = hdic.to_grid(x, y, exx_cols[LABEL])
    eyy_grid, _, _ = hdic.to_grid(x, y, eyy_cols[LABEL])
    exy_grid, _, _ = hdic.to_grid(x, y, exy_cols[LABEL])

    # gamma_maxによる事前の場所の足切りはしない(=候補地点を減らさない)。
    # grain_id>0(登録grain内)かつu,v,ひずみが有効な格子点は全てHeaviside DICを計算し、
    # delta量・主ひずみ法線フィルタは「計算結果の表示可否」の判定にのみ使う。
    subset_list = []
    p_angle_list = []
    for iy, cy_i in enumerate(ys):
        for ix, cx_i in enumerate(xs):
            gid = coord_to_grain.get((int(cx_i), int(cy_i)))
            if gid is None or gid <= 0:
                continue
            u0, v0 = u_grid[iy, ix], v_grid[iy, ix]
            if np.isnan(u0) or np.isnan(v0):
                continue
            exx_v, eyy_v, exy_v = exx_grid[iy, ix], eyy_grid[iy, ix], exy_grid[iy, ix]
            if np.isnan(exx_v) or np.isnan(eyy_v) or np.isnan(exy_v):
                p_ang = None
            else:
                p_ang = 0.5 * np.degrees(np.arctan2(2 * exy_v, exx_v - eyy_v))
            subset_list.append((int(cx_i), int(cy_i), float(u0), float(v0)))
            p_angle_list.append(p_ang)
    print(f"計算対象の全格子点数(grain登録内、gamma_max足切りなし): {len(subset_list)}")

    half = SUBSET_SIZE // 2
    keep_idx = [i for i, (cx, cy, u0, v0) in enumerate(subset_list)
                if cy - half - 3 >= 0 and cy + half + 3 < deformed.shape[0]
                and cx - half - 3 >= 0 and cx + half + 3 < deformed.shape[1]]
    subset_list = [subset_list[i] for i in keep_idx]
    p_angle_list = [p_angle_list[i] for i in keep_idx]

    min_side = int(SUBSET_SIZE * SUBSET_SIZE * MIN_SIDE_FRAC)
    t0 = time.perf_counter()
    global_cache = GpuMaskCache(theta_center=0.0, theta_range=90.0, theta_step=1.0,
                                 subset_size=SUBSET_SIZE)
    print(f"グローバルマスク行列: {global_cache.combined_mask.shape} "
          f"構築時間={time.perf_counter()-t0:.1f}s")

    t0 = time.perf_counter()
    results, n_full, n_boundary, batch_size = process_grain(
        ref, deformed, subset_list, global_cache, min_side_px=min_side)
    elapsed = time.perf_counter() - t0
    print(f"処理時間: {elapsed:.1f}s  (full={n_full}, boundary_fallback={n_boundary})")

    filtered_results = []
    for (cx, cy, u0, v0), p_ang, r in zip(subset_list, p_angle_list, results):
        if r is None:
            continue
        mag = float(np.sqrt(r["delta_s"] ** 2 + r["delta_n"] ** 2))
        if mag < DELTA_THR:
            continue
        if p_ang is None:
            filtered_results.append(r)
            continue
        d_pnorm = ang_dist(r["theta"], p_ang + 90)
        if d_pnorm > PNORM_THR:
            continue
        filtered_results.append(r)

    print(f"有効: {sum(r is not None for r in results)}/{len(subset_list)}  "
          f"delta+主ひずみ法線フィルタ後: {len(filtered_results)}")

    final = hdic.filter_by_neighbors(filtered_results)
    print(f"filter_by_neighbors後: {len(final)}")

    dst_png = os.path.join(OUT_DIR, "full_field_best_filter_delta_s.png")
    _save_png_fixed(final, deformed, dst_png,
                     label="1100MPa (GPU, no grain/Hough, 41px+40%+delta0.8+pnorm30)",
                     subset_size=SUBSET_SIZE)
    print(f"永続フォルダに保存: {dst_png}")


if __name__ == "__main__":
    main()
