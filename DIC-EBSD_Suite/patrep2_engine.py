"""EBSD PatRep v2 エンジン。

現行版からの変更点:
  * 参照点リストを .mat の refloc から読む（xlsx 不要・日本語パス対応）
  * ステージ名は .mat の projectname から判定（"pre-processed" 命名規則が不要）
  * index（0基準）だけで完結（命名倍率・find_closest_tif・off-by-one が消える）
  * .up2 を直接パッチ（TIFF 展開が不要）。原本は触らず replaced_<stage>/ に出力
  * 粒単位でマッチ状況を集計し、1点でも未マッチの粒は「解析対象外」として出力
"""
import os, re, csv, glob
import numpy as np

from patrep2_matio import read_string_vars, read_numeric, stage_name
from patrep2_matching import Scan, match_stage, sym_ops, infer_reference_criterion
import patrep2_up2io as up2io

NUMVARS = ['euler_phi1', 'euler_phi', 'euler_phi2', 'image_quality',
           'phase_index', 'grain_number', 'kernel_average_misorientation',
           'numcols', 'numrows', 'xstep', 'ystep']

CSV_COLS = ['dst_index', 'src_index', 'status', 'src_from', 'grain_id', 'phase',
            'dst_col', 'dst_row', 'src_col', 'src_row', 'dx_px', 'dy_px', 'dist_um',
            'misorientation_deg', 'min_misorientation', 'src_IQ',
            'n_candidates', 'runner_up_IQ', 'runner_up_misorientation']

# 差し替え元の選び方
SOURCE_MODES = {
    'window':      '窓内から選ぶ（従来）',
    'refloc_only': '参照ステージの参照点のみ',
    'staged':      '参照ステージの参照点を優先し、無ければ窓内から選ぶ',
}


def discover_stages(folder):
    """フォルダ直下の .mat を projectname で識別する。{stage: [path, ...]}"""
    found = {}
    for p in sorted(glob.glob(os.path.join(folder, '*.mat'))):
        try:
            s = stage_name(p)
        except Exception:
            s = None
        if s:
            found.setdefault(s, []).append(p)
    return found


def load_scan(mat_path):
    num = read_numeric(mat_path, NUMVARS)
    miss = [v for v in ('euler_phi1', 'numcols', 'numrows') if v not in num]
    if miss:
        raise ValueError(f"{os.path.basename(mat_path)}: 必須変数がありません {miss}")
    return Scan(num, num['numcols'], num['numrows'],
                num.get('xstep', 1.0), num.get('ystep', 1.0))


def load_reference_indices(mat_path):
    """refloc から参照点の index（0基準）を取り出す。"""
    rl = read_string_vars(mat_path, {'refloc'}).get('refloc')
    if not rl:
        raise ValueError(f"{os.path.basename(mat_path)}: refloc が読めません")
    out = []
    for s in (rl if isinstance(rl, list) else [rl]):
        m = re.search(r',\s*(\d+)\s*$', str(s))
        if m:
            out.append(int(m.group(1)))
    if not out:
        raise ValueError(f"{os.path.basename(mat_path)}: refloc から index を抽出できません")
    return out


def grain_summary(rows):
    """粒ごとに全参照がマッチしたかを集計する。1点でも未マッチなら除外。"""
    g = {}
    for r in rows:
        gid = r.get('grain_id', -1)
        d = g.setdefault(gid, {'n': 0, 'ok': 0, 'bad': []})
        d['n'] += 1
        if r['status'] == 'ok':
            d['ok'] += 1
        else:
            d['bad'].append((r['dst_index'], r['status']))
    usable = {k: v for k, v in g.items() if k >= 0 and v['n'] == v['ok']}
    excluded = {k: v for k, v in g.items() if k < 0 or v['n'] != v['ok']}
    return usable, excluded


def write_csv(path, rows, meta):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        for k, v in meta.items():
            f.write(f"# {k}: {v}\n")
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction='ignore')
        w.writeheader()
        for r in sorted(rows, key=lambda x: x['dst_index']):
            w.writerow(r)


