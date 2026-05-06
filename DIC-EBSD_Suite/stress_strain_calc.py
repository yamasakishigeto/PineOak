"""
stress_strain_calc.py
=====================
stress_strain_mapper_v2.py から分離したスタンドアロン計算関数群。
GUI に依存しない純粋な数値計算のみを含む。
"""

import re

import numpy as np
import pandas as pd
from scipy.io import loadmat
from scipy.spatial.transform import Rotation as _Rotation

# ============================================================
# 結晶対称性
# ============================================================

# PatRepモジュールと同じ対称群マッピング
SYM_GROUPS = {
    "Cubic":        "O",   # 48 ops
    "Hexagonal":    "D6",  # 24 ops
    "Tetragonal":   "D4",  # 16 ops
    "Trigonal":     "D3",  # 12 ops
    "Orthorhombic": "D2",  # 8 ops
    "Monoclinic":   "C2",  # 4 ops
    "Triclinic":    "C1",  # 1 op
}

def _get_sym_ops(sym_name: str) -> np.ndarray:
    """対称群名 → (S, 3, 3) 回転行列配列。"""
    group = SYM_GROUPS.get(sym_name, "O")
    return _Rotation.create_group(group).as_matrix()


# ============================================================
# スタンドアロン計算関数
# ============================================================

def parse_mat(path):
    """mat ファイルを static / per_stage / stages に分解する。"""
    raw = loadmat(str(path), squeeze_me=False)
    stage_pat = re.compile(r"^(.+)_s(\d+MPa)$")

    static = {}
    per_stage = {}
    stage_set = set()

    for key, val in raw.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, np.ndarray):
            continue
        arr = val.flatten()
        m = stage_pat.match(key)
        if m:
            base, stage = m.group(1), m.group(2)
            per_stage.setdefault(base, {})[stage] = arr
            stage_set.add(stage)
        else:
            static[key] = arr

    def stage_key(s):
        return int(re.match(r"(\d+)MPa", s).group(1))

    stages = sorted(stage_set, key=stage_key)
    return static, per_stage, stages


def build_ss(per_stage, x_base, y_base):
    """X/Y 軸変数の共通ステージで SS アレイを構築する。"""
    x_stages = set(per_stage[x_base].keys())
    y_stages = set(per_stage[y_base].keys())

    def stage_key(s):
        return int(re.match(r"(\d+)MPa", s).group(1))

    common_stages = sorted(x_stages & y_stages, key=stage_key)
    if not common_stages:
        raise ValueError(f"共通ステージが存在しません: {x_base} vs {y_base}")

    sv = np.column_stack([per_stage[x_base][st] for st in common_stages]).astype(float)
    ss = np.column_stack([per_stage[y_base][st] for st in common_stages]).astype(float)
    return sv, ss, common_stages


