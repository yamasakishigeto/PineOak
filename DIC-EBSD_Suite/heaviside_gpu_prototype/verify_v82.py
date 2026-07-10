"""
heaviside_dic_v82.py の run_heaviside_dic(use_gpu=True) を実データで実行し、
golden_1100MPa.npz(無改変v81の生データ)ではなく、より直接的に
v82 の use_gpu=False(既存シリアル分岐、v81と同一ロジック)の出力と
use_gpu=True(新GPU分岐)の出力を突き合わせる。

これにより「v82に統合した後もCPU分岐とGPU分岐が同じ結果を返すか」を
実際の公開インターフェース(run_heaviside_dic)経由で検証できる。
"""
import os
import sys
import time

import numpy as np

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)

import heaviside_dic_v82 as hdic82

DATA_DIR = r"C:\Claude_Code_Tanaka_Lab\PineOak\7_Heaviside_DIC_X750"
SCRATCH = os.environ.get("CLAUDE_SCRATCHPAD", os.path.dirname(os.path.abspath(__file__)))

GRAIN_THETA_MAP = {1: 74.0, 7: 73.5, 14: -45.0, 15: 73.0, 18: -63.0}
GRAIN_THR_MAP = {1: 0.1007, 7: 0.1007, 14: 0.0909, 15: 0.0909, 18: 0.1291}


def run(use_gpu):
    xlsx_path = os.path.join(DATA_DIR, "dic_results_georef.xlsx")
    ref_path = os.path.join(DATA_DIR, "SEM_images", "0MPa.bmp")
    def_path = os.path.join(DATA_DIR, "SEM_images", "1100MPa.bmp")
    alignment_json_path = os.path.join(DATA_DIR, "sem_alignment.json")
    coord_to_grain = hdic82.load_grain_assignment(xlsx_path)

    out_path = os.path.join(
        SCRATCH, f"v82_{'gpu' if use_gpu else 'cpu'}_output.xlsx")

    t0 = time.perf_counter()
    results, deformed, subset_list = hdic82.run_heaviside_dic(
        xlsx_path=xlsx_path, ref_path=ref_path, def_path=def_path,
        label="1100MPa", grain_theta_map=GRAIN_THETA_MAP,
        coord_to_grain=coord_to_grain, out_path=out_path,
        n_jobs=1, alignment_json_path=alignment_json_path,
        grain_thr_map=GRAIN_THR_MAP, use_gpu=use_gpu,
    )
    elapsed = time.perf_counter() - t0
    return results, elapsed


def main():
    print(f"GPU_AVAILABLE = {hdic82.GPU_AVAILABLE}")

    print("\n--- use_gpu=False (既存シリアル分岐, v81と同一ロジック) ---")
    cpu_results, cpu_elapsed = run(use_gpu=False)
    print(f"CPU: {len(cpu_results)}件  {cpu_elapsed:.1f}s")

    print("\n--- use_gpu=True (新GPU分岐) ---")
    gpu_results, gpu_elapsed = run(use_gpu=True)
    print(f"GPU: {len(gpu_results)}件  {gpu_elapsed:.2f}s")

    cpu_by_loc = {(r["cx"], r["cy"]): r for r in cpu_results}
    gpu_by_loc = {(r["cx"], r["cy"]): r for r in gpu_results}

    common = set(cpu_by_loc) & set(gpu_by_loc)
    only_cpu = set(cpu_by_loc) - set(gpu_by_loc)
    only_gpu = set(gpu_by_loc) - set(cpu_by_loc)

    theta_match = offset_match = 0
    s_diffs, n_diffs = [], []
    for loc in common:
        rc, rg = cpu_by_loc[loc], gpu_by_loc[loc]
        if abs(rc["theta"] - rg["theta"]) < 1e-6:
            theta_match += 1
        if abs(rc["offset"] - rg["offset"]) < 1e-6:
            offset_match += 1
        s_diffs.append(abs(rc["delta_s"] - rg["delta_s"]))
        n_diffs.append(abs(rc["delta_n"] - rg["delta_n"]))

    print("\n" + "=" * 60)
    print(f"CPU件数={len(cpu_results)}  GPU件数={len(gpu_results)}  "
          f"共通座標={len(common)}  CPUのみ={len(only_cpu)}  GPUのみ={len(only_gpu)}")
    print(f"theta一致: {theta_match}/{len(common)}  offset一致: {offset_match}/{len(common)}")
    if s_diffs:
        print(f"delta_s差分 max={max(s_diffs):.3e}  mean={np.mean(s_diffs):.3e}")
        print(f"delta_n差分 max={max(n_diffs):.3e}  mean={np.mean(n_diffs):.3e}")
    print(f"速度: CPU {cpu_elapsed:.1f}s -> GPU {gpu_elapsed:.2f}s "
          f"({cpu_elapsed/gpu_elapsed:.1f}倍)")
    print("=" * 60)


if __name__ == "__main__":
    main()
