#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
grainmap_tj_gui.py — 座標テーブル読み込み型の粒番号マップ表示・TJ対応付けGUI (DIC ↔ EBSD)

主な機能
- DIC/EBSD の CSV/TSV を読み込み（文字コード自動推定、区切り自動判定）
- X/Y/Grain ID 列を指定して「粒番号マップ」をラスタ化して表示
- 粒界三重点(TJ)を自動検出（2x2ブロックに3相以上）
- DIC 側→EBSD 側の順でクリックして対応点(GCP)を登録（TJにスナップ可）
- GCP を CSV で保存
- 簡易アフィンでのチェック用チェッカーボード重ね合わせプレビュー

修正点（この版）
- 粒番号列に NaN が含まれても落ちないように、Series のまま notna() でマスクしてから int 化
- 区切りを自動判定（sep=None, engine="python"）
"""

from __future__ import annotations
import os, csv, math
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

import numpy as np
import pandas as pd

from PyQt5 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from scipy.spatial import cKDTree

# ---------- ユーティリティ ----------

def normalize_cols(cols):
    out = []
    for c in cols:
        s = str(c).strip().replace('\u3000',' ')
        out.append(s)
    return out

def guess_encoding(path):
    for enc in ["utf-8-sig","utf-8","cp932","shift_jis","euc_jp"]:
        try:
            pd.read_csv(path, encoding=enc, nrows=1)
            return enc
        except Exception:
            continue
    return "utf-8"

def read_table(path):
    enc = guess_encoding(path)
    # 区切りは常に自動判定（CSV/TSV/セミコロン対応）
    df = pd.read_csv(path, encoding=enc, sep=None, engine="python")
    df.columns = normalize_cols(df.columns)
    return df, enc

def median_grid_step(xy: np.ndarray) -> float:
    if len(xy) < 5:
        return max(1.0, np.ptp(xy[:,0])/128 if len(xy)>0 else 1.0)
    tree = cKDTree(xy)
    d, _ = tree.query(xy, k=2)
    med = np.median(d[:,1])
    if not np.isfinite(med) or med <= 0:
        med = max(np.ptp(xy[:,0])/256, 1.0)
    return float(med)

def rasterize_labels(xy: np.ndarray, labels: np.ndarray, cell: float, margin: float=1.0):
    x_min, y_min = xy.min(axis=0) - margin*cell
    x_max, y_max = xy.max(axis=0) + margin*cell
    W = int(math.ceil((x_max - x_min)/cell))
    H = int(math.ceil((y_max - y_min)/cell))
    W = max(W, 1); H = max(H, 1)
    grid = np.full((H, W), -1, dtype=np.int32)
    ix = np.clip(((xy[:,0]-x_min)/cell).astype(int), 0, W-1)
    iy = np.clip(((xy[:,1]-y_min)/cell).astype(int), 0, H-1)
    from collections import defaultdict
    buckets: Dict[Tuple[int,int], Dict[int,int]] = defaultdict(lambda: defaultdict(int))
    for i in range(len(xy)):
        buckets[(iy[i], ix[i])][int(labels[i])] += 1
    for (r,c), cnts in buckets.items():
        lab = max(cnts.items(), key=lambda kv: kv[1])[0]
        grid[r,c] = lab
    return grid, x_min, y_min, cell

def detect_tj_from_label_grid(grid: np.ndarray):
    H, W = grid.shape
    tjs = []
    for r in range(H-1):
        for c in range(W-1):
            block = grid[r:r+2, c:c+2].ravel()
            s = set(int(v) for v in block if v >= 0)
            if len(s) >= 3:
                tjs.append((r+0.5, c+0.5))
    return np.array(tjs, dtype=float) if tjs else np.zeros((0,2), dtype=float)

def label_to_rgb(grid: np.ndarray):
    H,W = grid.shape
    rgb = np.zeros((H,W,3), dtype=np.uint8)
    vals = np.unique(grid[grid>=0])
    if len(vals)==0:
        return rgb
    import colorsys
    for v in vals:
        hv = (hash(int(v)) % 360)/360.0
        r,g,b = colorsys.hsv_to_rgb(hv, 0.65, 1.0)
        rgb[grid==v] = (int(r*255), int(g*255), int(b*255))
    return rgb

# ---------- モデル ----------

@dataclass
class GCP:
    id: int
    dic_x: float
    dic_y: float
    ebsd_x: float
    ebsd_y: float
    weight: float = 1.0
    note: str = ""

# ---------- ビュー ----------

class ImageAxes(FigureCanvas):
    clicked = QtCore.pyqtSignal(float, float)
    def __init__(self, parent=None):
        fig = Figure(figsize=(5, 5), dpi=100)
        self.ax = fig.add_subplot(111)
        super().__init__(fig)
        self.setParent(parent)
        self.img = None
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.mpl_connect('button_press_event', self._on_click)

    def set_image(self, img: np.ndarray, title:str=""):
        self.ax.clear(); self.img = img
        if img.ndim==2:
            self.ax.imshow(img, cmap="gray", origin="upper", interpolation="nearest")
        else:
            self.ax.imshow(img, origin="upper", interpolation="nearest")
        self.ax.set_title(title); self.ax.set_xticks([]); self.ax.set_yticks([]); self.draw()

    def scatter_points(self, pts_xy: np.ndarray, color="C1", s=24, marker="x"):
        if self.img is None or len(pts_xy)==0: return
        xs = pts_xy[:,1]; ys = pts_xy[:,0]
        self.ax.scatter(xs, ys, s=s, facecolors='none', edgecolors=color, marker=marker, linewidths=1.0)
        self.draw()

    def _on_click(self, event):
        if event.inaxes != self.ax or self.img is None: return
        self.clicked.emit(event.xdata, event.ydata)

# ---------- コントローラ/アプリ ----------

class GrainTJGui(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Grain Map TJ Picker — 座標テーブル対応付けGUI (DIC ↔ EBSD)")
        self.resize(1400, 800)

        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        hsplit = QtWidgets.QHBoxLayout(central)

        self.view_dic = ImageAxes(self)
        self.view_ebsd = ImageAxes(self)

        side = QtWidgets.QTabWidget()

        # ---- IO/設定タブ
        tab_io = QtWidgets.QWidget(); tab_layout = QtWidgets.QFormLayout(tab_io)

        self.btn_load_dic = QtWidgets.QPushButton("DIC CSV を開く…")
        self.btn_load_dic.clicked.connect(self.load_dic)
        self.btn_load_ebsd = QtWidgets.QPushButton("EBSD CSV を開く…")
        self.btn_load_ebsd.clicked.connect(self.load_ebsd)

        self.cb_dic_x = QtWidgets.QComboBox()
        self.cb_dic_y = QtWidgets.QComboBox()
        self.cb_dic_gid = QtWidgets.QComboBox()
        self.cb_ebsd_x = QtWidgets.QComboBox()
        self.cb_ebsd_y = QtWidgets.QComboBox()
        self.cb_ebsd_gid = QtWidgets.QComboBox()
        for cb in (self.cb_dic_x, self.cb_dic_y, self.cb_dic_gid, self.cb_ebsd_x, self.cb_ebsd_y, self.cb_ebsd_gid):
            cb.setEditable(True)

        self.sb_cell = QtWidgets.QDoubleSpinBox(); self.sb_cell.setRange(0.1, 1e6); self.sb_cell.setValue(1.0); self.sb_cell.setDecimals(3)
        self.btn_auto_cell = QtWidgets.QPushButton("グリッド間隔を自動推定")
        self.btn_auto_cell.clicked.connect(self.auto_cell_estimate)

        self.chk_tj = QtWidgets.QCheckBox("TJ候補を表示・スナップ有効"); self.chk_tj.setChecked(True)
        self.sb_snap = QtWidgets.QDoubleSpinBox(); self.sb_snap.setRange(0.0, 1000.0); self.sb_snap.setValue(5.0); self.sb_snap.setDecimals(2); self.sb_snap.setSuffix(" px")

        self.btn_render = QtWidgets.QPushButton("粒番号マップを描画")
        self.btn_render.clicked.connect(self.render_maps)

        self.btn_preview = QtWidgets.QPushButton("チェッカーボード重ね合わせ")
        self.btn_preview.clicked.connect(self.preview_overlay)

        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["id","dic_x","dic_y","ebsd_x","ebsd_y","weight","note"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        self.btn_del = QtWidgets.QPushButton("選択行を削除"); self.btn_del.clicked.connect(self.delete_selected)
        self.btn_save = QtWidgets.QPushButton("GCPペアをCSV保存…"); self.btn_save.clicked.connect(self.save_csv)
        self.lbl_pairs = QtWidgets.QLabel("対応点: 0 ペア")

        tab_layout.addRow(self.btn_load_dic)
        tab_layout.addRow(self.btn_load_ebsd)
        tab_layout.addRow(QtWidgets.QLabel("DIC: x / y / grain_id"))
        tab_layout.addRow(self.cb_dic_x); tab_layout.addRow(self.cb_dic_y); tab_layout.addRow(self.cb_dic_gid)
        tab_layout.addRow(QtWidgets.QLabel("EBSD: x / y / grain_id"))
        tab_layout.addRow(self.cb_ebsd_x); tab_layout.addRow(self.cb_ebsd_y); tab_layout.addRow(self.cb_ebsd_gid)
        tab_layout.addRow(QtWidgets.QLabel("グリッド間隔 (px or µm)"))
        tab_layout.addRow(self.sb_cell); tab_layout.addRow(self.btn_auto_cell)
        tab_layout.addRow(self.chk_tj)
        tab_layout.addRow(QtWidgets.QLabel("スナップ半径"))
        tab_layout.addRow(self.sb_snap)
        tab_layout.addRow(self.btn_render)
        tab_layout.addRow(self.btn_preview)
        tab_layout.addRow(self.lbl_pairs)

        side.addTab(tab_io, "入出力 & 設定")

        # ---- 対応点タブ
        tab_pairs = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(tab_pairs)
        pl.addWidget(self.table); pl.addWidget(self.btn_del); pl.addWidget(self.btn_save)
        side.addTab(tab_pairs, "対応点リスト")

        hsplit.addWidget(self.view_dic, 5)
        hsplit.addWidget(self.view_ebsd, 5)
        hsplit.addWidget(side, 4)

        # 状態
        self.dic_df: Optional[pd.DataFrame] = None
        self.ebsd_df: Optional[pd.DataFrame] = None
        self.dic_grid = None; self.ebsd_grid = None
        self.dic_origin = (0,0,1); self.ebsd_origin = (0,0,1)
        self.tj_dic = np.zeros((0,2)); self.tj_ebsd = np.zeros((0,2))
        self.pending_dic = None

        # イベント
        self.view_dic.clicked.connect(self.on_click_dic)
        self.view_ebsd.clicked.connect(self.on_click_ebsd)
        QtWidgets.QShortcut(QtGui.QKeySequence("Delete"), self, activated=self.delete_selected)

    # ---------- ロード ----------
    def load_dic(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "DIC CSVを開く", "", "CSV/TSV (*.csv *.tsv *.txt);;All (*)")
        if not path: return
        df, _ = read_table(path)
        self.dic_df = df
        self.fill_role_boxes(df.columns, is_dic=True)

    def load_ebsd(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "EBSD CSVを開く", "", "CSV/TSV (*.csv *.tsv *.txt);;All (*)")
        if not path: return
        df, _ = read_table(path)
        self.ebsd_df = df
        self.fill_role_boxes(df.columns, is_dic=False)

    def fill_role_boxes(self, cols, is_dic=True):
        targets = (self.cb_dic_x, self.cb_dic_y, self.cb_dic_gid) if is_dic else (self.cb_ebsd_x, self.cb_ebsd_y, self.cb_ebsd_gid)
        for cb in targets:
            cb.clear(); cb.addItems(cols)
        low = [c.lower() for c in cols]
        def find(cands):
            for i,c in enumerate(low):
                for pat in cands:
                    if pat in c:
                        return i
            return 0
        if is_dic:
            targets[0].setCurrentIndex(find(["x","x_pix","x_um","easting"]))
            targets[1].setCurrentIndex(find(["y","y_pix","y_um","northing"]))
            targets[2].setCurrentIndex(find(["grain","grain_id","grain number","grain_number","grains"]))
        else:
            targets[0].setCurrentIndex(find(["x","easting"]))
            targets[1].setCurrentIndex(find(["y","northing"]))
            targets[2].setCurrentIndex(find(["grain","grain_id","grain number","grain_number","grains"]))

    # ---------- ユーティリティ ----------
    def auto_cell_estimate(self):
        if self.dic_df is None: return
        x = pd.to_numeric(self.dic_df[self.cb_dic_x.currentText()], errors="coerce")
        y = pd.to_numeric(self.dic_df[self.cb_dic_y.currentText()], errors="coerce")
        m = x.notna() & y.notna()
        xy = np.column_stack([x[m].to_numpy(), y[m].to_numpy()])
        if len(xy)==0:
            QtWidgets.QMessageBox.warning(self, "推定失敗", "数値のX/Yが見つかりません"); return
        step = median_grid_step(xy)
        self.sb_cell.setValue(round(float(step),3))

    # ---------- 描画 ----------
    def render_maps(self):
        if self.dic_df is None or self.ebsd_df is None:
            QtWidgets.QMessageBox.warning(self, "描画", "DIC と EBSD の両方を読み込んでください")
            return
        cell = self.sb_cell.value()

        # --- DIC 側（NaN安全・Seriesでマスク→配列化） ---
        dx = pd.to_numeric(self.dic_df[self.cb_dic_x.currentText()], errors="coerce")
        dy = pd.to_numeric(self.dic_df[self.cb_dic_y.currentText()], errors="coerce")
        dg = pd.to_numeric(self.dic_df[self.cb_dic_gid.currentText()], errors="coerce").astype("Int64")
        m = dx.notna() & dy.notna() & dg.notna()
        dx = dx[m].to_numpy(); dy = dy[m].to_numpy(); dg = dg[m].astype(int).to_numpy()

        # --- EBSD 側（NaN安全） ---
        ex = pd.to_numeric(self.ebsd_df[self.cb_ebsd_x.currentText()], errors="coerce")
        ey = pd.to_numeric(self.ebsd_df[self.cb_ebsd_y.currentText()], errors="coerce")
        eg = pd.to_numeric(self.ebsd_df[self.cb_ebsd_gid.currentText()], errors="coerce").astype("Int64")
        m2 = ex.notna() & ey.notna() & eg.notna()
        ex = ex[m2].to_numpy(); ey = ey[m2].to_numpy(); eg = eg[m2].astype(int).to_numpy()

        if len(dx)==0 or len(ex)==0:
            QtWidgets.QMessageBox.warning(self, "描画", "有効な数値データが見つかりません"); return

        dic_grid, ox, oy, cc = rasterize_labels(np.column_stack([dx,dy]), dg, cell)
        self.dic_grid = dic_grid; self.dic_origin = (ox, oy, cc); self.tj_dic = detect_tj_from_label_grid(dic_grid)

        ebsd_grid, ox2, oy2, cc2 = rasterize_labels(np.column_stack([ex,ey]), eg, cell)
        self.ebsd_grid = ebsd_grid; self.ebsd_origin = (ox2, oy2, cc2); self.tj_ebsd = detect_tj_from_label_grid(ebsd_grid)

        self.view_dic.set_image(label_to_rgb(dic_grid), title="DIC: 粒番号マップ")
        if self.chk_tj.isChecked(): self.view_dic.scatter_points(self.tj_dic, color="C0", s=18, marker="x")
        self.view_ebsd.set_image(label_to_rgb(ebsd_grid), title="EBSD: 粒番号マップ")
        if self.chk_tj.isChecked(): self.view_ebsd.scatter_points(self.tj_ebsd, color="C3", s=18, marker="x")

    # ---------- クリック ----------
    def snap_to_tj(self, view: str, x: float, y: float) -> Tuple[float,float]:
        if not self.chk_tj.isChecked(): return x,y
        r = self.sb_snap.value()
        pts = self.tj_dic if view=="dic" else self.tj_ebsd
        if pts is None or len(pts)==0: return x,y
        q = np.array([y, x])
        tree = cKDTree(pts); d, idx = tree.query(q, k=1)
        if np.isfinite(d) and d <= r:
            rr, cc = pts[idx]; return cc, rr
        return x,y

    def on_click_dic(self, x: float, y: float):
        if self.dic_grid is None: return
        x2,y2 = self.snap_to_tj("dic", x, y); self.pending_dic = (x2,y2)
        self.view_dic.ax.scatter([x2],[y2], s=40, facecolors='none', edgecolors="yellow", marker="o", linewidths=1.5)
        self.view_dic.draw()

    def on_click_ebsd(self, x: float, y: float):
        if self.ebsd_grid is None: return
        if self.pending_dic is None:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "先に左(DIC)でクリックしてください"); return
        x2,y2 = self.snap_to_tj("ebsd", x, y)
        dic_x, dic_y = self.pending_dic; self.pending_dic = None
        self.view_ebsd.ax.scatter([x2],[y2], s=40, facecolors='none', edgecolors="yellow", marker="o", linewidths=1.5)
        self.view_ebsd.draw()
        self.add_pair(dic_x, dic_y, x2, y2, weight=1.0, note="")

    # ---------- 対応点テーブル ----------
    def add_pair(self, dic_x, dic_y, ebsd_x, ebsd_y, weight=1.0, note=""):
        r = self.table.rowCount(); self.table.insertRow(r)
        vals = [r+1, dic_x, dic_y, ebsd_x, ebsd_y, weight, note]
        for c,v in enumerate(vals):
            it = QtWidgets.QTableWidgetItem(str(v))
            if c==0: it.setFlags(it.flags() ^ QtCore.Qt.ItemIsEditable)
            self.table.setItem(r,c,it)
        self.lbl_pairs.setText(f"対応点: {self.table.rowCount()} ペア")

    def delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows: self.table.removeRow(r)
        for r in range(self.table.rowCount()):
            self.table.item(r,0).setText(str(r+1))
        self.lbl_pairs.setText(f"対応点: {self.table.rowCount()} ペア")

    def get_pairs(self) -> List[GCP]:
        out = []
        for r in range(self.table.rowCount()):
            id_ = int(float(self.table.item(r,0).text()))
            dic_x = float(self.table.item(r,1).text()); dic_y = float(self.table.item(r,2).text())
            ebsd_x = float(self.table.item(r,3).text()); ebsd_y = float(self.table.item(r,4).text())
            weight = float(self.table.item(r,5).text())
            note = self.table.item(r,6).text() if self.table.item(r,6) else ""
            out.append(GCP(id_, dic_x, dic_y, ebsd_x, ebsd_y, weight, note))
        return out

    def save_csv(self):
        pairs = self.get_pairs()
        if not pairs:
            QtWidgets.QMessageBox.warning(self, "保存", "対応点がありません"); return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "GCPペアを保存", "gcp_pairs.csv", "CSV (*.csv)")
        if not path: return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["id","dic_x","dic_y","ebsd_x","ebsd_y","weight","note"])
            for g in pairs:
                w.writerow([g.id, g.dic_x, g.dic_y, g.ebsd_x, g.ebsd_y, g.weight, g.note])
        QtWidgets.QMessageBox.information(self, "保存", f"保存しました:\n{path}")

    def preview_overlay(self):
        if self.dic_grid is None or self.ebsd_grid is None:
            QtWidgets.QMessageBox.warning(self, "プレビュー", "まず粒番号マップを描画してください"); return
        pairs = self.get_pairs()
        if len(pairs) < 3:
            QtWidgets.QMessageBox.warning(self, "プレビュー", "プレビューには最低3点の対応が必要です"); return

        def img_to_grid_coords(x, y, origin):
            ox, oy, cell = origin; c = (x - ox)/cell; r = (y - oy)/cell; return c, r

        # 最小二乗のアフィン（簡易プレビュー）
        src = []; dst = []
        for g in pairs:
            c_src, r_src = img_to_grid_coords(g.ebsd_x, g.ebsd_y, self.ebsd_origin)
            c_dst, r_dst = img_to_grid_coords(g.dic_x,  g.dic_y,  self.dic_origin)
            src.append([c_src, r_src, 1.0]); dst.append([c_dst, r_dst])
        src = np.asarray(src, float); dst = np.asarray(dst, float)
        Wls, *_ = np.linalg.lstsq(src, dst, rcond=None); A = Wls.T

        H, Wd = self.dic_grid.shape
        yy, xx = np.indices((H, Wd)); XY1 = np.stack([xx, yy, np.ones_like(xx)], axis=-1).reshape(-1,3)
        M = np.vstack([A, [0,0,1]])
        try:
            Minv = np.linalg.inv(M)
        except np.linalg.LinAlgError:
            QtWidgets.QMessageBox.warning(self, "プレビュー", "アフィン行列が特異です"); return

        src_xy1 = XY1 @ Minv.T
        sx = src_xy1[:,0].reshape(H,Wd); sy = src_xy1[:,1].reshape(H,Wd)
        sx_n = np.clip(np.rint(sx).astype(int), 0, self.ebsd_grid.shape[1]-1)
        sy_n = np.clip(np.rint(sy).astype(int), 0, self.ebsd_grid.shape[0]-1)
        warped = self.ebsd_grid[sy_n, sx_n]

        tile = 16; cb = (((xx // tile) + (yy // tile)) % 2 == 0)
        dic_rgb = label_to_rgb(self.dic_grid); ebs_rgb = label_to_rgb(warped)
        comp_cb = np.where(cb[...,None], dic_rgb, ebs_rgb)

        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("チェッカーボードプレビュー")
        v = QtWidgets.QVBoxLayout(dlg)
        fig = Figure(figsize=(6,6), dpi=100); canvas = FigureCanvas(fig); ax = fig.add_subplot(111)
        ax.imshow(comp_cb, origin="upper"); ax.set_xticks([]); ax.set_yticks([])
        v.addWidget(canvas); dlg.resize(720,720); dlg.exec_()

def main():
    import sys
    app = QtWidgets.QApplication(sys.argv)
    w = GrainTJGui(); w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