def compute_rss(phi1_deg, PHI_deg, phi2_deg,
                s11, s22, s33, s12, s23, s31,
                n_slip, b_slip, sym_ops,
                return_trace=False):
    """全等価すべり系の最大分解せん断応力（|RSS|）を計算する（ベクトル化）。

    Parameters
    ----------
    phi1_deg, PHI_deg, phi2_deg : ndarray shape (N,)  Bunge オイラー角 [degrees]
    s11..s31 : ndarray shape (N,)  対称応力テンソル成分
    n_slip : ndarray shape (3,)  すべり面法線（結晶直交座標系・正規化済み）
    b_slip : ndarray shape (3,)  すべり方向（結晶直交座標系・正規化済み）
    sym_ops : ndarray shape (S, 3, 3)  対称操作行列群
    return_trace : bool  True のとき XY 面上のトレース方向 (N, 2) も返す

    Returns
    -------
    rss : ndarray shape (N,)  最大 |RSS| 値
    trace : ndarray shape (N, 2)  トレース方向単位ベクトル（return_trace=True のとき）
    """
    N = len(phi1_deg)
    rss = np.full(N, np.nan)

    valid = (np.isfinite(phi1_deg) & np.isfinite(PHI_deg) & np.isfinite(phi2_deg)
             & np.isfinite(s11) & np.isfinite(s22) & np.isfinite(s33)
             & np.isfinite(s12) & np.isfinite(s23) & np.isfinite(s31))
    if not np.any(valid):
        if return_trace:
            return rss, np.full((N, 2), np.nan)
        return rss

    Nv = int(np.count_nonzero(valid))
    sigma = np.zeros((Nv, 3, 3))
    sigma[:, 0, 0] = s11[valid]; sigma[:, 1, 1] = s22[valid]; sigma[:, 2, 2] = s33[valid]
    sigma[:, 0, 1] = sigma[:, 1, 0] = s12[valid]
    sigma[:, 1, 2] = sigma[:, 2, 1] = s23[valid]
    sigma[:, 0, 2] = sigma[:, 2, 0] = s31[valid]

    g = _euler_to_matrices_rad(
        np.radians(phi1_deg[valid].astype(float)),
        np.radians(PHI_deg[valid].astype(float)),
        np.radians(phi2_deg[valid].astype(float)))

    # 等価すべり系を対称操作で生成: (S, 3)
    n_equivs = sym_ops @ n_slip
    b_equivs = sym_ops @ b_slip

    # 結晶座標系 → 試料座標系: shape (Nv, S, 3)
    n_samp = np.einsum('nij,sj->nsi', g, n_equivs)
    b_samp = np.einsum('nij,sj->nsi', g, b_equivs)

    # RSS[n,s] = n_samp[n,s] · sigma[n] · b_samp[n,s]
    sigma_b = np.einsum('nij,nsj->nsi', sigma, b_samp)   # (Nv, S, 3)
    rss_all = np.einsum('nsi,nsi->ns', n_samp, sigma_b)   # (Nv, S)

    max_idx = np.abs(rss_all).argmax(axis=1)              # (Nv,)
    rss[valid] = np.abs(rss_all)[np.arange(Nv), max_idx]

    if not return_trace:
        return rss

    # RSS 最大のすべり系の試料座標系法線からトレース方向を計算
    # n = [nx, ny, nz] → trace = [ny, -nx] (XY 面との交線方向)
    n_max = n_samp[np.arange(Nv), max_idx, :]             # (Nv, 3)
    tx =  n_max[:, 1]
    ty = -n_max[:, 0]
    norm = np.sqrt(tx**2 + ty**2)
    norm[norm < 1e-12] = 1.0
    trace = np.full((N, 2), np.nan)
    trace[valid, 0] = tx / norm
    trace[valid, 1] = ty / norm
    return rss, trace


def _parse_slip_system(plane_text, dir_text, ca_ratio=1.633):
    """すべり面・方向のテキストを結晶直交座標系の単位ベクトルに変換する。

    3指数（立方晶）と4指数（六方晶）を自動判別。
    Returns (n_slip, b_slip) どちらも shape (3,), 正規化済み。
    """
    def _parse_nums(text):
        return [float(x) for x in text.replace(',', ' ').split()]

    plane = _parse_nums(plane_text)
    direc = _parse_nums(dir_text)

    if len(plane) == 4:
        # 4指数 Miller-Bravais → 3指数 Miller 変換
        h, k, _i, l = plane
        plane = [h, k, l]
        u, v, t, w = direc
        direc = [2*u + v, u + 2*v, w]
        # HCP 直交座標変換 (a=1, c=ca_ratio)
        c = ca_ratio
        def to_cartesian_hcp_plane(h, k, l):
            return np.array([h, (h + 2*k) / np.sqrt(3), l / c])
        def to_cartesian_hcp_dir(U, V, W):
            return np.array([U - V/2, V * np.sqrt(3)/2, W * c])
        n_raw = to_cartesian_hcp_plane(*plane)
        b_raw = to_cartesian_hcp_dir(*direc)
    else:
        n_raw = np.array(plane, dtype=float)
        b_raw = np.array(direc, dtype=float)

    nn = np.linalg.norm(n_raw)
    bn = np.linalg.norm(b_raw)
    if nn < 1e-12 or bn < 1e-12:
        raise ValueError("すべり面または方向のノルムがゼロです")
    n_unit = n_raw / nn
    b_unit = b_raw / bn
    dot = abs(float(n_unit @ b_unit))
    if dot > 1e-4:
        raise ValueError(
            f"すべり方向がすべり面内にありません（内積 = {dot:.4f}、ゼロである必要があります）")
    return n_unit, b_unit


