"""
Step0: NCC探索(process_one_subset)が run_heaviside_dic 全体のうち
何割の実行時間を占めるか実測する。

heaviside_dic_v81.py は一切変更しない。実行時にモジュール関数を
モンキーパッチして各 process_one_subset 呼び出し時間を計測する。
（joblib 並列だとサブプロセス内でパッチが効かないため n_jobs=1 で実行する）

使い方:
    python step0_profile.py
"""
import os
import sys
import time

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)

import heaviside_dic_v81 as hdic

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
OUT_PATH = os.path.join(
    os.environ.get("CLAUDE_SCRATCHPAD", os.path.dirname(os.path.abspath(__file__))),
    "step0_profile_output.xlsx",
)

# hdic_config_1100MPa.txt の「登録結晶粒」セクションをそのまま転記
GRAIN_THETA_MAP = {1: 74.0, 7: 73.5, 14: -45.0, 15: 73.0, 18: -63.0}
GRAIN_THR_MAP = {1: 0.1007, 7: 0.1007, 14: 0.0909, 15: 0.0909, 18: 0.1291}


def main():
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    ref_path = os.path.join(DATA_DIR, "SEM_images", "0MPa.bmp")
    def_path = os.path.join(DATA_DIR, "SEM_images", "1100MPa.bmp")
    alignment_json_path = os.path.join(DATA_DIR, "sem_alignment.json")

    coord_to_grain = hdic.load_grain_assignment(xlsx_path)

    subset_times = []
    orig_process_one_subset = hdic.process_one_subset

    def timed_process_one_subset(*args, **kwargs):
        t0 = time.perf_counter()
        result = orig_process_one_subset(*args, **kwargs)
        subset_times.append(time.perf_counter() - t0)
        return result

    hdic.process_one_subset = timed_process_one_subset

    t_start = time.perf_counter()
    try:
        results, deformed, subset_list = hdic.run_heaviside_dic(
            xlsx_path=xlsx_path,
            ref_path=ref_path,
            def_path=def_path,
            label="1100MPa",
            grain_theta_map=GRAIN_THETA_MAP,
            coord_to_grain=coord_to_grain,
            out_path=OUT_PATH,
            n_jobs=1,  # モンキーパッチを効かせるため強制シリアル実行
            alignment_json_path=alignment_json_path,
            grain_thr_map=GRAIN_THR_MAP,
        )
    finally:
        hdic.process_one_subset = orig_process_one_subset
    total_wall = time.perf_counter() - t_start

    ncc_total = sum(subset_times)
    overhead = total_wall - ncc_total
    n = len(subset_times)

    print("\n" + "=" * 60)
    print(f"サブセット数            : {n}")
    print(f"全体実行時間            : {total_wall:.3f} s")
    print(f"NCC探索(process_one_subset)合計 : {ncc_total:.3f} s "
          f"({ncc_total / total_wall * 100:.1f}%)")
    print(f"その他(画像読込/Hough等/Excel出力) : {overhead:.3f} s "
          f"({overhead / total_wall * 100:.1f}%)")
    if n:
        print(f"1サブセットあたり平均   : {ncc_total / n * 1000:.2f} ms")
        print(f"1サブセットあたり最大   : {max(subset_times) * 1000:.2f} ms")
        print(f"1サブセットあたり最小   : {min(subset_times) * 1000:.2f} ms")
    print("=" * 60)


if __name__ == "__main__":
    main()
