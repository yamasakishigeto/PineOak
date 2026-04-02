
# 全画素misorientation参照マッチングモジュール
import numpy as np
import pandas as pd
import re
from scipy.io import loadmat
from scipy.spatial.transform import Rotation as R
from tqdm import tqdm
import tkinter as tk
from tkinter import simpledialog
from preprocessed_loader import load_preprocessed_xlsx, load_preprocessed_mat, get_value_by_label

# スケールファクターダイアログ用のグローバルキャッシュ
cached_scale_factor = None

# φ1, Φ, φ2 から回転行列を生成する
def euler_to_matrix(phi1, Phi, phi2):
    c1, c, c2 = np.cos([phi1, Phi, phi2])
    s1, s, s2 = np.sin([phi1, Phi, phi2])
    return np.array([
        [ c1*c2 - s1*s2*c, -c1*s2 - s1*c2*c,  s1*s ],
        [ s1*c2 + c1*s2*c, -s1*s2 + c1*c2*c, -c1*s ],
        [        s2*s     ,         c2*s    ,     c ]
    ])

# 2つの結晶指向行列間のmisorientation角を計算する（対称操作を考慮し、度単位で返す）
def misorientation_angle_deg(g1, g2, sym_ops):
    min_angle = 180.0
    g1_inv = np.linalg.inv(g1)
    for sym in sym_ops:
        delta = g2 @ sym @ g1_inv
        trace = np.trace(delta)
        angle_rad = np.arccos(np.clip((trace - 1) / 2, -1.0, 1.0))
        angle_deg = np.degrees(angle_rad)
        if angle_deg < min_angle:
            min_angle = angle_deg
    return min_angle

# Excelファイルから参照ステップを読み取り、DataFrameで返す
def read_steps_from_excel(excel_path):
    df = pd.read_excel(excel_path, sheet_name="Project Details", header=None)
    x_step = float(get_value_by_label(df, "x_step"))
    y_step = float(get_value_by_label(df, "y_step"))
    return x_step, y_step

# matファイル内の全点のオイラー角、IQ、位相をフラット化してDataFrameにまとめる
def flatten_all_points(mat):
    phi1 = mat["euler_phi1"]
    Phi  = mat["euler_phi"]
    phi2 = mat["euler_phi2"]
    IQ   = mat["image_quality"]
    phase = mat.get("phase_index", None)
    nrows, ncols = phi1.shape
    data = []
    for idx in range(nrows * ncols):
        r = idx // ncols
        c = idx % ncols
        entry = {
            "Index": idx + 1,
            "phi1": phi1[r, c],
            "phi":  Phi[r, c],
            "phi2": phi2[r, c],
            "IQ":   IQ[r, c],
            "row":  r,
            "col":  c
        }
        if phase is not None:
            entry["phase"] = int(phase[r, c])
        data.append(entry)
    return pd.DataFrame(data), ncols

# 指定されたExcelおよびmatファイルからターゲット（変形）点情報を抽出する
def extract_target_points(excel_path, mat_path):
    df_excel = pd.read_excel(excel_path, sheet_name="Project Details", header=None)
    n_targets = int(get_value_by_label(df_excel, "Number of References"))
    idx0 = df_excel[df_excel.iloc[:,0].astype(str).str.contains("Number of References")].index[0]
    target_lines = df_excel.iloc[idx0+1:idx0+1+n_targets, 1].dropna()
    extracted = target_lines.str.extract(r'(^.+\.tif),(\d+)$')
    extracted.columns = ["Deformed_Filename", "Deformed_Index"]
    extracted["Deformed_Index"] = extracted["Deformed_Index"].astype(int)
    mat = loadmat(mat_path)
    phi1 = mat["euler_phi1"]
    Phi  = mat["euler_phi"]
    phi2 = mat["euler_phi2"]
    phase = mat.get('phase_index', None)  # フェーズマップ
    phase = mat.get("phase_index", None)
    nrows, ncols = phi1.shape
    data = []
    for _, row in extracted.iterrows():
        i = row["Deformed_Index"] - 1
        r, c = divmod(i, ncols)
        entry = {
            "Deformed_Filename": row["Deformed_Filename"],
            "Deformed_Index": row["Deformed_Index"],
            "phi1": phi1[r, c],
            "phi":  Phi[r, c],
            "phi2": phi2[r, c]
        }
        if phase is not None:
            entry["phase"] = int(phase[r, c])
        data.append(entry)
    return pd.DataFrame(data)

