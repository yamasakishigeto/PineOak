"""
dic_icgn.py  —  IC-GN（逆合成ガウス・ニュートン法）によるサブピクセル精密化

整数画素探索＋相関ピークの放物線フィットは、ピーク形状の仮定ずれによる 1px 周期の
系統誤差（ピクセルロッキング, ±0.03〜0.1px）を避けられない。IC-GN は
  「参照サブセットを 1 次形状関数（u, ux, uy, v, vx, vy）で変形させ、
    高次補間した変形画像との ZNSSD を最小化する」
ことで変位を直接求め、この誤差を 1 桁以上減らす（Pan et al., 2013 の定式化）。

IC（逆合成）の利点: ヘッセ行列と最急降下画像は参照側で 1 回だけ計算すればよく、
反復ごとに必要なのは変形画像の補間と残差だけ。リアルタイム用途と相性がよい。

補間は CPU/GPU とも 3 次 B-spline（係数化してから 16 タップ）。
  CPU (numpy) : scipy.ndimage.map_coordinates をスレッド並列（GIL を解放するので 8 本で約 6 倍）
  GPU (CuPy)  : cupyx spline_filter で係数化 → xp の fancy index で 16 タップ gather
cv2.remap は座標を 1/32 px に量子化するため IC-GN の収束に使えない（検証済み）。
係数化（spline_filter）は 1080p 全面で約 40 ms かかるので、ROI の外接矩形に限定して行う。
"""
from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple
import numpy as np
import cv2

from scipy import ndimage as _ndi

try:
    import cupy as _cp
    from cupyx.scipy import ndimage as _cpx_ndi
except Exception:      # ImportError / CUDA 不在
    _cp = None
    _cpx_ndi = None


def _is_cupy(a) -> bool:
    return _cp is not None and isinstance(a, _cp.ndarray)


def _to_numpy(a):
    return _cp.asnumpy(a) if _is_cupy(a) else np.asarray(a)


