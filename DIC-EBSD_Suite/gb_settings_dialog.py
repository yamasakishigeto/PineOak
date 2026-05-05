"""
gb_settings_dialog.py
=====================
粒界定義ごとの詳細設定ダイアログ。
stats_analysis と stress_strain_mapper の両方から呼び出す。

使い方:
    dlg = GBSettingsDialog(definition_key, current_params, parent=self)
    if dlg.exec():
        params = dlg.get_params()
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout,
    QLabel, QDoubleSpinBox, QComboBox, QCheckBox,
    QDialogButtonBox, QWidget, QLineEdit,
)

from gb_definitions import ALL_DEFINITIONS, DEFINITION_MAP, SYM_GROUPS


# ============================================================
# ベースダイアログ
# ============================================================

class GBSettingsDialog(QDialog):
    """粒界定義の詳細設定ダイアログ。

    Parameters
    ----------
    definition_key  : str   gb_definitions.DEFINITION_MAP のキー
    current_params  : dict  現在のパラメータ（空 dict でも可）
    parent          : QWidget or None
    """

    def __init__(self, definition_key: str, current_params: dict, parent=None):
        super().__init__(parent)
        self._key    = definition_key
        self._params = dict(current_params)

        defn = DEFINITION_MAP[definition_key]
        # デフォルト値で未設定キーを補完
        for k, v in defn.default_params.items():
            self._params.setdefault(k, v)

        self.setWindowTitle(f"粒界設定 — {defn.label}")
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        form_widget = self._build_form(definition_key)
        root.addWidget(form_widget)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._apply_style()

    # ----------------------------------------------------------
    # フォーム構築（定義別）
    # ----------------------------------------------------------

    def _build_form(self, key: str) -> QWidget:
        if key == "grain_id":
            return self._form_grain_id()
        elif key == "misorientation":
            return self._form_misorientation()
        elif key == "m_prime":
            return self._form_m_prime()
        else:
            lbl = QLabel(f"定義 '{key}' の設定項目はありません。")
            return lbl

    def _form_grain_id(self) -> QWidget:
        w = QWidget()
        from PyQt6.QtWidgets import QVBoxLayout
        l = QVBoxLayout(w)
        l.addWidget(QLabel("grain_id が異なる隣接点を粒界とみなします。\n追加設定はありません。"))
        return w

    def _form_m_prime(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._mp_threshold = QDoubleSpinBox()
        self._mp_threshold.setRange(0.0, 1.0)
        self._mp_threshold.setSingleStep(0.05)
        self._mp_threshold.setDecimals(2)
        self._mp_threshold.setValue(float(self._params.get('threshold', 0.8)))
        fl.addRow("m' 閾値（≤ threshold → 境界）\n※FCC: min≈0.72、0.80=最困難4%、0.90=約半数:", self._mp_threshold)

        self._mp_plane = QLineEdit(str(self._params.get('slip_plane', '1 1 1')))
        self._mp_plane.setPlaceholderText("例: 1 1 1（FCC）/ 0 0 0 1（HCP）")
        fl.addRow("すべり面（Miller 指数）:", self._mp_plane)

        self._mp_dir = QLineEdit(str(self._params.get('slip_dir', '1 -1 0')))
        self._mp_dir.setPlaceholderText("例: 1 -1 0（FCC）/ 1 1 -2 0（HCP）")
        fl.addRow("すべり方向（Miller 指数）:", self._mp_dir)

        self._mp_src = QComboBox()
        self._mp_src.addItem("参照ステージ（phi1_ref / PHI_ref / phi2_ref）", "ref")
        self._mp_src.addItem("各ステージ（euler_phi1/phi/phi2_sXXX）",        "stage")
        idx = 1 if self._params.get('euler_source', 'ref') == 'stage' else 0
        self._mp_src.setCurrentIndex(idx)
        fl.addRow("オイラー角ソース:", self._mp_src)

        return w

    def _form_misorientation(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._mis_theta = QDoubleSpinBox()
        self._mis_theta.setRange(0.1, 180.0)
        self._mis_theta.setSingleStep(1.0)
        self._mis_theta.setDecimals(1)
        self._mis_theta.setSuffix(" °")
        self._mis_theta.setValue(float(self._params.get('theta_deg', 15.0)))
        fl.addRow("ミスオリエンテーション閾値 θ:", self._mis_theta)

        self._mis_src = QComboBox()
        self._mis_src.addItem("参照ステージ（phi1_ref / PHI_ref / phi2_ref）", "ref")
        self._mis_src.addItem("各ステージ（euler_phi1/phi/phi2_sXXX）",        "stage")
        idx = 1 if self._params.get('euler_source', 'ref') == 'stage' else 0
        self._mis_src.setCurrentIndex(idx)
        fl.addRow("オイラー角ソース:", self._mis_src)

        self._mis_same = QCheckBox("同じ相(phase_index)間のみ評価する")
        self._mis_same.setChecked(bool(self._params.get('same_phase_only', False)))
        fl.addRow("", self._mis_same)

        return w

    # ----------------------------------------------------------
    # OK ハンドラ
    # ----------------------------------------------------------

    def _on_accept(self):
        """UIから現在値を収集して _params に格納する。"""
        if self._key == "misorientation":
            self._params['theta_deg']       = self._mis_theta.value()
            self._params['euler_source']    = self._mis_src.currentData()
            self._params['same_phase_only'] = self._mis_same.isChecked()
        elif self._key == "m_prime":
            self._params['threshold']   = self._mp_threshold.value()
            self._params['slip_plane']  = self._mp_plane.text().strip()
            self._params['slip_dir']    = self._mp_dir.text().strip()
            self._params['euler_source'] = self._mp_src.currentData()
        self.accept()

    def get_params(self) -> dict:
        """ダイアログで設定したパラメータ辞書を返す。"""
        return dict(self._params)

    # ----------------------------------------------------------
    # スタイル
    # ----------------------------------------------------------

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog, QWidget          { background:#1a1d24; color:#e0e4ec; font-size:12px; }
            QComboBox, QDoubleSpinBox, QSpinBox {
                                        background:#111318; border:1px solid #2a2d35;
                                        color:#e0e4ec; padding:3px 6px; border-radius:3px; }
            QComboBox::drop-down      { border:none; }
            QComboBox QAbstractItemView { background:#1a1d24; color:#e0e4ec;
                                        selection-background-color:#00d4ff;
                                        selection-color:#000; }
            QPushButton               { background:#111318; border:1px solid #2a2d35;
                                        color:#e0e4ec; padding:5px 12px; border-radius:3px; }
            QPushButton:hover         { border-color:#00d4ff; color:#00d4ff; }
            QCheckBox, QLabel         { color:#e0e4ec; }
            QDialogButtonBox QPushButton { min-width:80px; }
        """)
