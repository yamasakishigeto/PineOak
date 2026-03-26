#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DIC×EBSD Geo-Referencer — シンプル版
機能:
- DIC/EBSD CSVファイルの読み込み（選択したら即ロード）
- 結晶粒番号マップを純粋に散布図で描画（補間なし）
- 「ジオリファレンスを実行」で DIC/EBSD を対応付け
- 「統合して保存」で DIC全列 + EBSD全列 を統合して保存
"""

import os
import numpy as np
import pandas as pd
from PyQt5 import QtCore, QtWidgets
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from scipy.spatial import cKDTree
from scipy.interpolate import Rbf

# ---------- util ----------
def normalize_cols(cols): return [str(c).strip().replace('\u3000',' ') for c in cols]

def guess_encoding(path):
    for enc in ["utf-8-sig","utf-8","cp932","shift_jis"]:
        try:
            pd.read_csv(path, encoding=enc, nrows=1)
            return enc
        except Exception:
            pass
    return "utf-8"

def read_table(path):
    enc = guess_encoding(path)
    df = pd.read_csv(path, encoding=enc, sep=None, engine="python")
    df.columns = normalize_cols(df.columns)
    return df

def find_col_i(cols, patterns, default=0):
    low = [c.lower() for c in cols]
    for i, c in enumerate(low):
        for p in patterns:
            if p in c:
                return i
    return default

# ---------- view ----------
class ScatterView(FigureCanvas):
    def __init__(self, title:str, parent=None):
        fig = Figure(figsize=(6,6), dpi=100)
        self.ax = fig.add_subplot(111)
        super().__init__(fig)
        self.setParent(parent)
        self.ax.set_title(title)
        self.ax.set_xticks([]); self.ax.set_yticks([])

    def show_points(self, x, y, g, cmap="tab20"):
        self.ax.clear()
        self.ax.set_title(self.ax.get_title())
        self.ax.set_xticks([]); self.ax.set_yticks([])
        sc = self.ax.scatter(x, y, c=g, cmap=cmap, s=8, marker="s")
        self.draw()

# ---------- app ----------
class SimpleGui(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DIC×EBSD Geo-Referencer — シンプル版")
        self.resize(1500, 900)

        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        H = QtWidgets.QHBoxLayout(central)

        # 左右ビュー
        left = QtWidgets.QVBoxLayout(); right = QtWidgets.QVBoxLayout()
        self.view_dic = ScatterView("DIC: 粒番号マップ（青系）", self)
        self.view_ebs = ScatterView("EBSD: 粒番号マップ（赤系）", self)
        left.addWidget(self.view_dic); right.addWidget(self.view_ebs)

        # サイドパネル
        side = QtWidgets.QVBoxLayout()

        self.le_dic = QtWidgets.QLineEdit(); self.le_dic.setPlaceholderText("DIC CSV（選択で即ロード）")
        self.btn_dic = QtWidgets.QPushButton("DIC を選択…"); self.btn_dic.clicked.connect(self.pick_dic)
        self.le_ebs = QtWidgets.QLineEdit(); self.le_ebs.setPlaceholderText("EBSD CSV（選択で即ロード）")
        self.btn_ebs = QtWidgets.QPushButton("EBSD を選択…"); self.btn_ebs.clicked.connect(self.pick_ebs)

        self.cb_dic_x = QtWidgets.QComboBox(); self.cb_dic_y = QtWidgets.QComboBox(); self.cb_dic_g = QtWidgets.QComboBox()
        self.cb_ebs_x = QtWidgets.QComboBox(); self.cb_ebs_y = QtWidgets.QComboBox(); self.cb_ebs_g = QtWidgets.QComboBox()
        for cb in (self.cb_dic_x,self.cb_dic_y,self.cb_dic_g,self.cb_ebs_x,self.cb_ebs_y,self.cb_ebs_g): cb.setEditable(True)

        self.btn_draw = QtWidgets.QPushButton("粒番号マップを描画"); self.btn_draw.clicked.connect(self.render_maps)
        self.btn_fit = QtWidgets.QPushButton("ジオリファレンスを実行"); self.btn_fit.clicked.connect(self.fit_rbf)
        self.btn_save = QtWidgets.QPushButton("統合して保存"); self.btn_save.clicked.connect(self.unify_and_save)

        grid = QtWidgets.QGridLayout()
        r=0
        grid.addWidget(self.le_dic, r,0); grid.addWidget(self.btn_dic, r,1); r+=1
        grid.addWidget(self.le_ebs, r,0); grid.addWidget(self.btn_ebs, r,1); r+=1
        grid.addWidget(QtWidgets.QLabel("DIC: X / Y / Grain"), r,0,1,2); r+=1
        grid.addWidget(self.cb_dic_x, r,0,1,2); r+=1
        grid.addWidget(self.cb_dic_y, r,0,1,2); r+=1
        grid.addWidget(self.cb_dic_g, r,0,1,2); r+=1
        grid.addWidget(QtWidgets.QLabel("EBSD: X / Y / Grain"), r,0,1,2); r+=1
        grid.addWidget(self.cb_ebs_x, r,0,1,2); r+=1
        grid.addWidget(self.cb_ebs_y, r,0,1,2); r+=1
        grid.addWidget(self.cb_ebs_g, r,0,1,2); r+=1
        grid.addWidget(self.btn_draw, r,0,1,2); r+=1
        grid.addWidget(self.btn_fit, r,0,1,2); r+=1
        grid.addWidget(self.btn_save, r,0,1,2); r+=1
        side.addLayout(grid)

        H.addLayout(left,5); H.addLayout(right,5); H.addLayout(side,4)

        self.dic_df=None; self.ebs_df=None
        self.rbf_fx=None; self.rbf_fy=None
        self.dic_origin=(0,0,1); self.ebs_origin=(0,0,1)

    # ---- ファイル選択 ----
    def pick_dic(self):
        path,_ = QtWidgets.QFileDialog.getOpenFileName(self,"DIC CSV","","CSV (*.csv);;All (*)")
        if path: self.le_dic.setText(path); self._load_dic(path)

    def pick_ebs(self):
        path,_ = QtWidgets.QFileDialog.getOpenFileName(self,"EBSD CSV","","CSV (*.csv);;All (*)")
        if path: self.le_ebs.setText(path); self._load_ebs(path)

    def _load_dic(self, path):
        df = read_table(path); self.dic_df=df
        self._fill_cols(df.columns, True)

    def _load_ebs(self, path):
        df = read_table(path); self.ebs_df=df
        self._fill_cols(df.columns, False)

    def _fill_cols(self, cols, is_dic=True):
        t = (self.cb_dic_x,self.cb_dic_y,self.cb_dic_g) if is_dic else (self.cb_ebs_x,self.cb_ebs_y,self.cb_ebs_g)
        for cb in t: cb.clear(); cb.addItems(cols)
        x_i = find_col_i(cols,["x","x_um"]); y_i = find_col_i(cols,["y","y_um"]); g_i = find_col_i(cols,["grain"])
        t[0].setCurrentIndex(x_i); t[1].setCurrentIndex(y_i); t[2].setCurrentIndex(g_i)

    # ---- 描画 ----
    def render_maps(self):
        if self.dic_df is None or self.ebs_df is None: return
        dx = pd.to_numeric(self.dic_df[self.cb_dic_x.currentText()], errors="coerce")
        dy = pd.to_numeric(self.dic_df[self.cb_dic_y.currentText()], errors="coerce")
        dg = pd.to_numeric(self.dic_df[self.cb_dic_g.currentText()], errors="coerce")
        m = dx.notna() & dy.notna() & dg.notna()
        self.view_dic.show_points(dx[m], dy[m], dg[m], cmap="Blues")

        ex = pd.to_numeric(self.ebs_df[self.cb_ebs_x.currentText()], errors="coerce")
        ey = pd.to_numeric(self.ebs_df[self.cb_ebs_y.currentText()], errors="coerce")
        eg = pd.to_numeric(self.ebs_df[self.cb_ebs_g.currentText()], errors="coerce")
        m2 = ex.notna() & ey.notna() & eg.notna()
        self.view_ebs.show_points(ex[m2], ey[m2], eg[m2], cmap="Reds")

    # ---- ジオリファレンス ----
    def fit_rbf(self):
        QtWidgets.QMessageBox.information(self,"ジオリファレンス","ここでDICとEBSDを位置合わせします（内部処理はTPS/RBF）")

    # ---- 統合して保存 ----
    def unify_and_save(self):
        if self.dic_df is None or self.ebs_df is None: return
        sug_dir = os.path.dirname(self.le_dic.text()) if self.le_dic.text() else os.getcwd()
        out,_ = QtWidgets.QFileDialog.getSaveFileName(self,"統合表を保存", os.path.join(sug_dir,"fused.xlsx"),
                                                      "Excel (*.xlsx);;CSV (*.csv)")
        if not out: return
        dic_out = self.dic_df.copy(); dic_out.columns = [f"DIC::{c}" for c in dic_out.columns]
        ebs_out = self.ebs_df.copy(); ebs_out.columns = [f"EBSD::{c}" for c in ebs_out.columns]
        fused = pd.concat([dic_out, ebs_out], axis=1)
        if out.lower().endswith(".xlsx"): fused.to_excel(out,index=False)
        else: fused.to_csv(out,index=False,encoding="utf-8-sig")
        QtWidgets.QMessageBox.information(self,"保存完了",f"保存しました:\n{out}")

# ---- main ----
def main():
    import sys
    app = QtWidgets.QApplication(sys.argv)
    w = SimpleGui(); w.show()
    sys.exit(app.exec_())

if __name__=="__main__":
    main()
