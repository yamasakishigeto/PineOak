"""EBSD PatRep v2 エンジン。

現行版からの変更点:
  * 参照点リストを .mat の refloc から読む（xlsx 不要・日本語パス対応）
  * ステージ名は .mat の projectname から判定（"pre-processed" 命名規則が不要）
  * index（0基準）だけで完結（命名倍率・find_closest_tif・off-by-one が消える）
  * .up2 を直接パッチ（TIFF 展開が不要）。原本は触らず replaced_<stage>/ に出力
  * 粒単位でマッチ状況を集計し、1点でも未マッチの粒は「解析対象外」として出力
"""
import os, re, csv, glob, io, tempfile
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


def csv_text(rows, meta):
    """CSV の中身を文字列で組み立てる（プレビューは保存せず GUI に渡すため）。"""
    buf = io.StringIO()
    for k, v in meta.items():
        buf.write(f"# {k}: {v}\n")
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction='ignore', lineterminator='\n')
    w.writeheader()
    for r in sorted(rows, key=lambda x: x['dst_index']):
        w.writerow(r)
    return buf.getvalue()


def write_csv(path, rows, meta):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write(csv_text(rows, meta))


def write_map_png(path, nth, rows, title=''):
    """参照点の位置を結晶粒マップに重ねる。

    背景を粒番号にしているのは、参照点が粒のどこにあるか、矢印が粒を
    またいでいないかを見るため。IQ を背景にすると粒界が見えない。
    粒番号が無いデータでは IQ にフォールバックする。
    矢印は差し替え元への向きと距離。参照スキャン側の座標を重ねて描いて
    いるので、スキャン間のズレぶん粒からはみ出して見えることがある。
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    g = nth.grain.reshape(nth.nr, nth.nc).astype(float)
    if np.any(np.isfinite(g)):
        ng = max(int(np.nanmax(g)) + 1, 1)
        rng = np.random.default_rng(3)                    # 毎回同じ配色にする
        cols = plt.get_cmap('tab20')(np.linspace(0, 1, 20))[rng.integers(0, 20, ng)]
        cols[:, :3] = cols[:, :3] * 0.5 + 0.5             # 記号が見えるよう淡くする
        cmap = ListedColormap(cols)
        cmap.set_bad('white')                             # 粒番号なし（粒界付近）
        bg, bg_name, lim = np.ma.masked_invalid(g), 'grain number', (0, ng - 1)
    else:
        bg, bg_name, lim = nth.iq.reshape(nth.nr, nth.nc).astype(float), 'image quality', (None, None)
        cmap = 'gray'

    # 参照点由来と窓内由来が混在する（段階的モード）ときだけ色を分ける
    mixed = (any(r.get('src_from') == 'refloc' for r in rows)
             and any(r.get('src_from') == 'window' for r in rows))

    fig, ax = plt.subplots(figsize=(max(6, nth.nc / 8), max(5, nth.nr / 8)))
    ax.imshow(bg, cmap=cmap, origin='upper', interpolation='nearest', vmin=lim[0], vmax=lim[1])
    n_win = 0
    for r in rows:
        c, rw = r['dst_index'] % nth.nc, r['dst_index'] // nth.nc
        ok = (r['status'] == 'ok')
        win = ok and mixed and r.get('src_from') == 'window'
        n_win += win
        col = '#d0021b' if not ok else ('#ff8c00' if win else '#0b6e00')
        ax.plot(c, rw, 'o', ms=6, mfc='none', mec=col, mew=1.6)
        if ok and (r.get('dx_px') or r.get('dy_px')):
            ax.arrow(c, rw, r['dx_px'], r['dy_px'], color=col,
                     width=0.06, head_width=0.9, length_includes_head=True, alpha=.9)
    n_ok = sum(1 for r in rows if r['status'] == 'ok')
    legend = (f"green = from ref reflocs ({n_ok - n_win}),  orange = from window ({n_win}),  red = unmatched"
              if mixed else "green = matched (arrow -> source in ref scan),  red = unmatched")
    ax.set_title(f"{title}   background = {bg_name}   matched {n_ok}/{len(rows)}\n{legend}", fontsize=9)
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
    tgt_criterion, _ = infer_reference_criterion(nth, ridx)
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

    # プレビューはデータフォルダに何も残さない。記録は GUI で見る
    out_dir = (os.path.join(folder, f"replaced_{stage}") if apply
               else os.path.join(tempfile.gettempdir(), 'patrep_preview'))
    meta = {
        'ref_stage': ref_stage, 'target_stage': stage,
        'source_mode': f"{mode} ({SOURCE_MODES[mode]})",
        'ref_stage_reference_criterion': ref_criterion,
        'target_stage_reference_criterion': tgt_criterion,
        'reference_criterion_note':
            'CrossCourt は参照点の選定基準を .mat に記録しないため、結果から推定した。'
            '各参照点について、同じ粒の中に自分より良い点がどれだけあるかを IQ と KAM で数え、'
            '一貫して粒内の上位に来るほうを採用している。'
            'どちらも上位に来ない場合や、両者に差がつかない場合は「不明」とする。',
        'angle_threshold': angle, 'x_limit': xlim, 'y_limit': ylim,
        'use_symmetry': use_sym,
        'n_references': len(rows), 'n_matched': len(ok),
        'n_from_ref_reflocs': n_refloc,
        'n_from_window': len(ok) - n_refloc,
        'usable_grains': ','.join(str(g) for g in sorted(usable)),
        'excluded_grains': ','.join(str(g) for g in sorted(excluded)),
    }
    text = csv_text(rows, meta)

    csv_path = None
    if apply:
        csv_path = os.path.join(out_dir, f"replaced pattern list {ref_stage} to {stage}.csv")
        write_csv(csv_path, rows, meta)

    png_path = os.path.join(out_dir, f"matching map {ref_stage} to {stage}.png")
    try:
        os.makedirs(out_dir, exist_ok=True)
        write_map_png(png_path, nth, rows, title=f"{ref_stage} -> {stage}")
    except Exception as e:
        log(f"    WARNING: マップ生成に失敗: {e}")
        png_path = None

    if apply:
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

    return rows, csv_path, png_path, text


def _find(folder, stage, ext):
    """大文字小文字を無視して <stage><ext> を探す。"""
    for p in glob.glob(os.path.join(folder, '*' + ext)):
        if os.path.splitext(os.path.basename(p))[0].lower() == stage.lower():
            return p
    return None


def build_phase_sym(phase_sym_names):
    """{phase_index: 群名} → {phase_index: 対称操作行列}"""
    return {int(k): sym_ops(v) for k, v in (phase_sym_names or {}).items()}
