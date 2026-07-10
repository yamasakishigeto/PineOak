"""
Step4 / Stage3: 複数サブセットのバッチ処理 + VRAM使用量に応じたバッチサイズ自動調整。

設計:
  Stage2はサブセット1件ずつGPU呼び出しを行っていた(2.09ms/subset、カーネル起動・
  転送オーバーヘッドが支配的)。Stage3では同一grain内の複数サブセットをまとめて
  バッチ化されたGEMM(torch.bmm)に載せ、オーバーヘッドを償却する。

  同一grainのサブセット群のうち、shift探索候補(49通り)が画像端で欠けない
  「フルセット(K=49)」のものだけをバッチ化する。画像端でKが49に満たない
  少数のサブセットは Stage2 の1件ずつのGEMM(stage2_mask_matmul.gpu_process_one_subset)
  にフォールバックする(正当性が既に検証済みのため)。

  VRAM自動調整: torch.cuda.mem_get_info() で空きVRAMを取得し、1サブセットあたりの
  推定メモリ使用量から安全率を掛けたバッチサイズを決定し、必要なら複数チャンクに
  分割して処理する。

検証:
  golden_1100MPa.npz の全476サブセットに対し、grainごとにバッチ処理した結果を
  Stage2と同じ2段階(theta/offset選択一致、ncc・delta_s/delta_n誤差)で検証し、
  Stage2比でどれだけ追加の高速化が得られたかを計測する。

heaviside_dic_v81.py は一切変更しない。
"""
import os
import sys
import time

import numpy as np
import torch

SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import heaviside_dic_v81 as hdic
from stage2_mask_matmul import (
    GpuMaskCache, gpu_process_one_subset, load_ref_deformed,
    GRAIN_THETA_MAP, GRAIN_THR_MAP, DEVICE, GOLDEN_NPZ,
)

torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

SEARCH_RANGE = 3
K_FULL = (2 * SEARCH_RANGE + 1) ** 2  # 49


def is_full_range(ref_shape, deformed_shape, cx, cy, subset_size,
                   search_range=SEARCH_RANGE):
    half = subset_size // 2
    if (cy - half < 0 or cy + half >= ref_shape[0] or
            cx - half < 0 or cx + half >= ref_shape[1]):
        return False
    lo, hi = -search_range, search_range
    cy_min, cy_max = cy + lo - half, cy + hi + half
    cx_min, cx_max = cx + lo - half, cx + hi + half
    return (cy_min >= 0 and cy_max < deformed_shape[0] and
            cx_min >= 0 and cx_max < deformed_shape[1])


def estimate_bytes_per_subset(n_combo2, k=K_FULL, elem_bytes=4):
    """FM_batch, G_batch, および中間テンソル(sumG/sumG2/sumFG/numer/denomF系/ncc)の
    概算メモリ使用量[bytes/subset]。安全側に多めに見積もる。"""
    fm = n_combo2 * 961          # (2C,961) per subset
    g = k * 961                  # (K,961) per subset
    inter = 6 * k * n_combo2     # sumG, sumG2, sumFG, numer, denom, ncc 相当
    return elem_bytes * (fm + g + inter) * 1.3  # 1.3倍の安全マージン


