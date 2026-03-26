import sys, json
from pathlib import Path

param_file = sys.argv[1]
dic_module = sys.argv[2]

with open(param_file, encoding='utf-8') as f:
    p = json.load(f)

import importlib.util
spec = importlib.util.spec_from_file_location("dic", dic_module)
mod  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

import numpy as np

# ---- パラメータをモジュールグローバルに反映 ----
mod.SUBSET_SIZE     = p["subset_size"]
mod.STEP_COARSE     = p["s1_step"]
mod.STAGE1_AUTO     = p["s1_auto"]
mod.STAGE1_MARGIN   = p["s1_margin"]
mod.SEARCH_COARSE   = p["s1_fixed"]
mod.STEP_FINE       = p["s2_step"]
mod.SEARCH_FINE     = p["s2_search"]
mod.NCC_THRESHOLD   = p["ncc_threshold"]
mod.N_WORKERS       = p["n_workers"]
mod.GAUGE_LENGTH    = p["gauge_length"]
mod.USE_PREV_STAGE1 = p["use_prev_stage1"]

SCALE_CONFIG = {k: (v[0], v[1]) for k, v in p["scale"].items()}
mod.SCALE_CONFIG = SCALE_CONFIG
json_path    = Path(p["json_path"]) if p.get("json_path") else None
REF_PATH     = Path(p["ref_path"])
DEF_PATHS    = [Path(dp) for dp in p["def_paths"]]
BASE_FOLDER  = Path(p["folder"])
mod.ALIGNMENT_JSON = json_path
mod.REF_PATH   = REF_PATH
mod.DEF_PATHS  = DEF_PATHS
mod.BASE_FOLDER = BASE_FOLDER

# ---- アライメント読み込み ----
shifts, mod.TRIM_BOTTOM = mod.load_alignment(json_path)
TRIM_BOTTOM = mod.TRIM_BOTTOM
STEP_FINE   = mod.STEP_FINE
STEP_COARSE = mod.STEP_COARSE
SEARCH_FINE = mod.SEARCH_FINE
SUBSET_SIZE = mod.SUBSET_SIZE
NCC_THRESHOLD = mod.NCC_THRESHOLD
GAUGE_LENGTH  = mod.GAUGE_LENGTH
STAGE1_AUTO   = mod.STAGE1_AUTO
STAGE1_MARGIN = mod.STAGE1_MARGIN
SEARCH_COARSE = mod.SEARCH_COARSE
USE_PREV_STAGE1 = mod.USE_PREV_STAGE1

# ---- ROI計算 ----
if shifts:
    _img = mod.imread_safe(REF_PATH)
    _h, _w = _img.shape[:2]
    if TRIM_BOTTOM > 0:
        _h -= TRIM_BOTTOM
    roi = mod.calc_valid_roi(shifts, (_h, _w))
else:
    roi = None

total = len(DEF_PATHS)
print(f"\n処理開始: {total}ペアのDICを実行します")
print(f"  REF: {REF_PATH.name}")

OUTPUT_DIR = BASE_FOLDER / f"dic_{REF_PATH.stem}"
mod.OUTPUT_DIR = OUTPUT_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
print(f"  出力先: {OUTPUT_DIR}")

_old_pngs = list(OUTPUT_DIR.glob("*.png"))
if _old_pngs:
    for _p in _old_pngs:
        _p.unlink()
    print(f"  前回の残留PNG {len(_old_pngs)}件を削除しました")

# ---- REFグリッド準備 ----
_ref_img = mod.load_and_preprocess(REF_PATH, TRIM_BOTTOM)
_ref_al  = mod.apply_alignment(_ref_img, REF_PATH.name, shifts, _ref_img.shape)
_ref_cr  = mod.crop_roi(_ref_al, roi) if roi is not None else _ref_al
_h, _w   = _ref_cr.shape
half     = SUBSET_SIZE // 2
margin   = half + SEARCH_FINE + 5
_xs = np.arange(margin, _w - margin, STEP_FINE)
_ys = np.arange(margin, _h - margin, STEP_FINE)
_cx = np.array([x for _ in _ys for x in _xs])
_cy = np.array([y for y in _ys for _ in _xs])
_zeros = np.zeros(len(_cx))
_ones  = np.ones(len(_cx))
ref_stem = REF_PATH.stem
print(f"  REFグリッド: {len(_cx)}点")
_ref_strain = mod.calc_strain_field(_cx, _cy, _zeros, _zeros, STEP_FINE,
                                    gauge_length=GAUGE_LENGTH)
del _ref_img, _ref_al, _ref_cr

results_list = [{
    'label': REF_PATH.stem,
    'cx': _cx, 'cy': _cy,
    'u': _zeros.copy(), 'v': _zeros.copy(),
    'ncc': _ones.copy(),
    'strain': _ref_strain,
}]

# ---- 設定条件保存 ----
config_lines = [
    'DIC解析 設定条件', '=' * 40,
    f'REF          : {REF_PATH.name}',
    f'DEF          : {", ".join(p.name for p in DEF_PATHS)}',
    f'alignment file: {json_path.name if json_path else "なし"}',
    f'trim_bottom  : {TRIM_BOTTOM} px', '',
    '[Stage 1]',
    f'  step       : {STEP_COARSE} px',
    f'  search     : {"グローバルシフト + " + str(STAGE1_MARGIN) + " px（自動）" if STAGE1_AUTO else str(SEARCH_COARSE) + " px（固定）"}',
    f'  前段参照   : {"あり（高速化）" if USE_PREV_STAGE1 else "なし（独立処理）"}', '',
    '[Stage 2]',
    f'  step       : {STEP_FINE} px',
    f'  search     : {SEARCH_FINE} px', '',
    '[共通]',
    f'  subset     : {SUBSET_SIZE} px',
    f'  gauge      : {GAUGE_LENGTH} 倍',
    f'  NCC閾値    : {NCC_THRESHOLD}',
    f'  workers    : {mod.N_WORKERS}', '',
    '[カラースケール]',
]
for k, (lo, hi) in SCALE_CONFIG.items():
    config_lines.append(f'  {k:<12}: min={lo if lo is not None else "自動"}  max={hi if hi is not None else "自動"}')

