
import os
import re
import shutil
from pathlib import Path
import pandas as pd
from tkinter import Tk, filedialog, simpledialog
from reference_search_module_allpoints_250709 import run_misorientation_matching_all_vs_targets
from visualize_grain_map_overlay_250709 import visualize_grain_map  # 同じディレクトリに必要
from tkinter import Tk, Label, Button
from tkinter import ttk
from scipy.spatial.transform import Rotation as R
from preprocessed_loader import load_preprocessed_xlsx, load_preprocessed_mat, get_value_by_label

def select_folder(prompt, initialdir=None):
    print(prompt)
    root = Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title=prompt, initialdir=initialdir)
    print(f"✔ 選択: {folder}")
    return Path(folder)

def select_multiple_folders_manual(prompt="処理対象の nth フォルダを1つずつ選択（キャンセルで終了）"):
    print(prompt)
    folders = []
    root = Tk()
    root.withdraw()
    last_dir = os.getcwd()  # 初期ディレクトリをカレントに設定

    while True:
        folder = filedialog.askdirectory(title=prompt, initialdir=last_dir)
        if not folder:
            break
        path = Path(folder)
        if path not in folders:
            folders.append(path)
            last_dir = str(path.parent)  # 次回はこのフォルダの親ディレクトリから始める
            print(f"✔ 追加: {path}")
    print(f"✔ 選択された nth フォルダ: {[f.name for f in folders]}")
    return folders

def find_closest_tif(x, y, coord_map):
    min_dist = float('inf')
    closest_file = None
    for (cx, cy), file in coord_map.items():
        dist = (cx - x)**2 + (cy - y)**2
        if dist < min_dist:
            min_dist = dist
            closest_file = file
    return closest_file

# === Step 0: フォルダを選択 ===
print("🗂 0th フォルダが含まれる親フォルダを選択してください")
parent_folder = select_folder("0th を含む親フォルダを選択")
folder_0th = parent_folder / "0th"

print("🗂 nth フォルダを1つずつ選択してください（キャンセルで終了）")
folders_nth = select_multiple_folders_manual("処理対象の nth フォルダを1つずつ選択（キャンセルで終了）")

# === Step 1: しきい値の入力,  対称性の選択（全体共通） ===
def get_symmetry_ops():
    sym_options = [
        ("cubic", "O"),
        ("hexagonal", "D6"),
        ("tetragonal", "D4"),
        ("orthorhombic", "D2"),
        ("trigonal", "D3"),
        ("monoclinic", "C2"),
        ("triclinic", "C1")
    ]
    labels = [label for label, _ in sym_options]
    selected_index = {"value": None}

    def on_ok():
        idx = combo.current()
        if idx < 0:
            raise ValueError("❌ 対称性が選択されていません。")
        selected_index["value"] = idx
        root.quit()

    root = Tk()
    root.title("対称性の選択")
    Label(root, text="結晶の対称性を選んでください:", font=("Arial", 12)).pack(pady=10)

    combo = ttk.Combobox(root, values=labels, state="readonly", font=("Arial", 12), width=20)
    combo.current(0)
    combo.pack(padx=20, pady=10)

    Button(root, text="OK", command=on_ok, width=10).pack(pady=10)

    root.mainloop()
    root.destroy()

    idx = selected_index["value"]
    if idx is None:
        raise RuntimeError("❌ 対称性が確定されませんでした")

    label, group = sym_options[idx]
    sym_ops = R.create_group(group).as_matrix()
    print(f"✅ 選択された対称性: '{label}' → group '{group}', 操作数: {len(sym_ops)}")
    return sym_ops

# ── Phaseごとに対称性を選択 ───────────────
mat0_dict      = load_preprocessed_mat(str(parent_folder), '0th')
phase_idx_map  = mat0_dict['phase_index']
phase_names    = [str(n) for n in mat0_dict['phasetxt'][0]]
phases = sorted(set(map(int, phase_idx_map.flatten())))
phase_sym_map  = {}
for idx in phases:
    name = phase_names[idx]
    print(f"🧩 Phase '{name}' (index {idx}) の対称性を選択中…")
    phase_sym_map[idx] = get_symmetry_ops()
# ──────────────────────────────────

root = Tk()
root.withdraw()
angle_threshold = simpledialog.askfloat("Misorientation Threshold", "Max misorientation angle (deg):", initialvalue=5.0)

