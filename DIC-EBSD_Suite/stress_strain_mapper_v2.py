"""
stress_strain_mapper_v2.py
==========================
integrated_georef.mat を読み込んで応力・ひずみ解析を行うインタラクティブGUIツール。
PyQt6 + matplotlib Qt6Agg バックエンドで描画。

使用方法:
    python stress_strain_mapper_v2.py --file path/to/integrated_georef.mat
    python stress_strain_mapper_v2.py          # ファイルダイアログで選択
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import os
os.environ.setdefault("QT_API", "PyQt6")
import matplotlib
matplotlib.use("QtAgg")
import matplotlib.colors as mcolors
import matplotlib.ticker
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvasQtAgg, NavigationToolbar2QT
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from scipy.io import loadmat

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTabWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QSlider, QPushButton, QLineEdit,
    QRadioButton, QButtonGroup, QGroupBox, QFileDialog,
    QMessageBox, QSizePolicy, QCheckBox,
)
from PyQt6.QtCore import Qt

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


def compute_boundary_segments(x, y, grain_id):
    """隣接サブセット間で grain_id が異なる箇所の境界線分を返す。"""
    coord_to_idx = {(int(xi), int(yi)): i for i, (xi, yi) in enumerate(zip(x, y))}
    segments = []
    for i, (xi, yi, gi) in enumerate(zip(x, y, grain_id)):
        xi_i, yi_i = int(xi), int(yi)
        for dx, dy in ((1, 0), (0, 1)):
            nb = coord_to_idx.get((xi_i + dx, yi_i + dy))
            if nb is not None and grain_id[nb] != gi:
                segments.append([(xi, yi), (x[nb], y[nb])])
    return segments


# ============================================================
# 定数
# ============================================================

CMAPS = [
    "viridis", "plasma", "inferno", "magma", "coolwarm",
    "RdBu_r", "seismic", "bwr", "jet", "turbo",
    "rainbow", "hot", "Blues", "Reds",
]

DIC_STRAIN_BASES = ["exx", "eyy", "exy", "e1", "gamma_max", "omega_xy"]
EXCLUDE_Y_BASES = {"u", "v", "ncc"} | set(DIC_STRAIN_BASES)


# ============================================================
# matplotlib Canvas ウィジェット
# ============================================================

class MapCanvas(FigureCanvasQtAgg):
    """散布図マップ用キャンバス（アスペクト比固定）。"""

    def __init__(self, parent=None):
        self._fig = Figure(tight_layout=True)
        self.ax = self._fig.add_subplot(111)
        super().__init__(self._fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._colorbar = None

    def draw_scatter(self, x, y, data, cmap, vmin, vmax, title,
                     xlim=None, ylim=None, cbar_label=None):
        # figure ごとクリアして axes を作り直す（colorbar による縮小を防ぐ）
        self._fig.clf()
        self.ax = self._fig.add_subplot(111)
        self._colorbar = None

        sc = self.ax.scatter(x, y, c=data, cmap=cmap, vmin=vmin, vmax=vmax, s=8, alpha=0.9)
        self._colorbar = self._fig.colorbar(sc, ax=self.ax)
        if cbar_label:
            self._colorbar.set_label(cbar_label)
        self.ax.set_title(title)
        self.ax.set_xlabel("X")
        self.ax.set_ylabel("Y")
        # 軸範囲を明示的に指定（NaNで範囲が縮まるのを防ぐ）
        if xlim is not None:
            self.ax.set_xlim(xlim)
        if ylim is not None:
            self.ax.set_ylim(ylim)
        else:
            self.ax.invert_yaxis()
        # adjustable='box': xlim/ylimを固定したままaxesボックスを縮めてアスペクト比を保持
        self.ax.set_aspect("equal", adjustable="box")
        self.draw()


class CurveCanvas(FigureCanvasQtAgg):
    """SS カーブ用キャンバス。"""

    def __init__(self, parent=None):
        self._fig = Figure(tight_layout=True)
        self.ax = self._fig.add_subplot(111)
        super().__init__(self._fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def plot_curve(self, x, y, label, title,
                   xlim=None, ylim=None, xlabel="Strain", ylabel="Stress"):
        ax = self.ax
        ax.clear()
        ax.plot(x, y, marker="o", label=label)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend()
        if xlim is not None:
            ax.set_xlim(xlim)
        if ylim is not None:
            ax.set_ylim(ylim)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.1f"))
        self.draw()


# ============================================================
# メインウィンドウ
# ============================================================

class StressStrainMapperApp(QMainWindow):

    def __init__(self, mat_path: Path):
        super().__init__()
        self.mat_path = mat_path
        self.setWindowTitle(f"Stress–Strain Mapper v2  —  {mat_path.name}")
        self.resize(1600, 1200)

        # データ読み込み
        self.static, self.per_stage, self.stages = parse_mat(mat_path)
        self._load_base_arrays()

        # SS カーブ用
        self.sv = None
        self.ss = None
        self.ss_stages = []
        self._ss_x_base = ""
        self._ss_y_base = ""

        # 派生マップ結果
        self._derived_results = {}

        # Grain マップ操作イベントID
        self._grain_cid = None

        self._build_ui()
        self._draw_grain_map()

    # ----------------------------------------------------------
    # データ準備
    # ----------------------------------------------------------

    def _load_base_arrays(self):
        s = self.static
        self.cx = s.get("cx", np.array([])).astype(float)
        self.cy = s.get("cy", np.array([])).astype(float)
        self.grain_id = s.get("grain_id", np.zeros(len(self.cx), dtype=int)).astype(int)
        self.subset_id = s.get("subset_id", np.arange(len(self.cx))).astype(int)
        N = len(self.cx)
        def _load_euler(key_new, key_old):
            # static から探す
            v = s.get(key_new, np.full(N, np.nan)).astype(float).ravel()
            if not np.any(np.isfinite(v)):
                v = s.get(key_old, np.full(N, np.nan)).astype(float).ravel()
            # static になければ per_stage の最初のステージから探す
            if not np.any(np.isfinite(v)):
                for k in (key_new, key_old):
                    stages_for_k = self.per_stage.get(k, {})
                    if stages_for_k:
                        first_stage = next(iter(stages_for_k))
                        v = stages_for_k[first_stage].astype(float).ravel()
                        break
            return v
        self.phi1_deg = _load_euler("phi1_ref", "euler_phi1")
        self.PHI_deg  = _load_euler("PHI_ref",  "euler_phi")
        self.phi2_deg = _load_euler("phi2_ref", "euler_phi2")
        self._grain_unique = np.unique(self.grain_id)
        self._grain_code = np.searchsorted(self._grain_unique, self.grain_id)
        # 全サブセットの軸範囲（両マップで共有）
        if len(self.cx) > 0:
            self._xlim = (float(np.min(self.cx)), float(np.max(self.cx)))
            self._ylim = (float(np.max(self.cy)), float(np.min(self.cy)))  # y反転
        else:
            self._xlim = (0.0, 1.0)
            self._ylim = (1.0, 0.0)

    # ----------------------------------------------------------
    # UI 構築（2×2 グリッド）
    # ----------------------------------------------------------

    def _build_ui(self):
        # 水平スプリッター（左列 / 右列）
        main_split = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_split)

        # 左列: Grain ID Map（上） + SS Curve（下）
        self.canvas_grain = MapCanvas()
        self.canvas_curve = CurveCanvas()
        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.addWidget(self._wrap_canvas(self.canvas_grain, "Grain ID Map"))
        left_split.addWidget(self._wrap_canvas(self.canvas_curve, "Stress-Strain Curve"))
        left_split.setSizes([550, 550])

        # 右列: Variable Map（上） + コントロールパネル（下）
        self.canvas_map = MapCanvas()
        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self._wrap_canvas(self.canvas_map, "Variable Map"))
        right_split.addWidget(self._build_control_panel())
        right_split.setSizes([550, 550])

        main_split.addWidget(left_split)
        main_split.addWidget(right_split)
        main_split.setSizes([800, 800])

        # マウスホイールでステージを切り替え
        self.canvas_map.mpl_connect("scroll_event", self._on_map_scroll)

    def _wrap_canvas(self, canvas, title: str) -> QWidget:
        """キャンバス + ナビゲーションツールバーをまとめた QWidget を返す。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        toolbar = NavigationToolbar2QT(canvas, w)
        layout.addWidget(QLabel(f"<b>{title}</b>"))
        layout.addWidget(toolbar)
        layout.addWidget(canvas)
        return w

    def _build_control_panel(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setMinimumHeight(300)

        tab_map = QWidget()
        tab_ss = QWidget()
        tab_derived = QWidget()
        tabs.addTab(tab_map, "Map")
        tabs.addTab(tab_ss, "Stress-Strain Curve")
        tabs.addTab(tab_derived, "Derived Maps")

        self._build_tab_map(tab_map)
        self._build_tab_ss(tab_ss)
        self._build_tab_derived(tab_derived)
        return tabs

    # ----------------------------------------------------------
    # タブ1: Map
    # ----------------------------------------------------------

    def _build_tab_map(self, parent):
        layout = QVBoxLayout(parent)

        # 変数プルダウン
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("変数:"))
        self._map_var_cb = QComboBox()
        self._map_var_cb.addItems(self._all_variable_names())
        self._map_var_cb.currentTextChanged.connect(self._on_map_var_changed)
        row1.addWidget(self._map_var_cb, stretch=1)
        layout.addLayout(row1)

        # ステージスライダー
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("ステージ:"))
        self._map_stage_slider = QSlider(Qt.Orientation.Horizontal)
        self._map_stage_slider.setMinimum(0)
        self._map_stage_slider.setMaximum(0)
        self._map_stage_slider.valueChanged.connect(self._on_map_stage_changed)
        self._map_stage_label = QLabel("")
        row2.addWidget(self._map_stage_slider, stretch=1)
        row2.addWidget(self._map_stage_label)
        layout.addLayout(row2)

        # 座標系
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("座標系:"))
        self._map_coord_bg = QButtonGroup(parent)
        rb_ref = QRadioButton("Reference (cx, cy)")
        self._rb_deformed = QRadioButton("Deformed (cx+u, cy+v)")
        rb_ref.setChecked(True)
        self._map_coord_bg.addButton(rb_ref, 0)
        self._map_coord_bg.addButton(self._rb_deformed, 1)
        row3.addWidget(rb_ref)
        row3.addWidget(self._rb_deformed)
        layout.addLayout(row3)

        # カラーマップ
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("カラーマップ:"))
        self._map_cmap_cb = QComboBox()
        self._map_cmap_cb.addItems(CMAPS)
        row4.addWidget(self._map_cmap_cb)
        layout.addLayout(row4)

        # Min / Max
        row5 = QHBoxLayout()
        row5.addWidget(QLabel("Min:"))
        self._map_min_edit = QLineEdit()
        self._map_min_edit.setPlaceholderText("auto")
        row5.addWidget(self._map_min_edit)
        row5.addWidget(QLabel("Max:"))
        self._map_max_edit = QLineEdit()
        self._map_max_edit.setPlaceholderText("auto")
        row5.addWidget(self._map_max_edit)
        btn_auto = QPushButton("Auto")
        btn_auto.clicked.connect(self._map_auto_minmax)
        row5.addWidget(btn_auto)
        layout.addLayout(row5)

        # 結晶粒界オーバーレイ
        row_gb = QHBoxLayout()
        self._map_show_gb_cb = QCheckBox("Grain boundaries")
        self._map_show_gb_cb.setChecked(False)
        row_gb.addWidget(self._map_show_gb_cb)
        row_gb.addStretch()
        layout.addLayout(row_gb)

        # ボタン
        row6 = QHBoxLayout()
        btn_draw = QPushButton("Draw Map")
        btn_draw.clicked.connect(self._draw_map_current)
        btn_export = QPushButton("Export PNG")
        btn_export.clicked.connect(self._export_map_current)
        btn_all = QPushButton("Export All Stages")
        btn_all.clicked.connect(self._export_map_all_stages)
        row6.addWidget(btn_draw)
        row6.addWidget(btn_export)
        row6.addWidget(btn_all)
        layout.addLayout(row6)

        layout.addStretch()

        # 初期化
        self._on_map_var_changed(self._map_var_cb.currentText())

    def _all_variable_names(self):
        return sorted(self.per_stage.keys()) + sorted(self.static.keys())

    def _stages_for_base(self, base):
        if base not in self.per_stage:
            return []
        def key(s):
            m = re.match(r"(\d+)MPa", s)
            return int(m.group(1)) if m else 0
        return sorted(self.per_stage[base].keys(), key=key)

    def _on_map_var_changed(self, base=None):
        if base is None:
            base = self._map_var_cb.currentText()
        sts = self._stages_for_base(base)
        if sts:
            self._map_stage_slider.setMaximum(len(sts) - 1)
            self._map_stage_slider.setEnabled(True)
            self._map_stage_label.setText(sts[min(self._map_stage_slider.value(), len(sts) - 1)])
        else:
            self._map_stage_slider.setMaximum(0)
            self._map_stage_slider.setEnabled(False)
            self._map_stage_label.setText("(静的変数)")
        has_uv = "u" in self.per_stage and "v" in self.per_stage
        self._rb_deformed.setEnabled(has_uv)
        if not has_uv:
            self._map_coord_bg.button(0).setChecked(True)

    def _on_map_stage_changed(self, val):
        base = self._map_var_cb.currentText()
        sts = self._stages_for_base(base)
        if sts and 0 <= val < len(sts):
            self._map_stage_label.setText(sts[val])

    def _get_map_data(self, base, stage_idx):
        if base in self.per_stage:
            sts = self._stages_for_base(base)
            stage = sts[stage_idx] if 0 <= stage_idx < len(sts) else sts[0]
            data = self.per_stage[base][stage]
        else:
            data = self.static[base]
            stage = None

        if self._map_coord_bg.checkedId() == 1 and stage and \
                "u" in self.per_stage and "v" in self.per_stage:
            x = self.cx + self.per_stage["u"].get(stage, np.zeros_like(self.cx))
            y = self.cy + self.per_stage["v"].get(stage, np.zeros_like(self.cy))
        else:
            x, y = self.cx, self.cy
        return data, x, y, stage

    def _parse_minmax(self, data):
        try:
            vmin = float(self._map_min_edit.text())
        except ValueError:
            vmin = float(np.nanmin(data))
        try:
            vmax = float(self._map_max_edit.text())
        except ValueError:
            vmax = float(np.nanmax(data))
        return vmin, vmax

    def _var_unit(self, base):
        """変数名から単位文字列を返す。不明な場合は空文字。"""
        if base in DIC_STRAIN_BASES or base.startswith("map_e") or base.startswith("rmap_e"):
            return "-"
        if base.startswith("map_w") or base.startswith("rmap_w"):
            return "rad"
        if base.startswith("map_s") or base.startswith("rmap_s"):
            return "GPa"
        if base in ("u", "v"):
            return "px"
        return ""

    def _map_auto_minmax(self):
        base = self._map_var_cb.currentText()
        data, _, _, _ = self._get_map_data(base, self._map_stage_slider.value())
        self._map_min_edit.setText(f"{np.nanmin(data):.6g}")
        self._map_max_edit.setText(f"{np.nanmax(data):.6g}")

    def _draw_map_current(self):
        base = self._map_var_cb.currentText()
        idx = self._map_stage_slider.value()
        data, x, y, stage = self._get_map_data(base, idx)
        vmin, vmax = self._parse_minmax(data)
        cmap = self._map_cmap_cb.currentText()
        title = f"{base}  [{stage}]" if stage else base
        unit = self._var_unit(base)
        cbar_label = f"{base} [{unit}]" if unit else base
        self.canvas_map.draw_scatter(x, y, data, cmap, vmin, vmax, title, xlim=self._xlim, ylim=self._ylim, cbar_label=cbar_label)
        if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
            segs = compute_boundary_segments(x, y, self.grain_id)
            if segs:
                self.canvas_map.ax.add_collection(
                    LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k")
                )
                self.canvas_map.draw()

    def _on_map_scroll(self, event):
        """マウスホイールでステージスライダーを1段階ずつ切り替える。"""
        if not self._map_stage_slider.isEnabled():
            return
        current = self._map_stage_slider.value()
        if event.button == "up":
            new_val = max(0, current - 1)
        elif event.button == "down":
            new_val = min(self._map_stage_slider.maximum(), current + 1)
        else:
            return
        if new_val != current:
            self._map_stage_slider.setValue(new_val)
            self._draw_map_current()

    def _export_map_current(self):
        base = self._map_var_cb.currentText()
        idx = self._map_stage_slider.value()
        data, x, y, stage = self._get_map_data(base, idx)
        vmin, vmax = self._parse_minmax(data)
        cmap = self._map_cmap_cb.currentText()
        title = f"{base}  [{stage}]" if stage else base
        unit = self._var_unit(base)
        cbar_label = f"{base} [{unit}]" if unit else base
        self.canvas_map.draw_scatter(x, y, data, cmap, vmin, vmax, title, xlim=self._xlim, ylim=self._ylim, cbar_label=cbar_label)
        fname = f"map_{base}_{stage}.png" if stage else f"map_{base}.png"
        out = self.mat_path.parent / fname
        self.canvas_map._fig.savefig(str(out), dpi=150, bbox_inches="tight")
        QMessageBox.information(self, "Export", f"保存しました:\n{out}")

    def _export_map_all_stages(self):
        base = self._map_var_cb.currentText()
        if base not in self.per_stage:
            QMessageBox.warning(self, "Export All", "ステージ付き変数を選択してください。")
            return
        cmap = self._map_cmap_cb.currentText()
        sts = self._stages_for_base(base)
        try:
            vmin = float(self._map_min_edit.text())
            vmax = float(self._map_max_edit.text())
        except ValueError:
            all_vals = np.concatenate([self.per_stage[base][st] for st in sts])
            vmin, vmax = float(np.nanmin(all_vals)), float(np.nanmax(all_vals))

        unit = self._var_unit(base)
        cbar_label = f"{base} [{unit}]" if unit else base
        for stage in sts:
            data = self.per_stage[base][stage]
            if self._map_coord_bg.checkedId() == 1 and \
                    "u" in self.per_stage and "v" in self.per_stage:
                x = self.cx + self.per_stage["u"].get(stage, np.zeros_like(self.cx))
                y = self.cy + self.per_stage["v"].get(stage, np.zeros_like(self.cy))
            else:
                x, y = self.cx, self.cy
            self.canvas_map.draw_scatter(x, y, data, cmap, vmin, vmax, f"{base} [{stage}]", xlim=self._xlim, ylim=self._ylim, cbar_label=cbar_label)
            self.canvas_map._fig.savefig(
                str(self.mat_path.parent / f"map_{base}_{stage}.png"),
                dpi=150, bbox_inches="tight")

        QMessageBox.information(self, "Export All",
                                f"{len(sts)} 枚を保存しました:\n{self.mat_path.parent}")

    # ----------------------------------------------------------
    # タブ2: SS Curve
    # ----------------------------------------------------------

    def _build_tab_ss(self, parent):
        layout = QVBoxLayout(parent)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("X軸 (ひずみ):"))
        x_choices = [b for b in DIC_STRAIN_BASES if b in self.per_stage]
        self._ss_x_cb = QComboBox()
        self._ss_x_cb.addItems(x_choices)
        row1.addWidget(self._ss_x_cb, stretch=1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Y軸 (応力):"))
        self._ss_y_cb = QComboBox()
        self._ss_y_cb.addItems(self._ss_y_choices())
        self._set_default_cb(self._ss_y_cb, "rmap_s11")
        row2.addWidget(self._ss_y_cb, stretch=1)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        btn_apply = QPushButton("Apply")
        btn_apply.clicked.connect(self._ss_apply)
        self._ss_status_lbl = QLabel("Apply を押してください")
        self._ss_status_lbl.setStyleSheet("color: gray;")
        row3.addWidget(btn_apply)
        row3.addWidget(self._ss_status_lbl, stretch=1)
        layout.addLayout(row3)

        row_stage_range = QHBoxLayout()
        row_stage_range.addWidget(QLabel("表示ステージ:"))
        self._ss_stage_from_cb = QComboBox()
        self._ss_stage_from_cb.setEnabled(False)
        self._ss_stage_to_cb = QComboBox()
        self._ss_stage_to_cb.setEnabled(False)
        row_stage_range.addWidget(self._ss_stage_from_cb, stretch=1)
        row_stage_range.addWidget(QLabel("〜"))
        row_stage_range.addWidget(self._ss_stage_to_cb, stretch=1)
        layout.addLayout(row_stage_range)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("グループ:"))
        self._ss_group_bg = QButtonGroup(parent)
        for i, val in enumerate(("Subset", "Grain avg", "Phase avg")):
            rb = QRadioButton(val)
            if i == 0:
                rb.setChecked(True)
            self._ss_group_bg.addButton(rb, i)
            row4.addWidget(rb)
        layout.addLayout(row4)

        self._phase_group = QGroupBox("Phase 名")
        self._phase_group_layout = QVBoxLayout(self._phase_group)
        self._phase_entries = {}
        self._build_phase_entries()
        layout.addWidget(self._phase_group)

        row5 = QHBoxLayout()
        row5.addWidget(QLabel("操作モード:"))
        self._ss_mode_bg = QButtonGroup(parent)
        for i, val in enumerate(("Click", "Hover")):
            rb = QRadioButton(val)
            if i == 0:
                rb.setChecked(True)
            self._ss_mode_bg.addButton(rb, i)
            row5.addWidget(rb)
        self._ss_mode_bg.idClicked.connect(self._reconnect_grain_events)
        layout.addLayout(row5)

        row_axis = QHBoxLayout()
        row_axis.addWidget(QLabel("X min:"))
        self._ss_xmin_edit = QLineEdit(); self._ss_xmin_edit.setPlaceholderText("auto")
        row_axis.addWidget(self._ss_xmin_edit)
        row_axis.addWidget(QLabel("X max:"))
        self._ss_xmax_edit = QLineEdit(); self._ss_xmax_edit.setPlaceholderText("auto")
        row_axis.addWidget(self._ss_xmax_edit)
        row_axis.addWidget(QLabel("Y min:"))
        self._ss_ymin_edit = QLineEdit(); self._ss_ymin_edit.setPlaceholderText("auto")
        row_axis.addWidget(self._ss_ymin_edit)
        row_axis.addWidget(QLabel("Y max:"))
        self._ss_ymax_edit = QLineEdit(); self._ss_ymax_edit.setPlaceholderText("auto")
        row_axis.addWidget(self._ss_ymax_edit)
        layout.addLayout(row_axis)

        layout.addStretch()
        self._reconnect_grain_events(0)

    def _ss_y_choices(self):
        return [b for b in sorted(self.per_stage.keys()) if b not in EXCLUDE_Y_BASES]

    def _set_default_cb(self, cb: QComboBox, preferred: str):
        """コンボボックスの選択肢に preferred があれば選択する。"""
        idx = cb.findText(preferred)
        if idx >= 0:
            cb.setCurrentIndex(idx)

    def _build_phase_entries(self):
        for i in reversed(range(self._phase_group_layout.count())):
            w = self._phase_group_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._phase_entries.clear()

        pi_arr = None
        if self.stages and "phase_index" in self.per_stage:
            pi_arr = self.per_stage["phase_index"].get(
                self.stages[0],
                next(iter(self.per_stage["phase_index"].values()), None))

        if pi_arr is not None:
            for ph in np.unique(pi_arr[~np.isnan(pi_arr)].astype(int)):
                row_w = QWidget()
                row_l = QHBoxLayout(row_w)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.addWidget(QLabel(f"Phase {ph}:"))
                edit = QLineEdit(str(ph))
                self._phase_entries[int(ph)] = edit
                row_l.addWidget(edit)
                self._phase_group_layout.addWidget(row_w)
        else:
            self._phase_group_layout.addWidget(QLabel("phase_index が見つかりません"))

    def _ss_apply(self):
        x_base = self._ss_x_cb.currentText()
        y_base = self._ss_y_cb.currentText()
        if not x_base or not y_base:
            self._ss_status_lbl.setText("X軸・Y軸を選択してください")
            return
        try:
            self.sv, self.ss, self.ss_stages = build_ss(self.per_stage, x_base, y_base)
            self._ss_x_base = x_base
            self._ss_y_base = y_base
            self._ss_status_lbl.setText(
                f"OK: {len(self.ss_stages)} ステージ, {self.sv.shape[0]} サブセット")
            self._ss_status_lbl.setStyleSheet("color: green;")
            for cb in (self._ss_stage_from_cb, self._ss_stage_to_cb):
                cb.blockSignals(True)
                cb.clear()
                cb.addItems(self.ss_stages)
                cb.setEnabled(True)
                cb.blockSignals(False)
            self._ss_stage_from_cb.setCurrentIndex(0)
            self._ss_stage_to_cb.setCurrentIndex(len(self.ss_stages) - 1)
        except Exception as e:
            self._ss_status_lbl.setText(f"エラー: {e}")
            self._ss_status_lbl.setStyleSheet("color: red;")
            self.sv = self.ss = None
            self.ss_stages = []

    def _reconnect_grain_events(self, mode_id=None):
        if self._grain_cid is not None:
            self.canvas_grain.mpl_disconnect(self._grain_cid)
            self._grain_cid = None
        if mode_id is None:
            mode_id = self._ss_mode_bg.checkedId()
        event = "motion_notify_event" if mode_id == 1 else "button_press_event"
        self._grain_cid = self.canvas_grain.mpl_connect(event, self._on_grain_event)

    def _nearest_idx(self, x0, y0):
        d2 = (self.cx - x0) ** 2 + (self.cy - y0) ** 2
        return int(np.argmin(d2))

    def _on_grain_event(self, event):
        if event.inaxes != self.canvas_grain.ax:
            return
        if event.xdata is None or event.ydata is None:
            return
        self._update_ss_curve(self._nearest_idx(event.xdata, event.ydata))

    def _ss_axis_limits(self):
        """SS カーブのX/Y軸範囲を入力欄から読む。空欄はNone（auto）。"""
        def _parse(edit):
            try:
                return float(edit.text())
            except ValueError:
                return None
        xmin = _parse(self._ss_xmin_edit)
        xmax = _parse(self._ss_xmax_edit)
        ymin = _parse(self._ss_ymin_edit)
        ymax = _parse(self._ss_ymax_edit)
        xlim = (xmin, xmax) if xmin is not None or xmax is not None else None
        ylim = (ymin, ymax) if ymin is not None or ymax is not None else None
        return xlim, ylim

    def _ss_stage_slice(self):
        """選択されたステージ範囲のインデックスを返す（from_idx, to_idx+1）。"""
        from_idx = self._ss_stage_from_cb.currentIndex()
        to_idx = self._ss_stage_to_cb.currentIndex()
        if from_idx < 0:
            from_idx = 0
        if to_idx < 0:
            to_idx = len(self.ss_stages) - 1
        if from_idx > to_idx:
            from_idx, to_idx = to_idx, from_idx
        return from_idx, to_idx + 1

    def _update_ss_curve(self, idx):
        if self.sv is None or self.ss is None:
            return
        gid = self.grain_id[idx] if idx < len(self.grain_id) else 0
        sid = self.subset_id[idx] if idx < len(self.subset_id) else idx
        group_id = self._ss_group_bg.checkedId()

        s0, s1 = self._ss_stage_slice()
        sv = self.sv[:, s0:s1]
        ss = self.ss[:, s0:s1]
        xlim, ylim = self._ss_axis_limits()

        xu = self._var_unit(self._ss_x_base)
        yu = self._var_unit(self._ss_y_base)
        xlabel = f"{self._ss_x_base} [{xu}]" if xu else self._ss_x_base
        ylabel = f"{self._ss_y_base} [{yu}]" if yu else self._ss_y_base

        if group_id == 0:  # Subset
            if idx < sv.shape[0]:
                self.canvas_curve.plot_curve(
                    sv[idx, :], ss[idx, :],
                    f"Subset {sid}", f"SS Curve — Subset {sid} (Grain {gid})",
                    xlim=xlim, ylim=ylim, xlabel=xlabel, ylabel=ylabel)
        elif group_id == 1:  # Grain avg
            mask = self.grain_id == gid
            if mask.sum() > 0:
                self.canvas_curve.plot_curve(
                    np.nanmean(sv[mask, :], axis=0),
                    np.nanmean(ss[mask, :], axis=0),
                    f"Grain {gid} avg", f"SS Curve — Grain {gid} average",
                    xlim=xlim, ylim=ylim, xlabel=xlabel, ylabel=ylabel)
        elif group_id == 2:  # Phase avg
            first_stage = self.ss_stages[0] if self.ss_stages else None
            if first_stage and "phase_index" in self.per_stage:
                pi_arr = self.per_stage["phase_index"].get(
                    first_stage, next(iter(self.per_stage["phase_index"].values()), None))
                if pi_arr is not None and idx < len(pi_arr):
                    ph = int(pi_arr[idx])
                    mask = pi_arr.astype(int) == ph
                    ph_name = self._phase_entries.get(ph, QLineEdit(str(ph))).text()
                    self.canvas_curve.plot_curve(
                        np.nanmean(sv[mask, :], axis=0),
                        np.nanmean(ss[mask, :], axis=0),
                        f"Phase: {ph_name}", f"SS Curve — Phase {ph_name} average",
                        xlim=xlim, ylim=ylim, xlabel=xlabel, ylabel=ylabel)

    # ----------------------------------------------------------
    # タブ3: Derived Maps
    # ----------------------------------------------------------

    def _build_tab_derived(self, parent):
        layout = QVBoxLayout(parent)
        x_choices = [b for b in DIC_STRAIN_BASES if b in self.per_stage]
        y_choices = self._ss_y_choices()

        # 加工硬化率
        grp1 = QGroupBox("加工硬化率 (Hardening Rate)")
        g1 = QVBoxLayout(grp1)
        ra1 = QHBoxLayout()
        ra1.addWidget(QLabel("X軸 (ひずみ):"))
        self._hr_x_cb = QComboBox(); self._hr_x_cb.addItems(x_choices)
        ra1.addWidget(self._hr_x_cb, stretch=1)
        ra1.addWidget(QLabel("Y軸 (応力):"))
        self._hr_y_cb = QComboBox(); self._hr_y_cb.addItems(y_choices)
        self._set_default_cb(self._hr_y_cb, "rmap_s11")
        ra1.addWidget(self._hr_y_cb, stretch=1)
        g1.addLayout(ra1)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Stage n:"))
        self._hr_n_edit = QLineEdit("0"); self._hr_n_edit.setFixedWidth(50)
        r1.addWidget(self._hr_n_edit)
        r1.addWidget(QLabel("m:"))
        self._hr_m_edit = QLineEdit("3"); self._hr_m_edit.setFixedWidth(50)
        r1.addWidget(self._hr_m_edit)
        r1.addWidget(QLabel("カラーマップ:"))
        self._hr_cmap_cb = QComboBox(); self._hr_cmap_cb.addItems(CMAPS)
        r1.addWidget(self._hr_cmap_cb)
        g1.addLayout(r1)
        rmm1 = QHBoxLayout()
        rmm1.addWidget(QLabel("Min:"))
        self._hr_vmin_edit = QLineEdit(); self._hr_vmin_edit.setPlaceholderText("auto")
        rmm1.addWidget(self._hr_vmin_edit)
        rmm1.addWidget(QLabel("Max:"))
        self._hr_vmax_edit = QLineEdit(); self._hr_vmax_edit.setPlaceholderText("auto")
        rmm1.addWidget(self._hr_vmax_edit)
        g1.addLayout(rmm1)
        r2 = QHBoxLayout()
        b1 = QPushButton("Compute & Map"); b1.clicked.connect(self._compute_hardening_rate)
        b2 = QPushButton("Export PNG"); b2.clicked.connect(lambda: self._export_derived("hardening_rate"))
        r2.addWidget(b1); r2.addWidget(b2)
        g1.addLayout(r2)
        layout.addWidget(grp1)

        # 降伏応力
        grp2 = QGroupBox("降伏応力 (Yield Stress)")
        g2 = QVBoxLayout(grp2)
        ra2 = QHBoxLayout()
        ra2.addWidget(QLabel("X軸 (ひずみ):"))
        self._ys_x_cb = QComboBox(); self._ys_x_cb.addItems(x_choices)
        ra2.addWidget(self._ys_x_cb, stretch=1)
        ra2.addWidget(QLabel("Y軸 (応力):"))
        self._ys_y_cb = QComboBox(); self._ys_y_cb.addItems(y_choices)
        self._set_default_cb(self._ys_y_cb, "rmap_s11")
        ra2.addWidget(self._ys_y_cb, stretch=1)
        g2.addLayout(ra2)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("オフセット (%):"))
        self._ys_offset_edit = QLineEdit("0.2"); self._ys_offset_edit.setFixedWidth(60)
        r3.addWidget(self._ys_offset_edit)
        r3.addWidget(QLabel("カラーマップ:"))
        self._ys_cmap_cb = QComboBox(); self._ys_cmap_cb.addItems(CMAPS)
        r3.addWidget(self._ys_cmap_cb)
        g2.addLayout(r3)
        # 方位依存ヤング率（PatRep Excel + 荷重方向）
        re2 = QHBoxLayout()
        self._ys_excel_lbl = QLabel("PatRep Excel: (未選択)")
        self._ys_excel_lbl.setStyleSheet("color: gray; font-size: 10px;")
        re2.addWidget(self._ys_excel_lbl, stretch=1)
        btn_excel = QPushButton("参照…"); btn_excel.setFixedWidth(60)
        btn_excel.clicked.connect(self._select_ys_excel)
        re2.addWidget(btn_excel)
        g2.addLayout(re2)
        rd2 = QHBoxLayout()
        rd2.addWidget(QLabel("荷重方向 (試料系) nx ny nz:"))
        self._ys_nx_edit = QLineEdit("1"); self._ys_nx_edit.setFixedWidth(40)
        self._ys_ny_edit = QLineEdit("0"); self._ys_ny_edit.setFixedWidth(40)
        self._ys_nz_edit = QLineEdit("0"); self._ys_nz_edit.setFixedWidth(40)
        rd2.addWidget(self._ys_nx_edit); rd2.addWidget(self._ys_ny_edit); rd2.addWidget(self._ys_nz_edit)
        rd2.addStretch()
        g2.addLayout(rd2)
        self._ys_excel_path = None   # 選択済みパスを保持
        rmm2 = QHBoxLayout()
        rmm2.addWidget(QLabel("Min:"))
        self._ys_vmin_edit = QLineEdit(); self._ys_vmin_edit.setPlaceholderText("auto")
        rmm2.addWidget(self._ys_vmin_edit)
        rmm2.addWidget(QLabel("Max:"))
        self._ys_vmax_edit = QLineEdit(); self._ys_vmax_edit.setPlaceholderText("auto")
        rmm2.addWidget(self._ys_vmax_edit)
        g2.addLayout(rmm2)
        r4 = QHBoxLayout()
        b3 = QPushButton("Compute & Map"); b3.clicked.connect(self._compute_yield_stress)
        b4 = QPushButton("Export PNG"); b4.clicked.connect(lambda: self._export_derived("yield_stress"))
        r4.addWidget(b3); r4.addWidget(b4)
        g2.addLayout(r4)
        layout.addWidget(grp2)

        # ひずみエネルギー
        grp3 = QGroupBox("ひずみエネルギー (Strain Energy)")
        g3 = QVBoxLayout(grp3)
        ra3 = QHBoxLayout()
        ra3.addWidget(QLabel("X軸 (ひずみ):"))
        self._se_x_cb = QComboBox(); self._se_x_cb.addItems(x_choices)
        ra3.addWidget(self._se_x_cb, stretch=1)
        ra3.addWidget(QLabel("Y軸 (応力):"))
        self._se_y_cb = QComboBox(); self._se_y_cb.addItems(y_choices)
        self._set_default_cb(self._se_y_cb, "rmap_s11")
        ra3.addWidget(self._se_y_cb, stretch=1)
        g3.addLayout(ra3)
        r5 = QHBoxLayout()
        r5.addWidget(QLabel("カラーマップ:"))
        self._se_cmap_cb = QComboBox(); self._se_cmap_cb.addItems(CMAPS)
        r5.addWidget(self._se_cmap_cb)
        g3.addLayout(r5)
        rmm3 = QHBoxLayout()
        rmm3.addWidget(QLabel("Min:"))
        self._se_vmin_edit = QLineEdit(); self._se_vmin_edit.setPlaceholderText("auto")
        rmm3.addWidget(self._se_vmin_edit)
        rmm3.addWidget(QLabel("Max:"))
        self._se_vmax_edit = QLineEdit(); self._se_vmax_edit.setPlaceholderText("auto")
        rmm3.addWidget(self._se_vmax_edit)
        g3.addLayout(rmm3)
        r6 = QHBoxLayout()
        b5 = QPushButton("Compute & Map"); b5.clicked.connect(self._compute_strain_energy)
        b6 = QPushButton("Export PNG"); b6.clicked.connect(lambda: self._export_derived("strain_energy"))
        r6.addWidget(b5); r6.addWidget(b6)
        g3.addLayout(r6)
        layout.addWidget(grp3)

        self._derived_status_lbl = QLabel("")
        self._derived_status_lbl.setStyleSheet("color: goldenrod;")
        layout.addWidget(self._derived_status_lbl)
        layout.addStretch()

    def _build_derived_ss(self, x_cb, y_cb):
        """Derived Maps 用に指定変数から sv/ss を構築する。エラー時は None を返す。"""
        x_base = x_cb.currentText()
        y_base = y_cb.currentText()
        if not x_base or not y_base:
            self._derived_status_lbl.setText("エラー: X軸・Y軸を選択してください。")
            self._derived_status_lbl.setStyleSheet("color: red;")
            return None, None, None
        try:
            sv, ss, stages = build_ss(self.per_stage, x_base, y_base)
            return sv, ss, stages
        except Exception as e:
            self._derived_status_lbl.setText(f"エラー: {e}")
            self._derived_status_lbl.setStyleSheet("color: red;")
            return None, None, None

    def _parse_derived_minmax(self, result, vmin_edit, vmax_edit):
        try:
            vmin = float(vmin_edit.text())
        except ValueError:
            vmin = float(np.nanmin(result[~np.isnan(result)])) if np.any(~np.isnan(result)) else 0.0
        try:
            vmax = float(vmax_edit.text())
        except ValueError:
            vmax = float(np.nanmax(result[~np.isnan(result)])) if np.any(~np.isnan(result)) else 1.0
        return vmin, vmax

    def _draw_derived(self, result, cmap, title, vmin, vmax, cbar_label=None):
        self.canvas_map.draw_scatter(self.cx, self.cy, result, cmap, vmin, vmax, title, xlim=self._xlim, ylim=self._ylim, cbar_label=cbar_label)

    def _compute_hardening_rate(self):
        sv, ss, stages = self._build_derived_ss(self._hr_x_cb, self._hr_y_cb)
        if sv is None:
            return
        try:
            n, m = int(self._hr_n_edit.text()), int(self._hr_m_edit.text())
        except ValueError:
            self._derived_status_lbl.setText("エラー: n, m には整数を入力してください。")
            self._derived_status_lbl.setStyleSheet("color: red;")
            return
        if n > m:
            n, m = m, n
        if m >= sv.shape[1]:
            self._derived_status_lbl.setText(f"エラー: m はステージ数({sv.shape[1]}) 未満にしてください。")
            self._derived_status_lbl.setStyleSheet("color: red;")
            return
        result = compute_hardening_rate(sv, ss, n, m)
        self._derived_results["hardening_rate"] = result
        vmin, vmax = self._parse_derived_minmax(result, self._hr_vmin_edit, self._hr_vmax_edit)
        y_unit = self._var_unit(self._hr_y_cb.currentText())
        cbar_label = f"Hardening Rate [{y_unit}]" if y_unit else "Hardening Rate"
        self._draw_derived(result, self._hr_cmap_cb.currentText(), f"Hardening Rate (n={n}, m={m})", vmin, vmax, cbar_label=cbar_label)
        self._derived_status_lbl.setText(f"加工硬化率を計算しました (n={n}, m={m})")
        self._derived_status_lbl.setStyleSheet("color: goldenrod;")

    def _select_ys_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "PatRep Excel を選択", "", "Excel Files (*.xlsx *.xls)")
        if path:
            self._ys_excel_path = path
            self._ys_excel_lbl.setText(f"PatRep Excel: {Path(path).name}")
            self._ys_excel_lbl.setStyleSheet("color: #90ee90; font-size: 10px;")

    def _compute_yield_stress(self):
        sv, ss, stages = self._build_derived_ss(self._ys_x_cb, self._ys_y_cb)
        if sv is None:
            return
        try:
            offset_pct = float(self._ys_offset_edit.text())
        except ValueError:
            self._derived_status_lbl.setText("エラー: オフセットには数値を入力してください。")
            self._derived_status_lbl.setStyleSheet("color: red;")
            return

        # 方位依存ヤング率の計算（PatRep Excel が選択されている場合）
        E_per_subset = None
        e_mode_label = "E: 2-point est."
        if self._ys_excel_path and np.any(np.isfinite(self.phi1_deg)):
            try:
                nx = float(self._ys_nx_edit.text())
                ny = float(self._ys_ny_edit.text())
                nz = float(self._ys_nz_edit.text())
                stress_dir = np.array([nx, ny, nz], dtype=float)
                if np.linalg.norm(stress_dir) == 0:
                    raise ValueError("荷重方向ベクトルがゼロです")
                C_voigt = read_stiffness_from_patrep_excel(self._ys_excel_path)
                E_per_subset = compute_E_per_subset(
                    self.phi1_deg, self.PHI_deg, self.phi2_deg, C_voigt, stress_dir
                )
                e_mode_label = f"E: orientation-dep. n=[{nx:.2g},{ny:.2g},{nz:.2g}]"
            except Exception as ex:
                self._derived_status_lbl.setText(f"警告: 方位依存E計算失敗 ({ex})、2点推定にフォールバック")
                self._derived_status_lbl.setStyleSheet("color: orange;")

        result = compute_yield_stress(sv, ss, offset=offset_pct / 100.0, E_per_subset=E_per_subset)
        self._derived_results["yield_stress"] = result
        vmin, vmax = self._parse_derived_minmax(result, self._ys_vmin_edit, self._ys_vmax_edit)
        y_unit = self._var_unit(self._ys_y_cb.currentText())
        cbar_label = f"Yield Stress [{y_unit}]" if y_unit else "Yield Stress"
        title = f"Yield Stress (offset={offset_pct}%, {e_mode_label})"
        self._draw_derived(result, self._ys_cmap_cb.currentText(), title, vmin, vmax, cbar_label=cbar_label)
        self._derived_status_lbl.setText(f"降伏応力を計算しました (offset={offset_pct}%, {e_mode_label})")
        self._derived_status_lbl.setStyleSheet("color: goldenrod;")

    def _compute_strain_energy(self):
        sv, ss, stages = self._build_derived_ss(self._se_x_cb, self._se_y_cb)
        if sv is None:
            return
        result = compute_strain_energy(sv, ss)
        self._derived_results["strain_energy"] = result
        vmin, vmax = self._parse_derived_minmax(result, self._se_vmin_edit, self._se_vmax_edit)
        cbar_label = "Strain Energy [GJ/m\u00b3]"
        self._draw_derived(result, self._se_cmap_cb.currentText(), "Strain Energy", vmin, vmax, cbar_label=cbar_label)
        self._derived_status_lbl.setText("ひずみエネルギーを計算しました")
        self._derived_status_lbl.setStyleSheet("color: goldenrod;")

    def _export_derived(self, key):
        if key not in self._derived_results:
            QMessageBox.warning(self, "Export", "先に Compute & Map を実行してください。")
            return
        out = self.mat_path.parent / f"derived_{key}.png"
        self.canvas_map._fig.savefig(str(out), dpi=150, bbox_inches="tight")
        QMessageBox.information(self, "Export", f"保存しました:\n{out}")

    # ----------------------------------------------------------
    # Grain ID マップの描画
    # ----------------------------------------------------------

    def _draw_grain_map(self):
        ax = self.canvas_grain.ax
        fig = self.canvas_grain._fig
        ax.clear()
        if self.canvas_grain._colorbar is not None:
            try:
                self.canvas_grain._colorbar.remove()
            except Exception:
                pass
            self.canvas_grain._colorbar = None

        K = len(self._grain_unique)
        cmap = "turbo"
        norm = mcolors.Normalize(vmin=0, vmax=max(1, K - 1))

        sc = ax.scatter(self.cx, self.cy, s=6, alpha=0.9,
                        c=self._grain_code, cmap=cmap, norm=norm)
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("Grain ID")
        self.canvas_grain._colorbar = cbar
        if K <= 20:
            cbar.set_ticks(np.linspace(0, K - 1, K))
            cbar.set_ticklabels([str(g) for g in self._grain_unique])

        if len(self.cx) > 0:
            segs = compute_boundary_segments(self.cx, self.cy, self.grain_id)
            if segs:
                ax.add_collection(LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))

        ax.set_title("Grain ID Map")
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        ax.set_xlim(self._xlim)
        ax.set_ylim(self._ylim)
        ax.set_aspect("equal", adjustable="box")
        self.canvas_grain.draw()


# ============================================================
# エントリポイント
# ============================================================

def choose_mat_via_dialog():
    app = QApplication.instance() or QApplication(sys.argv)
    path, _ = QFileDialog.getOpenFileName(
        None, "integrated_georef.mat を選択", "",
        "MAT files (*.mat);;All files (*.*)")
    return Path(path) if path else None


def main():
    parser = argparse.ArgumentParser(description="Stress–Strain Mapper v2")
    parser.add_argument("--file", type=str, default="")
    args = parser.parse_args()

    mat_path = Path(args.file).expanduser() if args.file else None
    if mat_path is None or not mat_path.exists():
        mat_path = choose_mat_via_dialog()
        if mat_path is None:
            print("ファイルが選択されませんでした。終了します。")
            sys.exit(0)

    app = QApplication(sys.argv)
    window = StressStrainMapperApp(mat_path.resolve())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
