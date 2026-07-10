"""
複数の応力段階に対して、full_field_best_filter.py と同じ設定
(SUBSET_SIZE=41, MIN_SIDE=40%, theta全域探索, grain登録内は全格子点を計算し
 gamma_maxでの事前足切りはしない、delta>=0.8 + 主ひずみ法線<=30度を事後フィルタ)
でHeaviside DICを実行し、各段階のdelta_sマップを生成する。

heaviside_dic_v81.py は一切変更しない。
"""
import os
import sys
import time
import json

import numpy as np
import openpyxl
import torch
from PIL import Image as PILImage

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import heaviside_dic_v81 as hdic
from stage3_batch import GpuMaskCache, process_grain, DEVICE
from full_field_best_filter import _save_png_fixed, ang_dist

torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

SUBSET_SIZE = 41
MIN_SIDE_FRAC = 0.40
DELTA_THR = 0.8
PNORM_THR = 30.0

STAGES = ["750MPa", "800MPa", "850MPa", "900MPa", "950MPa", "1000MPa",
          "1050MPa", "1100MPa", "1150MPa", "1200MPa", "1250MPa"]
VLIM_SOURCE_STAGE = "1250MPa"
MANUAL_VLIM = 3.0  # Noneなら VLIM_SOURCE_STAGE の実際の最大/最小値を使う


def load_ref_deformed_for_label(label):
    ref_path = os.path.join(DATA_DIR, "SEM_images", "0MPa.bmp")
    def_path = os.path.join(DATA_DIR, "SEM_images", f"{label}.bmp")
    alignment_json_path = os.path.join(DATA_DIR, "sem_alignment.json")

    ref_raw = np.array(PILImage.open(ref_path).convert("L"))
    deformed_raw = np.array(PILImage.open(def_path).convert("L"))
    ref = ref_raw[:-hdic.TRIM_BOTTOM, :] if hdic.TRIM_BOTTOM > 0 else ref_raw
    deformed = deformed_raw[:-hdic.TRIM_BOTTOM, :] if hdic.TRIM_BOTTOM > 0 else deformed_raw

    with open(alignment_json_path, encoding="utf-8") as f:
        align = json.load(f)
    shifts_raw = align.get("shifts", {})
    shifts = {k: (int(v["dx"]), int(v["dy"])) for k, v in shifts_raw.items()}
    def_filename = os.path.basename(def_path)
    deformed = hdic.apply_alignment(deformed, def_filename, shifts)
    dx, dy = shifts.get(def_filename, (0, 0))
    print(f"  [{label}] アライメント補正: dx={dx}, dy={dy}")
    return ref, deformed


def process_stage(label, coord_to_grain):
    print(f"\n=== {label} ===")
    ref, deformed = load_ref_deformed_for_label(label)

    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    x, y, u_cols = hdic.load_sheet(wb["u"])
    _, _, v_cols = hdic.load_sheet(wb["v"])
    _, _, exx_cols = hdic.load_sheet(wb["exx"])
    _, _, eyy_cols = hdic.load_sheet(wb["eyy"])
    _, _, exy_cols = hdic.load_sheet(wb["exy"])
    wb.close()
    u_grid, xs, ys = hdic.to_grid(x, y, u_cols[label])
    v_grid, _, _ = hdic.to_grid(x, y, v_cols[label])
    exx_grid, _, _ = hdic.to_grid(x, y, exx_cols[label])
    eyy_grid, _, _ = hdic.to_grid(x, y, eyy_cols[label])
    exy_grid, _, _ = hdic.to_grid(x, y, exy_cols[label])

    # 候補地点はu,vの有効性だけで決める(exx/eyy/exyの欠損で候補自体を除外しない)。
    # 主ひずみ方向はフィルタ適用時に使うだけで、無ければNoneとしてフィルタ側で扱う。
    subset_list, p_angle_list = [], []
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

    half = SUBSET_SIZE // 2
    keep_idx = [i for i, (cx, cy, u0, v0) in enumerate(subset_list)
                if cy - half - 3 >= 0 and cy + half + 3 < deformed.shape[0]
                and cx - half - 3 >= 0 and cx + half + 3 < deformed.shape[1]]
    subset_list = [subset_list[i] for i in keep_idx]
    p_angle_list = [p_angle_list[i] for i in keep_idx]
    print(f"  計算対象の全格子点数: {len(subset_list)}")

    min_side = int(SUBSET_SIZE * SUBSET_SIZE * MIN_SIDE_FRAC)
    global_cache = GpuMaskCache(theta_center=0.0, theta_range=90.0, theta_step=1.0,
                                 subset_size=SUBSET_SIZE)

    t0 = time.perf_counter()
    results, n_full, n_boundary, batch_size = process_grain(
        ref, deformed, subset_list, global_cache, min_side_px=min_side)
    elapsed = time.perf_counter() - t0
    print(f"  処理時間: {elapsed:.1f}s")

    filtered = []
    n_no_strain = 0
    for (cx, cy, u0, v0), p_ang, r in zip(subset_list, p_angle_list, results):
        if r is None:
            continue
        mag = float(np.sqrt(r["delta_s"] ** 2 + r["delta_n"] ** 2))
        if mag < DELTA_THR:
            continue
        if p_ang is None:
            # 主ひずみが欠損している場所はこのフィルタを適用できないので、
            # delta条件のみで判定する(候補自体は捨てない)
            n_no_strain += 1
            filtered.append(r)
            continue
        if ang_dist(r["theta"], p_ang + 90) > PNORM_THR:
            continue
        filtered.append(r)
    print(f"  delta+主ひずみ法線フィルタ後: {len(filtered)} "
          f"(うち主ひずみ欠損でdelta単独判定: {n_no_strain})")

    final = hdic.filter_by_neighbors(filtered)
    print(f"  filter_by_neighbors後: {len(final)}")

    return deformed, final, len(subset_list), len(filtered), len(final)


def main():
    print(f"device: {DEVICE}")
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    coord_to_grain = hdic.load_grain_assignment(xlsx_path)

    stage_data = {}
    summary = []
    for label in STAGES:
        deformed, final, n_total, n_filt, n_final = process_stage(label, coord_to_grain)
        stage_data[label] = (deformed, final)
        summary.append((label, n_total, n_filt, n_final))

    vlim = MANUAL_VLIM if MANUAL_VLIM is not None else None
    if vlim is None:
        _, vlim_results = stage_data[VLIM_SOURCE_STAGE]
        all_ds = [r["delta_s"] for r in vlim_results]
        vlim = float(max(abs(min(all_ds)), abs(max(all_ds))))
    print(f"\n固定カラーレンジ: ±{vlim:.3f} px")

    for label in STAGES:
        deformed, final = stage_data[label]
        dst_png = os.path.join(OUT_DIR, f"full_field_{label}_delta_s.png")
        _save_png_fixed(final, deformed, dst_png,
                         label=f"{label} (GPU, no grain/Hough, 41px+40%+delta0.8+pnorm30, "
                               f"固定レンジ±{vlim:.2f}px)",
                         subset_size=SUBSET_SIZE, vlim=vlim)

    print("\n" + "=" * 60)
    print(f"{'stage':>8} {'計算対象':>8} {'フィルタ後':>10} {'最終':>8}")
    for label, n_total, n_filt, n_final in summary:
        print(f"{label:>8} {n_total:>8} {n_filt:>10} {n_final:>8}")
    print("=" * 60)


if __name__ == "__main__":
    main()
