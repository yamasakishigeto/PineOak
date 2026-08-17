"""
_patrep_runner.py  (v2)
=======================
EBSD PatRep バッチ処理の param-driven ランナー。

v1 からの変更:
  * 参照点リストを .mat の refloc から読む（xlsx 不要・日本語パス対応）
  * ステージ名は .mat の projectname から判定（"pre-processed" 命名規則が不要）
  * index（0基準）のみで完結（命名倍率・find_closest_tif・off-by-one が消滅）
  * .up2 を直接パッチ。原本は触らず replaced_<stage>/ に .osc とペアで出力
  * 粒単位のマッチ集計。1点でも未マッチの粒は「解析対象外」として出力

標準出力の書式は v1 と同じ。使わなくなったパラメータ（scale_factor,
nth_xlsx_overrides, ref_tif, nth_folder_names）はウィザードから送られなく
なったが、古い JSON をそのまま流せるよう受け取っても無視する。

JSON 形式:
{
    "mode":             "preview" | "execute",
    "patrep_dir":       "...",
    "parent_folder":    "...",
    "ref_name":         "0th",
    "nth_names":        ["1th", "2th"],
    "angle_threshold":  5.0,
    "phase_sym":        {"0": "cubic"},
    "use_symmetry":     false,
    "x_limit":          null,
    "y_limit":          null,
    "nth_thresholds":   {"1th": {"angle_threshold": 3.0, "x_limit": 5, "y_limit": 5}},
    "nth_mat_overrides": {"1th": "..."}
}
"""
import sys
import os
import json
import base64
import traceback

import matplotlib
matplotlib.use('Agg')

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

if len(sys.argv) < 2:
    print("Usage: python _patrep_runner.py <param_file.json>")
    sys.exit(1)

with open(sys.argv[1], encoding='utf-8') as _f:
    params = json.load(_f)

mode          = params.get('mode', 'preview')
patrep_dir    = params.get('patrep_dir') or os.path.dirname(os.path.abspath(__file__))
parent_folder = params['parent_folder']
ref_name      = params.get('ref_name', 'ref')
nth_names     = params.get('nth_names', [])
angle_thr     = float(params.get('angle_threshold', 5.0))
phase_sym     = params.get('phase_sym', {})
use_symmetry  = bool(params.get('use_symmetry', False))
x_limit       = params.get('x_limit', None)
y_limit       = params.get('y_limit', None)
nth_thresholds = params.get('nth_thresholds', {})
nth_mat_over  = params.get('nth_mat_overrides', {})
source_mode   = params.get('source_mode', 'window')
output_folder = params.get('output_folder') or None

sys.path.insert(0, patrep_dir)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patrep2_engine import (discover_stages, load_scan, run_stage, build_phase_sym,   # noqa: E402
                            load_reference_indices, SOURCE_MODES)
from patrep2_matching import infer_reference_criterion                                # noqa: E402


def log(*a):
    print(*a, flush=True)


log("=" * 55)
log(f"  EBSD PatRep v2   モード: {'置き換え実行' if mode == 'execute' else 'プレビュー'}")
log("=" * 55)

# ---- ステージの識別（projectname 由来。ファイル名の命名規則に依存しない） ----
found = discover_stages(parent_folder)
log(f"\n  .mat の識別: {len(found)} ステージ")
for s, ps in sorted(found.items()):
    log(f"    {s:14s} <- {', '.join(os.path.basename(p) for p in ps)}")

if ref_name not in found:
    log(f"ERROR: 参照ステージ '{ref_name}' の .mat が見つかりません")
    sys.exit(1)

log(f"\n  参照スキャン {ref_name} を読み込み中 ...")
try:
    ref_scan = load_scan(found[ref_name][0])
except Exception as e:
    log(f"ERROR: 参照スキャンの読み込みに失敗: {e}")
    traceback.print_exc()
    sys.exit(1)
log(f"    {ref_scan.nc}x{ref_scan.nr} = {ref_scan.n} 点, "
    f"step {ref_scan.xstep:g}x{ref_scan.ystep:g} um, 有効 {int(ref_scan.valid.sum())} 点")

# 参照ステージの参照点（refloc）と、その選定基準の推定
try:
    ref_ref_idx = load_reference_indices(found[ref_name][0])
    ref_crit, _ = infer_reference_criterion(ref_scan, ref_ref_idx)
    log(f"    参照点 {len(ref_ref_idx)} 個  選定基準の推定: {ref_crit}")
except Exception as e:
    ref_ref_idx, ref_crit = None, '不明'
    log(f"    WARNING: 参照ステージの参照点を読めません: {e}")

log(f"\n  差し替え元の選び方: {SOURCE_MODES.get(source_mode, source_mode)}")

# 出力先。指定が無ければ親フォルダの下に replaced_<stage>/ を作る（従来どおり）
if mode == 'execute':
    if output_folder:
        if not os.path.isdir(output_folder):
            try:
                os.makedirs(output_folder, exist_ok=True)
            except Exception as e:
                log(f"ERROR: 出力先フォルダを作れません: {output_folder}  ({e})")
                sys.exit(1)
        log(f"  出力先: {output_folder}")
        log("         このフォルダに直接出力します。参照ステージを変えて出すときは")
        log("         .up2 が上書きされるので、出力先フォルダを分けてください")
    else:
        log(f"  出力先: {parent_folder}\\replaced_<ステージ名>\\  （親フォルダの下）")

# phase → 対称群
if not phase_sym:
    import numpy as _np
    ph = ref_scan.phase
    phase_sym = {str(int(v)): 'cubic' for v in _np.unique(ph[_np.isfinite(ph)])}
sym_ops_map = build_phase_sym(phase_sym)
for k, v in sorted(phase_sym.items()):
    log(f"    Phase {k}: {v}")

n_success = 0
for nth_name in nth_names:
    log("\n" + "=" * 55)
    log(f"  Processing: {nth_name}")
    log("=" * 55)
    try:
        mat_stage = nth_mat_over.get(nth_name, nth_name)
        if mat_stage not in found:
            log(f"  ERROR [{nth_name}]: ステージ '{mat_stage}' の .mat が見つかりません")
            continue

        ovr = nth_thresholds.get(nth_name, {})
        p = dict(
            angle_threshold=float(ovr.get('angle_threshold', angle_thr)),
            x_limit=ovr.get('x_limit', x_limit),
            y_limit=ovr.get('y_limit', y_limit),
            use_symmetry=use_symmetry,
            phase_sym_ops=sym_ops_map,
            source_mode=source_mode,
        )
        rows, csv_path, png_path, csv_body = run_stage(
            parent_folder, ref_scan, ref_name, nth_name, found[mat_stage][0],
            p, log=log, apply=(mode == 'execute'),
            ref_ref_idx=ref_ref_idx, ref_criterion=ref_crit,
            out_root=output_folder)

        # プレビューでは保存しないので、一覧の中身そのものを GUI へ渡す
        if csv_body:
            b64 = base64.b64encode(csv_body.encode('utf-8')).decode('ascii')
            log(f"PREVIEW_TABLE:{nth_name}:{b64}")
        if csv_path:
            log(f"    一覧を保存: {csv_path}")
        if png_path:
            log(f"PREVIEW_PNG:{nth_name}:{png_path}")
        n_success += 1
        log(f"  {nth_name}: 完了")

    except Exception as e:
        log(f"  ERROR [{nth_name}]: {e}")
        traceback.print_exc()

log("\n" + "=" * 55)
log(f"  {'全処理完了' if mode == 'execute' else 'プレビュー完了'}  ({n_success}/{len(nth_names)} 成功)")
log("=" * 55)
