"""
gb_definitions.py
=================
粒界定義の共有モジュール。stress_strain_mapper と stats_analysis の両方から使用。

各定義クラスは compute_pairs() → list of (i, j) を実装する。
返り値の (i, j) は「この2点の間に境界が存在する」ことを意味する隣接点ペア。

新しい定義の追加方法:
  1. GBDefinition を継承したクラスを実装し、compute_pairs() をオーバーライドする
  2. ALL_DEFINITIONS リストに追加する
  3. has_settings=True の場合は gb_settings_dialog.py に対応フォームを追加する

pairs_to_distance_map() → stats_analysis の距離フィルタに使用
pairs_to_segments()     → stress_strain_mapper の境界線描画に使用
"""

from __future__ import annotations
import numpy as np
from scipy.ndimage import distance_transform_edt

# stress_strain_calc から対称性演算をインポート
from stress_strain_calc import SYM_GROUPS, _get_sym_ops


# ============================================================
# 対称性ユーティリティ（公開ラッパー）
# ============================================================

def get_sym_ops(sym_name: str) -> np.ndarray:
    """対称群名 → (S, 3, 3) 回転行列配列。
    SYM_GROUPS と同じキー（Cubic / Hexagonal / ... / Triclinic）を受け付ける。
    """
    return _get_sym_ops(sym_name)


# ============================================================
# グリッド・隣接ペア
# ============================================================

def build_grid(cx: np.ndarray, cy: np.ndarray):
    """cx, cy から2Dグリッド情報を構築する。

    Returns
    -------
    ix, iy  : グリッドインデックス配列（各点のグリッド上の位置）
    step_x, step_y : グリッド間隔 [px]
    nx, ny  : グリッドサイズ
    """
    xs = np.unique(cx); ys = np.unique(cy)
    step_x = float(np.min(np.diff(np.sort(xs)))) if len(xs) > 1 else 1.0
    step_y = float(np.min(np.diff(np.sort(ys)))) if len(ys) > 1 else 1.0
    ix = np.round((cx - xs.min()) / step_x).astype(int)
    iy = np.round((cy - ys.min()) / step_y).astype(int)
    return ix, iy, step_x, step_y, len(xs), len(ys)


def get_adjacent_pairs(cx: np.ndarray, cy: np.ndarray) -> list[tuple[int, int]]:
    """水平・垂直に隣接する点ペアのリストを返す（i < j を保証）。"""
    ix, iy, *_ = build_grid(cx, cy)
    grid_to_idx: dict[tuple[int, int], int] = {}
    for k, (gx, gy) in enumerate(zip(ix, iy)):
        grid_to_idx[(int(gx), int(gy))] = k

    pairs: list[tuple[int, int]] = []
    for k, (gx, gy) in enumerate(zip(ix, iy)):
        for dgx, dgy in [(1, 0), (0, 1)]:   # 右・下
            nb = grid_to_idx.get((int(gx) + dgx, int(gy) + dgy))
            if nb is not None:
                pairs.append((k, nb) if k < nb else (nb, k))
    return pairs


# ============================================================
# オイラー角 → 回転行列
# ============================================================

def euler_to_rotmats(phi1_deg, PHI_deg, phi2_deg) -> np.ndarray:
    """Bunge ZXZ オイラー角（degrees）→ 回転行列配列 (N, 3, 3)。"""
    phi1 = np.radians(np.asarray(phi1_deg, float))
    PHI  = np.radians(np.asarray(PHI_deg,  float))
    phi2 = np.radians(np.asarray(phi2_deg, float))
    c1, c, c2 = np.cos(phi1), np.cos(PHI), np.cos(phi2)
    s1, s, s2 = np.sin(phi1), np.sin(PHI), np.sin(phi2)
    N = len(phi1)
    g = np.zeros((N, 3, 3))
    g[:, 0, 0] =  c1*c2 - s1*s2*c;  g[:, 0, 1] =  s1*c2 + c1*s2*c;  g[:, 0, 2] = s2*s
    g[:, 1, 0] = -c1*s2 - s1*c2*c;  g[:, 1, 1] = -s1*s2 + c1*c2*c;  g[:, 1, 2] = c2*s
    g[:, 2, 0] =  s1*s;              g[:, 2, 1] = -c1*s;              g[:, 2, 2] = c
    return g


