"""
mprime_debug.py
===============
m' 計算のステップごとデバッグ出力スクリプト。
STABIXの参照値と比較するために使用。

使い方:
    cd DIC-EBSD_Suite
    python mprime_debug.py <phi1_i> <PHI_i> <phi2_i> <phi1_j> <PHI_j> <phi2_j> [lx ly lz]

例 (FCC, 荷重X方向, 2粒のオイラー角を手入力):
    python mprime_debug.py 10 20 30 50 40 60 1 0 0

※ オイラー角はすべて degrees, 荷重方向はデフォルト [1,0,0]
"""

from __future__ import annotations
import sys
import numpy as np

# ─────────────────────────────────────────────────────────────────────
# Bunge ZXZ オイラー角 → 回転行列 (crystal → sample)
# ─────────────────────────────────────────────────────────────────────

def euler_to_rotmat(phi1_deg, PHI_deg, phi2_deg) -> np.ndarray:
    phi1 = np.radians(phi1_deg)
    PHI  = np.radians(PHI_deg)
    phi2 = np.radians(phi2_deg)
    c1, c, c2 = np.cos(phi1), np.cos(PHI), np.cos(phi2)
    s1, s, s2 = np.sin(phi1), np.sin(PHI), np.sin(phi2)
    return np.array([
        [ c1*c2 - s1*s2*c,  s1*c2 + c1*s2*c,  s2*s],
        [-c1*s2 - s1*c2*c, -s1*s2 + c1*c2*c,  c2*s],
        [ s1*s,            -c1*s,               c   ],
    ])


# ─────────────────────────────────────────────────────────────────────
# 対称操作取得 (proper cubic group O: 24 ops)
# ─────────────────────────────────────────────────────────────────────

def get_cubic_sym() -> np.ndarray:
    from scipy.spatial.transform import Rotation
    return Rotation.create_group("O").as_matrix()


# ─────────────────────────────────────────────────────────────────────
# すべり系パース (FCC デフォルト: (111)[1-10])
# ─────────────────────────────────────────────────────────────────────

def parse_slip(plane_text="1 1 1", dir_text="1 -1 0"):
    n_raw = np.array([float(x) for x in plane_text.split()])
    b_raw = np.array([float(x) for x in dir_text.split()])
    n_unit = n_raw / np.linalg.norm(n_raw)
    b_unit = b_raw / np.linalg.norm(b_raw)
    dot = abs(float(n_unit @ b_unit))
    if dot > 1e-4:
        raise ValueError(f"n·b = {dot:.6f} ≠ 0: すべり方向がすべり面内にありません")
    return n_unit, b_unit


# ─────────────────────────────────────────────────────────────────────
# メイン計算（ステップごと出力）
# ─────────────────────────────────────────────────────────────────────

