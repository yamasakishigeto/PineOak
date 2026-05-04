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
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QDialogButtonBox, QGroupBox, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

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

        # 定義ごとのフォームを追加
        form_widget = self._build_form(definition_key)
        root.addWidget(form_widget)

        # OK / キャンセル
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
        elif key == "special":
            return self._form_special()
        elif key == "m_prime":
            return self._form_m_prime()
        else:
            lbl = QLabel(f"定義 '{key}' の設定項目はありません。")
            return lbl

    def _form_grain_id(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(QLabel("grain_id が異なる隣接点を粒界とみなします。\n追加設定はありません。"))
        return w

    def _form_misorientation(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        # θ 閾値
        self._mis_theta = QDoubleSpinBox()
        self._mis_theta.setRange(0.1, 180.0)
        self._mis_theta.setSingleStep(1.0)
        self._mis_theta.setDecimals(1)
        self._mis_theta.setSuffix(" °")
        self._mis_theta.setValue(float(self._params.get('theta_deg', 15.0)))
        fl.addRow("ミスオリエンテーション閾値 θ:", self._mis_theta)

        # オイラー角ソース
        self._mis_src = QComboBox()
        self._mis_src.addItem("参照ステージ（phi1_ref / PHI_ref / phi2_ref）", "ref")
        self._mis_src.addItem("各ステージ（euler_phi1/phi/phi2_sXXX）",        "stage")
        idx = 1 if self._params.get('euler_source', 'ref') == 'stage' else 0
        self._mis_src.setCurrentIndex(idx)
        fl.addRow("オイラー角ソース:", self._mis_src)

        # 同相間のみ
        self._mis_same = QCheckBox("同じ相(phase_index)間のみ評価する")
        self._mis_same.setChecked(bool(self._params.get('same_phase_only', False)))
        fl.addRow("", self._mis_same)

        return w

    def _form_special(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        axis = self._params.get('axis_uvw', [1, 1, 1])

        # 回転軸 [u v w]
        axis_row = QHBoxLayout()
        self._sp_u = QSpinBox(); self._sp_u.setRange(-20, 20); self._sp_u.setValue(int(axis[0]))
        self._sp_v = QSpinBox(); self._sp_v.setRange(-20, 20); self._sp_v.setValue(int(axis[1]))
        self._sp_w = QSpinBox(); self._sp_w.setRange(-20, 20); self._sp_w.setValue(int(axis[2]))
        for lbl, sb in [("u", self._sp_u), ("v", self._sp_v), ("w", self._sp_w)]:
            axis_row.addWidget(QLabel(lbl))
            axis_row.addWidget(sb)
        axis_row.addStretch()
        fl.addRow("回転軸 [u v w]:", axis_row)  # type: ignore[arg-type]

        # 回転角
        self._sp_angle = QDoubleSpinBox()
        self._sp_angle.setRange(0.1, 180.0)
        self._sp_angle.setSingleStep(1.0)
        self._sp_angle.setDecimals(1)
        self._sp_angle.setSuffix(" °")
        self._sp_angle.setValue(float(self._params.get('angle_deg', 60.0)))
        fl.addRow("回転角:", self._sp_angle)

        # 軸 Tolerance
        self._sp_ax_tol = QDoubleSpinBox()
        self._sp_ax_tol.setRange(0.1, 30.0)
        self._sp_ax_tol.setSingleStep(0.5)
        self._sp_ax_tol.setDecimals(1)
        self._sp_ax_tol.setSuffix(" °")
        self._sp_ax_tol.setValue(float(self._params.get('axis_tol', 5.0)))
        fl.addRow("軸 Tolerance:", self._sp_ax_tol)

        # 角度 Tolerance
        self._sp_ang_tol = QDoubleSpinBox()
        self._sp_ang_tol.setRange(0.1, 30.0)
        self._sp_ang_tol.setSingleStep(0.5)
        self._sp_ang_tol.setDecimals(1)
        self._sp_ang_tol.setSuffix(" °")
        self._sp_ang_tol.setValue(float(self._params.get('angle_tol', 5.0)))
        fl.addRow("角度 Tolerance:", self._sp_ang_tol)

        # オイラー角ソース
        self._sp_src = QComboBox()
        self._sp_src.addItem("参照ステージ（phi1_ref / PHI_ref / phi2_ref）", "ref")
        self._sp_src.addItem("各ステージ（euler_phi1/phi/phi2_sXXX）",        "stage")
        idx = 1 if self._params.get('euler_source', 'ref') == 'stage' else 0
        self._sp_src.setCurrentIndex(idx)
        fl.addRow("オイラー角ソース:", self._sp_src)

        # 同相間のみ
        self._sp_same = QCheckBox("同じ相(phase_index)間のみ評価する")
        self._sp_same.setChecked(bool(self._params.get('same_phase_only', True)))
        fl.addRow("", self._sp_same)

        return w

    def _form_m_prime(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        # m' 閾値
        self._mp_thresh = QDoubleSpinBox()
        self._mp_thresh.setRange(0.0, 1.0)
        self._mp_thresh.setSingleStep(0.05)
        self._mp_thresh.setDecimals(3)
        self._mp_thresh.setValue(float(self._params.get('threshold', 0.5)))
        fl.addRow("m' 閾値（≤ → 境界）:", self._mp_thresh)

        # 荷重軸モード
        self._mp_mode = QComboBox()
        self._mp_mode.addItem("単一方向（手動指定）",  "single")
        self._mp_mode.addItem("局所応力テンソル使用",  "local_stress")
        cur_mode = self._params.get('loading_mode', 'single')
        self._mp_mode.setCurrentIndex(0 if cur_mode == 'single' else 1)
        fl.addRow("荷重軸モード:", self._mp_mode)

        # 荷重軸 XYZ
        load_row = QHBoxLayout()
        self._mp_lx = QDoubleSpinBox(); self._mp_lx.setRange(-1,1); self._mp_lx.setSingleStep(0.1); self._mp_lx.setDecimals(3); self._mp_lx.setValue(float(self._params.get('load_x', 0.0)))
        self._mp_ly = QDoubleSpinBox(); self._mp_ly.setRange(-1,1); self._mp_ly.setSingleStep(0.1); self._mp_ly.setDecimals(3); self._mp_ly.setValue(float(self._params.get('load_y', 1.0)))
        self._mp_lz = QDoubleSpinBox(); self._mp_lz.setRange(-1,1); self._mp_lz.setSingleStep(0.1); self._mp_lz.setDecimals(3); self._mp_lz.setValue(float(self._params.get('load_z', 0.0)))
        for lbl, sb in [("X", self._mp_lx), ("Y", self._mp_ly), ("Z", self._mp_lz)]:
            load_row.addWidget(QLabel(lbl))
            load_row.addWidget(sb)
        load_row.addStretch()
        fl.addRow("荷重方向 (X, Y, Z):", load_row)  # type: ignore[arg-type]

        # オイラー角ソース
        self._mp_src = QComboBox()
        self._mp_src.addItem("参照ステージ（phi1_ref / PHI_ref / phi2_ref）", "ref")
        self._mp_src.addItem("各ステージ（euler_phi1/phi/phi2_sXXX）",        "stage")
        idx = 1 if self._params.get('euler_source', 'ref') == 'stage' else 0
        self._mp_src.setCurrentIndex(idx)
        fl.addRow("オイラー角ソース:", self._mp_src)

        note = QLabel("※ Luster-Morris m' は現在実装中です。")
        note.setStyleSheet("color: #ff6b35; font-size: 10px;")
        fl.addRow("", note)

        return w

    # ----------------------------------------------------------
    # OK ハンドラ
    # ----------------------------------------------------------

    def _on_accept(self):
        """UIから現在値を収集して _params に格納する。"""
        key = self._key
        if key == "misorientation":
            self._params['theta_deg']      = self._mis_theta.value()
            self._params['euler_source']   = self._mis_src.currentData()
            self._params['same_phase_only'] = self._mis_same.isChecked()

        elif key == "special":
            self._params['axis_uvw']       = [self._sp_u.value(), self._sp_v.value(), self._sp_w.value()]
            self._params['angle_deg']      = self._sp_angle.value()
            self._params['axis_tol']       = self._sp_ax_tol.value()
            self._params['angle_tol']      = self._sp_ang_tol.value()
            self._params['euler_source']   = self._sp_src.currentData()
            self._params['same_phase_only'] = self._sp_same.isChecked()

        elif key == "m_prime":
            self._params['threshold']      = self._mp_thresh.value()
            self._params['loading_mode']   = self._mp_mode.currentData()
            self._params['load_x']         = self._mp_lx.value()
            self._params['load_y']         = self._mp_ly.value()
            self._params['load_z']         = self._mp_lz.value()
            self._params['euler_source']   = self._mp_src.currentData()

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
            QGroupBox                 { border:1px solid #2a2d35; border-radius:4px;
                                        margin-top:8px; padding-top:4px; }
            QGroupBox::title          { color:#00d4ff; font-size:11px;
                                        subcontrol-origin:margin; left:8px; padding:0 4px; }
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