def _load_euler(mat: dict, stage: str, source: str) -> np.ndarray:
    """オイラー角配列 (N, 3) を degrees で返す。

    matファイルはCrossCourtの慣例に従いEuler角を degrees で格納している。
    compute_schmid_factor 内で np.radians() が呼ばれていることで確認済み。

    source : 'ref'   → phi1_ref / PHI_ref / phi2_ref を優先、
                        なければ euler_phi1 / euler_phi / euler_phi2 にフォールバック
             'stage' → euler_phi1_sXXX / euler_phi_sXXX / euler_phi2_sXXX
    """
    def _get(keys):
        for k in keys:
            if k in mat:
                return mat[k].flatten().astype(float)
        raise KeyError(f"オイラー角変数が見つかりません: {keys}")

    if source == 'ref':
        p1  = _get(['phi1_ref', 'euler_phi1'])
        PHI = _get(['PHI_ref',  'euler_phi'])
        p2  = _get(['phi2_ref', 'euler_phi2'])
    else:
        p1  = _get([f'euler_phi1_s{stage}'])
        PHI = _get([f'euler_phi_s{stage}'])
        p2  = _get([f'euler_phi2_s{stage}'])
    # matはすでにdegrees → np.degrees()は不要（radians→degreesの誤変換を防ぐ）
    return np.column_stack([p1, PHI, p2])


def disorientation_angles_batch(
        pairs: np.ndarray,
        rotmats: np.ndarray,
        sym_ops: np.ndarray,
) -> np.ndarray:
    """隣接ペアのディスオリエンテーション角（degrees）を一括計算する。

    Parameters
    ----------
    pairs   : (P, 2) int   各行が [i, j] の点インデックスペア
    rotmats : (N, 3, 3)    各点の回転行列
    sym_ops : (S, 3, 3)    対称操作行列群（点群）

    Returns
    -------
    angles : (P,) float [degrees]  対称性を考慮した最小ミスオリエンテーション角

    Notes
    -----
    双晶対称（bicrystal symmetry）: S_k @ ΔR @ S_l^T を全 (k,l) 組合せで評価。
    左からのみの適用（S_k @ ΔR）では、同一粒内で対称性等価なオイラー角解
    （例: 立方晶の 90° 等価解）が異なるサブセットに割り当てられた場合に
    ミスオリエンテーション 0° を正しく検出できないため、両側適用が必要。

    効率化: S_k ごとにループし、trace(S_k @ ΔR @ S_l^T) = (S_k @ ΔR).flatten() · S_l.flatten()
    をフロベニウス内積で一括計算。メモリ O(S × P × 9)。
    """
    if len(pairs) == 0:
        return np.array([])
    pairs = np.asarray(pairs, int)
    Ri = rotmats[pairs[:, 0]]                      # (P, 3, 3)
    Rj = rotmats[pairs[:, 1]]                      # (P, 3, 3)
    deltaR = np.einsum('pji,pjk->pik', Ri, Rj)    # Ri.T @ Rj  (P, 3, 3)

    S = len(sym_ops)
    P = len(pairs)
    sym_flat = sym_ops.reshape(S, 9)               # (S, 9)  S_l をフラット化
    max_trace = np.full(P, -3.0)

    # 双晶対称: max_{k,l} trace(S_k @ ΔR @ S_l^T)
    # = max_{k,l} Frobenius_inner(S_k @ ΔR,  S_l)
    for k in range(S):
        sdR_k      = np.einsum('ij,pjl->pil', sym_ops[k], deltaR)  # (P, 3, 3)
        sdR_k_flat = sdR_k.reshape(P, 9)                            # (P, 9)
        traces_kl  = sdR_k_flat @ sym_flat.T                        # (P, S)
        max_trace  = np.maximum(max_trace, np.max(traces_kl, axis=1))

    cos_theta = np.clip((max_trace - 1.0) / 2.0, -1.0, 1.0)
    return np.degrees(np.arccos(cos_theta))


# ============================================================
# ペア → 描画・フィルタ用変換
# ============================================================

def pairs_to_distance_map(pairs, cx: np.ndarray, cy: np.ndarray) -> np.ndarray:
    """境界ペアから各点の「粒界までの距離」配列（px, フラット）を計算する。
    stats_analysis の距離フィルタに使用。
    """
    n = len(cx)
    ix, iy, _, _, nx, ny = build_grid(cx, cy)

    is_gb_grid = np.zeros((ny, nx), bool)
    for i, j in pairs:
        is_gb_grid[iy[i], ix[i]] = True
        is_gb_grid[iy[j], ix[j]] = True

    dist_grid = distance_transform_edt(~is_gb_grid)
    return dist_grid[iy, ix].astype(float)