def choose_batch_size(n_combo2, n_subsets, device=DEVICE, safety_fraction=0.4):
    if device.type != "cuda":
        return max(1, n_subsets)
    free_bytes, _total = torch.cuda.mem_get_info(device)
    budget = free_bytes * safety_fraction
    per_subset = estimate_bytes_per_subset(n_combo2)
    batch_size = max(1, int(budget // per_subset))
    return min(batch_size, n_subsets)


def gpu_process_batch(ref, deformed, subset_list, mask_cache,
                       min_side_px=None, device=DEVICE):
    """subset_list: [(cx,cy,u_init,v_init), ...]  全件 K=K_FULL(49) 前提。
    戻り値: subset_listと同じ順のdictリスト(有効解が無いものはNone)。"""
    if min_side_px is None:
        min_side_px = hdic.MIN_SIDE_PX
    subset_size = mask_cache.subset_size
    half = subset_size // 2
    S = len(subset_list)

    F_list, G_list = [], []
    du0_list, dv0_list = [], []
    ref_patches = []
    for cx, cy, u_init, v_init in subset_list:
        du0, dv0 = int(round(u_init)), int(round(v_init))
        du0_list.append(du0)
        dv0_list.append(dv0)
        patches = []
        for dv in range(dv0 - SEARCH_RANGE, dv0 + SEARCH_RANGE + 1):
            for du in range(du0 - SEARCH_RANGE, du0 + SEARCH_RANGE + 1):
                cy_def, cx_def = cy + dv, cx + du
                patches.append(deformed[cy_def - half:cy_def + half + 1,
                                         cx_def - half:cx_def + half + 1])
        G_list.append(np.stack(patches).reshape(K_FULL, -1))
        rp = ref[cy - half:cy + half + 1, cx - half:cx + half + 1].astype(np.float32)
        ref_patches.append(rp)
        F_list.append(rp.flatten())

    F_batch = torch.from_numpy(np.stack(F_list).astype(np.float32)).to(device)   # (S,961)
    G_batch = torch.from_numpy(np.stack(G_list).astype(np.float32)).to(device)   # (S,K,961)

    M = mask_cache.combined_mask     # (2C,961)
    N = mask_cache.N                 # (2C,)
    n_combo2 = M.shape[0]

    sumF = F_batch @ M.T                          # (S,2C)
    sumF2 = (F_batch * F_batch) @ M.T              # (S,2C)
    sumG = torch.einsum("skp,cp->skc", G_batch, M)         # (S,K,2C)
    sumG2 = torch.einsum("skp,cp->skc", G_batch * G_batch, M)  # (S,K,2C)
    FM = F_batch.unsqueeze(1) * M.unsqueeze(0)     # (S,2C,961)
    sumFG = torch.bmm(G_batch, FM.transpose(1, 2))  # (S,K,961)@(S,961,2C) -> (S,K,2C)

    numer = sumFG - sumF.unsqueeze(1) * sumG / N.view(1, 1, -1)
    denomF = (sumF2 - sumF * sumF / N.view(1, -1)).clamp(min=0.0)      # (S,2C)
    denomG = (sumG2 - sumG * sumG / N.view(1, 1, -1)).clamp(min=0.0)   # (S,K,2C)
    denom = torch.sqrt(denomF.unsqueeze(1) * denomG)

    eps = 1e-7
    valid_shift = denom > eps
    ncc = torch.where(valid_shift, numer / denom.clamp(min=eps),
                       torch.full_like(numer, float("-inf")))
    mask_degenerate = denomF < eps * eps            # (S,2C)
    ncc = torch.where(mask_degenerate.unsqueeze(1),
                       torch.full_like(ncc, float("-inf")), ncc)

    best_ncc, best_shift_idx = ncc.max(dim=1)        # (S,2C), (S,2C)

    n_combo = mask_cache.n_combo
    ncc_A, ncc_B = best_ncc[:, :n_combo], best_ncc[:, n_combo:]
    N_A, N_B = N[:n_combo], N[n_combo:]

    valid_combo = ((N_A.view(1, -1) >= min_side_px) & (N_B.view(1, -1) >= min_side_px) &
                   (ncc_A >= hdic.NCC_THRESHOLD) & (ncc_B >= hdic.NCC_THRESHOLD) &
                   torch.isfinite(ncc_A) & torch.isfinite(ncc_B))     # (S,n_combo)

    score = torch.where(valid_combo, ncc_A + ncc_B,
                         torch.full_like(ncc_A, float("-inf")))       # (S,n_combo)
    best_i = torch.argmax(score, dim=1)              # (S,)
    best_score = score.gather(1, best_i.view(-1, 1)).squeeze(1)

    results = []
    for s in range(S):
        if not torch.isfinite(best_score[s]):
            results.append(None)
            continue
        cx, cy, u_init, v_init = subset_list[s]
        i = int(best_i[s].item())
        theta_best, offset_best = mask_cache.combos[i]
        shift_idx_A = int(best_shift_idx[s, i].item())
        shift_idx_B = int(best_shift_idx[s, n_combo + i].item())
        k = 2 * SEARCH_RANGE + 1  # 各サブセット固有のdu0,dv0を基準にインデックスを復元
        dv_off_A, du_off_A = divmod(shift_idx_A, k)
        dv_off_B, du_off_B = divmod(shift_idx_B, k)
        du_A = du0_list[s] - SEARCH_RANGE + du_off_A
        dv_A = dv0_list[s] - SEARCH_RANGE + dv_off_A
        du_B = du0_list[s] - SEARCH_RANGE + du_off_B
        dv_B = dv0_list[s] - SEARCH_RANGE + dv_off_B

        mask_A, mask_B = hdic.make_heaviside_mask(subset_size, theta_best, offset_best)
        ref_patch = ref_patches[s]

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
        results.append(dict(
            cx=cx, cy=cy, theta=theta_best, offset=offset_best,
            u_A=u_A, v_A=v_A, u_B=u_B, v_B=v_B,
            ncc_A=float(ncc_A[s, i].item()), ncc_B=float(ncc_B[s, i].item()),
            delta_u=du, delta_v=dv,
            delta_s=du * np.cos(theta_rad) + dv * np.sin(theta_rad),
            delta_n=du * (-np.sin(theta_rad)) + dv * np.cos(theta_rad),
        ))
    return results


def process_grain(ref, deformed, subset_list, mask_cache, min_side_px=None):
    """subset_list: [(cx,cy,u_init,v_init), ...]。フル49件のものはバッチ化、
    画像端で欠けるものは Stage2 の1件ずつ処理にフォールバックする。
    戻り値: subset_listと同じ順のdictリスト。"""
    full_idx, boundary_idx = [], []
    for idx, (cx, cy, u_init, v_init) in enumerate(subset_list):
        if is_full_range(ref.shape, deformed.shape, cx, cy, mask_cache.subset_size):
            full_idx.append(idx)
        else:
            boundary_idx.append(idx)

    results = [None] * len(subset_list)

    for b in boundary_idx:
        cx, cy, u_init, v_init = subset_list[b]
        results[b] = gpu_process_one_subset(ref, deformed, cx, cy, u_init, v_init,
                                             mask_cache, min_side_px=min_side_px)

    if full_idx:
        n_combo2 = mask_cache.combined_mask.shape[0]
        batch_size = choose_batch_size(n_combo2, len(full_idx))
        for start in range(0, len(full_idx), batch_size):
            chunk_idx = full_idx[start:start + batch_size]
            chunk_subsets = [subset_list[i] for i in chunk_idx]
            chunk_results = gpu_process_batch(ref, deformed, chunk_subsets,
                                               mask_cache, min_side_px=min_side_px)
            for i, r in zip(chunk_idx, chunk_results):
                results[i] = r

    return results, len(full_idx), len(boundary_idx), (
        batch_size if full_idx else None)


def main():
    print(f"device: {DEVICE}")
    if DEVICE.type == "cuda":
        free_b, total_b = torch.cuda.mem_get_info(DEVICE)
        print(f"VRAM: free={free_b/1e9:.1f}GB / total={total_b/1e9:.1f}GB")

    print("ref/deformed を捕獲中(フルNCC探索はスキップ)...")
    ref, deformed = load_ref_deformed()

    d = np.load(GOLDEN_NPZ)
    valid_mask = d["valid"]
    n_total = len(d["cx"])

    mask_caches = {gid: GpuMaskCache(tc) for gid, tc in GRAIN_THETA_MAP.items()}

    # grainごとにサブセットをまとめる(golden配列のインデックス順を保持)
    by_grain = {}
    for i in range(n_total):
        if not valid_mask[i]:
            continue
        gid = int(d["grain_id"][i])
        by_grain.setdefault(gid, []).append(i)

    all_results = {}
    t0 = time.perf_counter()
    for gid, idxs in by_grain.items():
        subset_list = [(int(d["cx"][i]), int(d["cy"][i]),
                         float(d["u_init"][i]), float(d["v_init"][i])) for i in idxs]
        results, n_full, n_boundary, batch_size = process_grain(
            ref, deformed, subset_list, mask_caches[gid])
        print(f"  Grain {gid}: {len(idxs)}件  full={n_full}(batch_size={batch_size})  "
              f"boundary_fallback={n_boundary}")
        for i, r in zip(idxs, results):
            all_results[i] = r
    elapsed = time.perf_counter() - t0

    n_valid = sum(valid_mask)
    theta_match, offset_match = 0, 0
    ncc_a_diffs, ncc_b_diffs, s_diffs, n_diffs = [], [], [], []
    fail_records = []
    for i in range(n_total):
        if not valid_mask[i]:
            continue
        r = all_results.get(i)
        if r is None:
            fail_records.append((i, "gpu_returned_none"))
            continue
        theta_g, offset_g = float(d["theta"][i]), float(d["offset"][i])
        if abs(r["theta"] - theta_g) < 1e-6:
            theta_match += 1
        else:
            fail_records.append((i, "theta_mismatch", r["theta"], theta_g))
        if abs(r["offset"] - offset_g) < 1e-6:
            offset_match += 1
        else:
            fail_records.append((i, "offset_mismatch", r["offset"], offset_g))
        ncc_a_diffs.append(abs(r["ncc_A"] - float(d["ncc_A"][i])))
        ncc_b_diffs.append(abs(r["ncc_B"] - float(d["ncc_B"][i])))
        s_diffs.append(abs(r["delta_s"] - float(d["delta_s"][i])))
        n_diffs.append(abs(r["delta_n"] - float(d["delta_n"][i])))

    print("\n" + "=" * 60)
    print(f"検証件数: {n_valid}  実行時間: {elapsed:.2f}s "
          f"({elapsed/n_valid*1000:.3f} ms/subset)")
    print(f"[1段階目] theta一致: {theta_match}/{n_valid} ({theta_match/n_valid*100:.1f}%)  "
          f"offset一致: {offset_match}/{n_valid} ({offset_match/n_valid*100:.1f}%)")
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