def compute_mprime_verbose(
        euler_i, euler_j,
        load_vec    = np.array([1.0, 0.0, 0.0]),
        plane_text  = "1 1 1",
        dir_text    = "1 -1 0",
        sym         = None,
):
    """2粒のオイラー角から m' をステップごと計算して表示する。

    Parameters
    ----------
    euler_i, euler_j : (phi1_deg, PHI_deg, phi2_deg)
    load_vec         : sample-frame 荷重方向 (正規化不要)
    plane_text, dir_text : すべり面・方向の Miller 指数
    sym              : (S,3,3) 対称操作行列 (None → cubic)
    """
    if sym is None:
        sym = get_cubic_sym()

    n_slip, b_slip = parse_slip(plane_text, dir_text)
    load = load_vec / np.linalg.norm(load_vec)

    n_equivs = sym @ n_slip   # (S, 3) crystal frame
    b_equivs = sym @ b_slip   # (S, 3) crystal frame
    S = len(sym)

    print("=" * 65)
    print("  m' デバッグ計算")
    print("=" * 65)
    print(f"  すべり系   : ({plane_text})[{dir_text}]")
    print(f"  荷重方向   : {load}")
    print(f"  対称操作数 : {S}")
    print()

    for label, euler in [("Grain i", euler_i), ("Grain j", euler_j)]:
        phi1, PHI, phi2 = euler
        g = euler_to_rotmat(phi1, PHI, phi2)

        # 荷重軸を結晶座標系へ変換: g @ load_sample
        load_crys = g @ load          # (3,)

        # Schmid 因子をすべての等価系で計算
        cos_phi_all = np.abs(load_crys @ n_equivs.T)   # (S,)
        cos_lam_all = np.abs(load_crys @ b_equivs.T)   # (S,)
        schmid_all  = cos_phi_all * cos_lam_all         # (S,)
        s_star      = int(schmid_all.argmax())

        n_crys_active = n_equivs[s_star]  # crystal frame
        b_crys_active = b_equivs[s_star]  # crystal frame
        n_samp_active = g.T @ n_crys_active  # sample frame
        b_samp_active = g.T @ b_crys_active  # sample frame

        print(f"  --- {label} (phi1={phi1} PHI={PHI} phi2={phi2}) ---")
        print(f"    g (sample→crystal):\n{g}")
        print(f"    荷重 (crystal frame)  : {load_crys}")
        print(f"    最大 Schmid 系 index  : {s_star}/{S-1}")
        print(f"    n_crystal (active)   : {n_crys_active}")
        print(f"    b_crystal (active)   : {b_crys_active}")
        print(f"    n_sample  (active)   : {n_samp_active}")
        print(f"    b_sample  (active)   : {b_samp_active}")
        print(f"    |cos φ|              : {cos_phi_all[s_star]:.6f}")
        print(f"    |cos λ|              : {cos_lam_all[s_star]:.6f}")
        print(f"    Schmid factor (max)  : {schmid_all[s_star]:.6f}")
        print()

        if label == "Grain i":
            n_i = n_samp_active
            b_i = b_samp_active
        else:
            n_j = n_samp_active
            b_j = b_samp_active

    cos_psi = float(np.abs(n_i @ n_j))
    cos_kap = float(np.abs(b_i @ b_j))
    mp_single = cos_psi * cos_kap

    print("  ─── m' (シュミット最大1系統同士) ───")
    print(f"    |cos ψ|  = |n_i · n_j| = {cos_psi:.6f}")
    print(f"    |cos κ|  = |b_i · b_j| = {cos_kap:.6f}")
    print(f"    m'       = {mp_single:.6f}")
    print()

    # 全組合せ最大値 (比較参考)
    n_samp_all_i = (g_i := euler_to_rotmat(*euler_i)).T @ n_equivs.T  # (3, S)
    b_samp_all_i = g_i.T @ b_equivs.T
    n_samp_all_j = (g_j := euler_to_rotmat(*euler_j)).T @ n_equivs.T
    b_samp_all_j = g_j.T @ b_equivs.T
    # (S_i, S_j) Schmid factor * m' factor
    cos_psi_all = np.abs(n_samp_all_i.T @ n_samp_all_j)   # (S, S)
    cos_kap_all = np.abs(b_samp_all_i.T @ b_samp_all_j)   # (S, S)
    mp_all = cos_psi_all * cos_kap_all                      # (S, S)
    mp_max = float(mp_all.max())
    idx_max = np.unravel_index(mp_all.argmax(), mp_all.shape)

    print("  ─── m' 参考 (全組合せ最大値) ───")
    print(f"    argmax インデックス (si={idx_max[0]}, sj={idx_max[1]})")
    print(f"    max m' = {mp_max:.6f}")
    print("=" * 65)

    return mp_single, mp_max


# ─────────────────────────────────────────────────────────────────────
# mat ファイルから粒界ペアを解析して m' 分布を表示
# ─────────────────────────────────────────────────────────────────────