def _euler_to_matrices_rad(phi1, PHI, phi2):
    """Bunge オイラー角配列（ラジアン）→ 回転行列配列 (N, 3, 3)（ベクトル化）。"""
    c1, c, c2 = np.cos(phi1), np.cos(PHI), np.cos(phi2)
    s1, s, s2 = np.sin(phi1), np.sin(PHI), np.sin(phi2)
    N = len(phi1)
    g = np.zeros((N, 3, 3))
    g[:, 0, 0] =  c1*c2 - s1*s2*c;  g[:, 0, 1] =  s1*c2 + c1*s2*c;  g[:, 0, 2] = s2*s
    g[:, 1, 0] = -c1*s2 - s1*c2*c;  g[:, 1, 1] = -s1*s2 + c1*c2*c;  g[:, 1, 2] = c2*s
    g[:, 2, 0] =  s1*s;              g[:, 2, 1] = -c1*s;              g[:, 2, 2] = c
    return g


def compute_schmid_factor(phi1_deg, PHI_deg, phi2_deg, n_slip, b_slip, load_vecs, sym_ops,
                          return_trace=False):
    """全等価すべり系の最大シュミット因子を計算する（完全ベクトル化）。

    Parameters
    ----------
    phi1_deg, PHI_deg, phi2_deg : ndarray shape (N,)  Bunge オイラー角 [degrees]
    n_slip : ndarray shape (3,)  すべり面法線（結晶直交座標系・正規化済み）
    b_slip : ndarray shape (3,)  すべり方向（結晶直交座標系・正規化済み）
    load_vecs : ndarray shape (N, 3) or (3,)  負荷軸（試料座標系・正規化済み）
    sym_ops : ndarray shape (S, 3, 3)  対称操作行列群
    return_trace : bool  True のとき XY 面上のトレース方向 (N, 2) も返す

    Returns
    -------
    schmid : ndarray shape (N,)  最大 Schmid 因子 (0〜0.5)
    trace  : ndarray shape (N, 2)  トレース方向単位ベクトル（return_trace=True のとき）
    """
    N = len(phi1_deg)
    schmid = np.full(N, np.nan)

    lvecs = (np.broadcast_to(load_vecs, (N, 3)).copy() if np.asarray(load_vecs).ndim == 1
             else np.asarray(load_vecs, dtype=float))

    valid = (np.isfinite(phi1_deg) & np.isfinite(PHI_deg) & np.isfinite(phi2_deg)
             & np.all(np.isfinite(lvecs), axis=1))
    if not np.any(valid):
        if return_trace:
            return schmid, np.full((N, 2), np.nan)
        return schmid

    # 等価すべり系を対称操作で生成: (S, 3)
    n_equivs = sym_ops @ n_slip   # (S, 3)
    b_equivs = sym_ops @ b_slip   # (S, 3)

    # 全有効点の回転行列: (Nv, 3, 3)
    g = _euler_to_matrices_rad(
        np.radians(phi1_deg[valid].astype(float)),
        np.radians(PHI_deg[valid].astype(float)),
        np.radians(phi2_deg[valid].astype(float)))

    # 負荷軸を結晶座標系へ変換: n_crys[n] = g[n] @ lv[n]
    n_load_crys = np.einsum('nji,ni->nj', g, lvecs[valid])   # (Nv, 3)

    # 全等価系のシュミット因子を一括計算して最大を取る
    cos_phi = np.abs(n_load_crys @ n_equivs.T)   # (Nv, S)
    cos_lam = np.abs(n_load_crys @ b_equivs.T)   # (Nv, S)
    sf_all  = cos_phi * cos_lam                   # (Nv, S)
    max_idx = sf_all.argmax(axis=1)               # (Nv,)
    schmid[valid] = sf_all[np.arange(len(max_idx)), max_idx]

    if not return_trace:
        return schmid

    # 最大シュミット因子のすべり系を試料座標系に変換してトレース方向を計算
    n_samp = np.einsum('nij,sj->nsi', g, n_equivs)          # (Nv, S, 3)
    n_max  = n_samp[np.arange(len(max_idx)), max_idx, :]     # (Nv, 3)
    tx =  n_max[:, 1]
    ty = -n_max[:, 0]
    norm = np.sqrt(tx**2 + ty**2)
    norm[norm < 1e-12] = 1.0
    trace = np.full((N, 2), np.nan)
    trace[valid, 0] = tx / norm
    trace[valid, 1] = ty / norm
    return schmid, trace


