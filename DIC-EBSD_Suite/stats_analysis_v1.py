"""
stats_analysis_v1.py
====================
integrated_georef_derived.mat を読み込んで統計解析を行うインタラクティブGUIツール。
PyQt6 + matplotlib Qt6Agg バックエンドで描画。

使用方法:
    python stats_analysis_v1.py --file path/to/integrated_georef_derived.mat
    python stats_analysis_v1.py          # ファイルダイアログで選択

モード:
    散布図       — 任意の2変数の散布図 (相関係数表示、第三変数でカラーリング可)
    ヒストグラム — 変数の分布を複数ステージ重ねて比較
    ステージ変化 — 平均±std / 中央値±IQR / 箱ひげ図でステージ依存性を表示
"""

import argparse
import re
import sys
from pathlib import Path

import os
os.environ.setdefault("QT_API", "PyQt6")

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as _plt
import numpy as np
from scipy.ndimage import distance_transform_edt
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvasQtAgg,
    NavigationToolbar2QT,
)
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTabWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QLineEdit,
    QRadioButton, QButtonGroup, QGroupBox, QFileDialog,
    QMessageBox, QSizePolicy, QCheckBox,
    QListWidget, QListWidgetItem, QSpinBox, QDoubleSpinBox,
    QStatusBar,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


# ============================================================
# 定数
# ============================================================

CMAPS = [
    "viridis", "plasma", "inferno", "magma",
    "coolwarm", "RdBu_r", "seismic", "bwr",
    "jet", "turbo", "hot", "Blues", "Reds", "YlOrRd",
]

# ステージ非依存変数のうち座標・ID系など解析対象外のもの（リストから除外）
_INDEP_EXCLUDE = {"cx", "cy", "subset_id"}

# 品質フィルタ定義: (表示名, 変数プレフィックス, 演算子, min, max, step, decimals, default)
# 演算子は "≥"（下限）または "≤"（上限）のみ。matにない変数は自動でグレーアウト。
_QUALITY_FILTERS = [
    ("CI",            "confidence_index",  "≥",   0.0, 1.0,    0.01,  3, 0.0   ),
    ("IQ",            "image_quality",     "≥",   0.0, 200000, 100.0, 0, 0.0   ),
    ("ZNCC",          "zncc",              "≥",  -1.0, 1.0,    0.001, 3, 0.0   ),
    ("PK hgt (map)",  "map_geompkhgt",     "≥",   0.0, 10.0,   0.01,  3, 0.0   ),
    ("PK hgt (rmap)", "rmap_geompkhgt",    "≥",   0.0, 10.0,   0.01,  3, 0.0   ),
    ("MAE (map)",     "map_mae",           "≤",   0.0, 9999.0, 0.01,  3, 9999.0),
    ("MAE (rmap)",    "rmap_mae",          "≤",   0.0, 9999.0, 0.01,  3, 9999.0),
]

# 粒界定義一覧 (label, key)。新しい定義を追加するには：
#   1. ここにエントリを追加する
#   2. _compute_gb_dist() に対応する elif ブランチを実装する
_GB_DEFINITIONS = [
    ("grain_id — 隣接粒IDが異なる境界", "grain_id"),
]


# ============================================================
# データ読み込み
# ============================================================

