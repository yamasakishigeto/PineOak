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

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTabWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QSlider, QPushButton, QLineEdit,
    QRadioButton, QButtonGroup, QGroupBox, QFileDialog,
    QMessageBox, QSizePolicy, QCheckBox, QScrollArea,
)
from PyQt6.QtCore import Qt

# ============================================================
# 計算関数（stress_strain_calc.py より）
# ============================================================

from stress_strain_calc import (
    SYM_GROUPS, _get_sym_ops,
    parse_mat, build_ss,
    _parse_slip_system, _euler_to_matrices_rad, compute_schmid_factor, compute_rss,
    compute_hardening_rate, compute_strain_energy, compute_yield_stress,
    read_stiffness_from_patrep_excel,
    _euler_to_matrix_rad, compute_E_per_subset,
    compute_boundary_segments,
    _svd_mean_SO3, _misori_angles_batch, _align_orientations, compute_grod,
)
from gb_definitions import (
    ALL_DEFINITIONS, DEFINITION_MAP,
    build_phase_sym_map, pairs_to_segments, get_sym_ops,
)
from gb_settings_dialog import GBSettingsDialog

# ============================================================
# 定数
# ============================================================

CMAPS = [
    "viridis", "plasma", "inferno", "magma", "coolwarm",
    "RdBu_r", "seismic", "bwr", "jet", "turbo",
    "rainbow", "hot", "Blues", "Reds",
]