def write_map_png(path, nth, rows, title=''):
    """参照点の位置を IQ マップに重ねる。緑=マッチ, 赤=未マッチ。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    try:
        import japanize_matplotlib  # noqa: F401
    except Exception:
        pass

    iq = nth.iq.reshape(nth.nr, nth.nc).astype(float)
    fig, ax = plt.subplots(figsize=(max(6, nth.nc / 12), max(5, nth.nr / 12)))
    ax.imshow(iq, cmap='gray', origin='upper', interpolation='nearest')
    for r in rows:
        c, rw = r['dst_index'] % nth.nc, r['dst_index'] // nth.nc
        ok = (r['status'] == 'ok')
        ax.plot(c, rw, 'o', ms=5, mfc='none',
                mec='#00d000' if ok else '#ff0000', mew=1.4)
        if ok and (r.get('dx_px') or r.get('dy_px')):
            ax.arrow(c, rw, r['dx_px'], r['dy_px'], color='#00d000',
                     width=0.05, head_width=0.8, length_includes_head=True, alpha=.7)
    n_ok = sum(1 for r in rows if r['status'] == 'ok')
    ax.set_title(f"{title}   matched {n_ok}/{len(rows)}\n"
                 f"green = matched (arrow -> source in ref scan),  red = unmatched",
                 fontsize=9)
    ax.set_xlabel('col'); ax.set_ylabel('row')
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def run_stage(folder, ref_scan, ref_stage, stage, mat_path, params, log=print, apply=False,
              ref_ref_idx=None, ref_criterion='不明'):
    """1ステージ分の処理。戻り値 (rows, csv_path, png_path)

    ref_ref_idx : 参照ステージの参照点 index。refloc_only / staged で使う。
    """
    nth = load_scan(mat_path)
    ridx = load_reference_indices(mat_path)
    tgt_criterion, tgt_scores = infer_reference_criterion(nth, ridx)
    log(f"    参照点 {len(ridx)} 個 (refloc 由来, 0基準)  選定基準の推定: {tgt_criterion}")

    angle = float(params.get('angle_threshold', 5.0))
    xlim = params.get('x_limit'); ylim = params.get('y_limit')
    use_sym = bool(params.get('use_symmetry', False))
    psym = params.get('phase_sym_ops')
    mode = params.get('source_mode', 'window')
    if mode not in SOURCE_MODES:
        mode = 'window'
    log(f"    しきい値: 方位差 {angle}°  X {xlim}  Y {ylim} [px]  対称操作 {'あり' if use_sym else 'なし'}")
    log(f"    差し替え元: {SOURCE_MODES[mode]}")

    # 参照ステージの参照点だけを候補にするマスク
    pool = None
    if mode in ('refloc_only', 'staged'):
        if not ref_ref_idx:
            log("    WARNING: 参照ステージの参照点が読めないため、窓内から選ぶ方式に切り替えます")
            mode = 'window'
        else:
            pool = np.zeros(ref_scan.n, bool)
            valid = [i for i in ref_ref_idx if 0 <= i < ref_scan.n]
            pool[valid] = True
            log(f"    参照ステージ {ref_stage} の参照点 {int(pool.sum())} 個を候補にします")

    if mode == 'window':
        rows = match_stage(ref_scan, nth, ridx, angle, xlim, ylim, psym, use_sym)
    else:
        rows = match_stage(ref_scan, nth, ridx, angle, xlim, ylim, psym, use_sym,
                           allowed_src=pool, src_label='refloc')
        if mode == 'staged':
            miss = [r['dst_index'] for r in rows if r['status'] != 'ok']
            if miss:
                log(f"    参照点だけでは {len(miss)} 点が未マッチ → 窓内から探し直します")
                extra = {r['dst_index']: r for r in
                         match_stage(ref_scan, nth, miss, angle, xlim, ylim, psym, use_sym)}
                rows = [extra.get(r['dst_index'], r) if r['status'] != 'ok' else r for r in rows]

    ok = [r for r in rows if r['status'] == 'ok']
    n_refloc = sum(1 for r in ok if r.get('src_from') == 'refloc')
    log(f"    マッチ {len(ok)} / {len(rows)}"
        + (f"   （参照点由来 {n_refloc} / 窓内由来 {len(ok) - n_refloc}）" if mode == 'staged' else ""))
    if ok:
        d = np.array([r['dist_um'] for r in ok]); m = np.array([r['misorientation_deg'] for r in ok])
        log(f"    距離 [um]   中央 {np.median(d):6.2f}  p90 {np.percentile(d,90):6.2f}  max {d.max():6.2f}")
        log(f"    方位差[deg] 中央 {np.median(m):6.3f}  p90 {np.percentile(m,90):6.3f}  max {m.max():6.3f}")

    usable, excluded = grain_summary(rows)
    if rows and all(r.get('grain_id', -1) < 0 for r in rows):
        log("    WARNING: grain_number が読めないため粒単位の判定ができません")
    log(f"    粒: 使用可 {len(usable)} / 除外 {len(excluded)}")
    for gid, v in sorted(excluded.items()):
        log(f"      [除外] 粒 {gid}: 参照 {v['n']} 個中 {v['ok']} 個のみマッチ")
        for di, st in v['bad']:
            log(f"          index {di}: {st}")

    out_dir = os.path.join(folder, f"replaced_{stage}") if apply else folder
    csv_path = os.path.join(out_dir, f"replaced pattern list {ref_stage} to {stage}.csv")
    write_csv(csv_path, rows, {
        'ref_stage': ref_stage, 'target_stage': stage,
        'source_mode': f"{mode} ({SOURCE_MODES[mode]})",
        'ref_stage_reference_criterion': f"{ref_criterion}  ※.mat には記録されないためデータからの推定",
        'target_stage_reference_criterion': f"{tgt_criterion}  "
            + '  '.join(f"{k}={v*100:.1f}%" for k, v in sorted(tgt_scores.items())),
        'angle_threshold': angle, 'x_limit': xlim, 'y_limit': ylim,
        'use_symmetry': use_sym,
        'n_references': len(rows), 'n_matched': len(ok),
        'n_from_ref_reflocs': n_refloc,
        'n_from_window': len(ok) - n_refloc,
        'usable_grains': ','.join(str(g) for g in sorted(usable)),
        'excluded_grains': ','.join(str(g) for g in sorted(excluded)),
    })

    png_path = os.path.join(out_dir, f"matching map {ref_stage} to {stage}.png")
    try:
        write_map_png(png_path, nth, rows, title=f"{ref_stage} -> {stage}")
    except Exception as e:
        log(f"    WARNING: マップ生成に失敗: {e}")
        png_path = None

    if apply:
        # プレビュー時に親フォルダへ出した同名の記録は、いまの出力で置き換わるので消す
        for p in (csv_path, png_path):
            stale = os.path.join(folder, os.path.basename(p)) if p else None
            if stale and os.path.exists(stale) and os.path.abspath(stale) != os.path.abspath(p):
                os.remove(stale)
                log(f"    プレビュー時の {os.path.basename(stale)} を削除（{os.path.basename(out_dir)} 側に出力）")

        src_up2 = _find(folder, ref_stage, '.up2')
        dst_up2 = _find(folder, stage, '.up2')
        dst_osc = _find(folder, stage, '.osc')
        if not (src_up2 and dst_up2):
            log("    [スキップ] .up2 が見つからないため書き込みを行いません")
        elif not dst_osc:
            log(f"    [スキップ] {stage}.osc がありません。CrossCourt が読めないので書き込みません")
        else:
            log(f"    出力先 {out_dir}")
            backup_dir = os.path.join(out_dir, 'orig_patterns')
            # 退避が残っていない既存コピーは未差し替えか確認できないのでコピーし直す
            osc_out, up2_out, reused = up2io.prepare_output(
                dst_osc, dst_up2, out_dir, allow_reuse=os.path.isdir(backup_dir), log=log)
            if reused:
                up2io.restore(up2_out, backup_dir, log=log)
            src = up2io.Up2(src_up2)
            up2io.patch(src, up2_out, [(r['dst_index'], r['src_index']) for r in ok],
                        backup_dir=backup_dir, log=log)
            log(f"    CrossCourt には {osc_out} を読ませてください")

    return rows, csv_path, png_path


def _find(folder, stage, ext):
    """大文字小文字を無視して <stage><ext> を探す。"""
    for p in glob.glob(os.path.join(folder, '*' + ext)):
        if os.path.splitext(os.path.basename(p))[0].lower() == stage.lower():
            return p
    return None


def build_phase_sym(phase_sym_names):
    """{phase_index: 群名} → {phase_index: 対称操作行列}"""
    return {int(k): sym_ops(v) for k, v in (phase_sym_names or {}).items()}