config_path = OUTPUT_DIR / 'dic_config.txt'
with open(config_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(config_lines) + '\n')
print(f"  設定条件を保存しました: {config_path.name}")

# ---- パス1: 全DEFのDIC計算 ----
print(f"\n{'─' * 60}")
print(f"  [パス1] 全DEFのDIC計算（{total}ペア）")
print(f"{'─' * 60}")
prev_cx1 = prev_cy1 = prev_u1 = prev_v1 = None
for i, def_path in enumerate(DEF_PATHS, 1):
    print(f"\n[{i}/{total}] {def_path.name} を処理中...")
    prev_cx1, prev_cy1, prev_u1, prev_v1, res = mod.run_dic_pair(
        REF_PATH, def_path, shifts, roi,
        ncc_threshold=NCC_THRESHOLD, n_workers=mod.N_WORKERS,
        prev_cx1=prev_cx1, prev_cy1=prev_cy1,
        prev_u1=prev_u1,   prev_v1=prev_v1,
        save_png=False,
    )
    results_list.append(res)

# ---- カラースケール自動統一 ----
SCALE_KEYS_DISP = ['u', 'v']
SCALE_KEYS_SYM  = ['exx', 'eyy', 'exy', 'omega_xy']
SCALE_KEYS_ASYM = ['e1', 'gamma_max']

def _collect(key):
    arrays = []
    for res in results_list:
        if key in ('u', 'v', 'ncc'):
            arr = np.array(res[key], dtype=float).flatten()
        else:
            d = res['strain'].get(key)
            arr = np.array(d, dtype=float).flatten() if d is not None else np.array([])
        valid = arr[~np.isnan(arr)]
        if len(valid) > 0:
            arrays.append(valid)
    return np.concatenate(arrays) if arrays else np.array([])

unified_scale = {}
for k in SCALE_KEYS_DISP + SCALE_KEYS_SYM + SCALE_KEYS_ASYM:
    gui_lo, gui_hi = SCALE_CONFIG.get(k, (None, None))
    if gui_lo is not None and gui_hi is not None:
        unified_scale[k] = (gui_lo, gui_hi)
        continue
    vals = _collect(k)
    if len(vals) == 0:
        unified_scale[k] = (gui_lo, gui_hi)
        continue
    if k in SCALE_KEYS_DISP:
        vabs = max(float(np.percentile(np.abs(vals), 95)), 1.0)
        unified_scale[k] = (gui_lo if gui_lo is not None else -vabs,
                            gui_hi if gui_hi is not None else  vabs)
    elif k in SCALE_KEYS_SYM:
        vabs = max(float(np.percentile(np.abs(vals), 98)), 1e-6)
        unified_scale[k] = (gui_lo if gui_lo is not None else -vabs,
                            gui_hi if gui_hi is not None else  vabs)
    else:
        unified_scale[k] = (gui_lo if gui_lo is not None else float(np.percentile(vals, 2)),
                            gui_hi if gui_hi is not None else float(np.percentile(vals, 98)))
unified_scale['ncc'] = SCALE_CONFIG.get('ncc', (None, None))

# ---- パス2: PNG保存 ----
print(f"\n  [パス2] 統一スケールでPNG保存")
mod.visualize_displacement(_cx, _cy, _zeros, _zeros, _ones, 0, 0, STEP_FINE,
                           ncc_threshold=NCC_THRESHOLD, def_stem=ref_stem,
                           scale_config=unified_scale)
mod.visualize_strain(_ref_strain, STEP_FINE, REF_PATH.name, REF_PATH.name,
                     def_stem=ref_stem, scale_config=unified_scale)

for i, (def_path, res) in enumerate(zip(DEF_PATHS, results_list[1:]), 1):
    def_stem_i = def_path.stem
    print(f"  [{i}/{total}] PNG保存: {def_path.name}")
    mod.visualize_displacement(res['cx'], res['cy'], res['u'], res['v'],
                               res['ncc'], 0, 0, STEP_FINE,
                               ncc_threshold=NCC_THRESHOLD, def_stem=def_stem_i,
                               scale_config=unified_scale)
    mod.visualize_strain(res['strain'], STEP_FINE, REF_PATH.name, def_path.name,
                         def_stem=def_stem_i, scale_config=unified_scale)

# ---- Excel出力 ----
xlsx_path = OUTPUT_DIR / 'dic_results.xlsx'
print(f"\nExcelファイルを出力しています...")
mod.export_xlsx(results_list, unified_scale, xlsx_path, roi=roi)

print(f"\n{'=' * 60}")
print(f"  全{total}ペアの処理が完了しました！")
print(f"  出力先: {OUTPUT_DIR}")
print(f"{'=' * 60}")

import matplotlib.pyplot as plt
saved_pngs = sorted(OUTPUT_DIR.glob("*.png"))
for png in saved_pngs:
    img = plt.imread(str(png))
    fig, ax = plt.subplots(figsize=(min(img.shape[1]/100, 18), min(img.shape[0]/100, 10)))
    ax.imshow(img)
    ax.axis('off')
    ax.set_title(png.name, fontsize=10)
    plt.tight_layout()
plt.show()