def pairs_to_segments(
        pairs,
        cx: np.ndarray,
        cy: np.ndarray,
        x_draw=None,
        y_draw=None,
) -> list:
    """境界ペアから線分リストを生成する（stress_strain_mapper の LineCollection 用）。

    x_draw, y_draw を指定すると変形後座標で描画する。
    """
    cx = np.asarray(cx, float)
    cy = np.asarray(cy, float)
    xd = cx if x_draw is None else np.asarray(x_draw, float)
    yd = cy if y_draw is None else np.asarray(y_draw, float)

    ix, iy, step_x, step_y, *_ = build_grid(cx, cy)
    dx, dy = step_x, step_y

    segments = []
    for i, j in pairs:
        mx = (xd[i] + xd[j]) / 2
        my = (yd[i] + yd[j]) / 2
        if iy[i] == iy[j]:          # 水平隣接 → 垂直線分
            segments.append([(mx, my - dy / 2), (mx, my + dy / 2)])
        else:                        # 垂直隣接 → 水平線分
            segments.append([(mx - dx / 2, my), (mx + dx / 2, my)])
    return segments


def pairs_to_segments_valued(
        pairs,
        pair_values: np.ndarray,
        cx: np.ndarray,
        cy: np.ndarray,
        x_draw=None,
        y_draw=None,
) -> tuple[list, np.ndarray]:
    """値付き境界ペアから (線分リスト, 値配列) を返す（m' カラー描画用）。

    Returns
    -------
    segments   : list of [(x0,y0),(x1,y1)]
    values     : np.ndarray shape (P,) — LineCollection の array= に渡す値
    """
    segs = pairs_to_segments(pairs, cx, cy, x_draw, y_draw)
    return segs, np.asarray(pair_values, float)


# ============================================================
# 粒界定義基底クラス
# ============================================================

class GBDefinition:
    """粒界定義の基底クラス。すべての定義はこれを継承する。"""

    key:           str  = ""     # 内部識別キー
    label:         str  = ""     # UIに表示する名前
    has_settings:  bool = False  # True → 「設定…」ボタンでダイアログを開く
    is_classifier: bool = False  # True → 色分けコンボに表示（classify_pairs を実装済み）
    default_params: dict = {}   # gb_settings_dialog に渡すデフォルト値

    def compute_pairs(
            self,
            mat:           dict,
            stage:         str,
            phase_sym_map: dict,      # {phase_int: sym_ops (S,3,3)}
            params:        dict,
    ) -> list[tuple[int, int]]:
        """境界ペアのリストを返す。

        Parameters
        ----------
        mat           : matファイルの変数辞書
        stage         : ステージ文字列（例: "750MPa"）
        phase_sym_map : 相ごとの対称操作行列
        params        : has_settings=True のとき gb_settings_dialog から渡される

        Returns
        -------
        list of (i, j) — 境界が存在する隣接点ペア
        """
        raise NotImplementedError

    def classify_pairs(
            self,
            base_pairs:    list,
            mat:           dict,
            stage:         str,
            phase_sym_map: dict,
            params:        dict,
    ) -> list[tuple[int, int]]:
        """ベースペアのうちこの定義を満たすもの（ハイライト対象）を返す。

        デフォルトは空リスト（= 分類なし）。
        サブクラスでオーバーライドして色分け対象のペアを返す。
        """
        return []

    def classify_pairs_valued(
            self,
            base_pairs:    list,
            mat:           dict,
            stage:         str,
            phase_sym_map: dict,
            params:        dict,
    ) -> tuple[list, np.ndarray]:
        """全ベースペアに値（m' 等）を付けて返す。グラデーション描画用。

        デフォルトは ([], array([])) — 未対応定義は空を返す。
        サブクラスでオーバーライドして (pairs, values) を返す。
        values は 0〜1 の float 配列。
        """
        return [], np.array([])


# ============================================================
# 定義① grain_id が異なる境界（既存・後方互換）
# ============================================================

class GrainIDDef(GBDefinition):
    key           = "grain_id"
    label         = "grain_id — 隣接粒IDが異なる境界"
    has_settings  = False
    default_params = {}

    def compute_pairs(self, mat, stage, phase_sym_map, params):
        cx  = mat['cx'].flatten()
        cy  = mat['cy'].flatten()
        gid = mat['grain_id'].flatten()

        boundary = []
        for i, j in get_adjacent_pairs(cx, cy):
            if gid[i] != gid[j]:
                boundary.append((i, j))
        return boundary


