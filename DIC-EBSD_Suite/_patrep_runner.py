"""
_patrep_runner.py
=================
EBSD PatRep バッチ処理の param-driven ランナー。
tkinter ダイアログなしで動作し、パラメータを JSON ファイルから受け取る。

Usage:
    python _patrep_runner.py <param_file.json>

JSON 形式:
{
    "patrep_dir":       "E:/.../EBSD PatRep",
    "parent_folder":    "E:/.../experiment_folder",
    "nth_names":        ["1st", "2nd", "900MPa"],
    "angle_threshold":  5.0,
    "scale_factor":     100.0,
    "phase_sym":        {"0": "cubic", "1": "hexagonal"}
}
"""

import sys
import os
import json
import re
import shutil
import traceback
from pathlib import Path

# UTF-8 出力設定
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

if len(sys.argv) < 2:
    print("Usage: python _patrep_runner.py <param_file.json>")
    sys.exit(1)

with open(sys.argv[1], encoding='utf-8') as _f:
    params = json.load(_f)

patrep_dir       = params['patrep_dir']
parent_folder    = Path(params['parent_folder'])
nth_names        = params['nth_names']
angle_thr        = float(params['angle_threshold'])
scale_factor     = float(params['scale_factor'])
phase_sym        = params['phase_sym']          # {"0": "cubic", ...}
folder_0th_name  = params.get('folder_0th_name', '0th')
nth_folder_names = params.get('nth_folder_names', {})  # {"8th": "1100MPa", ...}

# EBSD PatRep モジュールをパスに追加
sys.path.insert(0, patrep_dir)

import numpy as np
import pandas as pd
from scipy.spatial.transform import Rotation as R
from preprocessed_loader import smart_loadmat as loadmat

import reference_search_module_allpoints_250709 as ref_mod
from reference_search_module_allpoints_250709 import extract_target_points
from visualize_grain_map_overlay_250709 import visualize_grain_map
from preprocessed_loader import get_value_by_label

# scale_factor をモジュールキャッシュに注入（tkinter ダイアログをスキップ）
ref_mod.cached_scale_factor = scale_factor

# 対称操作グループのマッピング
SYM_GROUPS = {
    'cubic':        'O',
    'hexagonal':    'D6',
    'tetragonal':   'D4',
    'orthorhombic': 'D2',
    'trigonal':     'D3',
    'monoclinic':   'C2',
    'triclinic':    'C1',
}

# phase_sym → {int: ndarray of rotation matrices}
phase_sym_map = {}
for idx_str, sym_name in phase_sym.items():
    group = SYM_GROUPS.get(sym_name, 'O')
    ops = R.create_group(group).as_matrix()
    phase_sym_map[int(idx_str)] = ops
    print(f"  Phase {idx_str}: {sym_name} → group '{group}'  ({len(ops)} ops)")

# 0th のパスを確定
mat_0th_candidates = sorted(parent_folder.glob("pre-processed 0th*.mat"))
if not mat_0th_candidates:
    print("ERROR: pre-processed 0th*.mat が見つかりません")
    sys.exit(1)
mat_0th    = mat_0th_candidates[0]
folder_0th = parent_folder / folder_0th_name
print(f"  0th パターンフォルダ: {folder_0th_name}")

# phase 情報を読む
_mat0 = loadmat(str(mat_0th), variable_names=['phasetxt', 'phase_index'])
phase_idx_map  = _mat0['phase_index']
phase_names_raw = [str(n) for n in _mat0['phasetxt'][0]]
phases = sorted(set(int(v) for v in phase_idx_map.flatten()
                    if not (isinstance(v, float) and np.isnan(v))))


def find_closest_tif(x, y, coord_map):
    min_dist = float('inf')
    closest_file = None
    for (cx, cy), f in coord_map.items():
        dist = (cx - x) ** 2 + (cy - y) ** 2
        if dist < min_dist:
            min_dist = dist
            closest_file = f
    return closest_file


visualization_targets = []
n_success = 0