DIC_STRAIN_BASES = ["exx", "eyy", "exy", "e1", "gamma_max", "omega_xy"]
EXCLUDE_Y_BASES = {"u", "v", "zncc"} | set(DIC_STRAIN_BASES)


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

        # Min/Max 範囲外の点を NaN にして透明表示
        plot_data = np.array(data, dtype=float)
        if vmin is not None:
            plot_data[plot_data < vmin] = np.nan
        if vmax is not None:
            plot_data[plot_data > vmax] = np.nan
        cmap_obj = matplotlib.colormaps[cmap].copy()
        cmap_obj.set_bad(alpha=0)

        sc = self.ax.scatter(x, y, c=plot_data, cmap=cmap_obj, vmin=vmin, vmax=vmax, s=8, alpha=0.9)
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
                   xlim=None, ylim=None, xlabel="Strain", ylabel="Stress",
                   color=None):
        ax = self.ax
        ax.clear()
        kwargs = dict(marker="o", label=label)
        if color is not None:
            kwargs["color"] = color
        ax.plot(x, y, **kwargs)
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
        self.setWindowTitle(f"PineOak v1.0  —  Stress–Strain Mapper  —  {mat_path.name}")
        self.resize(1280, 960)

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

        # Mapタブの表示モード ("var" or "grod")
        self._map_mode = "var"

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

        # gb_definitions.compute_pairs が必要とするフラット形式のmatを構築
        self._mat_flat = {**self.static}
        for base, stg_dict in self.per_stage.items():
            for stg, arr in stg_dict.items():
                self._mat_flat[f"{base}_s{stg}"] = arr
        # _load_base_arrays で解決済みのオイラー角を正規化キーで注入
        # （matファイルの変数名が euler_phi1 等でも gb_definitions._load_euler が確実に見つけられるよう）
        self._mat_flat['phi1_ref'] = self.phi1_deg
        self._mat_flat['PHI_ref']  = self.PHI_deg
        self._mat_flat['phi2_ref'] = self.phi2_deg
        self._gb_seg_cache: dict = {}  # (def_key, params_hash, stage) -> pairs

    # ----------------------------------------------------------
    # UI 構築（2×2 グリッド）
    # ----------------------------------------------------------

    def _build_ui(self):
        # 水平スプリッター（左列 / 右列）
        main_split = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_split)

        # 左列: マップ（上） + SS Curve（下）
        self.canvas_map = MapCanvas()
        self.canvas_curve = CurveCanvas()
        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.addWidget(self._wrap_canvas(self.canvas_map, "Map"))
        left_split.addWidget(self._wrap_canvas(self.canvas_curve, "Stress-Strain Curve"))
        left_split.setSizes([440, 440])

        # 右列: コントロールパネル（上下貫通）
        main_split.addWidget(left_split)
        main_split.addWidget(self._build_control_panel())
        main_split.setSizes([720, 560])

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
        # Derived Maps と同様にスクロール可能な外枠
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        scroll.setWidget(container)
        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # ---- 共通コントロール（全モード共通） ----
        grp_shared = QGroupBox("共通コントロール")
        g_shared = QVBoxLayout(grp_shared)

        sh0 = QHBoxLayout()
        sh0.addWidget(QLabel("モード:"))
        self._map_mode_bg = QButtonGroup(parent)
        for _id, _lbl in [(0, "DIC/EBSD変数"), (1, "GROD"), (2, "シュミット因子"), (3, "RSS")]:
            rb = QRadioButton(_lbl)
            if _id == 0:
                rb.setChecked(True)
            self._map_mode_bg.addButton(rb, _id)
            sh0.addWidget(rb)
        sh0.addStretch()
        self._map_mode_bg.idClicked.connect(self._on_map_mode_selector_changed)
        g_shared.addLayout(sh0)

        sh_coord = QHBoxLayout()
        sh_coord.addWidget(QLabel("座標系:"))
        self._map_coord_bg = QButtonGroup(parent)
        rb_ref = QRadioButton("Reference (cx, cy)")
        self._rb_deformed = QRadioButton("Deformed (cx+u, cy+v)")
        rb_ref.setChecked(True)
        self._map_coord_bg.addButton(rb_ref, 0)
        self._map_coord_bg.addButton(self._rb_deformed, 1)
        sh_coord.addWidget(rb_ref)
        sh_coord.addWidget(self._rb_deformed)
        sh_coord.addStretch()
        g_shared.addLayout(sh_coord)

        sh1 = QHBoxLayout()
        sh1.addWidget(QLabel("ステージ:"))
        self._map_shared_stage_slider = QSlider(Qt.Orientation.Horizontal)
        self._map_shared_stage_slider.setMinimum(0)
        self._map_shared_stage_slider.setMaximum(0)
        self._map_shared_stage_slider.valueChanged.connect(self._on_shared_stage_changed)
        self._map_shared_stage_label = QLabel("")
        sh1.addWidget(self._map_shared_stage_slider, stretch=1)
        sh1.addWidget(self._map_shared_stage_label)
        g_shared.addLayout(sh1)

        sh2 = QHBoxLayout()
        self._map_shared_draw_btn = QPushButton("Draw Map")
        self._map_shared_draw_btn.clicked.connect(self._on_shared_draw_compute)
        btn_shared_export = QPushButton("Export PNG (全ステージ)")
        btn_shared_export.clicked.connect(self._on_shared_export_png)
        btn_save_mat = QPushButton(f"{self.mat_path.stem}_derived.mat に保存")
        btn_save_mat.clicked.connect(self._on_shared_save_to_mat)
        sh2.addWidget(self._map_shared_draw_btn)
        sh2.addWidget(btn_shared_export)
        sh2.addWidget(btn_save_mat)
        g_shared.addLayout(sh2)

        layout.addWidget(grp_shared)

        # 各モードのスライダー・ラベルはすべて共有ウィジェットのエイリアス
        self._map_stage_slider      = self._map_shared_stage_slider
        self._map_stage_label       = self._map_shared_stage_label
        self._map_grod_stage_slider = self._map_shared_stage_slider
        self._map_grod_stage_label  = self._map_shared_stage_label
        self._map_sf_stage_slider   = self._map_shared_stage_slider
        self._map_sf_stage_label    = self._map_shared_stage_label
        self._map_rss_stage_slider  = self._map_shared_stage_slider
        self._map_rss_stage_label   = self._map_shared_stage_label

        # ---- DIC/EBSD変数 GroupBox ----
        grp_var = QGroupBox("DIC/EBSD変数")
        g_var = QVBoxLayout(grp_var)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("変数:"))
        self._map_var_cb = QComboBox()
        self._map_var_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_var_cb.addItems(self._all_variable_names())
        self._map_var_cb.currentTextChanged.connect(self._on_map_var_changed)
        row1.addWidget(self._map_var_cb, stretch=1)
        row1.addWidget(QLabel("カラーマップ:"))
        self._map_cmap_cb = QComboBox()
        self._map_cmap_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_cmap_cb.addItems(CMAPS)
        row1.addWidget(self._map_cmap_cb)
        g_var.addLayout(row1)

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
        row5.addStretch()
        g_var.addLayout(row5)

        self._map_ps_trace_widget = QWidget()
        row_ps = QHBoxLayout(self._map_ps_trace_widget)
        row_ps.setContentsMargins(0, 0, 0, 0)
        self._map_ps_trace_cb = QCheckBox("最大主ひずみ方向を表示")
        self._map_ps_trace_cb.stateChanged.connect(self._draw_map_current)
        row_ps.addWidget(self._map_ps_trace_cb)
        row_ps.addWidget(QLabel("線長:"))
        self._map_ps_trace_len_edit = QLineEdit("10.0")
        self._map_ps_trace_len_edit.setFixedWidth(50)
        row_ps.addWidget(self._map_ps_trace_len_edit)
        row_ps.addWidget(QLabel("線幅:"))
        self._map_ps_trace_lw_edit = QLineEdit("0.5")
        self._map_ps_trace_lw_edit.setFixedWidth(40)
        row_ps.addWidget(self._map_ps_trace_lw_edit)
        row_ps.addStretch()
        self._map_ps_trace_widget.setVisible(False)
        g_var.addWidget(self._map_ps_trace_widget)

        layout.addWidget(grp_var)

        # ---- GROD GroupBox ----
        grp_grod = QGroupBox("GROD")
        g_grod = QVBoxLayout(grp_grod)

        grod_stages = self._grod_euler_stages()

        rg01 = QHBoxLayout()
        rg01.addWidget(QLabel("対称性:"))
        self._map_grod_sym_cb = QComboBox()
        self._map_grod_sym_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_grod_sym_cb.addItems(list(SYM_GROUPS.keys()))
        self._map_grod_sym_cb.setCurrentText("Cubic")
        rg01.addWidget(self._map_grod_sym_cb)
        rg01.addWidget(QLabel("基準ステージ:"))
        self._map_grod_ref_cb = QComboBox()
        self._map_grod_ref_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_grod_ref_cb.addItems(grod_stages)
        rg01.addWidget(self._map_grod_ref_cb)
        rg01.addStretch()
        g_grod.addLayout(rg01)

        rg24 = QHBoxLayout()
        rg24.addWidget(QLabel("表示変数:"))
        self._map_grod_var_cb = QComboBox()
        self._map_grod_var_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_grod_var_cb.addItems(["GROD Angle [deg]", "GROD Axis (未実装)"])
        rg24.addWidget(self._map_grod_var_cb)
        rg24.addWidget(QLabel("カラーマップ:"))
        self._map_grod_cmap_cb = QComboBox()
        self._map_grod_cmap_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_grod_cmap_cb.addItems(CMAPS)
        rg24.addWidget(self._map_grod_cmap_cb)
        rg24.addStretch()
        g_grod.addLayout(rg24)

        rg5 = QHBoxLayout()
        rg5.addWidget(QLabel("Min:"))
        self._map_grod_vmin_edit = QLineEdit()
        self._map_grod_vmin_edit.setPlaceholderText("auto")
        rg5.addWidget(self._map_grod_vmin_edit)
        rg5.addWidget(QLabel("Max:"))
        self._map_grod_vmax_edit = QLineEdit()
        self._map_grod_vmax_edit.setPlaceholderText("auto")
        rg5.addWidget(self._map_grod_vmax_edit)
        rg5.addStretch()
        g_grod.addLayout(rg5)

        layout.addWidget(grp_grod)

        # ---- シュミット因子 GroupBox ----
        grp_sf = QGroupBox("シュミット因子 (Schmid Factor)")
        g_sf = QVBoxLayout(grp_sf)

        sf_sym4 = QHBoxLayout()
        sf_sym4.addWidget(QLabel("対称性:"))
        self._map_sf_sym_cb = QComboBox()
        self._map_sf_sym_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_sf_sym_cb.addItems(list(SYM_GROUPS.keys()))
        self._map_sf_sym_cb.setCurrentText("Cubic")
        sf_sym4.addWidget(self._map_sf_sym_cb)
        sf_sym4.addWidget(QLabel("カラーマップ:"))
        self._map_sf_cmap_cb = QComboBox()
        self._map_sf_cmap_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_sf_cmap_cb.addItems(CMAPS)
        self._map_sf_cmap_cb.setCurrentText("jet")
        sf_sym4.addWidget(self._map_sf_cmap_cb)
        sf_sym4.addStretch()
        g_sf.addLayout(sf_sym4)

        sf01 = QHBoxLayout()
        sf01.addWidget(QLabel("すべり面:"))
        self._map_sf_plane_edit = QLineEdit("1 1 1")
        self._map_sf_plane_edit.setPlaceholderText("例: 1 1 1  または  0 0 0 1")
        sf01.addWidget(self._map_sf_plane_edit, stretch=1)
        sf01.addWidget(QLabel("すべり方向:"))
        self._map_sf_dir_edit = QLineEdit("1 -1 0")
        self._map_sf_dir_edit.setPlaceholderText("例: 1 -1 0  または  1 1 -2 0")
        sf01.addWidget(self._map_sf_dir_edit, stretch=1)
        g_sf.addLayout(sf01)

        sf2 = QHBoxLayout()
        sf2.addWidget(QLabel("c/a 比:"))
        self._map_sf_ca_edit = QLineEdit("1.633")
        self._map_sf_ca_edit.setEnabled(False)
        sf2.addWidget(self._map_sf_ca_edit)
        sf2.addStretch()
        g_sf.addLayout(sf2)

        def _update_ca_enable():
            txt = self._map_sf_plane_edit.text()
            nums = [x for x in txt.replace(',', ' ').split() if x]
            self._map_sf_ca_edit.setEnabled(len(nums) == 4)
        self._map_sf_plane_edit.textChanged.connect(lambda _: _update_ca_enable())

        sf3 = QHBoxLayout()
        sf3.addWidget(QLabel("負荷軸:"))
        self._map_sf_load_bg = QButtonGroup(parent)
        rb_lx = QRadioButton("X方向")
        rb_ly = QRadioButton("Y方向")
        rb_lv = QRadioButton("任意:")
        rb_lx.setChecked(True)
        for _i, _rb in enumerate([rb_lx, rb_ly, rb_lv]):
            self._map_sf_load_bg.addButton(_rb, _i)
            sf3.addWidget(_rb)
        self._map_sf_load_vec_edit = QLineEdit("0 0 1")
        self._map_sf_load_vec_edit.setPlaceholderText("x y z")
        self._map_sf_load_vec_edit.setEnabled(False)
        self._map_sf_load_bg.idClicked.connect(
            lambda i: self._map_sf_load_vec_edit.setEnabled(i == 2))
        sf3.addWidget(self._map_sf_load_vec_edit)
        sf3.addStretch()
        g_sf.addLayout(sf3)

        sf5 = QHBoxLayout()
        sf5.addWidget(QLabel("Min:"))
        self._map_sf_vmin_edit = QLineEdit("0")
        sf5.addWidget(self._map_sf_vmin_edit)
        sf5.addWidget(QLabel("Max:"))
        self._map_sf_vmax_edit = QLineEdit("0.5")
        sf5.addWidget(self._map_sf_vmax_edit)
        sf5.addStretch()
        g_sf.addLayout(sf5)

        sf_trace = QHBoxLayout()
        self._map_sf_trace_cb = QCheckBox("すべり面トレースを表示")
        sf_trace.addWidget(self._map_sf_trace_cb)
        sf_trace.addWidget(QLabel("線長:"))
        self._map_sf_trace_len_edit = QLineEdit("10.0")
        self._map_sf_trace_len_edit.setFixedWidth(50)
        sf_trace.addWidget(self._map_sf_trace_len_edit)
        sf_trace.addWidget(QLabel("線幅:"))
        self._map_sf_trace_lw_edit = QLineEdit("0.5")
        self._map_sf_trace_lw_edit.setFixedWidth(40)
        sf_trace.addWidget(self._map_sf_trace_lw_edit)
        sf_trace.addStretch()
        g_sf.addLayout(sf_trace)

        layout.addWidget(grp_sf)

        # ---- RSS GroupBox ----
        grp_rss = QGroupBox("分解せん断応力 (Resolved Shear Stress)")
        g_rss = QVBoxLayout(grp_rss)

        rss_sym = QHBoxLayout()
        rss_sym.addWidget(QLabel("対称性:"))
        self._map_rss_sym_cb = QComboBox()
        self._map_rss_sym_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_rss_sym_cb.addItems(list(SYM_GROUPS.keys()))
        self._map_rss_sym_cb.setCurrentText("Cubic")
        rss_sym.addWidget(self._map_rss_sym_cb)
        rss_sym.addWidget(QLabel("カラーマップ:"))
        self._map_rss_cmap_cb = QComboBox()
        self._map_rss_cmap_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._map_rss_cmap_cb.addItems(CMAPS)
        self._map_rss_cmap_cb.setCurrentText("coolwarm")
        rss_sym.addWidget(self._map_rss_cmap_cb)
        rss_sym.addStretch()
        g_rss.addLayout(rss_sym)

        rss_slip = QHBoxLayout()
        rss_slip.addWidget(QLabel("すべり面:"))
        self._map_rss_plane_edit = QLineEdit("1 1 1")
        self._map_rss_plane_edit.setPlaceholderText("例: 1 1 1  または  0 0 0 1")
        rss_slip.addWidget(self._map_rss_plane_edit, stretch=1)
        rss_slip.addWidget(QLabel("すべり方向:"))
        self._map_rss_dir_edit = QLineEdit("1 -1 0")
        self._map_rss_dir_edit.setPlaceholderText("例: 1 -1 0  または  1 1 -2 0")
        rss_slip.addWidget(self._map_rss_dir_edit, stretch=1)
        g_rss.addLayout(rss_slip)

        rss_ca = QHBoxLayout()
        rss_ca.addWidget(QLabel("c/a 比:"))
        self._map_rss_ca_edit = QLineEdit("1.633")
        self._map_rss_ca_edit.setEnabled(False)
        rss_ca.addWidget(self._map_rss_ca_edit)
        rss_ca.addStretch()
        g_rss.addLayout(rss_ca)

        def _update_rss_ca_enable():
            txt = self._map_rss_plane_edit.text()
            nums = [x for x in txt.replace(',', ' ').split() if x]
            self._map_rss_ca_edit.setEnabled(len(nums) == 4)
        self._map_rss_plane_edit.textChanged.connect(lambda _: _update_rss_ca_enable())

        rss_mm = QHBoxLayout()
        rss_mm.addWidget(QLabel("Min:"))
        self._map_rss_vmin_edit = QLineEdit()
        self._map_rss_vmin_edit.setPlaceholderText("auto")
        rss_mm.addWidget(self._map_rss_vmin_edit)
        rss_mm.addWidget(QLabel("Max:"))
        self._map_rss_vmax_edit = QLineEdit()
        self._map_rss_vmax_edit.setPlaceholderText("auto")
        rss_mm.addWidget(self._map_rss_vmax_edit)
        rss_mm.addStretch()
        g_rss.addLayout(rss_mm)

        rss_trace = QHBoxLayout()
        self._map_rss_trace_cb = QCheckBox("すべり面トレースを表示")
        rss_trace.addWidget(self._map_rss_trace_cb)
        rss_trace.addWidget(QLabel("線長:"))
        self._map_rss_trace_len_edit = QLineEdit("10.0")
        self._map_rss_trace_len_edit.setFixedWidth(50)
        rss_trace.addWidget(self._map_rss_trace_len_edit)
        rss_trace.addWidget(QLabel("線幅:"))
        self._map_rss_trace_lw_edit = QLineEdit("0.5")
        self._map_rss_trace_lw_edit.setFixedWidth(40)
        rss_trace.addWidget(self._map_rss_trace_lw_edit)
        rss_trace.addStretch()
        g_rss.addLayout(rss_trace)

        layout.addWidget(grp_rss)

        # ---- 共通：粒界描画・定義選択 ----
        row_gb = QHBoxLayout()
        self._map_show_gb_cb = QCheckBox("Grain boundaries")
        self._map_show_gb_cb.setChecked(True)
        self._map_show_gb_cb.toggled.connect(lambda _: self._redraw_current_map())
        row_gb.addWidget(self._map_show_gb_cb)
        self._map_gb_def_cb = QComboBox()
        for _defn in ALL_DEFINITIONS:
            self._map_gb_def_cb.addItem(_defn.label, _defn.key)
        self._map_gb_def_cb.setEnabled(True)
        self._map_gb_def_cb.setFixedWidth(160)
        row_gb.addWidget(self._map_gb_def_cb)
        self._map_gb_settings_btn = QPushButton("設定…")
        self._map_gb_settings_btn.setFixedWidth(52)
        self._map_gb_params_store: dict = {}  # def_key -> params dict
        # has_settings が False の定義ではボタン無効
        def _upd_map_gb_btn(idx):
            key = self._map_gb_def_cb.currentData()
            defn = DEFINITION_MAP.get(key)
            self._map_gb_settings_btn.setEnabled(bool(defn and defn.has_settings))
            self._gb_seg_cache.clear()
            self._redraw_current_map()
        self._map_gb_def_cb.currentIndexChanged.connect(_upd_map_gb_btn)
        _upd_map_gb_btn(0)
        def _open_map_gb_settings():
            key = self._map_gb_def_cb.currentData()
            defn = DEFINITION_MAP.get(key)
            if defn is None or not defn.has_settings:
                return
            dlg = GBSettingsDialog(key, self._map_gb_params_store.get(key, {}), parent=self)
            if dlg.exec():
                self._map_gb_params_store[key] = dlg.get_params()
                self._gb_seg_cache.clear()
                self._redraw_current_map()
        self._map_gb_settings_btn.clicked.connect(_open_map_gb_settings)
        row_gb.addWidget(self._map_gb_settings_btn)
        # 対称性（ミスオリエンテーション計算用）
        row_gb.addWidget(QLabel("  対称性:"))
        self._map_gb_sym_cb = QComboBox()
        self._map_gb_sym_cb.addItems(list(SYM_GROUPS.keys()))
        self._map_gb_sym_cb.setCurrentText("Cubic")
        self._map_gb_sym_cb.setFixedWidth(80)
        self._map_gb_sym_cb.currentIndexChanged.connect(
            lambda _: (self._gb_seg_cache.clear(), self._redraw_current_map()))
        row_gb.addWidget(self._map_gb_sym_cb)
        row_gb.addStretch()
        layout.addLayout(row_gb)

        row_cmap_btn = QHBoxLayout()
        btn_cmap_preview = QPushButton("カラーマップ色見本")
        btn_cmap_preview.clicked.connect(self._show_cmap_preview)
        row_cmap_btn.addWidget(btn_cmap_preview)
        row_cmap_btn.addStretch()
        layout.addLayout(row_cmap_btn)

        self._map_grod_status_lbl = QLabel("")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")
        layout.addWidget(self._map_grod_status_lbl)

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
        # 主ひずみ方向チェックボックスの表示切替
        if base == "e1":
            self._map_ps_trace_cb.setText("最大主ひずみ方向を表示")
            self._map_ps_trace_widget.setVisible(True)
        elif base == "gamma_max":
            self._map_ps_trace_cb.setText("最大主せん断ひずみ方向を表示")
            self._map_ps_trace_widget.setVisible(True)
        else:
            self._map_ps_trace_cb.setChecked(False)
            self._map_ps_trace_widget.setVisible(False)
        # 座標系の有効/無効は常に更新
        has_uv = "u" in self.per_stage and "v" in self.per_stage
        self._rb_deformed.setEnabled(has_uv)
        if not has_uv:
            self._map_coord_bg.button(0).setChecked(True)
        # スライダーは "var" モードのときだけ更新
        if self._map_mode != "var":
            return
        sts = self._stages_for_base(base)
        self._map_shared_stage_slider.blockSignals(True)
        if sts:
            self._map_shared_stage_slider.setMaximum(len(sts) - 1)
            self._map_shared_stage_slider.setEnabled(True)
            self._map_shared_stage_label.setText(
                sts[min(self._map_shared_stage_slider.value(), len(sts) - 1)])
        else:
            self._map_shared_stage_slider.setMaximum(0)
            self._map_shared_stage_slider.setEnabled(False)
            self._map_shared_stage_label.setText("(静的変数)")
        self._map_shared_stage_slider.blockSignals(False)

    def _on_map_mode_selector_changed(self, mode_id):
        """モード選択ラジオボタンが切り替わったとき。スライダー範囲とボタンラベルを更新。"""
        mode_map = {0: "var", 1: "grod", 2: "sf", 3: "rss"}
        self._map_mode = mode_map.get(mode_id, "var")
        labels = {0: "Draw Map", 1: "Compute GROD", 2: "Compute シュミット因子", 3: "Compute RSS"}
        self._map_shared_draw_btn.setText(labels.get(mode_id, "Draw Map"))
        # スライダー範囲を新モードに合わせて更新
        self._map_shared_stage_slider.blockSignals(True)
        if self._map_mode == "var":
            base = self._map_var_cb.currentText()
            sts = self._stages_for_base(base)
            if sts:
                self._map_shared_stage_slider.setMaximum(len(sts) - 1)
                self._map_shared_stage_slider.setEnabled(True)
                self._map_shared_stage_label.setText(
                    sts[min(self._map_shared_stage_slider.value(), len(sts) - 1)])
            else:
                self._map_shared_stage_slider.setMaximum(0)
                self._map_shared_stage_slider.setEnabled(False)
                self._map_shared_stage_label.setText("(静的変数)")
        elif self._map_mode == "grod":
            stages = self._grod_euler_stages()
            self._map_shared_stage_slider.setMaximum(max(0, len(stages) - 1))
            self._map_shared_stage_slider.setEnabled(bool(stages))
            if stages:
                self._map_shared_stage_label.setText(
                    stages[min(self._map_shared_stage_slider.value(), len(stages) - 1)])
        elif self._map_mode == "sf":
            stages = self._grod_euler_stages()
            self._map_shared_stage_slider.setMaximum(max(0, len(stages) - 1))
            self._map_shared_stage_slider.setEnabled(bool(stages))
            if stages:
                self._map_shared_stage_label.setText(
                    stages[min(self._map_shared_stage_slider.value(), len(stages) - 1)])
        elif self._map_mode == "rss":
            stages = self._rss_stages()
            self._map_shared_stage_slider.setMaximum(max(0, len(stages) - 1))
            self._map_shared_stage_slider.setEnabled(bool(stages))
            if stages:
                self._map_shared_stage_label.setText(
                    stages[min(self._map_shared_stage_slider.value(), len(stages) - 1)])
        self._map_shared_stage_slider.blockSignals(False)

    def _on_shared_draw_compute(self):
        """Draw/Compute ボタン。モードに応じて描画または計算+描画を実行。"""
        if self._map_mode == "var":
            self._draw_map_current()
        elif self._map_mode == "grod":
            self._on_compute_grod()
        elif self._map_mode == "sf":
            self._on_compute_sf()
        elif self._map_mode == "rss":
            self._on_compute_rss()

    def _on_shared_export_png(self):
        """Export PNG ボタン。モードに応じて全ステージを PNG 保存。"""
        if self._map_mode == "var":
            self._export_map_all_stages()
        elif self._map_mode == "grod":
            self._on_export_grod_all_stages()
        elif self._map_mode == "sf":
            self._on_export_sf_all_stages()
        elif self._map_mode == "rss":
            self._on_export_rss_all_stages()

    def _on_shared_stage_changed(self, val):
        """共有スライダーの valueChanged ハンドラ。モードに応じてラベル更新・再描画。"""
        if self._map_mode == "var":
            base = self._map_var_cb.currentText()
            sts = self._stages_for_base(base)
            if sts and 0 <= val < len(sts):
                self._map_shared_stage_label.setText(sts[val])
        elif self._map_mode == "grod":
            stages = self._grod_euler_stages()
            if 0 <= val < len(stages):
                self._map_shared_stage_label.setText(stages[val])
            if "GROD_angle" in self.per_stage:
                self._draw_grod_stage()
        elif self._map_mode == "sf":
            stages = self._grod_euler_stages()
            if 0 <= val < len(stages):
                self._map_shared_stage_label.setText(stages[val])
            if "SF_schmid" in self.per_stage:
                self._draw_sf_stage()
        elif self._map_mode == "rss":
            stages = self._rss_stages()
            if 0 <= val < len(stages):
                self._map_shared_stage_label.setText(stages[val])
            if "RSS_max" in self.per_stage:
                self._draw_rss_stage()

    def _on_shared_save_to_mat(self):
        """共有保存ボタン。現在のモードに応じて _derived.mat に保存する。"""
        if self._map_mode == "grod":
            self._on_add_grod_to_mat()
        elif self._map_mode == "sf":
            self._on_add_sf_to_mat()
        elif self._map_mode == "rss":
            self._on_add_rss_to_mat()
        else:
            QMessageBox.information(self, "保存",
                                    "保存対象がありません。\nGROD・シュミット因子・RSS のいずれかを計算してください。")

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

    def _get_xy(self, stage):
        """座標系ラジオボタンに従い (x, y) を返す。"""
        if self._map_coord_bg.checkedId() == 1 and stage and \
                "u" in self.per_stage and "v" in self.per_stage:
            x = self.cx + self.per_stage["u"].get(stage, np.zeros_like(self.cx))
            y = self.cy + self.per_stage["v"].get(stage, np.zeros_like(self.cy))
        else:
            x, y = self.cx, self.cy
        return x, y

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

    def _redraw_current_map(self):
        """現在のマップモードに応じて描画メソッドを呼ぶ。
        GB定義・設定・表示ON/OFFの変更後に呼ばれる。
        データ未ロード時・UI未初期化・描画失敗は無視する。
        """
        if not len(self.cx):
            return
        # UI構築中（canvas_map や _map_var_cb がまだない）は何もしない
        if not hasattr(self, 'canvas_map') or not hasattr(self, '_map_var_cb'):
            return
        try:
            mode = getattr(self, '_map_mode', 'var')
            if mode == 'var':
                self._draw_map_current()
            elif mode == 'grod':
                self._draw_grod_stage()
            elif mode == 'sf':
                self._draw_sf_stage()
            elif mode == 'rss':
                self._draw_rss_stage()
        except Exception as e:
            import traceback
            print(f"[redraw] エラー (mode={mode}): {e}")
            traceback.print_exc()

    def _draw_map_current(self):
        base = self._map_var_cb.currentText()
        idx = self._map_shared_stage_slider.value()
        data, x, y, stage = self._get_map_data(base, idx)
        vmin, vmax = self._parse_minmax(data)
        cmap = self._map_cmap_cb.currentText()
        title = f"{base}  [{stage}]" if stage else base
        unit = self._var_unit(base)
        cbar_label = f"{base} [{unit}]" if unit else base
        self.canvas_map.draw_scatter(x, y, data, cmap, vmin, vmax, title, xlim=self._xlim, ylim=self._ylim, cbar_label=cbar_label)
        if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
            segs = self._get_gb_segs(stage, x_draw=x, y_draw=y)
            if segs:
                self.canvas_map.ax.add_collection(
                    LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k")
                )
                self.canvas_map.draw()
        if self._map_ps_trace_cb.isChecked() and stage:
            exx_d = self.per_stage.get("exx", {}).get(stage)
            eyy_d = self.per_stage.get("eyy", {}).get(stage)
            exy_d = self.per_stage.get("exy", {}).get(stage)
            if exx_d is not None and eyy_d is not None and exy_d is not None:
                try:
                    L  = float(self._map_ps_trace_len_edit.text())
                    lw = float(self._map_ps_trace_lw_edit.text())
                except ValueError:
                    L, lw = 10.0, 0.5
                theta = 0.5 * np.arctan2(2.0 * exy_d, exx_d - eyy_d)
                if base == "gamma_max":
                    theta = theta + np.pi / 4
                tx = np.cos(theta)
                ty = np.sin(theta)
                mask = np.isfinite(theta) & np.isfinite(x) & np.isfinite(y)
                xi = x[mask]; yi = y[mask]
                txi = tx[mask]; tyi = ty[mask]
                pts_s = np.column_stack([xi - L * txi, yi - L * tyi])
                pts_e = np.column_stack([xi + L * txi, yi + L * tyi])
                t_segs = np.stack([pts_s, pts_e], axis=1)
                self.canvas_map.ax.add_collection(
                    LineCollection(t_segs, linewidths=lw, colors="black", alpha=0.7, zorder=3))
                self.canvas_map.draw()

    def _on_map_scroll(self, event):
        """マウスホイールでステージスライダーを1段階ずつ切り替える。"""
        slider = self._map_shared_stage_slider
        if not slider.isEnabled():
            return
        current = slider.value()
        if event.button == "up":
            new_val = max(0, current - 1)
        elif event.button == "down":
            new_val = min(slider.maximum(), current + 1)
        else:
            return
        if new_val != current:
            slider.setValue(new_val)
            # grod/ps: _on_shared_stage_changed が再描画
            # var: 明示的に再描画
            if self._map_mode == "var":
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
        self.canvas_map._fig.savefig(str(out), dpi=300, bbox_inches="tight")
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
                dpi=300, bbox_inches="tight")

        QMessageBox.information(self, "Export All",
                                f"{len(sts)} 枚を保存しました:\n{self.mat_path.parent}")

    # ----------------------------------------------------------
    # タブ2: SS Curve
    # ----------------------------------------------------------

    def _build_tab_ss(self, parent):
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        scroll.setWidget(container)
        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

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

    def _grain_color(self, gid):
        """Grain IDマップと同じturboカラーマップでグレインの色を返す。"""
        K = len(self._grain_unique)
        code = int(np.searchsorted(self._grain_unique, gid))
        return matplotlib.cm.turbo(code / max(1, K - 1))

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
            self.canvas_map.mpl_disconnect(self._grain_cid)
            self._grain_cid = None
        if mode_id is None:
            mode_id = self._ss_mode_bg.checkedId()
        event = "motion_notify_event" if mode_id == 1 else "button_press_event"
        self._grain_cid = self.canvas_map.mpl_connect(event, self._on_grain_event)

    def _nearest_idx(self, x0, y0):
        d2 = (self.cx - x0) ** 2 + (self.cy - y0) ** 2
        return int(np.argmin(d2))

    def _on_grain_event(self, event):
        if event.inaxes != self.canvas_map.ax:
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
                    f"Subset {sid}", f"Stress-Strain Curve — Subset {sid} (Grain {gid})",
                    xlim=xlim, ylim=ylim, xlabel=xlabel, ylabel=ylabel,
                    color=self._grain_color(gid))
        elif group_id == 1:  # Grain avg
            mask = self.grain_id == gid
            if mask.sum() > 0:
                self.canvas_curve.plot_curve(
                    np.nanmean(sv[mask, :], axis=0),
                    np.nanmean(ss[mask, :], axis=0),
                    f"Grain {gid} avg", f"Stress-Strain Curve — Grain {gid} average",
                    xlim=xlim, ylim=ylim, xlabel=xlabel, ylabel=ylabel,
                    color=self._grain_color(gid))
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
                        f"Phase: {ph_name}", f"Stress-Strain Curve — Phase {ph_name} average",
                        xlim=xlim, ylim=ylim, xlabel=xlabel, ylabel=ylabel)

    # ----------------------------------------------------------
    # タブ3: Derived Maps
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # シュミット因子マップ（Mapタブから使用）
    # ----------------------------------------------------------

    def _sf_load_vec(self, stage):
        """GUIの負荷軸設定から試料座標系の単位ベクトルを返す。shape (N, 3) または (3,)。"""
        load_id = self._map_sf_load_bg.checkedId()
        if load_id == 0:    # X
            return np.array([1.0, 0.0, 0.0])
        elif load_id == 1:  # Y
            return np.array([0.0, 1.0, 0.0])
        else:  # 任意 (id==2)
            nums = [float(x) for x in self._map_sf_load_vec_edit.text().split()]
            if len(nums) < 3:
                raise ValueError("負荷軸ベクトルは 3 成分必要です (例: 1 0 0)")
            v = np.array(nums[:3], dtype=float)
            norm = np.linalg.norm(v)
            if norm < 1e-12:
                raise ValueError("負荷軸ベクトルのノルムがゼロです")
            return v / norm

    def _on_compute_sf(self):
        stages = self._grod_euler_stages()
        if not stages:
            self._map_grod_status_lbl.setText("エラー: Euler角ステージデータが見つかりません")
            self._map_grod_status_lbl.setStyleSheet("color: red;")
            return

        try:
            ca = float(self._map_sf_ca_edit.text())
            n_slip, b_slip = _parse_slip_system(
                self._map_sf_plane_edit.text(),
                self._map_sf_dir_edit.text(),
                ca)
        except Exception as e:
            QMessageBox.critical(self, "入力エラー", str(e))
            return

        sym_ops = _get_sym_ops(self._map_sf_sym_cb.currentText())

        self.per_stage["SF_schmid"] = {}
        self.per_stage["SF_trace"] = {}
        n_total = len(stages)
        for i, stage in enumerate(stages):
            self._map_grod_status_lbl.setText(f"計算中... {i + 1}/{n_total}  [{stage}]")
            self._map_grod_status_lbl.setStyleSheet("color: gray;")
            QApplication.processEvents()

            phi1, PHI, phi2 = self._get_grod_euler(stage)
            if phi1 is None:
                continue
            try:
                load_vecs = self._sf_load_vec(stage)
            except ValueError as e:
                QMessageBox.critical(self, "負荷軸エラー", str(e))
                return

            schmid, trace = compute_schmid_factor(
                phi1.astype(float), PHI.astype(float), phi2.astype(float),
                n_slip, b_slip, load_vecs, sym_ops, return_trace=True)
            self.per_stage["SF_schmid"][stage] = schmid
            self.per_stage["SF_trace"][stage] = trace

        self._draw_sf_stage()
        self._map_grod_status_lbl.setText(f"完了: {n_total} ステージ計算済み")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")

    def _draw_sf_stage(self):
        stages = self._grod_euler_stages()
        val = self._map_sf_stage_slider.value()
        if not stages or val >= len(stages):
            return
        tgt_stage = stages[val]
        data = self.per_stage.get("SF_schmid", {}).get(tgt_stage)
        if data is None:
            return
        try:
            vmin = float(self._map_sf_vmin_edit.text())
        except ValueError:
            vmin = 0.0
        try:
            vmax = float(self._map_sf_vmax_edit.text())
        except ValueError:
            vmax = 0.5
        x, y = self._get_xy(tgt_stage)
        label = "Schmid Factor"
        title = f"{label}  [{tgt_stage}]"
        self.canvas_map.draw_scatter(
            x, y, data,
            self._map_sf_cmap_cb.currentText(), vmin, vmax, title,
            xlim=self._xlim, ylim=self._ylim, cbar_label=label)
        if self._map_sf_trace_cb.isChecked():
            trace = self.per_stage.get("SF_trace", {}).get(tgt_stage)
            if trace is not None:
                try:
                    L  = float(self._map_sf_trace_len_edit.text())
                    lw = float(self._map_sf_trace_lw_edit.text())
                except ValueError:
                    L, lw = 10.0, 0.5
                mask = np.isfinite(trace[:, 0]) & np.isfinite(x) & np.isfinite(y)
                xi = x[mask]; yi = y[mask]
                tx = trace[mask, 0]; ty = trace[mask, 1]
                pts_s = np.column_stack([xi - L * tx, yi - L * ty])
                pts_e = np.column_stack([xi + L * tx, yi + L * ty])
                t_segs = np.stack([pts_s, pts_e], axis=1)
                self.canvas_map.ax.add_collection(
                    LineCollection(t_segs, linewidths=lw, colors="black", alpha=0.7, zorder=3))
                self.canvas_map.draw()
        if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
            segs = self._get_gb_segs(tgt_stage, x_draw=x, y_draw=y)
            if segs:
                self.canvas_map.ax.add_collection(
                    LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))
                self.canvas_map.draw()

    def _on_export_sf_all_stages(self):
        if "SF_schmid" not in self.per_stage:
            self._on_compute_sf()
        if "SF_schmid" not in self.per_stage:
            return
        cmap = self._map_sf_cmap_cb.currentText()
        try:
            vmin = float(self._map_sf_vmin_edit.text())
            vmax = float(self._map_sf_vmax_edit.text())
        except ValueError:
            vmin, vmax = 0.0, 0.5
        label = "Schmid Factor"
        self._map_grod_status_lbl.setText("全ステージ出力中...")
        self._map_grod_status_lbl.setStyleSheet("color: gray;")
        show_trace = self._map_sf_trace_cb.isChecked()
        try:
            trace_L  = float(self._map_sf_trace_len_edit.text())
            trace_lw = float(self._map_sf_trace_lw_edit.text())
        except ValueError:
            trace_L, trace_lw = 10.0, 0.5
        saved = 0
        for stage, data in self.per_stage.get("SF_schmid", {}).items():
            x, y = self._get_xy(stage)
            title = f"{label}  [{stage}]"
            self.canvas_map.draw_scatter(
                x, y, data, cmap, vmin, vmax, title,
                xlim=self._xlim, ylim=self._ylim, cbar_label=label)
            if show_trace:
                trace = self.per_stage.get("SF_trace", {}).get(stage)
                if trace is not None:
                    mask = np.isfinite(trace[:, 0]) & np.isfinite(x) & np.isfinite(y)
                    xi = x[mask]; yi = y[mask]
                    tx = trace[mask, 0]; ty = trace[mask, 1]
                    pts_s = np.column_stack([xi - trace_L * tx, yi - trace_L * ty])
                    pts_e = np.column_stack([xi + trace_L * tx, yi + trace_L * ty])
                    t_segs = np.stack([pts_s, pts_e], axis=1)
                    self.canvas_map.ax.add_collection(
                        LineCollection(t_segs, linewidths=trace_lw, colors="black", alpha=0.7, zorder=3))
                    self.canvas_map.draw()
            if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
                segs = self._get_gb_segs(stage, x_draw=x, y_draw=y)
                if segs:
                    self.canvas_map.ax.add_collection(
                        LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))
                    self.canvas_map.draw()
            out = self.mat_path.parent / f"SF_schmid_{stage}.png"
            self.canvas_map._fig.savefig(str(out), dpi=300, bbox_inches="tight")
            saved += 1
        self._map_grod_status_lbl.setText(f"完了: {saved} 枚を保存")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")
        QMessageBox.information(self, "Export All",
                                f"{saved} 枚を保存しました:\n{self.mat_path.parent}")

    def _on_add_sf_to_mat(self):
        if "SF_schmid" not in self.per_stage or not self.per_stage["SF_schmid"]:
            QMessageBox.warning(self, "追加", "先に「Compute シュミット因子」を実行してください。")
            return
        self._map_grod_status_lbl.setText("保存中...")
        self._map_grod_status_lbl.setStyleSheet("color: gray;")
        from scipy.io import savemat
        data = self._load_derived_mat()
        new_vars = {}
        for stage, arr in self.per_stage["SF_schmid"].items():
            new_vars[f"SF_schmid_s{stage}"] = arr.reshape(1, -1)
        for stage, trace in self.per_stage.get("SF_trace", {}).items():
            angle = np.degrees(np.arctan2(trace[:, 1], trace[:, 0])) % 180
            new_vars[f"SF_trace_angle_s{stage}"] = angle.reshape(1, -1)
        data.update(new_vars)
        out = self._derived_mat_path()
        savemat(str(out), data)
        self._map_grod_status_lbl.setText(
            f"追加完了: {len(new_vars)} 変数を {out.name} に保存しました")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")

    # ----------------------------------------------------------
    # RSS マップ（Mapタブから使用）
    # ----------------------------------------------------------

    def _rss_stages(self):
        """euler + rmap_s* の共通ステージを返す。"""
        def key(s):
            m = re.match(r"(\d+)MPa", s)
            return int(m.group(1)) if m else 0
        euler_stages = set(self._grod_euler_stages())
        stress_keys = ("rmap_s11", "rmap_s22", "rmap_s33", "rmap_s12", "rmap_s23", "rmap_s31")
        stress_sets = [set(self.per_stage.get(k, {}).keys()) for k in stress_keys]
        stress_common = stress_sets[0]
        for s in stress_sets[1:]:
            stress_common &= s
        return sorted(euler_stages & stress_common, key=key)

    def _on_compute_rss(self):
        stages = self._rss_stages()
        if not stages:
            self._map_grod_status_lbl.setText(
                "エラー: Euler角と応力テンソル (rmap_s*) の共通ステージがありません")
            self._map_grod_status_lbl.setStyleSheet("color: red;")
            return

        try:
            ca = float(self._map_rss_ca_edit.text())
            n_slip, b_slip = _parse_slip_system(
                self._map_rss_plane_edit.text(),
                self._map_rss_dir_edit.text(),
                ca)
        except Exception as e:
            QMessageBox.critical(self, "入力エラー", str(e))
            return

        sym_ops = _get_sym_ops(self._map_rss_sym_cb.currentText())
        self.per_stage["RSS_max"] = {}
        self.per_stage["RSS_trace"] = {}
        n_total = len(stages)
        for i, stage in enumerate(stages):
            self._map_grod_status_lbl.setText(f"計算中... {i + 1}/{n_total}  [{stage}]")
            self._map_grod_status_lbl.setStyleSheet("color: gray;")
            QApplication.processEvents()

            phi1, PHI, phi2 = self._get_grod_euler(stage)
            if phi1 is None:
                continue
            s11 = self.per_stage["rmap_s11"][stage].astype(float)
            s22 = self.per_stage["rmap_s22"][stage].astype(float)
            s33 = self.per_stage["rmap_s33"][stage].astype(float)
            s12 = self.per_stage["rmap_s12"][stage].astype(float)
            s23 = self.per_stage["rmap_s23"][stage].astype(float)
            s31 = self.per_stage["rmap_s31"][stage].astype(float)
            rss, trace = compute_rss(
                phi1.astype(float), PHI.astype(float), phi2.astype(float),
                s11, s22, s33, s12, s23, s31,
                n_slip, b_slip, sym_ops, return_trace=True)
            self.per_stage["RSS_max"][stage] = rss
            self.per_stage["RSS_trace"][stage] = trace

        self._draw_rss_stage()
        self._map_grod_status_lbl.setText(f"完了: {n_total} ステージ計算済み")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")

    def _draw_rss_stage(self):
        stages = self._rss_stages()
        val = self._map_rss_stage_slider.value()
        if not stages or val >= len(stages):
            return
        tgt_stage = stages[val]
        data = self.per_stage.get("RSS_max", {}).get(tgt_stage)
        if data is None:
            return
        valid = data[np.isfinite(data)]
        try:
            vmin = float(self._map_rss_vmin_edit.text())
        except ValueError:
            vmin = float(np.min(valid)) if len(valid) else 0.0
        try:
            vmax = float(self._map_rss_vmax_edit.text())
        except ValueError:
            vmax = float(np.max(valid)) if len(valid) else 1.0
        x, y = self._get_xy(tgt_stage)
        label = "Max RSS [GPa]"
        title = f"{label}  [{tgt_stage}]"
        self.canvas_map.draw_scatter(
            x, y, data,
            self._map_rss_cmap_cb.currentText(), vmin, vmax, title,
            xlim=self._xlim, ylim=self._ylim, cbar_label=label)
        if self._map_rss_trace_cb.isChecked():
            trace = self.per_stage.get("RSS_trace", {}).get(tgt_stage)
            if trace is not None:
                try:
                    L  = float(self._map_rss_trace_len_edit.text())
                    lw = float(self._map_rss_trace_lw_edit.text())
                except ValueError:
                    L, lw = 10.0, 0.5
                mask = np.isfinite(trace[:, 0]) & np.isfinite(x) & np.isfinite(y)
                xi = x[mask]; yi = y[mask]
                tx = trace[mask, 0]; ty = trace[mask, 1]
                pts_s = np.column_stack([xi - L * tx, yi - L * ty])
                pts_e = np.column_stack([xi + L * tx, yi + L * ty])
                t_segs = np.stack([pts_s, pts_e], axis=1)
                self.canvas_map.ax.add_collection(
                    LineCollection(t_segs, linewidths=lw, colors="black", alpha=0.7, zorder=3))
                self.canvas_map.draw()
        if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
            segs = self._get_gb_segs(tgt_stage, x_draw=x, y_draw=y)
            if segs:
                self.canvas_map.ax.add_collection(
                    LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))
                self.canvas_map.draw()

    def _on_export_rss_all_stages(self):
        if "RSS_max" not in self.per_stage:
            self._on_compute_rss()
        if "RSS_max" not in self.per_stage:
            return
        cmap = self._map_rss_cmap_cb.currentText()
        try:
            vmin = float(self._map_rss_vmin_edit.text())
            vmax = float(self._map_rss_vmax_edit.text())
        except ValueError:
            all_vals = [d[np.isfinite(d)] for d in self.per_stage.get("RSS_max", {}).values()]
            combined = np.concatenate(all_vals) if all_vals else np.array([0.0, 1.0])
            vmin, vmax = float(np.nanmin(combined)), float(np.nanmax(combined))
        label = "Max RSS [GPa]"
        self._map_grod_status_lbl.setText("全ステージ出力中...")
        self._map_grod_status_lbl.setStyleSheet("color: gray;")
        show_trace = self._map_rss_trace_cb.isChecked()
        try:
            trace_L  = float(self._map_rss_trace_len_edit.text())
            trace_lw = float(self._map_rss_trace_lw_edit.text())
        except ValueError:
            trace_L, trace_lw = 10.0, 0.5
        saved = 0
        for stage, data in self.per_stage.get("RSS_max", {}).items():
            x, y = self._get_xy(stage)
            title = f"{label}  [{stage}]"
            self.canvas_map.draw_scatter(
                x, y, data, cmap, vmin, vmax, title,
                xlim=self._xlim, ylim=self._ylim, cbar_label=label)
            if show_trace:
                trace = self.per_stage.get("RSS_trace", {}).get(stage)
                if trace is not None:
                    mask = np.isfinite(trace[:, 0]) & np.isfinite(x) & np.isfinite(y)
                    xi = x[mask]; yi = y[mask]
                    tx = trace[mask, 0]; ty = trace[mask, 1]
                    pts_s = np.column_stack([xi - trace_L * tx, yi - trace_L * ty])
                    pts_e = np.column_stack([xi + trace_L * tx, yi + trace_L * ty])
                    t_segs = np.stack([pts_s, pts_e], axis=1)
                    self.canvas_map.ax.add_collection(
                        LineCollection(t_segs, linewidths=trace_lw, colors="black", alpha=0.7, zorder=3))
                    self.canvas_map.draw()
            if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
                segs = self._get_gb_segs(stage, x_draw=x, y_draw=y)
                if segs:
                    self.canvas_map.ax.add_collection(
                        LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))
                    self.canvas_map.draw()
            out = self.mat_path.parent / f"RSS_max_{stage}.png"
            self.canvas_map._fig.savefig(str(out), dpi=300, bbox_inches="tight")
            saved += 1
        self._map_grod_status_lbl.setText(f"完了: {saved} 枚を保存")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")
        QMessageBox.information(self, "Export All",
                                f"{saved} 枚を保存しました:\n{self.mat_path.parent}")

    def _on_add_rss_to_mat(self):
        if "RSS_max" not in self.per_stage or not self.per_stage["RSS_max"]:
            QMessageBox.warning(self, "追加", "先に「Compute RSS」を実行してください。")
            return
        self._map_grod_status_lbl.setText("保存中...")
        self._map_grod_status_lbl.setStyleSheet("color: gray;")
        from scipy.io import savemat
        data = self._load_derived_mat()
        new_vars = {}
        for stage, arr in self.per_stage["RSS_max"].items():
            new_vars[f"RSS_max_s{stage}"] = arr.reshape(1, -1)
        for stage, trace in self.per_stage.get("RSS_trace", {}).items():
            angle = np.degrees(np.arctan2(trace[:, 1], trace[:, 0])) % 180
            new_vars[f"RSS_trace_angle_s{stage}"] = angle.reshape(1, -1)
        data.update(new_vars)
        out = self._derived_mat_path()
        savemat(str(out), data)
        self._map_grod_status_lbl.setText(
            f"追加完了: {len(new_vars)} 変数を {out.name} に保存しました")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")

    # ----------------------------------------------------------
    # GROD ユーティリティ（Mapタブから使用）
    # ----------------------------------------------------------

    def _grod_euler_stages(self):
        """euler_phi1 / euler_phi / euler_phi2 の共通ステージを返す。"""
        s1 = set(self.per_stage.get("euler_phi1", {}).keys())
        sP = set(self.per_stage.get("euler_phi",  {}).keys())
        s2 = set(self.per_stage.get("euler_phi2", {}).keys())
        common = s1 & sP & s2
        def key(s):
            m = re.match(r"(\d+)MPa", s)
            return int(m.group(1)) if m else 0
        return sorted(common, key=key)

    def _get_grod_euler(self, stage):
        """指定ステージの euler_phi1/phi/phi2 を degrees で返す。"""
        phi1 = self.per_stage.get("euler_phi1", {}).get(stage)
        PHI  = self.per_stage.get("euler_phi",  {}).get(stage)
        phi2 = self.per_stage.get("euler_phi2", {}).get(stage)
        return phi1, PHI, phi2

    def _on_compute_grod(self):
        """全ステージ一括計算 → per_stage に保存 → 現在スライダー位置を表示。"""
        stages = self._grod_euler_stages()
        if not stages:
            self._map_grod_status_lbl.setText("エラー: Eulerステージデータが見つかりません")
            self._map_grod_status_lbl.setStyleSheet("color: red;")
            return
        ref_stage = self._map_grod_ref_cb.currentText()
        phi1_ref, PHI_ref, phi2_ref = self._get_grod_euler(ref_stage)
        if any(x is None for x in [phi1_ref, PHI_ref, phi2_ref]):
            self._map_grod_status_lbl.setText("エラー: Eulerデータの読み込みに失敗しました")
            self._map_grod_status_lbl.setStyleSheet("color: red;")
            return
        sym_name = self._map_grod_sym_cb.currentText()
        sym_ops  = _get_sym_ops(sym_name)

        # キャッシュをクリア
        for base in ("GROD_angle", "GROD_axis_x", "GROD_axis_y", "GROD_axis_z"):
            self.per_stage[base] = {}

        n_total = len(stages)
        for i, stage in enumerate(stages):
            self._map_grod_status_lbl.setText(
                f"計算中... {i + 1}/{n_total}  [{stage}]")
            self._map_grod_status_lbl.setStyleSheet("color: gray;")
            QApplication.processEvents()   # UIを都度更新
            phi1_t, PHI_t, phi2_t = self._get_grod_euler(stage)
            if any(x is None for x in [phi1_t, PHI_t, phi2_t]):
                continue
            angle, ax_x, ax_y, ax_z = compute_grod(
                phi1_ref, PHI_ref, phi2_ref,
                phi1_t, PHI_t, phi2_t, self.grain_id, sym_ops)
            self.per_stage["GROD_angle"][stage]  = angle
            self.per_stage["GROD_axis_x"][stage] = ax_x
            self.per_stage["GROD_axis_y"][stage] = ax_y
            self.per_stage["GROD_axis_z"][stage] = ax_z

        self._grod_cache_key = (ref_stage, sym_name)
        self._draw_grod_stage()
        n_done = len(self.per_stage["GROD_angle"])
        self._map_grod_status_lbl.setText(
            f"完了: {n_done} ステージ計算済み (ref={ref_stage}, {sym_name})")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")

    def _draw_grod_stage(self):
        """per_stage キャッシュからスライダーの現在ステージを描画する。"""
        stages = self._grod_euler_stages()
        val = self._map_grod_stage_slider.value()
        if not stages or val >= len(stages):
            return
        tgt_stage = stages[val]
        var_idx = self._map_grod_var_cb.currentIndex()
        base  = ["GROD_angle",      "GROD_axis"][var_idx]
        label = ["GROD Angle [deg]","GROD Axis (未実装)"][var_idx]
        data  = self.per_stage.get(base, {}).get(tgt_stage)
        if data is None:
            return
        ref_stage = getattr(self, "_grod_cache_key", ("?",))[0]
        valid = data[np.isfinite(data)]
        try:
            vmin = float(self._map_grod_vmin_edit.text())
        except ValueError:
            vmin = float(np.min(valid)) if len(valid) else 0.0
        try:
            vmax = float(self._map_grod_vmax_edit.text())
        except ValueError:
            vmax = float(np.max(valid)) if len(valid) else 1.0
        x, y = self._get_xy(tgt_stage)
        title = f"{label}  [{tgt_stage}]  (ref: {ref_stage})"
        self.canvas_map.draw_scatter(
            x, y, data,
            self._map_grod_cmap_cb.currentText(), vmin, vmax, title,
            xlim=self._xlim, ylim=self._ylim, cbar_label=label)
        if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
            segs = self._get_gb_segs(tgt_stage, x_draw=x, y_draw=y)
            if segs:
                self.canvas_map.ax.add_collection(
                    LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))
                self.canvas_map.draw()

    def _on_export_grod_png(self):
        if "GROD_angle" not in self.per_stage:
            QMessageBox.warning(self, "Export", "先に Compute Map を実行してください。")
            return
        stages = self._grod_euler_stages()
        val = self._map_grod_stage_slider.value()
        tgt = stages[val] if 0 <= val < len(stages) else "unknown"
        ref = getattr(self, "_grod_cache_key", ("?",))[0]
        var = (self._map_grod_var_cb.currentText()
               .replace(" ", "_").replace("[", "").replace("]", ""))
        out = self.mat_path.parent / f"GROD_{var}_{tgt}_ref{ref}.png"
        self.canvas_map._fig.savefig(str(out), dpi=300, bbox_inches="tight")
        QMessageBox.information(self, "Export", f"保存しました:\n{out}")

    def _on_export_grod_all_stages(self):
        """キャッシュがなければ先に全ステージ計算してから PNG を一括出力する。"""
        if "GROD_angle" not in self.per_stage:
            self._on_compute_grod()
        if "GROD_angle" not in self.per_stage:
            return
        ref_stage = getattr(self, "_grod_cache_key", ("?",))[0]
        var_idx = self._map_grod_var_cb.currentIndex()
        base  = ["GROD_angle",    "GROD_axis"][var_idx]
        label = ["GROD_Angle_deg","GROD_Axis"][var_idx]
        cmap  = self._map_grod_cmap_cb.currentText()
        try:
            vmin = float(self._map_grod_vmin_edit.text())
            vmax = float(self._map_grod_vmax_edit.text())
        except ValueError:
            all_vals = [d[np.isfinite(d)]
                        for d in self.per_stage.get(base, {}).values()]
            combined = np.concatenate(all_vals) if all_vals else np.array([0.0, 1.0])
            vmin, vmax = float(np.nanmin(combined)), float(np.nanmax(combined))

        self._map_grod_status_lbl.setText("全ステージ出力中...")
        self._map_grod_status_lbl.setStyleSheet("color: gray;")
        saved = 0
        for stage, data in self.per_stage.get(base, {}).items():
            x, y = self._get_xy(stage)
            title = f"{label}  [{stage}]  (ref: {ref_stage})"
            self.canvas_map.draw_scatter(
                x, y, data, cmap, vmin, vmax, title,
                xlim=self._xlim, ylim=self._ylim, cbar_label=label)
            if self._map_show_gb_cb.isChecked() and len(self.cx) > 0:
                segs = self._get_gb_segs(stage, x_draw=x, y_draw=y)
                if segs:
                    self.canvas_map.ax.add_collection(
                        LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))
                    self.canvas_map.draw()
            out = self.mat_path.parent / f"GROD_{label}_{stage}_ref{ref_stage}.png"
            self.canvas_map._fig.savefig(str(out), dpi=300, bbox_inches="tight")
            saved += 1

        self._map_grod_status_lbl.setText(f"完了: {saved} 枚を保存")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")
        QMessageBox.information(self, "Export All",
                                f"{saved} 枚を保存しました:\n{self.mat_path.parent}")

    def _derived_mat_path(self):
        """保存先の _derived.mat パスを返す。"""
        return self.mat_path.with_name(self.mat_path.stem + "_derived.mat")

    def _load_derived_mat(self):
        """_derived.mat があればそれを、なければ元ファイルをベースとして読み込む。"""
        from scipy.io import loadmat
        p = self._derived_mat_path()
        src = p if p.exists() else self.mat_path
        return {k: v for k, v in loadmat(str(src)).items()
                if not k.startswith("__")}

    def _on_add_grod_to_mat(self):
        """per_stage にある GROD 全ステージデータを _derived.mat に保存する。"""
        if "GROD_angle" not in self.per_stage or not self.per_stage["GROD_angle"]:
            QMessageBox.warning(self, "追加", "先に「Compute Map」を実行してください。")
            return
        self._map_grod_status_lbl.setText("保存中...")
        self._map_grod_status_lbl.setStyleSheet("color: gray;")
        from scipy.io import savemat
        data = self._load_derived_mat()
        new_vars = {}
        for stage, arr in self.per_stage.get("GROD_angle", {}).items():
            new_vars[f"GROD_angle_s{stage}"] = arr.reshape(1, -1)
        data.update(new_vars)
        out = self._derived_mat_path()
        savemat(str(out), data)
        self._map_grod_status_lbl.setText(
            f"追加完了: {len(new_vars)} 変数を {out.name} に保存しました")
        self._map_grod_status_lbl.setStyleSheet("color: goldenrod;")

    def _on_add_derived_to_mat(self):
        """計算済みの Derived Maps データを _derived.mat に保存する。"""
        if not self._derived_results:
            QMessageBox.warning(self, "追加",
                                "先に各項目の「Compute & Map」を実行してください。")
            return
        from scipy.io import savemat
        data = self._load_derived_mat()
        new_vars = {}
        for key, arr in self._derived_results.items():
            new_vars[key] = np.asarray(arr).reshape(1, -1)
        data.update(new_vars)
        out = self._derived_mat_path()
        savemat(str(out), data)
        keys_str = ", ".join(new_vars.keys())
        QMessageBox.information(self, "追加完了",
                                f"{len(new_vars)} 変数を保存しました:\n{keys_str}\n→ {out.name}")

    def _build_tab_derived(self, parent):
        # スクロール可能な外枠
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        scroll.setWidget(container)
        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        x_choices = [b for b in DIC_STRAIN_BASES if b in self.per_stage]
        y_choices = self._ss_y_choices()

        # 加工硬化率
        grp1 = QGroupBox("加工硬化率 (Work Hardening Rate)")
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
        btn_excel = QPushButton("弾性スティフネス参照…"); btn_excel.setFixedWidth(160)
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

        row_add = QHBoxLayout()
        btn_add_derived = QPushButton(f"{self.mat_path.stem}_derived.mat に保存")
        btn_add_derived.clicked.connect(self._on_add_derived_to_mat)
        row_add.addWidget(btn_add_derived)
        layout.addLayout(row_add)

        row_gb_d = QHBoxLayout()
        self._derived_show_gb_cb = QCheckBox("Grain boundaries")
        self._derived_show_gb_cb.setChecked(True)
        row_gb_d.addWidget(self._derived_show_gb_cb)
        row_gb_d.addWidget(QLabel("(粒界定義はMapタブと共通)"))
        row_gb_d.addStretch()
        layout.addLayout(row_gb_d)

        row_cmap_btn_d = QHBoxLayout()
        btn_cmap_preview_d = QPushButton("カラーマップ色見本")
        btn_cmap_preview_d.clicked.connect(self._show_cmap_preview)
        row_cmap_btn_d.addWidget(btn_cmap_preview_d)
        row_cmap_btn_d.addStretch()
        layout.addLayout(row_cmap_btn_d)

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

    def _get_gb_segs(self, stage=None, x_draw=None, y_draw=None):
        """現在の粒界定義設定に基づいてセグメントリストを返す。

        stage   : ステージ文字列（"0MPa" など）。None の場合は先頭ステージを使用。
        x_draw, y_draw : 描画用座標フィルタ（None = 全点）
        """
        # UI 構築中（_map_gb_sym_cb がまだ存在しない）は安全に抜ける
        if not hasattr(self, '_map_gb_sym_cb'):
            return []
        import hashlib, json as _json
        if not len(self.cx):
            return []
        if stage is None:
            stage = self.stages[0] if self.stages else "0MPa"

        def_key = self._map_gb_def_cb.currentData()
        defn    = DEFINITION_MAP.get(def_key)
        if defn is None:
            return []

        params      = {**defn.default_params, **self._map_gb_params_store.get(def_key, {})}
        sym_name    = self._map_gb_sym_cb.currentText()
        sym_ops     = get_sym_ops(sym_name)
        phase_sym_map = {ph: sym_ops for ph in range(256)}

        params_hash = hashlib.md5(
            _json.dumps(params, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        cache_key = (def_key, params_hash, stage)

        if cache_key not in self._gb_seg_cache:
            try:
                result_pairs = defn.compute_pairs(
                    self._mat_flat, stage, phase_sym_map, params)
                print(f"[GB] {def_key} ({stage}): {len(result_pairs)} pairs found")
                self._gb_seg_cache[cache_key] = result_pairs
            except Exception as e:
                import traceback
                print(f"[GB] compute_pairs エラー ({def_key}, {stage}): {e}")
                traceback.print_exc()
                self._gb_seg_cache[cache_key] = []

        pairs = self._gb_seg_cache[cache_key]
        try:
            segs = pairs_to_segments(pairs, self.cx, self.cy, x_draw=x_draw, y_draw=y_draw)
            print(f"[GB] {def_key}: {len(segs)} segments generated")
            return segs
        except Exception as e:
            import traceback
            print(f"[GB] pairs_to_segments エラー ({def_key}): {e}")
            traceback.print_exc()
            return []

    def _draw_derived(self, result, cmap, title, vmin, vmax, cbar_label=None):
        self.canvas_map.draw_scatter(self.cx, self.cy, result, cmap, vmin, vmax, title, xlim=self._xlim, ylim=self._ylim, cbar_label=cbar_label)
        if self._derived_show_gb_cb.isChecked() and len(self.cx) > 0:
            segs = self._get_gb_segs()
            if segs:
                self.canvas_map.ax.add_collection(
                    LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k")
                )
                self.canvas_map.draw()

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
        cbar_label = f"Work Hardening Rate [{y_unit}]" if y_unit else "Work Hardening Rate"
        self._draw_derived(result, self._hr_cmap_cb.currentText(), f"Work Hardening Rate (n={n}, m={m})", vmin, vmax, cbar_label=cbar_label)
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
        self.canvas_map._fig.savefig(str(out), dpi=300, bbox_inches="tight")
        QMessageBox.information(self, "Export", f"保存しました:\n{out}")

    # ----------------------------------------------------------
    # Grain ID マップの描画
    # ----------------------------------------------------------

    def _show_cmap_preview(self):
        """カラーマップ色見本を別ウィンドウで表示する。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        gradient = np.linspace(0, 1, 256).reshape(1, -1)
        n = len(CMAPS)
        fig = Figure(figsize=(3.5, n * 0.45 + 0.2), tight_layout=False)
        fig.subplots_adjust(left=0.30, right=0.97, top=0.99, hspace=0.25)
        for i, name in enumerate(CMAPS):
            ax = fig.add_subplot(n, 1, i + 1)
            ax.imshow(gradient, aspect="auto", cmap=name)
            ax.set_yticks([])
            ax.set_xticks([])
            ax.text(-0.01, 0.5, name, transform=ax.transAxes,
                    fontsize=8, va="center", ha="right")
        dlg = QDialog(self)
        dlg.setWindowTitle("Colormaps")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        canvas = FigureCanvasQtAgg(fig)
        layout.addWidget(canvas)
        screen = QApplication.primaryScreen().availableGeometry()
        dlg.resize(360, min(n * 48 + 20, screen.height() - 100))
        dlg.show()
        # show()後にサイズが確定するので、その後で中央移動
        dlg.move(screen.x() + (screen.width() - dlg.frameGeometry().width()) // 2,
                 screen.y() + (screen.height() - dlg.frameGeometry().height()) // 2)

    def _draw_grain_map(self):
        ax = self.canvas_map.ax
        fig = self.canvas_map._fig
        ax.clear()
        if self.canvas_map._colorbar is not None:
            try:
                self.canvas_map._colorbar.remove()
            except Exception:
                pass
            self.canvas_map._colorbar = None

        K = len(self._grain_unique)
        cmap = "turbo"
        norm = mcolors.Normalize(vmin=0, vmax=max(1, K - 1))

        sc = ax.scatter(self.cx, self.cy, s=6, alpha=0.9,
                        c=self._grain_code, cmap=cmap, norm=norm)
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("Grain ID")
        self.canvas_map._colorbar = cbar
        if K <= 20:
            cbar.set_ticks(np.linspace(0, K - 1, K))
            cbar.set_ticklabels([str(g) for g in self._grain_unique])

        if len(self.cx) > 0:
            segs = self._get_gb_segs()
            if segs:
                ax.add_collection(LineCollection(segs, linewidths=0.6, alpha=0.9, colors="k"))

        ax.set_title("Grain ID Map")
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        ax.set_xlim(self._xlim)
        ax.set_ylim(self._ylim)
        ax.set_aspect("equal", adjustable="box")
        self.canvas_map.draw()


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
    app.setStyleSheet("QRadioButton:checked { color: #FFD700; font-weight: bold; }")
    window = StressStrainMapperApp(mat_path.resolve())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
