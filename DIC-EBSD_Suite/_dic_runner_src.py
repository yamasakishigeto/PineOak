import sys, json
from pathlib import Path

# 非日本語環境でも日本語出力が文字化けしないようにUTF-8に固定する
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
mod.ZNCC_THRESHOLD   = p["zncc_threshold"]
mod.N_WORKERS       = p["n_workers"]
mod.GAUGE_LENGTH    = p["gauge_length"]
mod.STRAIN_TYPE     = p.get("strain_type", "infinitesimal")
mod.SUBPIXEL_METHOD = p.get("subpixel_method", "parabolic")
mod.USE_PREV_STAGE1 = p["use_prev_stage1"]
_dt_raw = p.get("dt", None)
DT = float(_dt_raw) if (_dt_raw is not None and float(_dt_raw) > 0) else None

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

# ---- トリミング設定 ----
TRIM = (
    int(p.get('trim_top',    0)),
    int(p.get('trim_bottom', 0)),
    int(p.get('trim_left',   0)),
    int(p.get('trim_right',  0)),
)
mod.TRIM_TOP, mod.TRIM_BOTTOM, mod.TRIM_LEFT, mod.TRIM_RIGHT = TRIM

# ---- アライメント読み込み ----
shifts = mod.load_alignment(json_path)
STEP_FINE   = mod.STEP_FINE
STEP_COARSE = mod.STEP_COARSE
SEARCH_FINE = mod.SEARCH_FINE
SUBSET_SIZE = mod.SUBSET_SIZE
ZNCC_THRESHOLD = mod.ZNCC_THRESHOLD
GAUGE_LENGTH  = mod.GAUGE_LENGTH
STRAIN_TYPE   = mod.STRAIN_TYPE
STAGE1_AUTO   = mod.STAGE1_AUTO
STAGE1_MARGIN = mod.STAGE1_MARGIN
SEARCH_COARSE = mod.SEARCH_COARSE
USE_PREV_STAGE1 = mod.USE_PREV_STAGE1

# ---- ROI計算 ----
if shifts:
    _ref_pre = mod.load_and_preprocess(REF_PATH, TRIM)
    _h, _w = _ref_pre.shape[:2]
    del _ref_pre
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
_ref_img = mod.load_and_preprocess(REF_PATH, TRIM)
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
                                    gauge_length=GAUGE_LENGTH,
                                    strain_type=STRAIN_TYPE)
del _ref_img, _ref_al, _ref_cr

results_list = [{
    'label': REF_PATH.stem,
    'cx': _cx, 'cy': _cy,
    'u': _zeros.copy(), 'v': _zeros.copy(),
    'zncc': _ones.copy(),
    'strain': _ref_strain,
}]

# ---- 設定条件保存 ----
config_lines = [
    'DIC解析 設定条件', '=' * 40,
    f'REF          : {REF_PATH.name}',
    f'DEF          : {", ".join(p.name for p in DEF_PATHS)}',
    f'alignment file: {json_path.name if json_path else "なし"}',
    f'trim         : 上{TRIM[0]} 下{TRIM[1]} 左{TRIM[2]} 右{TRIM[3]} px', '',
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
    f'  ZNCC閾値    : {ZNCC_THRESHOLD}',
    f'  サブピクセル: {mod.SUBPIXEL_METHOD}',
    f'  workers    : {mod.N_WORKERS}',
    f'  dt         : {f"{DT:.3f} 秒/フレーム" if DT else "（未設定）"}', '',
    '[カラースケール]',
]
for k, (lo, hi) in SCALE_CONFIG.items():
    config_lines.append(f'  {k:<12}: min={lo if lo is not None else "自動"}  max={hi if hi is not None else "自動"}')

_CMAP_DEF = {'u':'RdBu_r','v':'RdBu_r','exx':'RdBu_r','eyy':'RdBu_r','exy':'RdBu_r',
             'e1':'hot_r','gamma_max':'hot_r','omega_xy':'RdBu_r',
             'exx_rate':'RdBu_r','eyy_rate':'RdBu_r','exy_rate':'RdBu_r',
             'e1_rate':'hot_r','gamma_max_rate':'hot_r'}
