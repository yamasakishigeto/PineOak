"""参照パターンのマッチング。

すべて index（0基準、index = row*numcols + col）で完結させる。
ファイル名の組み立て・命名倍率・最近傍ファイル探索は行わない。
"""
import numpy as np
from scipy.spatial.transform import Rotation as R

SYM_GROUPS = {'cubic': 'O', 'hexagonal': 'D6', 'tetragonal': 'D4',
              'orthorhombic': 'D2', 'trigonal': 'D3', 'monoclinic': 'C2', 'triclinic': 'C1'}


def sym_ops(name):
    return R.create_group(SYM_GROUPS.get(name, 'O')).as_matrix()


def euler_to_matrix(e_deg):
    """Bunge (phi1, PHI, phi2) [deg] → 回転行列。(...,3) → (...,3,3)"""
    e = np.radians(np.asarray(e_deg, float))
    p1, P, p2 = e[..., 0], e[..., 1], e[..., 2]
    c1, c, c2 = np.cos(p1), np.cos(P), np.cos(p2)
    s1, s, s2 = np.sin(p1), np.sin(P), np.sin(p2)
    g = np.empty(e.shape[:-1] + (3, 3))
    g[..., 0, 0] = c1*c2 - s1*s2*c; g[..., 0, 1] = -c1*s2 - s1*c2*c; g[..., 0, 2] = s1*s
    g[..., 1, 0] = s1*c2 + c1*s2*c; g[..., 1, 1] = -s1*s2 + c1*c2*c; g[..., 1, 2] = -c1*s
    g[..., 2, 0] = s2*s;            g[..., 2, 1] = c2*s;             g[..., 2, 2] = c
    return g


def misorientation(g_refs, g_target, ops=None):
    """g_refs (N,3,3) と g_target (3,3) の方位差 [deg]。ops=None なら対称操作なし。"""
    M = g_target[None] @ g_refs.transpose(0, 2, 1)
    if ops is None:
        tr = M[..., 0, 0] + M[..., 1, 1] + M[..., 2, 2]
    else:
        MT = M.transpose(0, 2, 1)
        tr = np.max(np.concatenate([np.einsum('sij,nij->ns', ops, M),
                                    np.einsum('sij,nij->ns', ops, MT)], axis=1), axis=1)
    return np.degrees(np.arccos(np.clip((tr - 1.0) / 2.0, -1.0, 1.0)))


class Scan:
    """1スキャン分の点データ（すべて 1 次元 index 空間で保持）。"""

    def __init__(self, num, nc, nr, xstep, ystep):
        self.nc, self.nr = int(nc), int(nr)
        self.xstep, self.ystep = float(xstep), float(ystep)
        e = np.stack([num['euler_phi1'], num['euler_phi'], num['euler_phi2']], -1)
        self.n = e.shape[0] * e.shape[1]
        self.euler = e.reshape(-1, 3)
        self.iq = np.asarray(num['image_quality']).reshape(-1)
        ph = num.get('phase_index')
        self.phase = np.asarray(ph).reshape(-1) if ph is not None else np.zeros(self.n)
        gn = num.get('grain_number')
        self.grain = np.asarray(gn).reshape(-1) if gn is not None else np.full(self.n, np.nan)
        km = num.get('kernel_average_misorientation')
        self.kam = np.asarray(km).reshape(-1) if km is not None else np.full(self.n, np.nan)
        self.valid = ~np.isnan(self.euler).any(1)
        self.row, self.col = np.divmod(np.arange(self.n), self.nc)
        self.g = np.zeros((self.n, 3, 3))
        self.g[self.valid] = euler_to_matrix(self.euler[self.valid])