def analyze_mat(
        mat_path: str,
        plane_text = "1 1 1",
        dir_text   = "1 -1 0",
        load_vec   = np.array([1.0, 0.0, 0.0]),
        top_n      = 20,
):
    """mat ファイルを読み込み、粒界 m' の分布と上位ペアを表示する。

    使い方:
        python mprime_debug.py mat /path/to/file.mat [lx ly lz]
    """
    import scipy.io as sio
    from pathlib import Path

    mat = {k: v for k, v in sio.loadmat(mat_path).items() if not k.startswith("__")}
    cx  = mat['cx'].flatten()
    cy  = mat['cy'].flatten()
    gid = mat['grain_id'].flatten()

    # オイラー角 (参照ステージ優先)
    def _get(keys):
        for k in keys:
            if k in mat:
                return mat[k].flatten().astype(float)
        raise KeyError(f"Not found: {keys}")
    phi1 = _get(['phi1_ref', 'euler_phi1'])
    PHI  = _get(['PHI_ref',  'euler_phi'])
    phi2 = _get(['phi2_ref', 'euler_phi2'])

    N    = len(cx)
    valid = (
        (gid != -1)
        & np.isfinite(phi1) & np.isfinite(PHI) & np.isfinite(phi2)
    )

    sym      = get_cubic_sym()
    n_slip, b_slip = parse_slip(plane_text, dir_text)
    n_equivs = sym @ n_slip
    b_equivs = sym @ b_slip
    load     = load_vec / np.linalg.norm(load_vec)

    # 全点の回転行列
    g_all = np.full((N, 3, 3), np.nan)
    if np.any(valid):
        p1v = np.radians(phi1[valid]); Pv = np.radians(PHI[valid]); p2v = np.radians(phi2[valid])
        c1, c, c2 = np.cos(p1v), np.cos(Pv), np.cos(p2v)
        s1, s, s2 = np.sin(p1v), np.sin(Pv), np.sin(p2v)
        Nv = c1.shape[0]
        g_v = np.zeros((Nv, 3, 3))
        g_v[:,0,0]=c1*c2-s1*s2*c; g_v[:,0,1]=s1*c2+c1*s2*c; g_v[:,0,2]=s2*s
        g_v[:,1,0]=-c1*s2-s1*c2*c; g_v[:,1,1]=-s1*s2+c1*c2*c; g_v[:,1,2]=c2*s
        g_v[:,2,0]=s1*s; g_v[:,2,1]=-c1*s; g_v[:,2,2]=c
        g_all[valid] = g_v

    # 隣接ペア生成
    xs = np.unique(cx); ys = np.unique(cy)
    sx = float(np.min(np.diff(np.sort(xs)))) if len(xs)>1 else 1.0
    sy = float(np.min(np.diff(np.sort(ys)))) if len(ys)>1 else 1.0
    ix = np.round((cx-xs.min())/sx).astype(int)
    iy = np.round((cy-ys.min())/sy).astype(int)
    grid = {(int(a),int(b)):k for k,(a,b) in enumerate(zip(ix,iy))}
    adj = []
    for k,(a,b) in enumerate(zip(ix,iy)):
        for da,db in [(1,0),(0,1)]:
            nb=grid.get((int(a)+da,int(b)+db))
            if nb is not None:
                adj.append((k,nb) if k<nb else (nb,k))

    # 粒界ペアだけ
    gb_pairs = [(i,j) for i,j in adj if gid[i]!=gid[j] and valid[i] and valid[j]]

    if not gb_pairs:
        print("粒界ペアが見つかりませんでした。")
        return

    # 全点の m' を一括計算
    arr     = np.array(gb_pairs, int)
    all_pts = np.unique(arr)
    g_pts   = g_all[all_pts]

    load_crys = np.einsum('nij,j->ni', g_pts, load)
    cos_phi   = np.abs(load_crys @ n_equivs.T)
    cos_lam   = np.abs(load_crys @ b_equivs.T)
    max_idx   = (cos_phi * cos_lam).argmax(axis=1)

    n_active = np.einsum('nji,nj->ni', g_pts, n_equivs[max_idx])
    b_active = np.einsum('nji,nj->ni', g_pts, b_equivs[max_idx])

    pt_to_u = {pt:u for u,pt in enumerate(all_pts)}
    ui = np.array([pt_to_u[i] for i,_ in gb_pairs])
    uj = np.array([pt_to_u[j] for _,j in gb_pairs])
    mp_arr = np.clip(
        np.abs(np.einsum('pi,pi->p', n_active[ui], n_active[uj])) *
        np.abs(np.einsum('pi,pi->p', b_active[ui], b_active[uj])),
        0.0, 1.0,
    )

    print(f"\n粒界数: {len(gb_pairs)}")
    print(f"m' 統計: min={mp_arr.min():.4f}  max={mp_arr.max():.4f}  "
          f"mean={mp_arr.mean():.4f}  median={np.median(mp_arr):.4f}")

    buckets = [0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.001]
    print("\nヒストグラム:")
    for lo,hi in zip(buckets[:-1],buckets[1:]):
        cnt = int(np.sum((mp_arr>=lo)&(mp_arr<hi)))
        bar = "█" * (cnt*40//len(gb_pairs)+1 if cnt else 0)
        print(f"  [{lo:.1f},{hi:.1f}) {cnt:5d}  {bar}")

    order = np.argsort(mp_arr)[::-1]
    print(f"\n上位 {top_n} ペア (m' 高い順):")
    print(f"  {'rank':>4} {'m\'':>6}  {'gid_i':>6} {'gid_j':>6}  "
          f"{'φ1_i':>7} {'Φ_i':>7} {'φ2_i':>7}    "
          f"{'φ1_j':>7} {'Φ_j':>7} {'φ2_j':>7}")
    for rank, idx in enumerate(order[:top_n], 1):
        i, j = gb_pairs[idx]
        print(f"  {rank:4d} {mp_arr[idx]:6.4f}  {gid[i]:6.0f} {gid[j]:6.0f}  "
              f"{phi1[i]:7.2f} {PHI[i]:7.2f} {phi2[i]:7.2f}    "
              f"{phi1[j]:7.2f} {PHI[j]:7.2f} {phi2[j]:7.2f}")

    print(f"\n下位 {top_n} ペア (m' 低い順):")
    print(f"  {'rank':>4} {'m\'':>6}  {'gid_i':>6} {'gid_j':>6}  "
          f"{'φ1_i':>7} {'Φ_i':>7} {'φ2_i':>7}    "
          f"{'φ1_j':>7} {'Φ_j':>7} {'φ2_j':>7}")
    for rank, idx in enumerate(order[-top_n:][::-1], 1):
        i, j = gb_pairs[idx]
        print(f"  {rank:4d} {mp_arr[idx]:6.4f}  {gid[i]:6.0f} {gid[j]:6.0f}  "
              f"{phi1[i]:7.2f} {PHI[i]:7.2f} {phi2[i]:7.2f}    "
              f"{phi1[j]:7.2f} {PHI[j]:7.2f} {phi2[j]:7.2f}")


# ─────────────────────────────────────────────────────────────────────
# CLI エントリーポイント
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(0)

    # モード: "mat <path> [lx ly lz]"
    if args[0] == "mat":
        if len(args) < 2:
            print("使い方: python mprime_debug.py mat <matファイルパス> [lx ly lz]")
            sys.exit(1)
        mat_path = args[1]
        lv = np.array([float(x) for x in args[2:5]]) if len(args) >= 5 else np.array([1.0, 0.0, 0.0])
        print(f"ファイル: {mat_path}")
        print(f"荷重方向: {lv}")
        analyze_mat(mat_path, load_vec=lv)

    # モード: "phi1_i PHI_i phi2_i phi1_j PHI_j phi2_j [lx ly lz]"
    elif len(args) >= 6:
        euler_i = tuple(float(x) for x in args[0:3])
        euler_j = tuple(float(x) for x in args[3:6])
        lv = np.array([float(x) for x in args[6:9]]) if len(args) >= 9 else np.array([1.0, 0.0, 0.0])
        if len(args) < 9:
            print("(荷重方向未指定 → デフォルト [1,0,0] を使用)\n")
        compute_mprime_verbose(euler_i, euler_j, load_vec=lv)

    else:
        print(__doc__)
        sys.exit(1)