# ============================================================
# 定義② ミスオリエンテーション ≥ θ°
# ============================================================

class MisorientationDef(GBDefinition):
    key          = "misorientation"
    label        = "ミスオリエンテーション ≥ θ°"
    has_settings = True
    default_params = {
        'theta_deg':       15.0,   # 閾値角度 [degrees]
        'euler_source':   'stage', # 'ref' or 'stage'
        'same_phase_only': False,  # True → 同じ相間のみ評価
    }

    def compute_pairs(self, mat, stage, phase_sym_map, params):
        theta        = float(params.get('theta_deg',       self.default_params['theta_deg']))
        euler_source = str(params.get('euler_source',      self.default_params['euler_source']))
        same_phase   = bool(params.get('same_phase_only',  self.default_params['same_phase_only']))

        cx  = mat['cx'].flatten()
        cy  = mat['cy'].flatten()
        gid = mat['grain_id'].flatten()

        # オイラー角取得
        try:
            eulers = _load_euler(mat, stage, euler_source)  # (N, 3) in degrees
        except KeyError:
            # ステージのオイラー角がなければ参照ステージにフォールバック
            eulers = _load_euler(mat, stage, 'ref')

        # 相情報（なければ全点を同一相として扱う）
        phase_key = f'phase_index_s{stage}'
        if phase_key in mat:
            _pa = mat[phase_key].flatten().astype(float)
            phase_arr = np.where(np.isfinite(_pa), _pa, -1).astype(int)
        else:
            phase_arr = np.zeros(len(cx), int)

        # 有効点マスク（grain_id != -1 かつ全有限）
        valid = (
            (gid != -1)
            & np.isfinite(eulers[:, 0])
            & np.isfinite(eulers[:, 1])
            & np.isfinite(eulers[:, 2])
        )

        # 全有効点の回転行列を一括計算
        rotmats = np.full((len(cx), 3, 3), np.nan)
        if np.any(valid):
            rotmats[valid] = euler_to_rotmats(
                eulers[valid, 0], eulers[valid, 1], eulers[valid, 2])

        adj_pairs = get_adjacent_pairs(cx, cy)

        # 相ごとに隣接ペアを分類して一括計算
        # phase_sym_map に登録されていない相は cubic として扱う
        fallback_sym = list(phase_sym_map.values())[0] if phase_sym_map else get_sym_ops("Cubic")

        # (phase_i, phase_j) ごとにペアをグループ化
        from collections import defaultdict
        phase_pair_groups: dict[tuple, list] = defaultdict(list)
        phase_pair_idx:    dict[tuple, list] = defaultdict(list)

        boundary = []

        for idx, (i, j) in enumerate(adj_pairs):
            pi, pj = int(phase_arr[i]), int(phase_arr[j])

            # 異相 → 常に境界（same_phase_only でなければ追加）
            if pi != pj:
                if not same_phase:
                    boundary.append((i, j))
                continue

            # 非有効点はスキップ
            if not (valid[i] and valid[j]):
                continue

            phase_pair_groups[(pi, pj)].append((i, j))
            phase_pair_idx[(pi, pj)].append(idx)

        for (ph, _), group in phase_pair_groups.items():
            sym = phase_sym_map.get(ph, fallback_sym)
            pairs_np = np.array(group, int)
            angles = disorientation_angles_batch(pairs_np, rotmats, sym)
            for k, angle in enumerate(angles):
                if angle >= theta:
                    boundary.append(group[k])

        return boundary


# ============================================================
# 定義③ 特殊粒界（任意CSL・双晶）— 将来実装予定（現在無効）
# ============================================================