def infer_reference_criterion(scan, ref_idx):
    """参照点が何を基準に選ばれたかをデータから推定する。

    粒の中で参照点が「上位何割」に入るかを指標ごとに求め、その中央値をとる。
    0 に近いほどその指標で選ばれた可能性が高く、0.5 はランダム相当。
    平均ではなく中央値を使うのは、粒の切り方の違い等で一部の参照点が
    大きく外れることがあり、平均だとそれに引きずられるため。
    CrossCourt は選定基準を .mat に書かないので、結果からの推定になる。

    戻り値: (判定, {指標名: 粒内順位比の中央値})
    """
    scores = {}
    for name, (v, high_is_better) in (('IQ最大', (scan.iq, True)),
                                      ('KAM最小', (scan.kam, False))):
        v = np.asarray(v, float)
        if not np.any(np.isfinite(v)):
            continue
        pct = []
        for i in ref_idx:
            i = int(i)
            if not (0 <= i < scan.n) or not np.isfinite(scan.grain[i]) or not np.isfinite(v[i]):
                continue
            m = (scan.grain == scan.grain[i]) & np.isfinite(v)
            if m.sum() < 2:
                continue
            better = (v[m] > v[i]) if high_is_better else (v[m] < v[i])
            pct.append(better.sum() / m.sum())
        if pct:
            scores[name] = float(np.median(pct))
    if not scores:
        return '不明', {}
    best = min(scores, key=scores.get)
    rest = [s for k, s in scores.items() if k != best]
    # ランダム寄り、または他の指標と差がつかないときは断定しない
    if scores[best] > 0.25 or (rest and min(rest) - scores[best] < 0.10):
        return '不明', scores
    return best, scores


def match_stage(ref, nth, ref_idx, angle_thr, x_limit=None, y_limit=None,
                phase_sym=None, use_symmetry=False, progress=None,
                allowed_src=None, src_label='window'):
    """nth の参照点 ref_idx それぞれに対し、ref から最良の点を選ぶ。

    allowed_src : bool 配列 (ref.n,) または None
        差し替え元にできる点を限定する。None なら ref の全点が候補。
    src_label : 採用した点がどの候補群から来たかを表す印（CSV の src_from）

    戻り値: list of dict
    """
    out = []
    for k, ti in enumerate(ref_idx):
        if progress and k % 10 == 0:
            progress(k, len(ref_idx))
        ti = int(ti)
        if ti < 0 or ti >= nth.n:
            out.append(dict(dst_index=ti, grain_id=-1, status='index_out_of_range')); continue

        # 未マッチのときも粒ID・位置は必ず入れる（粒単位の集計に必要）
        base = dict(dst_index=ti,
                    grain_id=int(nth.grain[ti]) if np.isfinite(nth.grain[ti]) else -1,
                    dst_col=int(nth.col[ti]), dst_row=int(nth.row[ti]))
        if not nth.valid[ti]:
            out.append(dict(base, status='target_invalid')); continue

        tph = int(nth.phase[ti]) if np.isfinite(nth.phase[ti]) else None
        trow, tcol = nth.row[ti], nth.col[ti]
        base['phase'] = tph

        mask = ref.valid.copy()
        if allowed_src is not None:
            mask &= allowed_src
        if tph is not None:
            mask &= (ref.phase == tph)
        if x_limit is not None:
            mask &= (np.abs(ref.col - tcol) <= x_limit)
        if y_limit is not None:
            mask &= (np.abs(ref.row - trow) <= y_limit)
        if not mask.any():
            out.append(dict(base, status='no_candidate_in_window', n_candidates=0)); continue

        idx = np.where(mask)[0]
        ops = None
        if use_symmetry and phase_sym:
            ops = phase_sym.get(tph)
        ang = misorientation(ref.g[idx], nth.g[ti], ops)
        within = ang <= angle_thr
        if not within.any():
            out.append(dict(base, status='no_match', n_candidates=0,
                            min_misorientation=round(float(ang.min()), 3))); continue

        cand = idx[within]
        cang = ang[within]
        ciq = ref.iq[cand]
        best = int(np.argmax(ciq))
        si = int(cand[best])
        dx = int(ref.col[si] - tcol)
        dy = int(ref.row[si] - trow)
        order = np.argsort(-ciq)
        ru = int(cand[order[1]]) if len(order) > 1 else -1
        out.append(dict(
            base, src_index=si, status='ok', src_from=src_label,
            src_col=int(ref.col[si]), src_row=int(ref.row[si]),
            dx_px=dx, dy_px=dy,
            dist_um=float(np.hypot(dx * ref.xstep, dy * ref.ystep)),
            misorientation_deg=round(float(cang[best]), 3),
            src_IQ=float(ref.iq[si]),
            n_candidates=int(within.sum()),
            min_misorientation=round(float(cang.min()), 3),
            runner_up_IQ=float(ref.iq[ru]) if ru >= 0 else np.nan,
            runner_up_misorientation=round(float(ang[np.where(idx == ru)[0][0]]), 3) if ru >= 0 else np.nan,
        ))
    if progress:
        progress(len(ref_idx), len(ref_idx))
    return out
