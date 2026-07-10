"""
Step1: ゴールデンデータ生成

無改変の heaviside_dic_v81.py の process_one_subset を、実データ
(7_Heaviside_DIC_X750, 1100MPa, 5粒476サブセット)に対してそのまま
実行し、全サブセットの入力(cx, cy, u_init, v_init, theta_center)と
出力(theta, offset, ncc_A, ncc_B, u_A/v_A/u_B/v_B, delta_u/v/s/n)を
1件も欠かさず golden_1100MPa.npz に保存する。

filter_by_neighbors 等の後処理は一切適用しない「生の」結果を保存する。
これは GPU 版の各ステージ(shift探索単体 / マスク行列導入 / バッチ化)を
process_one_subset 単位で検証するための正解データであり、後処理込みの
最終結果とは対象が異なる。

heaviside_dic_v81.py は一切変更しない（モンキーパッチのみ）。
n_jobs=1 で強制シリアル実行することで、モンキーパッチをサブプロセスに
持ち出さずに済ませる（joblib の loky バックエンドはサブプロセス内で
モジュールを再 import するため、パッチが効かない）。

使い方:
    python make_golden.py
"""
import os
import sys
import time

import numpy as np

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)

import heaviside_dic_v81 as hdic

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
OUT_DIR = os.environ.get("CLAUDE_SCRATCHPAD", os.path.dirname(os.path.abspath(__file__)))
DUMMY_XLSX_OUT = os.path.join(OUT_DIR, "make_golden_dummy_output.xlsx")
GOLDEN_NPZ = os.path.join(OUT_DIR, "golden_1100MPa.npz")

GRAIN_THETA_MAP = {1: 74.0, 7: 73.5, 14: -45.0, 15: 73.0, 18: -63.0}
GRAIN_THR_MAP = {1: 0.1007, 7: 0.1007, 14: 0.0909, 15: 0.0909, 18: 0.1291}

FIELDS = [
    "cx", "cy", "u_init", "v_init", "theta_center", "grain_id",
    "valid", "theta", "offset", "ncc_A", "ncc_B",
    "u_A", "v_A", "u_B", "v_B",
    "delta_u", "delta_v", "delta_s", "delta_n",
]


def main():
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    ref_path = os.path.join(DATA_DIR, "SEM_images", "0MPa.bmp")
    def_path = os.path.join(DATA_DIR, "SEM_images", "1100MPa.bmp")
    alignment_json_path = os.path.join(DATA_DIR, "sem_alignment.json")

    coord_to_grain = hdic.load_grain_assignment(xlsx_path)

    records = []
    orig_process_one_subset = hdic.process_one_subset

    def recording_process_one_subset(ref, deformed, cx, cy, u_init, v_init,
                                      theta_center, offset_max=None,
                                      min_side_px=None, _mask_cache=None):
        result = orig_process_one_subset(
            ref, deformed, cx, cy, u_init, v_init, theta_center,
            offset_max=offset_max, min_side_px=min_side_px,
            _mask_cache=_mask_cache)
        row = dict(cx=cx, cy=cy, u_init=u_init, v_init=v_init,
                   theta_center=theta_center,
                   grain_id=coord_to_grain.get((cx, cy), -1))
        if result is None:
            row["valid"] = False
            for k in ["theta", "offset", "ncc_A", "ncc_B",
                      "u_A", "v_A", "u_B", "v_B",
                      "delta_u", "delta_v", "delta_s", "delta_n"]:
                row[k] = np.nan
        else:
            row["valid"] = True
            for k in ["theta", "offset", "ncc_A", "ncc_B",
                      "u_A", "v_A", "u_B", "v_B",
                      "delta_u", "delta_v", "delta_s", "delta_n"]:
                row[k] = result[k]
        records.append(row)
        return result

    hdic.process_one_subset = recording_process_one_subset

    t0 = time.perf_counter()
    try:
        hdic.run_heaviside_dic(
            xlsx_path=xlsx_path,
            ref_path=ref_path,
            def_path=def_path,
            label="1100MPa",
            grain_theta_map=GRAIN_THETA_MAP,
            coord_to_grain=coord_to_grain,
            out_path=DUMMY_XLSX_OUT,
            n_jobs=1,
            alignment_json_path=alignment_json_path,
            grain_thr_map=GRAIN_THR_MAP,
        )
    finally:
        hdic.process_one_subset = orig_process_one_subset
    elapsed = time.perf_counter() - t0

    n = len(records)
    n_valid = sum(r["valid"] for r in records)
    print(f"\n記録サブセット数: {n}  (valid={n_valid}, skipped={n - n_valid})")
    print(f"生成時間: {elapsed:.1f} s")

    arrays = {f: np.array([r[f] for r in records]) for f in FIELDS}
    np.savez(GOLDEN_NPZ, **arrays)
    print(f"Saved golden data: {GOLDEN_NPZ}")


if __name__ == "__main__":
    main()