for nth_name in nth_names:
    print(f"\n{'='*55}")
    print(f"  Processing: {nth_name}")
    print('='*55)

    try:
        mat_nth    = parent_folder / f"pre-processed {nth_name}.mat"
        excel_nth  = parent_folder / f"pre-processed {nth_name}.xlsx"
        nth_dir_name = nth_folder_names.get(nth_name, nth_name)
        folder_nth = parent_folder / nth_dir_name
        print(f"  {nth_name} パターンフォルダ: {nth_dir_name}")

        missing = [p for p in [mat_0th, mat_nth, excel_nth] if not p.exists()]
        if missing:
            print(f"  ERROR: 次のファイルが見つかりません: {[p.name for p in missing]}")
            continue

        replacing_dir = parent_folder / f"replacing_0th_{nth_name}"
        renamed_dir   = parent_folder / f"renamed_0th_{nth_name}"
        replaced_dir  = parent_folder / f"replaced_{nth_name}"
        for d in [replacing_dir, renamed_dir, replaced_dir]:
            d.mkdir(exist_ok=True)

        csv_path = parent_folder / f"replaced pattern list 0th_{nth_name}.csv"

        # Phase 別 misorientation 計算
        dfs = []
        for idx in phases:
            phase_name = phase_names_raw[idx] if idx < len(phase_names_raw) else f"Phase{idx}"
            sym_ops = phase_sym_map.get(idx)
            if sym_ops is None:
                print(f"  WARNING: Phase {idx} ({phase_name}) の対称操作が未設定。スキップ。")
                continue

            target_list = extract_target_points(str(excel_nth), str(mat_nth))
            count_ref = len(target_list[target_list['phase'] == idx])
            print(f"  Phase {idx} ({phase_name}): {count_ref} 参照点 — misorientation 計算中...")

            df_phase = ref_mod.run_misorientation_matching_all_vs_targets(
                mat_0th_path=str(mat_0th),
                sym_ops=sym_ops,
                excel_nth_path=str(excel_nth),
                mat_nth_path=str(mat_nth),
                output_csv=None,
                tif_dir=str(folder_0th),
                angle_threshold=angle_thr,
                target_phase=idx,
            )
            df_phase['phase'] = phase_name
            dfs.append(df_phase)

        if not dfs:
            print(f"  WARNING: 処理結果が空です。スキップします。")
            continue

        df = pd.concat(dfs, ignore_index=True)
        print(f"  マッチング完了: {len(df)} 点")

        # CSV 保存
        df_excel = pd.read_excel(str(excel_nth), sheet_name="Project Details", header=None)
        n_ref = int(get_value_by_label(df_excel, "Number of References"))
        matched_names = set(df["Deformed_Filename"])
        all_targets = set(
            df_excel.iloc[31:31+n_ref, 1].dropna()
                    .str.extract(r'(^.+\.tif)')[0]
        )
        unmatched = sorted(all_targets - matched_names)

        with open(str(csv_path), "w", encoding="utf-8") as f:
            f.write(f"# angle_threshold: {angle_thr}\n")
            f.write(f"# number_of_references: {n_ref}\n")
            f.write(f"# number_of_matched_patterns: {len(df)}\n")
            f.write('# no_matched_patterns: "' + " ".join(unmatched) + '"\n')
            df.to_csv(f, index=False, lineterminator="\n")

        # .tif コピー・置換
        print(f"  パターンファイルをコピー・置換中...")
        tif_files = list(folder_0th.glob("*.tif"))
        coord_map = {}
        _pat = re.compile(r"x(\d+)y(\d+)")
        for tif in tif_files:
            m = _pat.search(tif.name)
            if m:
                coord_map[(int(m.group(1)), int(m.group(2)))] = tif

        n_copied = 0
        for _, row in df.iterrows():
            matched_name  = row["Matched_0th_Filename"]
            deformed_name = row["Deformed_Filename"]
            m = _pat.search(matched_name)
            if not m:
                continue
            x, y = int(m.group(1)), int(m.group(2))
            matched_file = find_closest_tif(x, y, coord_map)
            if matched_file is None:
                print(f"    WARNING: {matched_name} に対応するファイルが見つかりません。")
                continue
            shutil.copy2(matched_file, replacing_dir / matched_file.name)
            shutil.copy2(matched_file, renamed_dir / deformed_name)
            nth_path = folder_nth / deformed_name
            if nth_path.exists():
                shutil.copy2(nth_path, replaced_dir / deformed_name)
            shutil.copy2(renamed_dir / deformed_name, nth_path)
            n_copied += 1

        print(f"  コピー完了: {n_copied} ファイル")
        visualization_targets.append((str(mat_nth), str(excel_nth), str(csv_path), nth_name))
        n_success += 1
        print(f"  {nth_name}: 完了")

    except Exception as e:
        print(f"  ERROR [{nth_name}]: {e}")
        traceback.print_exc()

# 可視化
print(f"\n{'='*55}")
print(f"  可視化を実行中 ({len(visualization_targets)} 件)...")
print('='*55)
for mat_nth, excel_nth, csv_path, nth_name in visualization_targets:
    print(f"  {nth_name}: grain map を保存中...")
    try:
        visualize_grain_map(mat_nth, excel_nth, csv_path)
    except Exception as e:
        print(f"  WARNING: {nth_name} の可視化でエラー: {e}")

print(f"\n{'='*55}")
print(f"  全処理完了  ({n_success}/{len(nth_names)} 成功)")
print('='*55)