class ICGNRefiner:
    """
    参照画像上の N 個のサブセット中心 (cx, cy) について IC-GN を一括反復する。

    使い方:
        ref = ICGNRefiner(ref_img_u8_or_f32, cx, cy, subset_size, xp=np)   # 参照側の前計算
        ref.set_deformed(def_img)                                          # フレームごと
        p, zncc, conv = ref.refine(p0)                                     # p0: (N,6) 初期値
    p の並びは (u, ux, uy, v, vx, vy)。u, v [px]、ux 等は無次元の変位勾配。
    """

    def __init__(self, ref_img: np.ndarray, cx: np.ndarray, cy: np.ndarray,
                 subset_size: int, xp=np, max_iter: int = 12, tol: float = 1e-3,
                 chunk: int = 2048, n_threads: Optional[int] = None, margin: int = 32):
        """margin: 変形画像を係数化する範囲（サブセット外接矩形からの余白 [px]）。変位がこれを超える点は精密化できない。"""
        self.xp = xp
        self.S = int(subset_size) | 1
        self.half = self.S // 2
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self.chunk = int(chunk)
        self.N = int(len(cx))
        self.cx = np.asarray(cx, np.int64)
        self.cy = np.asarray(cy, np.int64)

        f = self._as_f32(ref_img)
        H, W = f.shape
        self.img_shape = (H, W)
        if n_threads is None:
            n_threads = max(1, min(8, os.cpu_count() or 2))
        self.n_threads = int(n_threads)
        self._pool = ThreadPoolExecutor(self.n_threads) if (xp is np and self.n_threads > 1) else None
        if self._pool is not None:
            # CPU: チャンク単位でスレッド並列するので、スレッド数の 2 倍程度に分割する
            self.chunk = max(64, min(self.chunk, -(-self.N // (2 * self.n_threads))))
        # 変形画像の係数化範囲（ROI 外接矩形 + margin）
        m = self.half + int(margin)
        self.bx0 = int(max(0, self.cx.min() - m)); self.bx1 = int(min(W, self.cx.max() + m + 1))
        self.by0 = int(max(0, self.cy.min() - m)); self.by1 = int(min(H, self.cy.max() + m + 1))
        # 参照勾配（4 次精度中心差分）。反復には参照側の勾配だけが要る
        k = np.array([1, -8, 0, 8, -1], np.float32) / 12.0
        fx = cv2.filter2D(f, cv2.CV_32F, k[None, :], borderType=cv2.BORDER_REPLICATE)
        fy = cv2.filter2D(f, cv2.CV_32F, k[:, None], borderType=cv2.BORDER_REPLICATE)

        # サブセット局所座標
        r = np.arange(-self.half, self.half + 1, dtype=np.float32)
        Xl, Yl = np.meshgrid(r, r)
        self.xl = Xl.ravel()        # (S²,)
        self.yl = Yl.ravel()

        # 点ごとのサブセット画素インデックス（画像内にクランプ）
        rows = np.clip(self.cy[:, None] + self.yl[None, :].astype(np.int64), 0, H - 1)
        cols = np.clip(self.cx[:, None] + self.xl[None, :].astype(np.int64), 0, W - 1)
        fs = f[rows, cols]                                     # (N,S²)
        fxs = fx[rows, cols]
        fys = fy[rows, cols]
        f_mean = fs.mean(axis=1, keepdims=True)
        f_zm = fs - f_mean
        self.df = np.sqrt((f_zm ** 2).sum(axis=1))             # (N,)
        self.f_zm = xp.asarray(f_zm)
        self.df_x = xp.asarray(self.df.astype(np.float32))

        # 最急降下画像 J = ∇f·∂W/∂p (N,S²,6) とヘッセ逆行列 (N,6,6) をチャンクごとに前計算
        self.J = []
        self.Hinv = []
        self.valid = self.df > 1e-6
        for a in range(0, self.N, self.chunk):
            b = min(self.N, a + self.chunk)
            J = np.empty((b - a, self.S * self.S, 6), np.float32)
            J[:, :, 0] = fxs[a:b]
            J[:, :, 1] = fxs[a:b] * self.xl
            J[:, :, 2] = fxs[a:b] * self.yl
            J[:, :, 3] = fys[a:b]
            J[:, :, 4] = fys[a:b] * self.xl
            J[:, :, 5] = fys[a:b] * self.yl
            Hm = np.matmul(J.transpose(0, 2, 1), J).astype(np.float64)   # (n,6,6)
            Hm += np.eye(6)[None] * 1e-9
            try:
                Hinv = np.linalg.inv(Hm)
            except np.linalg.LinAlgError:
                Hinv = np.linalg.pinv(Hm)
            self.J.append(xp.asarray(J))
            self.Hinv.append(xp.asarray(Hinv.astype(np.float32)))
        self.xl_x = xp.asarray(self.xl)
        self.yl_x = xp.asarray(self.yl)
        self._coef = None

    # ------------------------------------------------------------------
    @staticmethod
    def _as_f32(img: np.ndarray) -> np.ndarray:
        img = _to_numpy(img)
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if img.dtype == np.uint8:
            return img.astype(np.float32) / 255.0
        if img.dtype == np.uint16:
            return img.astype(np.float32) / 65535.0
        return np.ascontiguousarray(img, dtype=np.float32)

    def set_deformed(self, def_img: np.ndarray, bbox: Optional[Tuple[int, int, int, int]] = None):
        """変形画像をセットし、ROI 外接矩形内（bbox=(x0,y0,x1,y1) で上書き可）を B-spline 係数化する。"""
        if bbox is not None:
            H, W = self.img_shape
            self.bx0 = int(max(0, bbox[0])); self.by0 = int(max(0, bbox[1]))
            self.bx1 = int(min(W, bbox[2])); self.by1 = int(min(H, bbox[3]))
        g = self._as_f32(def_img)[self.by0:self.by1, self.bx0:self.bx1]
        if self.xp is np:
            self._coef = _ndi.spline_filter(np.ascontiguousarray(g), order=3, output=np.float32, mode="mirror")
        else:
            self._coef = _cpx_ndi.spline_filter(self.xp.asarray(g), order=3, output=self.xp.float32, mode="mirror")

    # ------------------------------------------------------------------
    def _sample(self, X, Y, threaded: bool = True):
        """(M,S²) の実数座標で変形画像を補間。戻り値 (M,S²) float32（xp）。"""
        X = X - self.bx0
        Y = Y - self.by0
        if self.xp is np:
            c = self._coef
            out = np.empty(X.shape, np.float32)

            def work(a: int, b: int):
                out[a:b] = _ndi.map_coordinates(c, [Y[a:b].ravel(), X[a:b].ravel()], order=3,
                                                prefilter=False, mode="nearest",
                                                output=np.float32).reshape(b - a, -1)
            M = X.shape[0]
            if self._pool is None or M < 32 or not threaded:
                work(0, M)
            else:
                nt = self.n_threads
                bnd = np.linspace(0, M, nt + 1).astype(int)
                list(self._pool.map(lambda k: work(bnd[k], bnd[k + 1]), range(nt)))
            return out
        xp = self.xp
        c = self._coef
        H, W = c.shape
        ix = xp.floor(X); iy = xp.floor(Y)
        tx = (X - ix).astype(xp.float32); ty = (Y - iy).astype(xp.float32)
        ix = ix.astype(xp.int64); iy = iy.astype(xp.int64)

        def wts(t):
            t2 = t * t; t3 = t2 * t
            return ((1 - t) ** 3 / 6.0,
                    (3 * t3 - 6 * t2 + 4) / 6.0,
                    (-3 * t3 + 3 * t2 + 3 * t + 1) / 6.0,
                    t3 / 6.0)
        wx = wts(tx); wy = wts(ty)
        out = xp.zeros(X.shape, xp.float32)
        for j in range(4):
            ry = xp.clip(iy + (j - 1), 0, H - 1)
            row = xp.zeros(X.shape, xp.float32)
            for i in range(4):
                rx = xp.clip(ix + (i - 1), 0, W - 1)
                row += wx[i] * c[ry, rx]
            out += wy[j] * row
        return out

    # ------------------------------------------------------------------
    def refine(self, p0: np.ndarray, mask: Optional[np.ndarray] = None
               ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        p0: (N,6) 初期パラメータ（numpy）。mask: 反復する点（None で valid 全点）。
        戻り値（numpy）: p (N,6), zncc (N), converged (N,bool), n_iter (N)
        最終反復ではワープを更新せず評価だけ行うので、返す zncc は返す p に対応する。
        CPU ではチャンクごとに別スレッドで反復する（補間・行列演算とも GIL を解放するため並列が効く）。
        """
        N = self.N
        p_out = np.array(p0, np.float32).copy()
        z_out = np.full(N, -1.0, np.float32)
        conv_out = np.zeros(N, bool)
        it_out = np.zeros(N, np.int32)
        sel_all = self.valid if mask is None else (self.valid & np.asarray(mask, bool))

        def run_chunk(ci: int):
            a = ci * self.chunk
            b = min(N, a + self.chunk)
            idx = np.nonzero(sel_all[a:b])[0]
            if idx.size == 0:
                return
            p_fin, zncc, ok, n_it, gidx = self._refine_chunk(ci, a, b, idx, p_out[idx + a])
            p_out[gidx] = p_fin
            z_out[gidx] = zncc
            conv_out[gidx] = ok
            it_out[gidx] = n_it

        n_chunks = (N + self.chunk - 1) // self.chunk
        if self._pool is not None and n_chunks > 1:
            list(self._pool.map(run_chunk, range(n_chunks)))
        else:
            for ci in range(n_chunks):
                run_chunk(ci)
        return p_out, z_out, conv_out, it_out

    def _refine_chunk(self, ci: int, a: int, b: int, idx: np.ndarray, p_init: np.ndarray):
        xp = self.xp
        half = float(self.half)
        M = idx.size
        gidx = idx + a
        if M == b - a:
            # チャンク全点 → コピーせず参照（J は大きいので fancy index のコピーが重い）
            J = self.J[ci]; Hinv = self.Hinv[ci]
            f_zm = self.f_zm[a:b]; df = self.df_x[a:b]
        else:
            idx_x = xp.asarray(idx)
            J = self.J[ci][idx_x]                     # (M,S²,6)
            Hinv = self.Hinv[ci][idx_x]               # (M,6,6)
            gidx_x = xp.asarray(gidx)
            f_zm = self.f_zm[gidx_x]; df = self.df_x[gidx_x]
        cx = xp.asarray(self.cx[gidx].astype(np.float32))[:, None]
        cy = xp.asarray(self.cy[gidx].astype(np.float32))[:, None]

        p = xp.asarray(p_init)
        Wm = self._p_to_W(p)
        active = xp.ones(M, dtype=bool)
        n_it = xp.zeros(M, dtype=xp.int32)
        converged = xp.zeros(M, dtype=bool)
        zncc = xp.full(M, -1.0, dtype=xp.float32)
        xl = self.xl_x[None, :]; yl = self.yl_x[None, :]

        for k in range(self.max_iter):
            last = (k == self.max_iter - 1)
            A = Wm[:, 0, 0][:, None]; B = Wm[:, 0, 1][:, None]; C = Wm[:, 0, 2][:, None]
            D = Wm[:, 1, 0][:, None]; E = Wm[:, 1, 1][:, None]; F = Wm[:, 1, 2][:, None]
            X = cx + A * xl + B * yl + C
            Y = cy + D * xl + E * yl + F
            g = self._sample(X, Y, threaded=False)
            g_zm = g - g.mean(axis=1, keepdims=True)
            dg = xp.sqrt((g_zm * g_zm).sum(axis=1)) + 1e-12
            zncc = (f_zm * g_zm).sum(axis=1) / (df * dg)
            e = f_zm - (df / dg)[:, None] * g_zm                      # (M,S²)
            bvec = xp.matmul(e[:, None, :], J)[:, 0, :]              # (M,6)
            dp = -xp.matmul(Hinv, bvec[:, :, None])[:, :, 0]         # (M,6)
            dnorm = xp.sqrt(dp[:, 0] ** 2 + dp[:, 3] ** 2 +
                            (dp[:, 1] * half) ** 2 + (dp[:, 2] * half) ** 2 +
                            (dp[:, 4] * half) ** 2 + (dp[:, 5] * half) ** 2)
            newly = active & (dnorm < self.tol)
            converged |= newly
            n_it += active.astype(xp.int32)
            active = active & ~newly & xp.isfinite(dnorm)
            if last or not bool(active.any()):
                break
            # 逆合成更新: W ← W ∘ W(Δp)⁻¹（収束済み・打ち切り点は動かさない）
            dW = self._p_to_W(dp)
            Wm_new = xp.matmul(Wm, xp.linalg.inv(dW))
            Wm = xp.where(active[:, None, None], Wm_new, Wm)

        p_fin = self._W_to_p(Wm)
        ok = converged & xp.isfinite(zncc) & xp.all(xp.isfinite(p_fin), axis=1)
        good_val = xp.isfinite(zncc) & xp.all(xp.isfinite(p_fin), axis=1)
        p_fin = xp.where(good_val[:, None], p_fin, xp.asarray(p_init))
        zncc = xp.where(good_val, zncc, -1.0)
        return (_to_numpy(p_fin), _to_numpy(zncc).astype(np.float32),
                _to_numpy(ok), _to_numpy(n_it), gidx)

    def _p_to_W(self, p):
        xp = self.xp
        M = p.shape[0]
        W = xp.zeros((M, 3, 3), xp.float32)
        W[:, 0, 0] = 1 + p[:, 1]; W[:, 0, 1] = p[:, 2]; W[:, 0, 2] = p[:, 0]
        W[:, 1, 0] = p[:, 4];     W[:, 1, 1] = 1 + p[:, 5]; W[:, 1, 2] = p[:, 3]
        W[:, 2, 2] = 1
        return W

    def _W_to_p(self, W):
        xp = self.xp
        return xp.stack([W[:, 0, 2], W[:, 0, 0] - 1, W[:, 0, 1],
                         W[:, 1, 2], W[:, 1, 0], W[:, 1, 1] - 1], axis=1)


def refine_uv(ref_img: np.ndarray, def_img: np.ndarray,
              cx: np.ndarray, cy: np.ndarray, u0: np.ndarray, v0: np.ndarray,
              subset_size: int, xp=np, max_iter: int = 12, tol: float = 1e-3,
              ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    1 回限りの精密化（マップモード用）。整数探索の結果 (u0, v0) を初期値に IC-GN を回す。
    戻り値: u, v, zncc, converged（収束しなかった点は入力値と zncc=-1 のまま）
    """
    ok = np.isfinite(u0) & np.isfinite(v0)
    if not ok.any():
        return (np.array(u0, np.float32), np.array(v0, np.float32),
                np.full(len(u0), -1.0, np.float32), np.zeros(len(u0), bool))
    # 変位の大きさに応じて係数化範囲の余白を広げる
    margin = int(np.ceil(max(np.abs(u0[ok]).max(), np.abs(v0[ok]).max()))) + 8
    ref = ICGNRefiner(ref_img, cx[ok], cy[ok], subset_size, xp=xp, max_iter=max_iter, tol=tol, margin=margin)
    ref.set_deformed(def_img)
    p0 = np.zeros((int(ok.sum()), 6), np.float32)
    p0[:, 0] = u0[ok]; p0[:, 3] = v0[ok]
    p, z, conv, _ = ref.refine(p0)
    u = np.array(u0, np.float32); v = np.array(v0, np.float32)
    zncc = np.full(len(u0), -1.0, np.float32)
    convf = np.zeros(len(u0), bool)
    u[ok] = p[:, 0]; v[ok] = p[:, 3]; zncc[ok] = z; convf[ok] = conv
    return u, v, zncc, convf