def compute_hardening_rate(sv, ss, n, m):
    """ステージ n〜m の線形フィット傾きを各サブセットで計算する。"""
    N = sv.shape[0]
    result = np.full(N, np.nan)
    for i in range(N):
        x = sv[i, n:m + 1]
        y = ss[i, n:m + 1]
        valid = ~(np.isnan(x) | np.isnan(y))
        if valid.sum() >= 2:
            try:
                result[i] = np.polyfit(x[valid], y[valid], 1)[0]
            except np.linalg.LinAlgError:
                pass  # SVD失敗（x値が一定など）はnanのまま
    return result


def compute_strain_energy(sv, ss):
    """SS カーブの台形積分でひずみエネルギーを計算する。"""
    N = sv.shape[0]
    result = np.full(N, np.nan)
    for i in range(N):
        x = sv[i, :]
        y = ss[i, :]
        valid = ~(np.isnan(x) | np.isnan(y))
        if valid.sum() >= 2:
            xs, ys = x[valid], y[valid]
            order = np.argsort(xs)
            result[i] = np.trapz(ys[order], xs[order])
    return result


def compute_yield_stress(sv, ss, offset=0.002, E_per_subset=None):
    """0.2% オフセット法で各サブセットの降伏応力を近似計算する。

    E_per_subset が指定されていればサブセットごとの有効ヤング率を使い、
    なければ最初の 2 点から推定する。
    """
    N = sv.shape[0]
    result = np.full(N, np.nan)
    for i in range(N):
        x = sv[i, :]
        y = ss[i, :]
        valid = ~(np.isnan(x) | np.isnan(y))
        if valid.sum() < 3:
            continue
        xs, ys = x[valid], y[valid]
        order = np.argsort(xs)
        xs, ys = xs[order], ys[order]
        # ヤング率の決定
        if E_per_subset is not None and i < len(E_per_subset) and np.isfinite(E_per_subset[i]):
            E = float(E_per_subset[i])
        else:
            if xs[1] - xs[0] == 0:
                continue
            E = (ys[1] - ys[0]) / (xs[1] - xs[0])
        if E <= 0:
            continue
        # 弾性域ではSSカーブ > オフセット線（y = E*(x - offset)）
        # 降伏後はSSカーブの傾きが落ちてオフセット線に追い抜かれる
        # → SSカーブがオフセット線を初めて下回る点が降伏点
        for j in range(1, len(xs)):
            x_line = E * (xs[j] - offset)
            if ys[j] < x_line:  # SSカーブがオフセット線を下回った
                x_prev = E * (xs[j - 1] - offset)
                denom = ys[j] - ys[j - 1] - (x_line - x_prev)
                if denom != 0:
                    t = (x_prev - ys[j - 1]) / denom
                    result[i] = ys[j - 1] + t * (ys[j] - ys[j - 1])
                else:
                    result[i] = ys[j - 1]
                break
    return result


def read_stiffness_from_patrep_excel(excel_path):
    """PatRep の pre-processed Excel から弾性剛性テンソル（Voigt 6×6）を読む。

    "Project Details" シートの A 列に "Elastic Constants [GPa]" を含む行を探し、
    同行の B 列: 相名, C 列: 結晶系, D 列以降: 6×6 行列 (36 値・行優先) を返す。
    複数相が存在する場合は最初に見つかった相のものを返す。

    Returns
    -------
    C_voigt : ndarray, shape (6, 6), 単位 GPa
    """
    df = pd.read_excel(excel_path, sheet_name="Project Details", header=None)
    for idx, cell in df.iloc[:, 0].astype(str).items():
        if "elastic constants" in cell.lower():
            # 形式1: D列以降に数値が並ぶ場合
            vals = df.iloc[idx, 3:3+36].dropna().astype(float).to_numpy()
            if len(vals) >= 36:
                return vals[:36].reshape(6, 6)
            # 形式2: B列の1セルにタブ区切りで "相名\t結晶系\t値1\t値2\t..." が入る場合
            cell_b = str(df.iloc[idx, 1])
            parts = cell_b.replace('\t', ' ').split()
            nums = []
            for p in parts:
                try:
                    nums.append(float(p))
                except ValueError:
                    pass
            if len(nums) >= 36:
                return np.array(nums[:36]).reshape(6, 6)
            raise ValueError(f"剛性テンソルの値が不足しています（{len(nums)}/36）")
    raise KeyError("'Elastic Constants [GPa]' 行が Project Details シートに見つかりません")


