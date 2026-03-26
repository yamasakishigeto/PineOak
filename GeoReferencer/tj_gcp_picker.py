#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, csv
from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np
from PIL import Image
from PyQt5 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

@dataclass
class GCP:
    id: int
    dic_x: float
    dic_y: float
    ebsd_x: float
    ebsd_y: float
    weight: float = 1.0
    note: str = ""

class ImageAxes(FigureCanvas):
    clicked = QtCore.pyqtSignal(float, float)
    def __init__(self, parent=None):
        fig = Figure(figsize=(5, 5), dpi=100)
        self.ax = fig.add_subplot(111)
        super().__init__(fig)
        self.setParent(parent)
        self.image = None
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.mpl_connect('button_press_event', self.on_click)
    def load_image(self, path: str):
        im = Image.open(path)
        im = im.convert('L') if im.mode not in ('L','LA','RGB','RGBA') else im
        arr = np.array(im); self.image = arr
        self.ax.clear(); self.ax.imshow(arr, cmap='gray', origin='upper', interpolation='nearest')
        self.ax.set_title(os.path.basename(path)); self.ax.set_xticks([]); self.ax.set_yticks([]); self.draw()
    def on_click(self, event):
        if event.inaxes != self.ax or self.image is None: return
        self.clicked.emit(event.xdata, event.ydata)

class TJGCpPicker(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TJ-GCP Picker — DIC↔EBSD 手動対応")
        self.resize(1200, 700)
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        hbox = QtWidgets.QHBoxLayout(central)
        self.view_dic = ImageAxes(self); self.view_ebsd = ImageAxes(self)
        self.view_dic.ax.set_title("DIC (左)"); self.view_ebsd.ax.set_title("EBSD (右)")
        self.view_dic.clicked.connect(self.on_click_dic)
        self.view_ebsd.clicked.connect(self.on_click_ebsd)
        side = QtWidgets.QWidget(); side_layout = QtWidgets.QVBoxLayout(side)
        btn_load_dic = QtWidgets.QPushButton("DIC画像を開く…"); btn_load_dic.clicked.connect(self.load_dic)
        btn_load_ebsd = QtWidgets.QPushButton("EBSD画像を開く…"); btn_load_ebsd.clicked.connect(self.load_ebsd)
        self.label_mode = QtWidgets.QLabel("左(DIC) → 右(EBSD) の順でクリック")
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["id", "dic_x", "dic_y", "ebsd_x", "ebsd_y", "weight", "note"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        btn_del = QtWidgets.QPushButton("選択行を削除"); btn_del.clicked.connect(self.delete_selected)
        btn_undo = QtWidgets.QPushButton("Undo"); btn_undo.clicked.connect(self.undo_last)
        btn_save = QtWidgets.QPushButton("CSV保存…"); btn_save.clicked.connect(self.save_csv)
        left = QtWidgets.QVBoxLayout(); left.addWidget(self.view_dic)
        right = QtWidgets.QVBoxLayout(); right.addWidget(self.view_ebsd)
        left_w = QtWidgets.QWidget(); left_w.setLayout(left)
        right_w = QtWidgets.QWidget(); right_w.setLayout(right)
        hbox.addWidget(left_w,5); hbox.addWidget(right_w,5); hbox.addWidget(side,4)
        for w in (btn_load_dic, btn_load_ebsd, self.label_mode, self.table, btn_del, btn_undo, btn_save):
            side_layout.addWidget(w)
        self.pending_dic: Optional[Tuple[float,float]] = None

    def load_dic(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "DIC画像を開く", "", "Images (*.png *.jpg *.tif *.tiff)")
        if path: self.view_dic.load_image(path)

    def load_ebsd(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "EBSD画像を開く", "", "Images (*.png *.jpg *.tif *.tiff)")
        if path: self.view_ebsd.load_image(path)

    def on_click_dic(self, x: float, y: float):
        self.pending_dic = (x,y)
        self.view_dic.ax.scatter([x],[y], s=36, facecolors='none', edgecolors='C0'); self.view_dic.draw()

    def on_click_ebsd(self, x: float, y: float):
        if self.pending_dic is None:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "先に左(DIC)でクリックしてください"); return
        dic_x, dic_y = self.pending_dic; self.pending_dic = None
        self.view_ebsd.ax.scatter([x],[y], s=36, facecolors='none', edgecolors='C0'); self.view_ebsd.draw()
        self.add_gcp(dic_x, dic_y, x, y)

    def add_gcp(self, dic_x, dic_y, ebsd_x, ebsd_y, weight=1.0, note=""):
        r = self.table.rowCount(); self.table.insertRow(r)
        for c, v in enumerate([r+1, dic_x, dic_y, ebsd_x, ebsd_y, weight, note]):
            it = QtWidgets.QTableWidgetItem(str(v))
            if c == 0: it.setFlags(it.flags() ^ QtCore.Qt.ItemIsEditable)
            self.table.setItem(r, c, it)

    def delete_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for r in rows: self.table.removeRow(r)
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setText(str(r+1))

    def undo_last(self):
        last = self.table.rowCount() - 1
        if last >= 0: self.table.removeRow(last)

    def save_csv(self):
        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.warning(self, "保存", "対応点がありません"); return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "CSVとして保存", "gcp_tj_pairs.csv", "CSV (*.csv)")
        if not path: return
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(["id","dic_x","dic_y","ebsd_x","ebsd_y","weight","note"])
            for r in range(self.table.rowCount()):
                vals = [self.table.item(r,c).text() for c in range(7)]
                w.writerow(vals)
        QtWidgets.QMessageBox.information(self, "保存", f"保存しました:\n{path}")

def main():
    import sys
    app = QtWidgets.QApplication(sys.argv)
    w = TJGCpPicker(); w.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