def load_mat(path: str):
    """
    matファイルを読み込む。

    Returns
    -------
    mat        : dict  キー→ndarray
    prefixes   : list  ステージ依存変数のプレフィックス一覧（ソート済み）
    stages     : list  ステージ文字列一覧（MPa昇順）
    indep_vars : list  ステージ非依存変数のキー一覧
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from preprocessed_loader import smart_loadmat

    mat = {k: v for k, v in smart_loadmat(path).items() if not k.startswith("__")}

    prefixes_set = set()
    stages_set = set()
    for k in mat:
        m = re.match(r"^(.+)_s(\d+MPa)$", k)
        if m:
            prefixes_set.add(m.group(1))
            stages_set.add(m.group(2))

    def _stage_num(s):
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) if m else 0

    stages = sorted(stages_set, key=_stage_num)
    prefixes = sorted(prefixes_set)

    # ステージ非依存変数 = ステージパターンに一致しない数値配列、除外リスト以外
    indep_vars = sorted(
        k for k in mat
        if not re.match(r"^(.+)_s(\d+MPa)$", k)
        and k not in _INDEP_EXCLUDE
        and isinstance(mat[k], np.ndarray)
    )

    return mat, prefixes, stages, indep_vars


# ============================================================
# matplotlib キャンバス
# ============================================================

class PlotCanvas(FigureCanvasQtAgg):
    def __init__(self, parent=None):
        self._fig = Figure(facecolor="white", tight_layout=True)
        self._ax = self._fig.add_subplot(111)
        self._style_ax(self._ax)
        super().__init__(self._fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._colorbar = None

    @property
    def ax(self):
        return self._ax

    def _style_ax(self, ax):
        ax.set_facecolor("white")
        ax.tick_params(colors="#333333", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#888888")
        ax.xaxis.label.set_color("#333333")
        ax.yaxis.label.set_color("#333333")
        ax.title.set_color("#111111")

    def clear_plot(self):
        if self._colorbar is not None:
            self._colorbar.remove()
            self._colorbar = None
        self._fig.clear()
        self._ax = self._fig.add_subplot(111)
        self._style_ax(self._ax)
        self.draw()

    def finish(self):
        self._fig.tight_layout()
        self.draw()

    def save_png(self, path: str):
        self._fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")


# ============================================================
# メインウィンドウ
# ============================================================

class StatsWindow(QMainWindow):
    def __init__(self, mat_path: str = None):
        super().__init__()
        self.setWindowTitle("Statistical Analysis  —  PineOak")
        self.resize(1280, 820)

        self._mat = None
        self._prefixes = []
        self._stages = []
        self._indep_vars = []
        self._file_path = ""
        self._gb_dist_cache: dict = {}   # def_key -> np.ndarray（ファイル切替時にクリア）

        self._build_ui()
        self._apply_style()

        if mat_path:
            self._load_file(mat_path)

    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ---- ファイル選択 ----
        fg = QGroupBox("入力ファイル")
        fl = QHBoxLayout(fg)
        self._path_label = QLabel("（未読み込み）")
        self._path_label.setFont(QFont("Courier New", 9))
        self._path_label.setWordWrap(True)
        btn = QPushButton("参照…")
        btn.setFixedWidth(80)
        btn.clicked.connect(self._browse)
        fl.addWidget(self._path_label, 1)
        fl.addWidget(btn)
        outer.addWidget(fg)

        # ---- スプリッタ（左：コントロール / 右：キャンバス） ----
        sp = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(sp, 1)

        # 左タブ
        self._tabs = QTabWidget()
        self._tabs.setFixedWidth(310)
        self._tabs.addTab(self._build_scatter_tab(), "散布図")
        self._tabs.addTab(self._build_hist_tab(),    "ヒストグラム")
        self._tabs.addTab(self._build_evol_tab(),    "ステージ変化")
        sp.addWidget(self._tabs)

        # 右キャンバス
        cw = QWidget()
        cl = QVBoxLayout(cw)
        cl.setContentsMargins(0, 0, 0, 0)
        self._canvas = PlotCanvas()
        self._toolbar = NavigationToolbar2QT(self._canvas, cw)
        cl.addWidget(self._toolbar)
        cl.addWidget(self._canvas, 1)
        sp.addWidget(cw)
        sp.setStretchFactor(0, 0)
        sp.setStretchFactor(1, 1)

        # ステータスバー
        self._sb = QStatusBar()
        self.setStatusBar(self._sb)
        self._sb.showMessage("ファイルを読み込んでください")

    # ---------- 散布図タブ ----------

    def _build_scatter_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setSpacing(6)

        self._sc_x     = QComboBox()
        self._sc_y     = QComboBox()
        self._sc_stage = QComboBox()
        self._sc_color = QComboBox()
        self._sc_cmap  = QComboBox()
        self._sc_cmap.addItems(CMAPS)
        self._sc_cmap.setCurrentText("viridis")

        for lbl, cb in [("X 軸", self._sc_x), ("Y 軸", self._sc_y),
                        ("ステージ", self._sc_stage), ("カラー変数", self._sc_color),
                        ("カラーマップ", self._sc_cmap)]:
            L.addLayout(self._row(lbl, cb))

        self._sc_xlim, self._sc_ylim = [], []
        L.addWidget(self._axis_range_group(self._sc_xlim, self._sc_ylim))
        L.addWidget(self._build_region_filter_group("sc"))
        L.addWidget(self._build_quality_filter_group("sc"))

        self._sc_info = QLabel("")
        self._sc_info.setWordWrap(True)
        self._sc_info.setFont(QFont("Courier New", 9))
        self._sc_info.setStyleSheet("color: #9ca3af;")
        L.addWidget(self._sc_info)

        L.addStretch()
        L.addLayout(self._btn_row(self._plot_scatter, "scatter"))
        return w

    # ---------- ヒストグラムタブ ----------

    def _build_hist_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setSpacing(6)

        self._hist_var = QComboBox()
        L.addLayout(self._row("変数", self._hist_var))

        sg = QGroupBox("ステージ選択（複数可）")
        sl = QVBoxLayout(sg)
        self._hist_stages = QListWidget()
        self._hist_stages.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._hist_stages.setMaximumHeight(140)
        sl.addWidget(self._hist_stages)
        L.addWidget(sg)

        self._hist_bins    = QSpinBox()
        self._hist_bins.setRange(5, 500)
        self._hist_bins.setValue(50)
        self._hist_density = QCheckBox("密度表示（面積 = 1）")
        L.addLayout(self._row("ビン数", self._hist_bins))
        L.addWidget(self._hist_density)

        self._hist_xlim, self._hist_ylim = [], []
        L.addWidget(self._axis_range_group(self._hist_xlim, self._hist_ylim))
        L.addWidget(self._build_region_filter_group("hist"))
        L.addWidget(self._build_quality_filter_group("hist"))

        self._hist_info = QLabel("")
        self._hist_info.setWordWrap(True)
        self._hist_info.setFont(QFont("Courier New", 9))
        self._hist_info.setStyleSheet("color: #9ca3af;")
        L.addWidget(self._hist_info)

        L.addStretch()
        L.addLayout(self._btn_row(self._plot_hist, "histogram"))
        return w

    # ---------- ステージ変化タブ ----------

    def _build_evol_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setSpacing(6)

        self._evol_var = QComboBox()
        L.addLayout(self._row("変数", self._evol_var))

        tg = QGroupBox("プロットタイプ")
        tl = QVBoxLayout(tg)
        self._evol_type = QButtonGroup()
        for i, label in enumerate(["平均 ± 標準偏差", "中央値 ± IQR", "箱ひげ図"]):
            rb = QRadioButton(label)
            if i == 0:
                rb.setChecked(True)
            self._evol_type.addButton(rb, i)
            tl.addWidget(rb)
        L.addWidget(tg)

        self._evol_xlim, self._evol_ylim = [], []
        L.addWidget(self._axis_range_group(self._evol_xlim, self._evol_ylim))
        L.addWidget(self._build_region_filter_group("evol"))
        L.addWidget(self._build_quality_filter_group("evol"))

        L.addStretch()
        L.addLayout(self._btn_row(self._plot_evol, "evolution"))
        return w

    # ---------- 共通ヘルパー ----------

    @staticmethod
    def _row(label: str, widget):
        h = QHBoxLayout()
        lb = QLabel(label)
        lb.setFixedWidth(80)
        h.addWidget(lb)
        h.addWidget(widget)
        return h

    def _build_region_filter_group(self, prefix: str) -> QGroupBox:
        """Region Filter グループを構築し self._{prefix}_rf_* にウィジェットを登録する。"""
        grp = QGroupBox("Region Filter")
        gl = QVBoxLayout(grp)
        gl.setSpacing(3)

        # ---- 相フィルタ ----
        phase_en = QCheckBox("相フィルタ (phase_index)")
        # チェックボックスを動的に並べるコンテナ（_populate_phase_opts で中身を生成）
        phase_cb_area = QWidget()
        phase_cb_layout = QVBoxLayout(phase_cb_area)
        phase_cb_layout.setContentsMargins(16, 0, 0, 0)
        phase_cb_layout.setSpacing(2)
        phase_cb_area.setEnabled(False)
        phase_en.toggled.connect(phase_cb_area.setEnabled)
        gl.addWidget(phase_en)
        gl.addWidget(phase_cb_area)

        # ---- 粒界近傍フィルタ ----
        gb_en = QCheckBox("粒界近傍フィルタ")
        gl.addWidget(gb_en)

        def_row = QHBoxLayout()
        def_lbl = QLabel("定義:")
        def_lbl.setFixedWidth(32)
        gb_def = QComboBox()
        for label, _ in _GB_DEFINITIONS:
            gb_def.addItem(label)
        gb_def.setEnabled(False)
        def_row.addWidget(def_lbl)
        def_row.addWidget(gb_def, 1)
        gl.addLayout(def_row)

        dist_row = QHBoxLayout()
        gb_op = QComboBox()
        gb_op.addItems(["≦ (近傍)", "≧ (粒内部)"])
        gb_op.setFixedWidth(100)
        gb_op.setEnabled(False)
        gb_dist = QSpinBox()
        gb_dist.setRange(1, 999)
        gb_dist.setValue(5)
        gb_dist.setSuffix(" px")
        gb_dist.setEnabled(False)
        dist_row.addWidget(gb_op)
        dist_row.addWidget(gb_dist)
        dist_row.addStretch()
        gl.addLayout(dist_row)

        gb_en.toggled.connect(gb_def.setEnabled)
        gb_en.toggled.connect(gb_op.setEnabled)
        gb_en.toggled.connect(gb_dist.setEnabled)

        # ウィジェットをインスタンス属性として保持
        setattr(self, f"_{prefix}_rf_phase_en",      phase_en)
        setattr(self, f"_{prefix}_rf_phase_cb_area", phase_cb_area)
        setattr(self, f"_{prefix}_rf_phase_cbs",     [])   # (phase_val, QCheckBox) のリスト
        setattr(self, f"_{prefix}_rf_gb_en",         gb_en)
        setattr(self, f"_{prefix}_rf_gb_def",      gb_def)
        setattr(self, f"_{prefix}_rf_gb_op",       gb_op)
        setattr(self, f"_{prefix}_rf_gb_dist",     gb_dist)
        return grp

    def _build_quality_filter_group(self, prefix: str) -> QGroupBox:
        """Quality Filter グループを構築し self._{prefix}_qf_rows にウィジェットを登録する。"""
        grp = QGroupBox("Quality Filter")
        gl = QVBoxLayout(grp)
        gl.setSpacing(2)

        rows = []
        for label, var_prefix, op, vmin, vmax, step, dec, default in _QUALITY_FILTERS:
            h = QHBoxLayout()
            cb = QCheckBox(label)
            cb.setFixedWidth(112)
            op_lbl = QLabel(op)
            op_lbl.setFixedWidth(14)
            sp = QDoubleSpinBox()
            sp.setRange(vmin, vmax)
            sp.setSingleStep(step)
            sp.setDecimals(dec)
            sp.setValue(default)
            sp.setEnabled(False)
            cb.toggled.connect(sp.setEnabled)
            h.addWidget(cb)
            h.addWidget(op_lbl)
            h.addWidget(sp, 1)
            gl.addLayout(h)
            rows.append((var_prefix, op, cb, sp))

        setattr(self, f"_{prefix}_qf_rows", rows)
        return grp

    def _axis_range_group(self, x_edits: list, y_edits: list) -> QGroupBox:
        """X/Y軸のMin/Max入力欄グループを返す。editsリストに [min_edit, max_edit] を追加する。"""
        grp = QGroupBox("Axis Range  (空欄 = Auto)")
        gl = QVBoxLayout(grp)
        gl.setSpacing(3)
        for axis, edits in (("X", x_edits), ("Y", y_edits)):
            h = QHBoxLayout()
            h.addWidget(QLabel(f"{axis}:"))
            for placeholder in ("Min", "Max"):
                e = QLineEdit()
                e.setPlaceholderText(placeholder)
                e.setFixedWidth(72)
                h.addWidget(e)
                edits.append(e)
            h.addStretch()
            gl.addLayout(h)
        return grp

    @staticmethod
    def _parse_limit(edit: "QLineEdit"):
        """テキストを float に変換。空欄または不正値は None を返す。"""
        try:
            return float(edit.text())
        except ValueError:
            return None

    def _apply_lim(self, ax, xlim_edits: list, ylim_edits: list):
        """入力欄の値を軸範囲に適用する。空欄は無視。"""
        xlo = self._parse_limit(xlim_edits[0])
        xhi = self._parse_limit(xlim_edits[1])
        ylo = self._parse_limit(ylim_edits[0])
        yhi = self._parse_limit(ylim_edits[1])
        if xlo is not None or xhi is not None:
            cur = ax.get_xlim()
            ax.set_xlim(xlo if xlo is not None else cur[0],
                        xhi if xhi is not None else cur[1])
        if ylo is not None or yhi is not None:
            cur = ax.get_ylim()
            ax.set_ylim(ylo if ylo is not None else cur[0],
                        yhi if yhi is not None else cur[1])

    def _btn_row(self, plot_fn, tag: str):
        h = QHBoxLayout()
        bp = QPushButton("プロット")
        be = QPushButton("PNG 保存")
        bp.clicked.connect(plot_fn)
        be.clicked.connect(lambda: self._save_png(tag))
        h.addWidget(bp)
        h.addWidget(be)
        return h

    # ----------------------------------------------------------
    # スタイル
    # ----------------------------------------------------------

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget          { background:#1a1d24; color:#e0e4ec; font-size:12px; }
            QGroupBox                     { border:1px solid #2a2d35; border-radius:4px;
                                            margin-top:8px; padding-top:4px; }
            QGroupBox::title              { color:#00d4ff; font-size:11px; subcontrol-origin:margin;
                                            left:8px; padding:0 4px; }
            QComboBox, QSpinBox, QLineEdit { background:#111318; border:1px solid #2a2d35;
                                            color:#e0e4ec; padding:3px 6px; border-radius:3px; }
            QLineEdit::placeholder        { color:#6b7280; }
            QComboBox::drop-down          { border:none; }
            QComboBox QAbstractItemView   { background:#1a1d24; color:#e0e4ec;
                                            selection-background-color:#00d4ff;
                                            selection-color:#000; }
            QPushButton                   { background:#111318; border:1px solid #2a2d35;
                                            color:#e0e4ec; padding:5px 12px; border-radius:3px; }
            QPushButton:hover             { border-color:#00d4ff; color:#00d4ff;
                                            background:rgba(0,212,255,0.07); }
            QPushButton:disabled          { opacity:0.35; }
            QTabWidget::pane              { border:1px solid #2a2d35; }
            QTabBar::tab                  { background:#111318; color:#6b7280;
                                            padding:6px 14px; border:1px solid #2a2d35; }
            QTabBar::tab:selected         { background:#1a1d24; color:#00d4ff;
                                            border-bottom-color:#1a1d24; }
            QListWidget                   { background:#111318; border:1px solid #2a2d35;
                                            color:#e0e4ec; }
            QListWidget::item:selected    { background:rgba(0,212,255,0.2); color:#00d4ff; }
            QCheckBox, QRadioButton       { color:#e0e4ec; }
            QStatusBar                    { background:#111318; color:#6b7280; font-size:10px; }
            QSplitter::handle             { background:#2a2d35; width:2px; }
            NavigationToolbar2QT          { background:#111318; }
        """)

    # ----------------------------------------------------------
    # ファイル操作
    # ----------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "matファイルを選択",
            str(Path(self._file_path).parent) if self._file_path else "",
            "MAT files (*.mat);;All files (*.*)",
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        self._sb.showMessage(f"読み込み中: {path}")
        QApplication.processEvents()
        try:
            mat, prefixes, stages, indep_vars = load_mat(path)
        except Exception as e:
            QMessageBox.critical(self, "読み込みエラー", str(e))
            self._sb.showMessage("読み込みエラー")
            return

        self._mat        = mat
        self._prefixes   = prefixes
        self._stages     = stages
        self._indep_vars = indep_vars
        self._file_path  = path
        self._gb_dist_cache = {}          # ファイル切替でキャッシュ無効化
        self._path_label.setText(path)

        self._populate_widgets()
        n = self._mat[list(self._mat.keys())[0]].size
        self._sb.showMessage(
            f"読み込み完了 — 変数 {len(prefixes)} 種, ステージ {len(stages)} 個, "
            f"点数 {n:,}"
        )

    def _populate_widgets(self):
        all_vars   = self._prefixes + self._indep_vars
        stage_deps = self._prefixes

        # 散布図
        for cb in (self._sc_x, self._sc_y):
            cb.clear(); cb.addItems(all_vars)
        self._sc_color.clear()
        self._sc_color.addItems(["（なし）"] + all_vars)
        self._sc_stage.clear()
        self._sc_stage.addItems(self._stages)
        if self._stages:
            self._sc_stage.setCurrentIndex(len(self._stages) // 2)

        # ヒストグラム
        self._hist_var.clear()
        self._hist_var.addItems(stage_deps + self._indep_vars)
        self._hist_stages.clear()
        for s in self._stages:
            self._hist_stages.addItem(QListWidgetItem(s))
        if self._hist_stages.count() >= 2:
            self._hist_stages.item(0).setSelected(True)
            self._hist_stages.item(self._hist_stages.count() - 1).setSelected(True)

        # ステージ変化
        self._evol_var.clear()
        self._evol_var.addItems(stage_deps)
        for prefer in ("gamma_max", "e1", "exx"):
            if prefer in stage_deps:
                self._evol_var.setCurrentText(prefer)
                break

        self._populate_phase_opts(["sc", "hist", "evol"])
        self._update_quality_availability(["sc", "hist", "evol"])

    # ----------------------------------------------------------
    # Region Filter サポート
    # ----------------------------------------------------------

    def _update_quality_availability(self, prefixes: list):
        """matに存在しない品質変数の行をグレーアウトする。"""
        first = self._stages[0] if self._stages else None
        for p in prefixes:
            rows: list = getattr(self, f"_{p}_qf_rows", [])
            for var_prefix, op, cb, sp in rows:
                available = bool(first and f"{var_prefix}_s{first}" in self._mat)
                cb.setEnabled(available)
                if not available:
                    cb.setChecked(False)

    def _populate_phase_opts(self, prefixes: list):
        """全ステージの phase_index をスキャンして各タブの相リストを更新する。"""
        phase_vals: set = set()
        for s in self._stages:
            key = f"phase_index_s{s}"
            if key in self._mat:
                arr = self._mat[key].flatten()
                phase_vals |= {int(v) for v in np.unique(arr[np.isfinite(arr)])}

        has_phase = bool(phase_vals)
        has_gb = "grain_id" in self._mat and "cx" in self._mat and "cy" in self._mat

        for p in prefixes:
            # チェックボックスを再生成
            area: QWidget = getattr(self, f"_{p}_rf_phase_cb_area")
            layout = area.layout()
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            cbs = []
            for ph in sorted(phase_vals):
                cb = QCheckBox(f"phase {ph}")
                cb.setChecked(True)      # デフォルト全チェック
                layout.addWidget(cb)
                cbs.append((ph, cb))
            setattr(self, f"_{p}_rf_phase_cbs", cbs)

            getattr(self, f"_{p}_rf_phase_en").setEnabled(has_phase)

            # 粒界フィルタの有効化
            getattr(self, f"_{p}_rf_gb_en").setEnabled(has_gb)

    def _compute_gb_dist(self, definition: str) -> np.ndarray:
        """粒界からの距離マップを計算してキャッシュする（フラット配列, px）。

        新しい粒界定義を追加するには _GB_DEFINITIONS にエントリを追加し、
        ここに elif ブランチを実装する。
        """
        if definition in self._gb_dist_cache:
            return self._gb_dist_cache[definition]

        if definition == "grain_id":
            cx  = self._mat["cx"].flatten()
            cy  = self._mat["cy"].flatten()
            gid = self._mat["grain_id"].flatten()

            xs = np.unique(cx);  ys = np.unique(cy)
            step_x = float(np.min(np.diff(np.sort(xs)))) if len(xs) > 1 else 1.0
            step_y = float(np.min(np.diff(np.sort(ys)))) if len(ys) > 1 else 1.0
            nx = len(xs);  ny = len(ys)

            ix = np.round((cx - xs.min()) / step_x).astype(int)
            iy = np.round((cy - ys.min()) / step_y).astype(int)

            # グリッド再構築 (-2 = データなし)
            gid_grid = np.full((ny, nx), -2.0)
            gid_grid[iy, ix] = gid

            # 粒界点: 水平/垂直方向の隣接画素でgrain_idが異なる点
            is_gb = np.zeros((ny, nx), bool)
            for axis, sl_a, sl_b in [
                (None, (slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                (None, (slice(None, -1), slice(None)), (slice(1, None), slice(None))),
            ]:
                g_a = gid_grid[sl_a];  g_b = gid_grid[sl_b]
                valid = (g_a != -2) & (g_b != -2)
                diff  = valid & (g_a != g_b)
                is_gb[sl_a] |= diff
                is_gb[sl_b] |= diff

            # grain_id == -1 (非indexing点) も粒界扱い
            is_gb |= (gid_grid == -1)

            # 距離変換: 各点から最近傍の粒界点までの距離（粒界点自身 = 0）
            dist_grid = distance_transform_edt(~is_gb)
            dist_flat = dist_grid[iy, ix]

        # ── 将来の定義はここに elif で追加 ──────────────────────────
        # elif definition == "misorientation":
        #     ...
        # ────────────────────────────────────────────────────────────
        else:
            raise ValueError(f"未対応の粒界定義: {definition}")

        self._gb_dist_cache[definition] = dist_flat
        return dist_flat

    def _get_region_mask(self, stage: str, prefix: str) -> np.ndarray:
        """Region Filter の設定に基づいてブール配列マスクを返す（True = 含む）。"""
        n    = next(iter(self._mat.values())).size
        mask = np.ones(n, dtype=bool)

        # ---- 相フィルタ ----
        if getattr(self, f"_{prefix}_rf_phase_en").isChecked():
            cbs: list = getattr(self, f"_{prefix}_rf_phase_cbs")
            selected = {ph for ph, cb in cbs if cb.isChecked()}
            if selected:
                key = f"phase_index_s{stage}"
                if key in self._mat:
                    pi = self._mat[key].flatten().astype(float)
                    pm = np.zeros(n, dtype=bool)
                    for ph in selected:
                        pm |= (pi == ph)
                    mask &= pm

        # ---- 粒界近傍フィルタ ----
        if getattr(self, f"_{prefix}_rf_gb_en").isChecked():
            gb_def  = getattr(self, f"_{prefix}_rf_gb_def")
            gb_op   = getattr(self, f"_{prefix}_rf_gb_op")
            gb_dist = getattr(self, f"_{prefix}_rf_gb_dist")

            def_key   = _GB_DEFINITIONS[gb_def.currentIndex()][1]
            threshold = gb_dist.value()
            dist      = self._compute_gb_dist(def_key)

            if gb_op.currentIndex() == 0:   # ≦ (近傍)
                mask &= dist <= threshold
            else:                            # ≧ (粒内部)
                mask &= dist >= threshold

        return mask

    def _get_quality_mask(self, stage: str, prefix: str) -> np.ndarray:
        """Quality Filter の設定に基づいてブール配列マスクを返す（True = 含む）。"""
        n    = next(iter(self._mat.values())).size
        mask = np.ones(n, dtype=bool)

        rows: list = getattr(self, f"_{prefix}_qf_rows", [])
        for var_prefix, op, cb, sp in rows:
            if not cb.isChecked():
                continue
            key = f"{var_prefix}_s{stage}"
            if key not in self._mat:
                continue
            arr       = self._mat[key].flatten().astype(float)
            threshold = sp.value()
            finite    = np.isfinite(arr)
            if op == "≥":
                mask &= finite & (arr >= threshold)
            else:   # ≤
                mask &= finite & (arr <= threshold)

        return mask

    # ----------------------------------------------------------
    # データ取得
    # ----------------------------------------------------------

    def _get(self, prefix: str, stage: str) -> np.ndarray:
        """prefix + stage から flatten された float64 配列を返す"""
        if prefix in self._indep_vars:
            return self._mat[prefix].flatten().astype(float)
        key = f"{prefix}_s{stage}"
        if key not in self._mat:
            raise KeyError(f"変数 '{key}' がmatファイルに存在しません")
        return self._mat[key].flatten().astype(float)

    @staticmethod
    def _stage_mpa(s: str) -> int:
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) if m else 0

    # ----------------------------------------------------------
    # 散布図プロット
    # ----------------------------------------------------------

    def _plot_scatter(self):
        if not self._check_loaded():
            return
        try:
            xp    = self._sc_x.currentText()
            yp    = self._sc_y.currentText()
            stage = self._sc_stage.currentText()
            cp    = self._sc_color.currentText()
            cmap  = self._sc_cmap.currentText()

            x = self._get(xp, stage)
            y = self._get(yp, stage)
            mask = np.isfinite(x) & np.isfinite(y)

            c_arr = None
            if cp != "（なし）":
                c_raw = self._get(cp, stage)
                mask &= np.isfinite(c_raw)

            mask &= self._get_region_mask(stage, "sc") & self._get_quality_mask(stage, "sc")

            if cp != "（なし）":
                c_arr = c_raw[mask]

            x, y = x[mask], y[mask]
            if len(x) == 0:
                QMessageBox.warning(self, "データなし", "有効なデータ点がありません")
                return

            r = float(np.corrcoef(x, y)[0, 1])

            self._canvas.clear_plot()
            ax = self._canvas.ax

            if c_arr is not None:
                sc = ax.scatter(x, y, c=c_arr, cmap=cmap, s=4, alpha=0.6, linewidths=0)
                cb = self._canvas._fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
                cb.set_label(cp, fontsize=8, color="#9ca3af")
                cb.ax.yaxis.set_tick_params(colors="#9ca3af")
                self._canvas._colorbar = cb
            else:
                ax.scatter(x, y, s=4, alpha=0.45, linewidths=0, color="#00d4ff")

            ax.set_xlabel(xp, fontsize=10)
            ax.set_ylabel(yp, fontsize=10)
            ax.set_title(f"{yp}  vs  {xp}   [{stage}]   n={len(x):,}", fontsize=11)
            ax.grid(True, alpha=0.4, color="#cccccc")
            self._apply_lim(ax, self._sc_xlim, self._sc_ylim)
            self._canvas.finish()

            info = (f"n = {len(x):,}\n"
                    f"r = {r:.4f}\n"
                    f"X  mean={np.nanmean(x):.4g}  std={np.nanstd(x):.4g}\n"
                    f"Y  mean={np.nanmean(y):.4g}  std={np.nanstd(y):.4g}")
            self._sc_info.setText(info)
            self._sb.showMessage(f"散布図完了 — n={len(x):,}  r={r:.4f}")

        except Exception as e:
            self._err(e)

    # ----------------------------------------------------------
    # ヒストグラムプロット
    # ----------------------------------------------------------

    def _plot_hist(self):
        if not self._check_loaded():
            return
        try:
            var     = self._hist_var.currentText()
            sel     = [it.text() for it in self._hist_stages.selectedItems()]
            bins    = self._hist_bins.value()
            density = self._hist_density.isChecked()

            if not sel:
                QMessageBox.warning(self, "未選択", "ステージを1つ以上選択してください")
                return

            # ステージ非依存変数はステージ選択に関係なく1回だけ取得
            is_indep = var in self._indep_vars
            plot_stages = [sel[0]] if is_indep else sel

            # 全ステージのデータをまとめて共通ビンエッジを決定
            all_data = []
            for stage in plot_stages:
                d = self._get(var, stage)
                region_stage = plot_stages[0] if is_indep else stage
                d = d[np.isfinite(d) & self._get_region_mask(region_stage, "hist") & self._get_quality_mask(region_stage, "hist")]
                if len(d) > 0:
                    all_data.append(d)
            if not all_data:
                QMessageBox.warning(self, "データなし", "有効なデータがありません")
                return
            combined = np.concatenate(all_data)
            # X軸範囲指定があればそれをビン範囲に使う
            xlo = self._parse_limit(self._hist_xlim[0])
            xhi = self._parse_limit(self._hist_xlim[1])
            rng = (xlo if xlo is not None else float(combined.min()),
                   xhi if xhi is not None else float(combined.max()))
            bin_edges = np.linspace(rng[0], rng[1], bins + 1)

            self._canvas.clear_plot()
            ax = self._canvas.ax
            cmap_f = _plt.get_cmap("plasma")
            colors = [cmap_f(i / max(len(plot_stages) - 1, 1)) for i in range(len(plot_stages))]

            lines = []
            for stage, col, d in zip(plot_stages, colors, all_data):
                lbl = var if is_indep else stage
                ax.hist(d, bins=bin_edges, density=density, color=col,
                        alpha=0.55, label=lbl, edgecolor="none")
                lines.append(
                    f"{lbl}: mean={np.mean(d):.4g}  std={np.std(d):.4g}  "
                    f"med={np.median(d):.4g}  n={len(d):,}"
                )

            ax.set_xlabel(var, fontsize=10)
            ax.set_ylabel("Density" if density else "Count", fontsize=10)
            ax.set_title(f"{var}  Histogram", fontsize=11)
            leg = ax.legend(fontsize=8, facecolor="white", edgecolor="#cccccc",
                            labelcolor="#333333")
            ax.grid(True, alpha=0.4, color="#cccccc", axis="y")
            self._apply_lim(ax, self._hist_xlim, self._hist_ylim)
            self._canvas.finish()

            self._hist_info.setText("\n".join(lines))
            self._sb.showMessage(f"ヒストグラム完了 — {len(sel)} ステージ")

        except Exception as e:
            self._err(e)

    # ----------------------------------------------------------
    # ステージ変化プロット
    # ----------------------------------------------------------

    def _plot_evol(self):
        if not self._check_loaded():
            return
        try:
            var       = self._evol_var.currentText()
            plot_type = self._evol_type.checkedId()

            xs = list(range(len(self._stages)))
            data_list = []
            valid_xs     = []
            valid_stages = []
            for s, x in zip(self._stages, xs):
                d = self._get(var, s)
                d = d[np.isfinite(d) & self._get_region_mask(s, "evol") & self._get_quality_mask(s, "evol")]
                if len(d) == 0:
                    continue
                data_list.append(d)
                valid_xs.append(x)
                valid_stages.append(s)

            if not data_list:
                QMessageBox.warning(self, "データなし", "有効なデータがありません")
                return

            self._canvas.clear_plot()
            ax = self._canvas.ax

            if plot_type == 2:
                # 箱ひげ図
                bp = ax.boxplot(
                    data_list,
                    positions=valid_xs,
                    widths=0.6,
                    patch_artist=True,
                    showfliers=False,
                    medianprops=dict(color="#00d4ff", linewidth=2),
                    whiskerprops=dict(color="#6b7280"),
                    capprops=dict(color="#6b7280"),
                    boxprops=dict(facecolor=(0.0, 0.83, 1.0, 0.12), edgecolor="#2a2d35"),
                )

            elif plot_type == 0:
                # 平均 ± 標準偏差
                ys   = [np.mean(d)  for d in data_list]
                errs = [np.std(d)   for d in data_list]
                ax.errorbar(valid_xs, ys, yerr=errs,
                            fmt="o-", color="#00d4ff", ecolor="#ff6b35",
                            capsize=4, linewidth=2, markersize=5,
                            label="Mean +/- Std")
                ax.legend(fontsize=9, facecolor="white", edgecolor="#cccccc",
                          labelcolor="#333333")

            else:
                # 中央値 ± IQR
                ys     = [np.median(d)                    for d in data_list]
                lo_err = [np.median(d) - np.percentile(d, 25) for d in data_list]
                hi_err = [np.percentile(d, 75) - np.median(d) for d in data_list]
                ax.errorbar(valid_xs, ys, yerr=[lo_err, hi_err],
                            fmt="o-", color="#00d4ff", ecolor="#ff6b35",
                            capsize=4, linewidth=2, markersize=5,
                            label="Median +/- IQR")
                ax.legend(fontsize=9, facecolor="white", edgecolor="#cccccc",
                          labelcolor="#333333")

            ax.set_xlabel("Stage", fontsize=10)
            ax.set_ylabel(var, fontsize=10)
            ax.set_title(f"{var}  vs Stage", fontsize=11)
            ax.set_xticks(valid_xs)
            ax.set_xticklabels(valid_stages, rotation=45, fontsize=8)
            ax.grid(True, alpha=0.4, color="#cccccc")
            self._apply_lim(ax, self._evol_xlim, self._evol_ylim)
            self._canvas.finish()

            self._sb.showMessage(f"ステージ変化プロット完了 — {var}")

        except Exception as e:
            self._err(e)

    # ----------------------------------------------------------
    # PNG 保存
    # ----------------------------------------------------------

    def _save_png(self, tag: str):
        default = (Path(self._file_path).parent / f"stats_{tag}.png"
                   if self._file_path else Path(f"stats_{tag}.png"))
        path, _ = QFileDialog.getSaveFileName(
            self, "PNG保存", str(default), "PNG files (*.png)"
        )
        if path:
            try:
                self._canvas.save_png(path)
                self._sb.showMessage(f"保存完了: {path}")
            except Exception as e:
                self._err(e)

    # ----------------------------------------------------------
    # ユーティリティ
    # ----------------------------------------------------------

    def _check_loaded(self) -> bool:
        if self._mat is None:
            QMessageBox.warning(self, "未読み込み", "先にファイルを読み込んでください")
            return False
        return True

    def _err(self, e: Exception):
        QMessageBox.critical(self, "エラー", str(e))
        self._sb.showMessage(f"エラー: {e}")


# ============================================================
# エントリポイント
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="PineOak 統計解析モジュール")
    parser.add_argument("--file", default="", help="matファイルパス")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = StatsWindow(mat_path=args.file or None)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