def _euler_to_matrix_rad(phi1, PHI, phi2):
    """Bunge オイラー角（ラジアン）→ 回転行列 g（結晶座標系 → 試料座標系）。"""
    c1, c, c2 = np.cos(phi1), np.cos(PHI), np.cos(phi2)
    s1, s, s2 = np.sin(phi1), np.sin(PHI), np.sin(phi2)
    return np.array([
        [ c1*c2 - s1*s2*c,  s1*c2 + c1*s2*c,  s2*s],
        [-c1*s2 - s1*c2*c, -s1*s2 + c1*c2*c,  c2*s],
        [ s1*s,            -c1*s,               c   ],
    ])


def compute_E_per_subset(phi1_deg, PHI_deg, phi2_deg, C_voigt_GPa, stress_dir):
    """各サブセットの結晶方位を考慮した有効ヤング率 [GPa] を返す。

    Parameters
    ----------
    phi1_deg, PHI_deg, phi2_deg : array-like, shape (N,), 単位 degrees
    C_voigt_GPa : ndarray, shape (6, 6), 単位 GPa
    stress_dir  : array-like, shape (3,) 試料座標系での外力方向（単位ベクトル）

    Returns
    -------
    E : ndarray, shape (N,), 単位 GPa（NaN = 計算不可）
    """
    S_voigt = np.linalg.inv(C_voigt_GPa)            # コンプライアンス [1/GPa]
    n_sample = np.asarray(stress_dir, dtype=float)
    n_sample = n_sample / np.linalg.norm(n_sample)

    # Voigt 6×6 → 全テンソル S_ijkl（3×3×3×3）
    # コンプライアンスの Voigt→全テンソル変換（工学せん断ひずみ規約）
    vm = [(0,0),(1,1),(2,2),(1,2),(0,2),(0,1)]
    S_full = np.zeros((3,3,3,3))
    for I in range(6):
        for J in range(6):
            fi = 0.5 if I >= 3 else 1.0
            fj = 0.5 if J >= 3 else 1.0
            val = S_voigt[I, J] * fi * fj
            i, j = vm[I]; k, l = vm[J]
            for ii,jj,kk,ll in [(i,j,k,l),(j,i,k,l),(i,j,l,k),(j,i,l,k)]:
                S_full[ii,jj,kk,ll] = val

    N = len(phi1_deg)
    E = np.full(N, np.nan)
    for idx in range(N):
        p1 = np.radians(float(phi1_deg[idx]))
        pP = np.radians(float(PHI_deg[idx]))
        p2 = np.radians(float(phi2_deg[idx]))
        g = _euler_to_matrix_rad(p1, pP, p2)        # 結晶→試料
        n_crys = g.T @ n_sample                      # 外力方向を結晶座標系へ
        inv_E = np.einsum('ijkl,i,j,k,l', S_full, n_crys, n_crys, n_crys, n_crys)
        if inv_E > 0:
            E[idx] = 1.0 / inv_E
    return E