_cmap_cfg = p.get('cmap', {})
config_lines.append('')
config_lines.append('[カラーマップ]')
for k in ['u','v','exx','eyy','exy','e1','gamma_max','omega_xy']:
    config_lines.append(f'  {k:<12}: {_cmap_cfg.get(k) or _CMAP_DEF.get(k, "RdBu_r")}')
if DT is not None:
    config_lines.append('')
    config_lines.append('[ひずみ速度カラーマップ]')
    for k in ['exx_rate','eyy_rate','exy_rate','e1_rate','gamma_max_rate']:
        config_lines.append(f'  {k:<16}: {_cmap_cfg.get(k) or _CMAP_DEF.get(k, "RdBu_r")}')

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
        zncc_threshold=ZNCC_THRESHOLD, n_workers=mod.N_WORKERS,
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
        if key in ('u', 'v', 'zncc'):
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
unified_scale['zncc'] = SCALE_CONFIG.get('zncc', (None, None))

# ---- ひずみ速度スケール（Δt設定時のみ）----
if DT is not None:
    _rate_sym  = ['exx_rate', 'eyy_rate', 'exy_rate']
    _rate_asym = ['e1_rate', 'gamma_max_rate']
    def _rate_vals(rk):
        arrs = []
        for _res in results_list:
            _d = (_res.get('strain_rate') or {}).get(rk)
            if _d is not None:
                _v = np.array(_d, dtype=float).flatten()
                arrs.append(_v[~np.isnan(_v)])
        return np.concatenate(arrs) if arrs else np.array([])
    for _rk in _rate_sym:
        _gl, _gh = SCALE_CONFIG.get(_rk, (None, None))
        if _gl is not None and _gh is not None:
            unified_scale[_rk] = (_gl, _gh)
        else:
            _rv = _rate_vals(_rk)
            if len(_rv):
                _va = max(float(np.percentile(np.abs(_rv), 98)), 1e-12)
                unified_scale[_rk] = (_gl if _gl is not None else -_va,
                                      _gh if _gh is not None else  _va)
    for _rk in _rate_asym:
        _gl, _gh = SCALE_CONFIG.get(_rk, (None, None))
        if _gl is not None and _gh is not None:
            unified_scale[_rk] = (_gl, _gh)
        else:
            _rv = _rate_vals(_rk)
            if len(_rv):
                unified_scale[_rk] = (_gl if _gl is not None else float(np.percentile(_rv, 2)),
                                      _gh if _gh is not None else float(np.percentile(_rv, 98)))

# ---- パス2: ひずみ速度計算（Δt指定時のみ）----
if DT is not None:
    print(f"\n{'─' * 60}")
    print(f"  [パス2] ひずみ速度計算（Δt={DT:.3f} 秒/フレーム）")
    print(f"{'─' * 60}")
    _strain_rates = mod.calc_strain_rate(results_list, DT)
    for _res, _rate in zip(results_list, _strain_rates):
        _res['strain_rate'] = _rate
    print(f"  ひずみ速度を全{len(results_list)}フレームに設定しました")
else:
    print("\n  [パス2] Δt未設定 → ひずみ速度計算をスキップ")

# ---- 計算結果をpickleに保存（再描画・マップ保存ボタン用） ----
import pickle
pickle_path = OUTPUT_DIR / 'dic_results.pkl'
pickle_data = {
    'results_list':  results_list,
    'unified_scale': unified_scale,
    'step_fine':     STEP_FINE,
    'ref_path':      str(REF_PATH),
    'def_paths':     [str(p) for p in DEF_PATHS],
    'output_dir':    str(OUTPUT_DIR),
    'zncc_threshold': ZNCC_THRESHOLD,
    'roi':           roi,
    'gauge_length':  GAUGE_LENGTH,
    'strain_type':   STRAIN_TYPE,
    'dt':            DT,
}
with open(pickle_path, 'wb') as f:
    pickle.dump(pickle_data, f)

print(f"\n{'=' * 60}")
print(f"  全{total}ペアのDIC計算が完了しました！")
print(f"  出力先: {OUTPUT_DIR}")
print(f"  GUIの「再描画」ボタンでマップを確認し、")
print(f"  「マップ保存」ボタンでPNG/Excelを保存してください。")
print(f"{'=' * 60}")