class SpecialBoundaryDef(GBDefinition):
    key          = "special"
    label        = "特殊粒界（任意 CSL / 双晶）"
    has_settings = True
    default_params = {
        'axis_uvw':    [1, 1, 1],  # 回転軸 Miller 指数
        'angle_deg':   60.0,       # 回転角 [degrees]
        'axis_tol':    5.0,        # 軸 Tolerance [degrees]
        'angle_tol':   5.0,        # 角度 Tolerance [degrees]
        'euler_source': 'stage',
        'same_phase_only': True,
    }

    # チャンクサイズ: メモリ使用量を ~70 MB 程度に抑える
    _CHUNK = 2000

    def compute_pairs(self, mat, stage, phase_sym_map, params):
        axis_uvw    = np.asarray(params.get('axis_uvw',   self.default_params['axis_uvw']),  float)
        angle_tgt   = float(params.get('angle_deg',       self.default_params['angle_deg']))
        ax_tol      = float(params.get('axis_tol',        self.default_params['axis_tol']))
        ang_tol     = float(params.get('angle_tol',       self.default_params['angle_tol']))
        euler_source = str(params.get('euler_source',     self.default_params['euler_source']))
        same_phase  = bool(params.get('same_phase_only',  self.default_params['same_phase_only']))

        axis_ref = axis_uvw / (np.linalg.norm(axis_uvw) + 1e-12)

        cx  = mat['cx'].flatten()
        cy  = mat['cy'].flatten()
        gid = mat['grain_id'].flatten()

        try:
            eulers = _load_euler(mat, stage, euler_source)
        except KeyError:
            eulers = _load_euler(mat, stage, 'ref')

        phase_key = f'phase_index_s{stage}'
        if phase_key in mat:
            _pa = mat[phase_key].flatten().astype(float)
            phase_arr = np.where(np.isfinite(_pa), _pa, -1).astype(int)
        else:
            phase_arr = np.zeros(len(cx), int)

        valid = (
            (gid != -1)
            & np.isfinite(eulers[:, 0])
            & np.isfinite(eulers[:, 1])
            & np.isfinite(eulers[:, 2])
        )
        rotmats = np.full((len(cx), 3, 3), np.nan)
        if np.any(valid):
            rotmats[valid] = euler_to_rotmats(
                eulers[valid, 0], eulers[valid, 1], eulers[valid, 2])

        adj_pairs = get_adjacent_pairs(cx, cy)
        fallback_sym = list(phase_sym_map.values())[0] if phase_sym_map else get_sym_ops("Cubic")

        from collections import defaultdict
        phase_pair_groups: dict = defaultdict(list)
        boundary = []

        for i, j in adj_pairs:
            pi, pj = int(phase_arr[i]), int(phase_arr[j])
            if pi != pj:
                if not same_phase:
                    boundary.append((i, j))
                continue
            if not (valid[i] and valid[j]):
                continue
            phase_pair_groups[pi].append((i, j))

        for ph, group in phase_pair_groups.items():
            sym = phase_sym_map.get(ph, fallback_sym)   # (S, 3, 3)
            matched = self._check_special_vectorized(
                group, rotmats, sym, axis_ref, angle_tgt, ang_tol, ax_tol)
            for k, m in enumerate(matched):
                if m:
                    boundary.append(group[k])

        return boundary

    @staticmethod
    def _axis_angle_to_rotmat(axis: np.ndarray, angle_deg: float) -> np.ndarray:
        """回転軸（正規化済み）と回転角（degrees）から回転行列を生成する（Rodriguez公式）。"""
        a = np.radians(angle_deg)
        c, s = np.cos(a), np.sin(a)
        t    = 1.0 - c
        x, y, z = axis
        return np.array([
            [t*x*x + c,   t*x*y - s*z, t*x*z + s*y],
            [t*x*y + s*z, t*y*y + c,   t*y*z - s*x],
            [t*x*z - s*y, t*y*z + s*x, t*z*z + c  ],
        ], dtype=float)

    @staticmethod
    def _check_special_vectorized(group, rotmats, sym, axis_ref,
                                  angle_tgt, ang_tol, ax_tol):
        """特殊粒界判定: 両側対称操作を使いベクトル化して実行する。

        正しい判定式: ∃ si, sj ∈ G: si @ M @ sj ≈ target_R
          ⟺ max_{si,sj} tr(si @ M @ sj @ target_R^T) ≥ thresh

        左側のみ (si @ M) ではΣ3等で粒界が検出漏れするため
        両側対称操作が必須。

        Parameters
        ----------
        group   : list of (i, j)
        rotmats : (N, 3, 3)
        sym     : (S, 3, 3)   対称操作群
        axis_ref: (3,)         ターゲット回転軸（正規化済み）
        angle_tgt, ang_tol, ax_tol : float [degrees]

        Returns
        -------
        matched : list of bool
        """
        CHUNK = SpecialBoundaryDef._CHUNK
        S     = len(sym)

        target_R = SpecialBoundaryDef._axis_angle_to_rotmat(axis_ref, angle_tgt)  # (3,3)

        # 許容トレース閾値
        # tr(R) = 1 + 2cos(θ) なので θ = combined_tol のとき threshold を計算
        # combined_tol: 角度偏差 ang_tol と軸偏差による回転距離 ax_tol*sin(angle_tgt/2)*2 の二乗和
        combined_tol_rad = np.sqrt(
            np.radians(ang_tol)**2
            + (2.0 * np.sin(np.radians(angle_tgt) / 2.0) * np.radians(ax_tol))**2
        )
        thresh = 1.0 + 2.0 * np.cos(combined_tol_rad)

        # sym[t]^T をフラット化: tr(A @ sym[t]) = <A_flat, sym[t]^T_flat>
        sym_T_flat = sym.transpose(0, 2, 1).reshape(S, 9)  # (S, 9)

        matched_all = []

        for start in range(0, len(group), CHUNK):
            chunk = group[start:start + CHUNK]
            C     = len(chunk)
            arr   = np.array(chunk, int)
            Ri    = rotmats[arr[:, 0]]   # (C, 3, 3)
            Rj    = rotmats[arr[:, 1]]

            # M = Ri^T @ Rj  (C, 3, 3)
            M = np.einsum('pji,pjk->pik', Ri, Rj)

            # M^T も同時にチェック（i/j 割り当て曖昧さへの対処）
            M_both = np.concatenate([M, M.transpose(0, 2, 1)], axis=0)  # (2C, 3, 3)

            # N_{s,c} = sym[s] @ M_c: (S, 2C, 3, 3)
            N = np.einsum('sij,cjk->scik', sym, M_both)

            # Z_{s,c} = target_R^T @ N_{s,c}: (S, 2C, 3, 3)
            # → tr(Z @ sym[t]) = tr(target_R^T @ sym[s] @ M @ sym[t])
            #                   = tr(sym[s] @ M @ sym[t] @ target_R^T)  [cyclic]
            Z      = np.einsum('ij,scjk->scik', target_R.T, N)

            # cross_{s,c,t} = tr(Z_{s,c} @ sym[t]): (S, 2C, S)
            Z_flat = Z.reshape(S, 2 * C, 9)
            cross  = np.einsum('scn,tn->sct', Z_flat, sym_T_flat)

            # ペアごとに (si, sj) の全組合せ中の最大トレースを取得: (2C,)
            max_tr = np.max(cross, axis=(0, 2))

            # M と M^T の結果を合算: (C,)
            pair_match = np.maximum(max_tr[:C], max_tr[C:]) >= thresh
            matched_all.extend(pair_match.tolist())

        return matched_all


