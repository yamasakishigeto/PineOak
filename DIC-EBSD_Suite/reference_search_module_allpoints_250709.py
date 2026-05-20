
# 全画素misorientation参照マッチングモジュール
import numpy as np
import pandas as pd
import re
from preprocessed_loader import smart_loadmat as loadmat
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
# Bunge パッシブ規約: M = g2 @ g1^T に左から対称操作を適用（M と M^T の両方）
def misorientation_angle_deg(g1, g2, sym_ops):
    M   = g2 @ g1.T
    M_T = M.T
    max_trace = -1.0
    for sym in sym_ops:
        for mat in (M, M_T):
            t = np.trace(sym @ mat)
            if t > max_trace:
                max_trace = t
    return np.degrees(np.arccos(np.clip((max_trace - 1.0) / 2.0, -1.0, 1.0)))

# numpy ベクトル化版 misorientation 一括計算
def _misorientation_batch(g_refs, g_target, sym_ops, use_symmetry=False):
    """
    g_refs:       (N, 3, 3) — 参照点群の回転行列
    g_target:     (3, 3)    — 対象点の回転行列
    sym_ops:      (S, 3, 3) — 対称操作行列群
    use_symmetry: bool      — True: 左側対称操作を適用（M と M^T 両方）
                              False: 生のミスオリエンテーション（物理的方位差）
    戻り値:       (N,)      — 各参照点との misorientation 角（度）
    """
    # M[i] = g_target @ g_refs[i]^T  (N, 3, 3)
    M = g_target[np.newaxis] @ g_refs.transpose(0, 2, 1)      # (N, 3, 3)
    if use_symmetry:
        # Bunge パッシブ規約: 左から対称操作を適用（M と M^T の両方）
        M_T  = M.transpose(0, 2, 1)                            # M^{-1}  (N, 3, 3)
        sM   = sym_ops[:, np.newaxis] @ M[np.newaxis]          # (S, N, 3, 3)
        sM_T = sym_ops[:, np.newaxis] @ M_T[np.newaxis]        # (S, N, 3, 3)
        def _tr(a):
            return a[..., 0, 0] + a[..., 1, 1] + a[..., 2, 2]
        max_trace = np.max(np.concatenate([_tr(sM), _tr(sM_T)], axis=0), axis=0)
    else:
        # 対称操作なし: 物理的な方位差をそのまま使用
        max_trace = M[..., 0, 0] + M[..., 1, 1] + M[..., 2, 2]  # (N,)
    return np.degrees(np.arccos(np.clip((max_trace - 1.0) / 2.0, -1.0, 1.0)))

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
    # char 型変数を読み飛ばすため variable_names で数値変数のみ指定
    mat = loadmat(mat_path, variable_names=['euler_phi1', 'euler_phi', 'euler_phi2', 'phase_index'])
    phi1 = mat["euler_phi1"]
    Phi  = mat["euler_phi"]
    phi2 = mat["euler_phi2"]
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
            "phi2": phi2[r, c],
            "row":  r,
            "col":  c,
        }
        if phase is not None:
            entry["phase"] = int(phase[r, c])
        data.append(entry)
    return pd.DataFrame(data)

# すべてのref点とターゲット点間でmisorientationを計算し、最良一致をDataFrameで返す
def run_misorientation_matching_all_vs_targets(
    mat_ref_path,
    excel_nth_path,
    mat_nth_path,
    output_csv,
    tif_dir,
    angle_threshold=5.0,
    iq_percentile=0.0,
    sym_ops=None,
    target_phase=None,
    ref_name='ref',
    use_symmetry=False,
    x_limit=None,
    y_limit=None):
    global cached_scale_factor
    print(f"Selected symmetry operations count: {len(sym_ops)}")
    mat_ref = loadmat(mat_ref_path, variable_names=['euler_phi1', 'euler_phi', 'euler_phi2', 'image_quality', 'phase_index'])
    all_points_df, ncols = flatten_all_points(mat_ref)
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

    # --- 参照点（ref）の回転行列をループ前に一括計算 ---
    ref_eulers = all_points_df[["phi1", "phi", "phi2"]].values          # (N, 3)
    ref_valid  = ~np.isnan(ref_eulers).any(axis=1)                      # (N,)
    g_refs_all = np.array([
        euler_to_matrix(*np.radians(e)) if ref_valid[i] else np.eye(3)
        for i, e in enumerate(ref_eulers)
    ])                                                                   # (N, 3, 3)
    sym_ops_arr = np.asarray(sym_ops)                                    # (S, 3, 3)

    results = []
    for _, t_row in tqdm(target_df.iterrows(), total=len(target_df), desc="Computing misorientation"):
        if target_phase is not None and t_row.get("phase") != target_phase:
            continue
        if np.isnan(t_row["phi1"]) or np.isnan(t_row["phi"]) or np.isnan(t_row["phi2"]):
            continue

        g_target  = euler_to_matrix(*np.radians([t_row["phi1"], t_row["phi"], t_row["phi2"]]))
        tgt_phase = t_row.get("phase", None)

        # 有効点 + 同一フェーズ + 距離制約 のマスク
        mask = ref_valid.copy()
        if tgt_phase is not None and "phase" in all_points_df.columns:
            mask &= (all_points_df["phase"].values == tgt_phase)
        if x_limit is not None:
            mask &= (np.abs(all_points_df["col"].values - t_row["col"]) <= x_limit)
        if y_limit is not None:
            mask &= (np.abs(all_points_df["row"].values - t_row["row"]) <= y_limit)

        if not mask.any():
            continue

        masked_idx = np.where(mask)[0]

        # numpy ベクトル化で全参照点との misorientation を一括計算
        min_angles = _misorientation_batch(g_refs_all[mask], g_target, sym_ops_arr,
                                           use_symmetry=use_symmetry)

        # 閾値以内の候補のみ抽出
        within = min_angles <= angle_threshold
        if not within.any():
            continue

        # 候補の中で IQ 最大を選ぶ
        cand_idx    = masked_idx[within]
        cand_angles = min_angles[within]
        cand_iqs    = all_points_df["IQ"].values[cand_idx]
        best_local  = int(np.argmax(cand_iqs))
        best_idx    = cand_idx[best_local]
        best_angle  = float(cand_angles[best_local])

        best_row = all_points_df.iloc[best_idx]
        col = int(round(best_row["col"] * x_step * scale_factor))
        row = int(round(best_row["row"] * y_step * scale_factor))
        matched_filename = f"{ref_name}_x{col}y{row}.tif"
        results.append({
            "Deformed_Filename":   t_row["Deformed_Filename"],
            "Matched_Ref_Filename": matched_filename,
            "Deformed_Index":      t_row["Deformed_Index"],
            "Matched_Ref_Index":   best_row["Index"],
            "Matched_Ref_IQ":      best_row["IQ"],
            "Misorientation (deg)": round(best_angle, 1)
        })
    df = pd.DataFrame(results)
    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]
    df = df.sort_values(by="Deformed_Filename", key=lambda col: col.map(natural_sort_key))
    if output_csv:
        df.to_csv(output_csv, index=False)
    return df