# すべての0th点とターゲット点間でmisorientationを計算し、最良一致をDataFrameで返す
def run_misorientation_matching_all_vs_targets(
    mat_0th_path,
    excel_nth_path,
    mat_nth_path,
    output_csv,
    tif_dir,
    angle_threshold=5.0,
    iq_percentile=0.0,
    sym_ops=None,
    target_phase=None):
    global cached_scale_factor
    print(f"Selected symmetry operations count: {len(sym_ops)}")
    mat_0th = loadmat(mat_0th_path)
    all_points_df, ncols = flatten_all_points(mat_0th)
    target_df = extract_target_points(excel_nth_path, mat_nth_path)
    # Filter reference points by phase
    if target_phase is not None:
        target_df = target_df[target_df['phase'] == target_phase]
    x_step, y_step = read_steps_from_excel(excel_nth_path)

    # スケールファクターダイアログをキャッシュ
    if cached_scale_factor is None:
        root = tk.Tk()
        root.withdraw()
        cached_scale_factor = simpledialog.askfloat(
            "Scale Factor",
            "tif naming step multiplier (e.g., 100):",
            initialvalue=100.0
        )
        root.destroy()
    scale_factor = cached_scale_factor

    IQ_threshold = np.percentile(all_points_df["IQ"], iq_percentile)
    results = []
    for _, t_row in tqdm(target_df.iterrows(), total=len(target_df), desc="Computing misorientation"):
        # 指定されている場合、現在のフェーズにないターゲットポイントをスキップ
        if target_phase is not None and t_row.get("phase") != target_phase:
            continue
        if np.isnan(t_row["phi1"]) or np.isnan(t_row["phi"]) or np.isnan(t_row["phi2"]):
            continue
        g_target = euler_to_matrix(*np.radians([t_row["phi1"], t_row["phi"], t_row["phi2"]]))
        tgt_phase = t_row.get("phase", None)
        candidates = []
        for _, a_row in all_points_df.iterrows():
            if tgt_phase is not None and a_row.get("phase", None) != tgt_phase:
                continue
            g_ref = euler_to_matrix(*np.radians([a_row["phi1"], a_row["phi"], a_row["phi2"]]))
            angle = misorientation_angle_deg(g_ref, g_target, sym_ops)
            if angle <= angle_threshold:
                row_copy = a_row.copy()
                row_copy["angle"] = angle
                candidates.append(row_copy)
        if not candidates:
            continue
        best_row = max(candidates, key=lambda x: x["IQ"])
        min_angle = best_row["angle"]
        if min_angle <= angle_threshold:
            col = int(round(best_row["col"] * x_step * scale_factor))
            row = int(round(best_row["row"] * y_step * scale_factor))
            matched_filename = f"0th_x{col}y{row}.tif"
            results.append({
                "Deformed_Filename": t_row["Deformed_Filename"],
                "Matched_0th_Filename": matched_filename,
                "Deformed_Index": t_row["Deformed_Index"],
                "Matched_0th_Index": best_row["Index"],
                "Matched_0th_IQ": best_row["IQ"],
                "Misorientation (deg)": round(min_angle, 1)
            })
    df = pd.DataFrame(results)
    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]
    df = df.sort_values(by="Deformed_Filename", key=lambda col: col.map(natural_sort_key))
    if output_csv:
        df.to_csv(output_csv, index=False)
    return df
