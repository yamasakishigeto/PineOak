import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from preprocessed_loader import get_value_by_label
from scipy.io import loadmat

def generate_green_blue_color():
    r = np.random.uniform(0.0, 0.3)
    g = np.random.uniform(0.4, 1.0)
    b = np.random.uniform(0.4, 1.0)
    return np.array([r, g, b])

def visualize_grain_map(mat_path, xlsx_path, csv_path, save_path=None):
    if save_path is None:
        mat_dir = os.path.dirname(mat_path)
        nth_name = os.path.basename(mat_path).replace('pre-processed ', '').replace('.mat', '')
        save_path = os.path.join(mat_dir, f"matching map {nth_name}.png")

    """
    .matのgrain_numberを緑〜青で表示し、マッチ点を黒・非マッチ点を赤＋ファイル名付きで表示
    """
    # grainマップの準備
    mat = loadmat(mat_path)
    grain_id = mat["grain_number"]
    nrows, ncols = grain_id.shape
    unique_ids = np.unique(grain_id[~np.isnan(grain_id)])
    cmap_dict = {gid: generate_green_blue_color() for gid in unique_ids}
    rgb_map = np.ones((nrows, ncols, 3))
    for r in range(nrows):
        for c in range(ncols):
            gid = grain_id[r, c]
            if not np.isnan(gid):
                rgb_map[r, c] = cmap_dict[gid]

    # xlsxから参照ファイル名とIndexを取得
    df_excel = pd.read_excel(xlsx_path, sheet_name="Project Details", header=None)
    n_ref = int(get_value_by_label(df_excel, "Number of References"))
    idx = df_excel[df_excel.iloc[:,0].astype(str).str.contains("Number of References")].index[0]
    idx = df_excel[df_excel.iloc[:,0].astype(str).str.contains("Number of References")].index[0]
    target_lines = df_excel.iloc[idx+1:idx+1+n_ref, 1].dropna().dropna().dropna()
    extracted = target_lines.str.extract(r'(^.+\.tif),(\d+)$')
    extracted.columns = ["Filename", "Index"]
    extracted["Index"] = extracted["Index"].astype(int)

    # CSVからマッチ済みファイル名を取得
    df_csv = pd.read_csv(csv_path, comment="#")
    matched_filenames = set(df_csv["Deformed_Filename"].values)

    # ファイル名ごとにmatched/unmatched分類
    matched_df = extracted[extracted["Filename"].isin(matched_filenames)]
    unmatched_df = extracted[~extracted["Filename"].isin(matched_filenames)]

    # プロット
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(rgb_map, origin='upper')

    # matched → 黒 + ラベル、unmatched → 赤 + ラベル
    for _, row in matched_df.iterrows():
        idx = row["Index"]
        r, c = divmod(idx, ncols)
        ax.plot(c, r, 'ks', markersize=4)
        ax.text(c + 1, r, row["Filename"], fontsize=6, color='black')

    for _, row in unmatched_df.iterrows():
        idx = row["Index"]
        r, c = divmod(idx, ncols)
        ax.plot(c, r, 'rs', markersize=4)
        ax.text(c + 1, r, row["Filename"], fontsize=6, color='red')

    ax.set_title("Grain map (green-blue) + Match overlay with labels")
    ax.set_xlabel("X (col)")
    ax.set_ylabel("Y (row)")
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
    plt.show()