# ============================================================
# 定義④ Luster-Morris m'
# ============================================================

class MPrimeDef(GBDefinition):
    """Luster-Morris m' 粒界定義。

    隣接2粒の等価すべり系ベクトルをすべての組合せで比較し、
    m' = |cos ψ| × |cos κ| を計算する。全粒界にグラデーション表示する。

    ψ: 隣接粒のすべり面法線間の角度（試料座標系）
    κ: すべり方向間の角度（試料座標系）
    """

    key           = "m_prime"
    label         = "Luster-Morris m'"
    has_settings  = True
    is_classifier = True
    default_params = {
        'threshold':      1.0,    # フィルタ閾値（1.0 = 全境界）
        'threshold_mode': 'lte',  # 'lte': m' ≤ threshold / 'gte': m' ≥ threshold
        'euler_source': 'stage',
        'slip_plane':   '1 1 1',
        'slip_dir':     '1 -1 0',
        'load_x':       1.0,      # 荷重軸（試料座標系）X方向＝画面左右
        'load_y':       0.0,
        'load_z':       0.0,
    }

    def compute_pairs(self, mat, stage, phase_sym_map, params):
        pairs, _ = self._compute(mat, stage, phase_sym_map, params)
        return pairs

    def classify_pairs(self, base_pairs, mat, stage, phase_sym_map, params):
        """ベースペアに対して m' を計算し、threshold 以下のペアを返す（色分け用）。"""
        if not base_pairs:
            return []
        rotmats, valid, phase_arr, n_slip, b_slip, load_vec, fallback_sym = \
            self._prepare(mat, stage, phase_sym_map, params)

        from collections import defaultdict
        phase_groups: dict = defaultdict(list)
        for pair in base_pairs:
            i, j = int(pair[0]), int(pair[1])
            pi, pj = int(phase_arr[i]), int(phase_arr[j])
            if pi != pj or not (valid[i] and valid[j]):
                continue
            phase_groups[pi].append((i, j))

        threshold = float(params.get('threshold', self.default_params['threshold']))
        gte_mode  = params.get('threshold_mode', 'lte') == 'gte'
        highlighted: list[tuple[int, int]] = []
        for ph, group in phase_groups.items():
            sym = phase_sym_map.get(ph, fallback_sym)
            mp_arr = self._calc_mprime(group, rotmats, sym @ n_slip, sym @ b_slip, load_vec)
            for pair, mp in zip(group, mp_arr):
                if (mp >= threshold) if gte_mode else (mp <= threshold):
                    highlighted.append(pair)
        return highlighted

    def compute_pairs_valued(self, mat, stage, phase_sym_map, params):
        """境界ペアとそのm'値を返す。カラー描画用。"""
        return self._compute(mat, stage, phase_sym_map, params)

    def classify_pairs_valued(self, base_pairs, mat, stage, phase_sym_map, params):
        """全ベースペアの m' 値を返す（閾値フィルタなし）。グラデーション描画用。"""
        if not base_pairs:
            return [], np.array([])
        rotmats, valid, phase_arr, n_slip, b_slip, load_vec, fallback_sym = \
            self._prepare(mat, stage, phase_sym_map, params)

        from collections import defaultdict
        phase_groups: dict = defaultdict(list)
        for pair in base_pairs:
            i, j = int(pair[0]), int(pair[1])
            pi, pj = int(phase_arr[i]), int(phase_arr[j])
            if pi != pj or not (valid[i] and valid[j]):
                continue
            phase_groups[pi].append((i, j))

        all_pairs: list[tuple[int, int]] = []
        all_vals:  list[float]           = []
        for ph, group in phase_groups.items():
            sym = phase_sym_map.get(ph, fallback_sym)
            mp_arr = self._calc_mprime(group, rotmats, sym @ n_slip, sym @ b_slip, load_vec)
            all_pairs.extend(group)
            all_vals.extend(mp_arr.tolist())

        return all_pairs, np.array(all_vals)

    def _prepare(self, mat, stage, phase_sym_map, params):
        """共通前処理: rotmats, valid, phase_arr, slip系ベクトル, 荷重軸を返す。"""
        from stress_strain_calc import _parse_slip_system

        euler_source = str(params.get('euler_source', self.default_params['euler_source']))
        plane_text   = str(params.get('slip_plane',   self.default_params['slip_plane']))
        dir_text     = str(params.get('slip_dir',     self.default_params['slip_dir']))

        cx  = mat['cx'].flatten()
        N   = len(cx)

        try:
            eulers = _load_euler(mat, stage, euler_source)
        except KeyError:
            eulers = _load_euler(mat, stage, 'ref')

        phase_key = f'phase_index_s{stage}'
        if phase_key in mat:
            _pa = mat[phase_key].flatten().astype(float)
            phase_arr = np.where(np.isfinite(_pa), _pa, -1).astype(int)
        else:
            phase_arr = np.zeros(N, int)

        valid = (np.isfinite(eulers[:, 0]) & np.isfinite(eulers[:, 1]) & np.isfinite(eulers[:, 2]))
        rotmats = np.full((N, 3, 3), np.nan)
        if np.any(valid):
            rotmats[valid] = euler_to_rotmats(eulers[valid, 0], eulers[valid, 1], eulers[valid, 2])

        try:
            n_slip, b_slip = _parse_slip_system(plane_text, dir_text)
        except ValueError as e:
            raise ValueError(f"m' すべり系パラメータが不正: {e}") from e

        lv = np.array([float(params.get('load_x', self.default_params['load_x'])),
                       float(params.get('load_y', self.default_params['load_y'])),
                       float(params.get('load_z', self.default_params['load_z']))], dtype=float)
        norm = np.linalg.norm(lv)
        load_vec = lv / norm if norm > 1e-12 else np.array([1.0, 0.0, 0.0])

        fallback_sym = list(phase_sym_map.values())[0] if phase_sym_map else get_sym_ops("Cubic")
        return rotmats, valid, phase_arr, n_slip, b_slip, load_vec, fallback_sym

    def _compute(self, mat, stage, phase_sym_map, params):
        from collections import defaultdict

        rotmats, valid, phase_arr, n_slip, b_slip, load_vec, fallback_sym = \
            self._prepare(mat, stage, phase_sym_map, params)

        cx  = mat['cx'].flatten()
        cy  = mat['cy'].flatten()
        gid = mat['grain_id'].flatten()
        valid = valid & (gid != -1)

        adj_pairs = get_adjacent_pairs(cx, cy)

        phase_pair_groups: dict = defaultdict(list)
        for i, j in adj_pairs:
            pi, pj = int(phase_arr[i]), int(phase_arr[j])
            if pi != pj or not (valid[i] and valid[j]):
                continue
            if gid[i] == gid[j]:   # 同一粒内はスキップ（m'は粒界専用）
                continue
            phase_pair_groups[pi].append((i, j))

        threshold = float(params.get('threshold', self.default_params['threshold']))
        gte_mode  = params.get('threshold_mode', 'lte') == 'gte'
        boundary_pairs: list[tuple[int, int]] = []
        boundary_vals:  list[float]           = []

        for ph, group in phase_pair_groups.items():
            sym    = phase_sym_map.get(ph, fallback_sym)
            mp_arr = self._calc_mprime(group, rotmats, sym @ n_slip, sym @ b_slip, load_vec)
            for pair, mp in zip(group, mp_arr):
                if (mp >= threshold) if gte_mode else (mp <= threshold):
                    boundary_pairs.append(pair)
                    boundary_vals.append(float(mp))

        return boundary_pairs, np.array(boundary_vals)

    @staticmethod
    def _calc_mprime(group, rotmats, n_equivs, b_equivs, load_vec):
        """測定点ごとにシュミット因子最大の活性すべり系を選び、m' を返す。

        各点の活性系を荷重方向から独立に決定する:
          1. g @ load_sample で荷重軸を結晶座標系へ変換
          2. argmax(|cos φ| × |cos λ|) で活性系インデックスを決定
          3. g.T @ n_crystal で活性系を試料座標系へ変換
          4. 隣接点ペア (i, j) の m' = |n_i · n_j| × |b_i · b_j|
        """
        if not group:
            return np.array([])

        arr     = np.array(group, int)   # (P, 2)
        all_pts = np.unique(arr)         # 全登場点のインデックス
        g_pts   = rotmats[all_pts]       # (num_pts, 3, 3)

        # 荷重軸を各点の結晶座標系へ変換: g @ load_sample
        load_crys = np.einsum('nij,j->ni', g_pts, load_vec)   # (num_pts, 3)

        # シュミット因子を結晶座標系で計算して最大系を選ぶ
        cos_phi = np.abs(load_crys @ n_equivs.T)              # (num_pts, S)
        cos_lam = np.abs(load_crys @ b_equivs.T)              # (num_pts, S)
        max_idx = (cos_phi * cos_lam).argmax(axis=1)          # (num_pts,)

        # 活性系を試料座標系へ変換: g.T @ n_crystal
        n_active = np.einsum('nji,nj->ni', g_pts, n_equivs[max_idx])  # (num_pts, 3)
        b_active = np.einsum('nji,nj->ni', g_pts, b_equivs[max_idx])  # (num_pts, 3)

        pt_to_u = {pt: u for u, pt in enumerate(all_pts)}
        ui = np.array([pt_to_u[i] for i, _ in group])
        uj = np.array([pt_to_u[j] for _, j in group])
        n_i, n_j = n_active[ui], n_active[uj]
        b_i, b_j = b_active[ui], b_active[uj]

        # m' = |n_i · n_j| × |b_i · b_j|
        return np.clip(
            np.abs(np.einsum('pi,pi->p', n_i, n_j)) *
            np.abs(np.einsum('pi,pi->p', b_i, b_j)),
            0.0, 1.0,
        )


# ============================================================
# 全定義リスト（UIのドロップダウン順）
# ============================================================

ALL_DEFINITIONS: list[GBDefinition] = [
    MisorientationDef(),
    GrainIDDef(),
    MPrimeDef(),
]

# key → 定義オブジェクトのマッピング
DEFINITION_MAP: dict[str, GBDefinition] = {d.key: d for d in ALL_DEFINITIONS}


def get_definition(key: str) -> GBDefinition:
    """キーから定義オブジェクトを取得する。"""
    if key not in DEFINITION_MAP:
        raise KeyError(f"未知の粒界定義キー: {key!r}. 使用可能: {list(DEFINITION_MAP)}")
    return DEFINITION_MAP[key]


def build_phase_sym_map(phase_sym_names: dict) -> dict:
    """phase_index → sym_name の辞書から phase_index → sym_ops の辞書を生成する。

    Parameters
    ----------
    phase_sym_names : {phase_int: sym_name_str}
        例: {0: "Cubic", 1: "Hexagonal"}

    Returns
    -------
    {phase_int: np.ndarray (S,3,3)}
    """
    return {ph: get_sym_ops(name) for ph, name in phase_sym_names.items()}