# === Step 2: 各 nth フォルダに対して処理を実行 ===
visualization_targets = []  # 後でまとめて可視化
for folder_nth in folders_nth:
    try:
        parent_dir = folder_nth.parent
        nth_name = folder_nth.name

        mat_0th = folder_0th.parent / "pre-processed 0th.mat"
        mat_nth = folder_nth.parent / f"pre-processed {nth_name}.mat"
        excel_nth = folder_nth.parent / f"pre-processed {nth_name}.xlsx"

        if not (mat_0th.exists() and mat_nth.exists() and excel_nth.exists()):
            print(f"❌ {nth_name}: 必要な .mat または .xlsx ファイルが見つかりません")
            continue

        replacing_dir = parent_dir / f"replacing_0th_{nth_name}"
        renamed_dir = parent_dir / f"renamed_0th_{nth_name}"
        replaced_dir = parent_dir / f"replaced_{nth_name}"
        for folder in [replacing_dir, renamed_dir, replaced_dir]:
            folder.mkdir(exist_ok=True)

        print(f"🔍 {nth_name}: misorientation を計算中...")
        csv_path = parent_dir / f"replaced pattern list 0th_{nth_name}.csv"
        # ── Phase別 misorientation 計算 ─────────
        dfs = []
        for idx in phases:
            phase_name = phase_names[idx]
            print(f"🔍 {nth_name}: Phase '{phase_name}' の misorientation を計算中…")
            # Count reference points for this phase
            from reference_search_module_allpoints_250709 import extract_target_points
            target_list = extract_target_points(str(excel_nth), str(mat_nth))
            count_ref = len(target_list[target_list['phase'] == idx])
            print(f"Phase {idx} ({phase_names[idx]}): {count_ref} reference points to process")
            df_phase = run_misorientation_matching_all_vs_targets(
                mat_0th_path=str(mat_0th),
                sym_ops=phase_sym_map[idx],
                excel_nth_path=str(excel_nth),
                mat_nth_path=str(mat_nth),
                output_csv=None,
                tif_dir=str(folder_0th),
                angle_threshold=angle_threshold,
                target_phase=idx,
            )
            df_phase['phase'] = phase_name
            dfs.append(df_phase)
        df = pd.concat(dfs, ignore_index=True)
        # ──────────────────────────────────

        df_excel = pd.read_excel(excel_nth, sheet_name="Project Details", header=None)
        n_ref = int(get_value_by_label(df_excel, "Number of References"))
        matched_names = set(df["Deformed_Filename"])
        all_targets = set(df_excel.iloc[31:31+n_ref, 1].dropna().str.extract(r'(^.+\.tif)')[0])
        unmatched = sorted(all_targets - matched_names)

        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(f"# angle_threshold: {angle_threshold}\n")
            f.write(f"# number_of_references: {n_ref}\n")
            f.write(f"# number_of_matched_patterns: {len(df)}\n")
            f.write("# no_matched_patterns: \"" + " ".join(unmatched) + "\"\n")
            df.to_csv(f, index=False, lineterminator="\n")

        print(f"📂 {nth_name}: ファイルをコピー・置換します...")
        tif_files = list(folder_0th.glob("*.tif"))
        coord_map = {}
        pattern = re.compile(r"x(\d+)y(\d+)")
        for f in tif_files:
            match = pattern.search(f.name)
            if match:
                x, y = int(match.group(1)), int(match.group(2))
                coord_map[(x, y)] = f

        for _, row in df.iterrows():
            matched_name = row["Matched_0th_Filename"]
            deformed_name = row["Deformed_Filename"]
            match = pattern.search(matched_name)
            if match:
                x, y = int(match.group(1)), int(match.group(2))
                matched_file = find_closest_tif(x, y, coord_map)
                if matched_file is None:
                    print(f"⚠ {matched_name} に近いファイルが見つかりません。スキップします。")
                    continue
                shutil.copy2(matched_file, replacing_dir / matched_file.name)
                shutil.copy2(matched_file, renamed_dir / deformed_name)
                nth_path = folder_nth / deformed_name
                if nth_path.exists():
                    shutil.copy2(nth_path, replaced_dir / deformed_name)
                shutil.copy2(renamed_dir / deformed_name, nth_path)

        visualization_targets.append((mat_nth, excel_nth, csv_path, nth_name))

        print(f"✅ {nth_name}: 処理完了。\n")

    except Exception as e:
        print(f"❗ {folder_nth.name}: エラーが発生しました → {e}")

# === Step 6: マップ可視化を一括実行 ===
for mat_nth, excel_nth, csv_path, nth_name in visualization_targets:
    print(f"🖼 {nth_name}: グレインマップを表示・保存中...")
    visualize_grain_map(str(mat_nth), str(excel_nth), str(csv_path))