def compute_boundary_segments(x, y, grain_id, x_draw=None, y_draw=None):
    """隣接サブセット間で grain_id が異なる箇所の境界線分を返す。

    x, y       : 近傍探索用座標（参照座標・規則グリッド）
    grain_id   : 各点の粒ID
    x_draw, y_draw : 実際の描画位置（None の場合は x, y と同じ）
                     変形座標系使用時に cx+u, cy+v を渡す。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    grain_id = np.asarray(grain_id)
    xd = x if x_draw is None else np.asarray(x_draw, dtype=float)
    yd = y if y_draw is None else np.asarray(y_draw, dtype=float)

    cx_unique = np.unique(x[np.isfinite(x)])
    cy_unique = np.unique(y[np.isfinite(y)])
    if len(cx_unique) < 2 or len(cy_unique) < 2:
        return []

    dx = float(np.median(np.diff(cx_unique)))
    dy = float(np.median(np.diff(cy_unique)))
    if dx == 0 or dy == 0:
        return []

    # 近傍探索は参照グリッドインデックスで行う
    coord_to_idx = {}
    for i, (xi, yi) in enumerate(zip(x, y)):
        if np.isfinite(xi) and np.isfinite(yi):
            coord_to_idx[(round(xi / dx), round(yi / dy))] = i

    segments = []
    for i, (xi, yi, gi) in enumerate(zip(x, y, grain_id)):
        if not (np.isfinite(xi) and np.isfinite(yi)):
            continue
        gx, gy = round(xi / dx), round(yi / dy)
        # 右隣 → 垂直境界線（描画位置は変形座標の中点）
        nb = coord_to_idx.get((gx + 1, gy))
        if nb is not None and grain_id[nb] != gi:
            mx = (xd[i] + xd[nb]) / 2
            my = (yd[i] + yd[nb]) / 2
            segments.append([(mx, my - dy / 2), (mx, my + dy / 2)])
        # 下隣 → 水平境界線（描画位置は変形座標の中点）
        nb = coord_to_idx.get((gx, gy + 1))
        if nb is not None and grain_id[nb] != gi:
            mx = (xd[i] + xd[nb]) / 2
            my = (yd[i] + yd[nb]) / 2
            segments.append([(mx - dx / 2, my), (mx + dx / 2, my)])
    return segments


def _svd_mean_SO3(mats):
    """回転行列リストの SO(3) 平均をSVD射影で計算する。det<0 補正付き。"""
    M = np.mean(mats, axis=0)
    U, _, Vt = np.linalg.svd(M)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    return R


def _misori_angles_batch(R_ref, mats_arr):
    """R_ref と各行列 mats_arr[i] のミスオリエンテーション角度（degrees）を一括計算。"""
    # R_ref.T @ mats_arr[i] を全 i について求める
    products = np.einsum('ij,njk->nik', R_ref.T, mats_arr)   # (n,3,3)
    traces = np.trace(products, axis1=1, axis2=2)             # (n,)
    return np.degrees(np.arccos(np.clip((traces - 1.0) / 2.0, -1.0, 1.0)))


def _align_orientations(R_ref, mats_arr, sym_ops):
    """mats_arr の各行列を、対称操作で R_ref に最も近い等価方位に揃えて返す。

    Parameters
    ----------
    R_ref    : (3, 3) 基準回転行列
    mats_arr : (n, 3, 3) 揃える対象の回転行列群
    sym_ops  : (S, 3, 3) 結晶対称操作行列群

    Returns
    -------
    aligned  : (n, 3, 3) 揃え済み回転行列群
    """
    # (R_ref.T @ sym_ops[k]) @ mats_arr[i] を全 k, i で一括計算
    # R_ref.T @ sym_ops  → (S, 3, 3)
    R_ref_T_sym = R_ref.T[np.newaxis] @ sym_ops          # (S, 3, 3)
    # (n, S, 3, 3): deltas[i, k] = R_ref_T_sym[k] @ mats_arr[i]
    deltas = R_ref_T_sym[np.newaxis] @ mats_arr[:, np.newaxis]
    traces = deltas[..., 0, 0] + deltas[..., 1, 1] + deltas[..., 2, 2]  # (n, S)
    best_k = np.argmin(
        np.arccos(np.clip((traces - 1.0) / 2.0, -1.0, 1.0)), axis=1)    # (n,)
    # aligned[i] = sym_ops[best_k[i]] @ mats_arr[i]
    aligned = np.einsum('nij,njk->nik', sym_ops[best_k], mats_arr)       # (n, 3, 3)
    return aligned


def compute_grod(phi1_ref_deg, PHI_ref_deg, phi2_ref_deg,
                 phi1_tgt_deg, PHI_tgt_deg, phi2_tgt_deg,
                 grain_id, sym_ops: np.ndarray):
    """
    GROD (Grain Reference Orientation Deviation) を計算する。

    基準方位: ref ステージにおける各粒の平均回転行列（SVDで SO(3) に射影）
              外れ値（MAD法）を除去した 2パス推定を使用。
    対象方位: tgt ステージの各サブセットのオイラー角
    sym_ops : (S, 3, 3) 結晶対称操作行列群

    Returns
    -------
    angle  : ndarray (N,), 単位 degrees
    axis_x, axis_y, axis_z : ndarray (N,), 結晶座標系での回転軸成分
    """
    N = len(phi1_ref_deg)
    angle  = np.full(N, np.nan)
    axis_x = np.full(N, np.nan)
    axis_y = np.full(N, np.nan)
    axis_z = np.full(N, np.nan)

    # 各粒の基準平均回転行列を計算（2パス外れ値フィルタリング）
    grain_ref_mat = {}
    for gid_raw in np.unique(grain_id):
        gid = int(gid_raw)
        if gid <= 0:
            continue
        idx = np.where(grain_id == gid_raw)[0]

        # 有効点の回転行列を収集
        mats = []
        for i in idx:
            if not (np.isfinite(phi1_ref_deg[i]) and
                    np.isfinite(PHI_ref_deg[i]) and
                    np.isfinite(phi2_ref_deg[i])):
                continue
            mats.append(_euler_to_matrix_rad(
                np.radians(float(phi1_ref_deg[i])),
                np.radians(float(PHI_ref_deg[i])),
                np.radians(float(phi2_ref_deg[i]))))
        if not mats:
            continue

        mats_arr = np.array(mats)   # (n, 3, 3)

        # 1パス目: 対称性なしの仮平均（初期推定）
        R1 = _svd_mean_SO3(mats_arr)

        # 対称操作で全点を R1 に最も近い等価方位に揃えてから再平均
        mats_aligned = _align_orientations(R1, mats_arr, sym_ops)
        R2 = _svd_mean_SO3(mats_aligned)

        if len(mats_aligned) >= 5:
            # MADフィルタ（整合済み行列・対称性なしで偏差計算）
            dev = _misori_angles_batch(R2, mats_aligned)
            med = np.median(dev)
            mad = np.median(np.abs(dev - med))
            threshold = max(5.0, med + 3.0 * 1.4826 * mad)
            keep = dev <= threshold
            if keep.sum() >= 3:
                R_final = _svd_mean_SO3(mats_aligned[keep])
            else:
                R_final = R2
        else:
            R_final = R2

        grain_ref_mat[gid] = R_final

    # 各サブセットのディスオリエンテーション計算（対称操作を考慮）
    # sym_ops: (S, 3, 3)
    for i in range(N):
        gid = int(grain_id[i])
        if gid not in grain_ref_mat:
            continue
        if not (np.isfinite(phi1_tgt_deg[i]) and
                np.isfinite(PHI_tgt_deg[i]) and
                np.isfinite(phi2_tgt_deg[i])):
            continue
        g_ref = grain_ref_mat[gid]
        g_tgt = _euler_to_matrix_rad(
            np.radians(float(phi1_tgt_deg[i])),
            np.radians(float(PHI_tgt_deg[i])),
            np.radians(float(phi2_tgt_deg[i])))

        # 全対称等価な相対回転を求め、最小角度（ディスオリエンテーション）を採用
        # delta_k = g_ref.T @ sym_k @ g_tgt  （対称操作は両行列の間に挟む）
        deltas = (g_ref.T[np.newaxis] @ sym_ops) @ g_tgt   # (S, 3, 3)
        traces = deltas[:, 0, 0] + deltas[:, 1, 1] + deltas[:, 2, 2]  # (S,)
        angles_all = np.degrees(np.arccos(np.clip((traces - 1.0) / 2.0, -1.0, 1.0)))
        best = int(np.argmin(angles_all))

        theta = float(angles_all[best])
        angle[i] = theta

        dm = deltas[best]
        sin_t = np.sin(np.radians(theta))
        if sin_t > 1e-6:
            axis_x[i] = (dm[2, 1] - dm[1, 2]) / (2.0 * sin_t)
            axis_y[i] = (dm[0, 2] - dm[2, 0]) / (2.0 * sin_t)
            axis_z[i] = (dm[1, 0] - dm[0, 1]) / (2.0 * sin_t)

    return angle, axis_x, axis_y, axis_z
