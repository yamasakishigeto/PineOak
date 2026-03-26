"""
Interactive Stress–Strain Mapper
(Colorful Map + Control Figure + Boundaries=Black + Grain-Average Mode)
------------------------------------------------------------------------
- Grain_ID を turbo で色分け（よりカラフル）
- 選択点の stress–strain の線色は、その Grain の色に一致
- **Curve Mode** を追加：
    - point     : クリック/ホバーした点の曲線
    - grain-avg : その点が属する Grain の「平均」曲線
- 境界線は **黒** 固定（LineCollection color='k'）
- 別ウィンドウ（Matplotlib）で Click/Hover と Boundaries ON/OFF、Curve Mode を切替
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.widgets import RadioButtons, CheckButtons
from matplotlib.collections import LineCollection
from matplotlib import colors as mcolors


# 追加：ウィンドウ位置をずらすヘルパー（バックエンドごとに試行）
def set_window_position(fig, x, y):
    """ウィンドウ位置をずらす（GUIバックエンドに応じて可能な方法を試行）"""
    try:
        fig.canvas.manager.window.wm_geometry(f"+{x}+{y}")  # TkAgg
        return
    except Exception:
        pass
    try:
        fig.canvas.manager.window.move(x, y)  # QtAgg/Qt5Agg
        return
    except Exception:
        pass
    try:
        fig.canvas.manager.window.SetPosition((x, y))  # WXAgg
        return
    except Exception:
        pass


def choose_excel_via_dialog(initial: Path | None = None) -> Path | None:
    """TkファイルダイアログでExcelを選択（失敗時は None を返す）"""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.update()  # 安定化
        filetypes = [
            ("Excel files", "*.xlsx *.xls"),
            ("All files", "*.*"),
        ]
        initialdir = str(initial) if initial and initial.is_dir() else str(Path.cwd())
        path = filedialog.askopenfilename(
            title="Select Excel file",
            initialdir=initialdir,
            filetypes=filetypes,
        )
        root.destroy()
        if not path:
            return None
        return Path(path)
    except Exception:
        # ヘッドレスやtk無し環境ではダイアログをスキップ
        return None


def load_data(xlsx_path: Path):
    geo = pd.read_excel(xlsx_path, sheet_name="Geometric_Infomation")
    strain = pd.read_excel(xlsx_path, sheet_name="strain")
    stress = pd.read_excel(xlsx_path, sheet_name="stress")

    # 基本的な整合性チェック
    if not (len(geo) == len(strain) == len(stress)):
        raise ValueError("行数が一致しません: "
                         f"geo={len(geo)}, strain={len(strain)}, stress={len(stress)}")
    if list(strain.columns) != list(stress.columns):
        raise ValueError("strain/stress の列名が一致しません。")

    return geo, strain, stress


def compute_boundary_segments(x, y, grain_id):
    """
    隣接ピクセルの Grain_ID が異なる箇所の「境界エッジ」を線分として返す。
    - 水平方向（右隣）と垂直方向（上隣）だけ見ます。
    - 各点の座標が整数グリッド（x, y が整数）であることを想定。
    """
    # 座標 -> index の辞書
    coord_to_idx = {(int(xi), int(yi)): i for i, (xi, yi) in enumerate(zip(x, y))}

    segments = []  # 各要素は [(x1, y1), (x2, y2)]
    for i, (xi, yi, gi) in enumerate(zip(x, y, grain_id)):
        xi_int = int(xi)
        yi_int = int(yi)

        # 右隣 (x+1, y)
        nb = coord_to_idx.get((xi_int + 1, yi_int))
        if nb is not None and grain_id[nb] != gi:
            # 中点ではなく、ピクセル中心同士を結ぶ（簡便かつ視認性良好）
            segments.append([(xi, yi), (x[nb], y[nb])])

        # 上隣 (x, y+1)
        nb = coord_to_idx.get((xi_int, yi_int + 1))
        if nb is not None and grain_id[nb] != gi:
            segments.append([(xi, yi), (x[nb], y[nb])])

    return segments


def build_mapper_with_control_figure(geo, strain, stress, init_mode="click"):
    # 座標・属性
    x = geo["X_pixel_"].to_numpy()
    y = geo["Y_pixel_"].to_numpy()
    subset_id = geo["Subset_ID"].to_numpy()
    grain_id = geo["Grain_ID"].to_numpy()

    # 応力・ひずみ（列順にステップ 0th, 1st, ...）
    strain_vals = strain.to_numpy(dtype=float)  # shape: (N_points, N_steps)
    stress_vals = stress.to_numpy(dtype=float)

    # ---- Grain_ID を連番コード化（非連続IDにも安定） ----
    # codes[i] は 0..K-1、unique_grains[codes[i]] が元の Grain_ID
    codes, unique_grains = pd.factorize(grain_id, sort=True)
    K = len(unique_grains)
    # よりカラフルなカラーマップ（turbo）を離散風に使用
    cmap = plt.get_cmap('turbo')
    norm = mcolors.Normalize(vmin=0, vmax=max(1, K-1))

    # ---- Grainごとの平均曲線を前計算 ----
    # grain_id -> インデックス配列
    grain_to_idx = {}
    for i, gid in enumerate(grain_id):
        grain_to_idx.setdefault(gid, []).append(i)
    # 平均（NaNを無視）
    grain_mean_strain = {}
    grain_mean_stress = {}
    for gid, idxs in grain_to_idx.items():
        pts = np.array(idxs, dtype=int)
        grain_mean_strain[gid] = np.nanmean(strain_vals[pts, :], axis=0)
        grain_mean_stress[gid] = np.nanmean(stress_vals[pts, :], axis=0)

    # --- Figures ---
    fig_map, ax_map = plt.subplots()
    fig_curve, ax_curve = plt.subplots()
    fig_ctrl = plt.figure(figsize=(4.0, 3.6))
    try:
        fig_ctrl.canvas.manager.set_window_title("Controls")
    except Exception:
        pass

    # ウィンドウ位置をずらす（必要に応じて数値を調整してください）
    set_window_position(fig_map,   60,  60)
    set_window_position(fig_curve, 720, 60)
    set_window_position(fig_ctrl,  1420, 80)

    # ---- Grain_IDの色分け（よりカラフル） ----
    sc = ax_map.scatter(x, y, s=6, alpha=0.9, c=codes, cmap=cmap, norm=norm)
    cbar = fig_map.colorbar(sc, ax=ax_map)
    cbar.set_label("Grain_ID")
    # 可能なら目盛りを間引いてID表示（多すぎると読みにくいのでK<=15の時のみ）
    if K <= 15:
        tick_locs = np.linspace(0, K-1, K)
        cbar.set_ticks(tick_locs)
        cbar.set_ticklabels([str(g) for g in unique_grains])

    ax_map.set_title("Grain_ID map")
    ax_map.set_xlabel("X_pixel")
    ax_map.set_ylabel("Y_pixel")
    ax_map.invert_yaxis()

    # ---- 境界線（黒） ----
    segments = compute_boundary_segments(x, y, grain_id)
    lc = LineCollection(segments, linewidths=0.6, alpha=0.9, colors='k')  # 色は黒
    boundary_artist = ax_map.add_collection(lc)
    boundary_artist.set_visible(True)

    # 選択点のハイライト
    sel_sc = ax_map.scatter([], [], s=90, linewidths=0.9)

    # ステータス表示（現在モード・選択点情報）
    status_text = ax_map.text(
        0.02, 1.11,
        f"mode: {init_mode} | selected: None | curve: point",
        transform=ax_map.transAxes,
        va="top"
    )

    # 近傍探索
    def nearest_index(x0, y0):
        d2 = (x - x0) ** 2 + (y - y0) ** 2
        return int(np.argmin(d2))

    # 曲線モード状態
    curve_mode = ["point"]  # or "grain-avg"
    mode = [init_mode]      # "click" or "hover"

    # 応力-ひずみ曲線の更新（色をマップと一致）
    def update_curve(idx):
        ax_curve.clear()
        gid = grain_id[idx]
        code = codes[idx]
        color = cmap(norm(code))

        if curve_mode[0] == "grain-avg":
            s_strain = grain_mean_strain[gid]
            s_stress = grain_mean_stress[gid]
            title_extra = " (Grain Average)"
        else:
            s_strain = strain_vals[idx, :]
            s_stress = stress_vals[idx, :]
            title_extra = ""

        ax_curve.set_title(f"Stress–Strain curve (Subset_ID={int(subset_id[idx])}, Grain_ID={int(gid)}){title_extra}")
        ax_curve.set_xlabel("Strain [-]")
        ax_curve.set_ylabel("Stress [GPa]")
        ax_curve.plot(s_strain, s_stress, marker="o", color=color, markerfacecolor=color)
        fig_curve.canvas.draw_idle()

    # 選択点のハイライト更新（色も一致）
    def update_selection(idx):
        code = codes[idx]
        color = cmap(norm(code))
        sel_sc.set_offsets(np.array([[x[idx], y[idx]]]))
        sel_sc.set_color([color])
        status_text.set_text(f"mode: {mode[0]} | selected: Subset_ID={int(subset_id[idx])}, Grain_ID={int(grain_id[idx])} | curve: {curve_mode[0]}")
        fig_map.canvas.draw_idle()

    # マウス移動イベント（hover 用）
    def on_move(event):
        if mode[0] != "hover":
            return
        if event.inaxes != ax_map:
            return
        if event.xdata is None or event.ydata is None:
            return
        idx = nearest_index(event.xdata, event.ydata)
        update_selection(idx)
        update_curve(idx)

    # クリックイベント（click 用）
    def on_click(event):
        if mode[0] != "click":
            return
        if event.inaxes != ax_map:
            return
        if event.xdata is None or event.ydata is None:
            return
        idx = nearest_index(event.xdata, event.ydata)
        update_selection(idx)
        update_curve(idx)

    # キーイベントでモード切替
    def on_key(event):
        if event.key == "h":
            mode[0] = "hover"
        elif event.key == "c":
            mode[0] = "click"
        elif event.key == "t":
            mode[0] = "hover" if mode[0] == "click" else "click"
        status_text.set_text(f"mode: {mode[0]} | selected: None | curve: {curve_mode[0]}")
        fig_map.canvas.draw_idle()

    fig_map.canvas.mpl_connect("motion_notify_event", on_move)
    fig_map.canvas.mpl_connect("button_press_event", on_click)
    fig_map.canvas.mpl_connect("key_press_event", on_key)

    # ---- Control figure ----
    ax_radio = fig_ctrl.add_axes([0.12, 0.62, 0.76, 0.30])  # Click/Hover
    radio = RadioButtons(ax_radio, ('click', 'hover'), active=0 if init_mode=='click' else 1)
    def on_radio_mode(label):
        mode[0] = label
        status_text.set_text(f"mode: {mode[0]} | selected: None | curve: {curve_mode[0]}")
        fig_map.canvas.draw_idle()
    radio.on_clicked(on_radio_mode)

    ax_checks = fig_ctrl.add_axes([0.12, 0.12, 0.35, 0.35])  # Boundaries
    checks = CheckButtons(ax_checks, ('Boundaries',), (True,))
    def on_check(label):
        boundary_artist.set_visible(not boundary_artist.get_visible())
        fig_map.canvas.draw_idle()
    checks.on_clicked(on_check)

    ax_curve_mode = fig_ctrl.add_axes([0.55, 0.12, 0.33, 0.35])  # Curve Mode
    radio_curve = RadioButtons(ax_curve_mode, ('point', 'grain-avg'), active=0)
    def on_radio_curve(label):
        curve_mode[0] = label
        status_text.set_text(f"mode: {mode[0]} | selected: None | curve: {curve_mode[0]}")
        fig_map.canvas.draw_idle()
    radio_curve.on_clicked(on_radio_curve)

    # 初期選択
    if len(geo) > 0:
        update_selection(0)
        update_curve(0)

    # 3つのウィンドウを同時表示
    plt.show()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, default="",
                        help="Excel file path. 空の場合はダイアログで選択")
    parser.add_argument("--mode", type=str, choices=["click", "hover"], default="hover",
                        help="Interaction mode (click or hover). Default: click")
    args = parser.parse_args()

    xlsx_path = Path(args.file).expanduser() if args.file else None

    # パスが未指定 or 存在しない → ダイアログで選択
    if (xlsx_path is None) or (not xlsx_path.exists()):
        chosen = choose_excel_via_dialog(initial=Path.cwd())
        if chosen is None:
            raise FileNotFoundError("Excel ファイルが選択されませんでした。--file で直接指定も可能です。")
        xlsx_path = chosen.resolve()

    geo, strain, stress = load_data(xlsx_path)
    build_mapper_with_control_figure(geo, strain, stress, init_mode=args.mode)


if __name__ == "__main__":
    main()
