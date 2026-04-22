"""
統合ランチャー main.py  # v12
実行: py -3.13 main.py
"""

import eel
import subprocess
import sys
import os
import threading
from pathlib import Path

# --- パス設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = BASE_DIR
PYTHON = sys.executable

SEM_ALIGN_HTML = os.path.join(TOOLS_DIR, "sem_align_tool_v3.html")

# Eelの初期化（index.html / dic_wizard.html はmain.pyと同じフォルダ）
eel.init(BASE_DIR)

# 起動中のプロセスを追跡
_procs = {}

# 作業フォルダ
_work_dir = None

# DIC解析パラメータ（ウィザードから受け取る）
_dic_params = None


def _run_script(tool_id: str, script_path: str, work_dir: str = None):
    """別スレッドでPythonスクリプトを起動し、完了をHTML側に通知する"""
    try:
        cmd = [PYTHON, script_path]
        if work_dir:
            cmd.append(work_dir)
        proc = subprocess.Popen(cmd, cwd=TOOLS_DIR)
        _procs[tool_id] = proc
        eel.on_tool_started(tool_id)()
        proc.wait()
        returncode = proc.returncode
        _procs.pop(tool_id, None)
        eel.on_tool_finished(tool_id, returncode)()
    except FileNotFoundError:
        eel.on_tool_error(tool_id, f"ファイルが見つかりません: {script_path}")()
    except Exception as e:
        eel.on_tool_error(tool_id, str(e))()


# ================================================================
# ランチャー共通
# ================================================================

@eel.expose
def set_work_dir(path: str):
    global _work_dir
    _work_dir = path if path else None
    return _work_dir


@eel.expose
def get_work_dir():
    return _work_dir or ""


@eel.expose
def browse_work_dir():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    path = filedialog.askdirectory(title='作業フォルダを選択')
    root.destroy()
    if path:
        global _work_dir
        _work_dir = path
        return path
    return ""


@eel.expose
def launch_sem_align():
    """SEM位置合わせ: Eelウィンドウで開く"""
    try:
        eel.on_tool_started("sem_align")()
        eel.start("sem_align_tool_v3.html", size=(1280, 860), block=False)
        eel.on_tool_finished("sem_align", 0)()
    except Exception as e:
        eel.on_tool_error("sem_align", str(e))()


@eel.expose
def launch_dic():
    """DICウィザードHTMLを別ウィンドウで開く"""
    eel.start("dic_wizard.html", size=(1200, 900), block=False)


@eel.expose
def launch_ebsd():
    """EBSDウィザードHTMLを別ウィンドウで開く"""
    eel.start("ebsd_wizard.html", size=(740, 510), block=False)


@eel.expose
def launch_heaviside():
    """Heavisideウィザードを別ウィンドウで開く"""
    eel.start("heaviside_wizard.html", size=(740, 550), block=False)



# ================================================================
# EBSDウィザード用
# ================================================================

@eel.expose
def ebsd_browse_file(title: str, filetypes: list, initialdir: str):
    return _tk_filedialog('file', title,
                          filetypes=[tuple(f) for f in filetypes],
                          initialdir=initialdir or _work_dir)


@eel.expose
def ebsd_browse_dir(title: str, initialdir: str):
    return _tk_filedialog('dir', title, initialdir=initialdir or _work_dir)


@eel.expose
def ebsd_start_analysis(paths: dict):
    threading.Thread(target=_run_ebsd_analysis, args=(paths,), daemon=True).start()


def _run_ebsd_analysis(paths: dict):
    import json, tempfile
    param_file = tempfile.mktemp(suffix='.json')
    with open(param_file, 'w') as f:
        json.dump(paths, f)

    ebsd_script = os.path.join(TOOLS_DIR, "ebsd_georef_v68.py")

    eel.on_tool_started("ebsd")()
    eel.ebsd_on_status("起動中...", "ok")()

    # JSONパスを直接 ebsd_georef_v68.py に渡す（ファイル選択ダイアログをスキップ）
    _env = os.environ.copy()
    _env['MPLBACKEND'] = 'QtAgg'
    _env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.Popen(
        [PYTHON, ebsd_script, param_file],
        cwd=TOOLS_DIR,
        env=_env,
    )
    _procs["ebsd"] = proc
    proc.wait()
    _procs.pop("ebsd", None)

    if proc.returncode == 0:
        eel.on_tool_finished("ebsd", 0)()
        eel.ebsd_on_status("完了", "ok")()
    else:
        eel.on_tool_finished("ebsd", proc.returncode)()
        eel.ebsd_on_status(f"エラー（終了コード {proc.returncode}）", "err")()


# ================================================================
# Heavisideウィザード用
# ================================================================

@eel.expose
def hv_browse_file(title: str, filetypes: list, initialdir: str):
    return _tk_filedialog('file', title,
                          filetypes=[tuple(f) for f in filetypes],
                          initialdir=initialdir or _work_dir)


@eel.expose
def hv_browse_dir(title: str, initialdir: str):
    return _tk_filedialog('dir', title, initialdir=initialdir or _work_dir)


@eel.expose
def hv_start_analysis(paths: dict):
    threading.Thread(target=_run_hv_analysis, args=(paths,), daemon=True).start()


def _run_hv_analysis(paths: dict):
    import json, tempfile
    param_file = tempfile.mktemp(suffix='.json')
    with open(param_file, 'w') as f:
        json.dump(paths, f)

    hv_script = os.path.join(TOOLS_DIR, "heaviside_dic_v81.py")

    eel.on_tool_started("heaviside")()
    eel.hv_on_status("起動中...", "ok")()

    # JSONパスを直接 heaviside_dic_v81.py に渡す（ウィザードをスキップ）
    _env = os.environ.copy()
    _env['MPLBACKEND'] = 'QtAgg'
    _env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.Popen(
        [PYTHON, hv_script, param_file],
        cwd=TOOLS_DIR,
        env=_env,
    )
    _procs["heaviside"] = proc
    proc.wait()
    _procs.pop("heaviside", None)

    if proc.returncode == 0:
        eel.on_tool_finished("heaviside", 0)()
        eel.hv_on_status("完了", "ok")()
    else:
        eel.on_tool_finished("heaviside", proc.returncode)()
        eel.hv_on_status(f"エラー（終了コード {proc.returncode}）", "err")()


@eel.expose
def get_tools_dir():
    return TOOLS_DIR


# ================================================================
# DICウィザード用
# ================================================================

def _tk_filedialog(kind, title, filetypes=None, initialdir=None):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    if kind == 'dir':
        path = filedialog.askdirectory(title=title, initialdir=initialdir)
    else:
        path = filedialog.askopenfilename(title=title, filetypes=filetypes or [],
                                          initialdir=initialdir)
    root.destroy()
    return path or ""


@eel.expose
def dic_browse_folder():
    """画像フォルダ選択"""
    return _tk_filedialog('dir', 'SEM画像フォルダを選択', initialdir=_work_dir)


@eel.expose
def dic_browse_json(folder: str):
    """アライメントJSON選択"""
    return _tk_filedialog('file', 'アライメントJSONを選択（キャンセルでスキップ）',
                          filetypes=[('JSON', '*.json'), ('All', '*.*')],
                          initialdir=folder or _work_dir)


@eel.expose
def dic_list_images(folder: str):
    """フォルダ内の画像ファイル一覧を返す（自然順ソート）"""
    import re
    exts = {'.bmp', '.png', '.tif', '.tiff', '.jpg', '.jpeg'}

    def natural_key(f):
        # ファイル名中の数字を数値として解釈してソート
        return [int(c) if c.isdigit() else c.lower()
                for c in re.split(r'(\d+)', f.name)]

    try:
        files = sorted(
            [f for f in Path(folder).iterdir()
             if f.is_file() and f.suffix.lower() in exts],
            key=natural_key
        )
        return [{'name': f.name, 'path': str(f)} for f in files]
    except Exception as e:
        return []


@eel.expose
def dic_load_config(folder: str):
    """dic_config.txt を選択して読み込む"""
    import re
    path = _tk_filedialog('file', 'dic_config.txt を選択',
                          filetypes=[('テキスト', '*.txt'), ('All', '*.*')],
                          initialdir=folder or _work_dir)
    if not path:
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        result = {}
        in_s1 = in_s2 = in_scale = False

        for line in lines:
            line = line.strip()
            if line == '[Stage 1]':   in_s1, in_s2, in_scale = True,  False, False; continue
            if line == '[Stage 2]':   in_s1, in_s2, in_scale = False, True,  False; continue
            if line == '[カラースケール]': in_s1, in_s2, in_scale = False, False, True;  continue
            if line.startswith('['):  in_s1, in_s2, in_scale = False, False, False; continue
            if ':' not in line: continue

            k, _, v = line.partition(':')
            k, v = k.strip(), v.strip()

            if not in_s1 and not in_s2 and not in_scale:
                if k == 'subset':   result['subset']   = int(v.split()[0])
                if k == 'gauge':    result['gauge']    = int(v.split()[0])
                if k == 'workers':  result['workers']  = int(v.split()[0])
                if k == 'NCC閾値':  result['ncc_thr']  = float(v.split()[0])

            if in_s1:
                if k == 'step':   result['s1_step'] = int(v.split()[0])
                if k == 'search':
                    if 'グローバルシフト' in v:
                        result['s1_auto'] = True
                        m = re.search(r'\+\s*(\d+)', v)
                        if m: result['s1_margin'] = int(m.group(1))
                    else:
                        result['s1_auto'] = False
                        try: result['s1_fixed'] = int(v.split()[0])
                        except: pass
                if k == '前段参照': result['use_prev_s1'] = 'あり' in v

            if in_s2:
                if k == 'step':   result['s2_step']   = int(v.split()[0])
                if k == 'search': result['s2_search'] = int(v.split()[0])

            if in_scale:
                scale_keys = {'u','v','exx','eyy','exy','e1','gamma_max','omega_xy'}
                if k in scale_keys:
                    m_min = re.search(r'min=([^\s]+)', v)
                    m_max = re.search(r'max=([^\s]+)', v)
                    lo = float(m_min.group(1)) if m_min and m_min.group(1) != '自動' else None
                    hi = float(m_max.group(1)) if m_max and m_max.group(1) != '自動' else None
                    result.setdefault('scale', {})[k] = [lo, hi]

        return result
    except Exception as e:
        return {'error': str(e)}


@eel.expose
def dic_run_prescan(params: dict):
    """事前スキャンを実行して結果を返す"""
    try:
        # dic_sem_strain をインポートして prescan 関数を使う
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dic_sem_strain",
            os.path.join(TOOLS_DIR, "dic_sem_strain_v58.py")
        )
        mod = importlib.util.module_from_spec(spec)

        # サブプロセスで実行する方式（モジュールimportが複雑なため）
        import json, tempfile
        param_file = tempfile.mktemp(suffix='.json')
        result_file = tempfile.mktemp(suffix='.json')
        with open(param_file, 'w') as f:
            json.dump(params, f)

        script = os.path.join(TOOLS_DIR, "_prescan_runner.py")
        _write_prescan_runner(script)

        _env = os.environ.copy()
        _env['PYTHONIOENCODING'] = 'utf-8'
        proc = subprocess.run(
            [PYTHON, script, param_file, result_file,
             os.path.join(TOOLS_DIR, "dic_sem_strain_v58.py")],
            timeout=120,
            env=_env,
        )

        if os.path.exists(result_file):
            with open(result_file, 'r') as f:
                return json.load(f)
        return {'error': '結果ファイルが生成されませんでした'}

    except subprocess.TimeoutExpired:
        return {'error': 'タイムアウト（120秒）'}
    except Exception as e:
        return {'error': str(e)}


def _write_prescan_runner(path: str):
    """事前スキャン用の一時スクリプトを生成する"""
    code = '''
import sys, json
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

param_file  = sys.argv[1]
result_file = sys.argv[2]
dic_module  = sys.argv[3]

with open(param_file) as f:
    p = json.load(f)

import importlib.util
spec = importlib.util.spec_from_file_location("dic", dic_module)
mod  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ref_raw  = mod.load_and_preprocess(Path(p["ref_path"]), 0)
def_raw  = mod.load_and_preprocess(Path(p["def_path"]),  0)

json_path = Path(p["json_path"]) if p.get("json_path") else None
shifts, trim = mod.load_alignment(json_path)

ref_al = mod.apply_alignment(ref_raw, Path(p["ref_path"]).name, shifts, ref_raw.shape)
def_al = mod.apply_alignment(def_raw, Path(p["def_path"]).name, shifts, def_raw.shape)

if shifts:
    import cv2, numpy as np
    h, w = ref_al.shape[:2]
    if trim > 0: h -= trim
    roi = mod.calc_valid_roi(shifts, (h, w))
    ref_cr = mod.crop_roi(ref_al, roi)
    def_cr = mod.crop_roi(def_al, roi)
else:
    ref_cr, def_cr = ref_al, def_al

_, rec, est = mod.prescan(
    ref_cr, def_cr,
    subset_size=p["subset_size"],
    search_range=p["prescan_search"],
)

s1_margin = p.get("s1_margin", 15)
ok = rec <= s1_margin
status = (
    f"✓ 推奨 {rec}px ≤ 現在のStage1 search {s1_margin}px → OK"
    if ok else
    f"⚠ 推奨 {rec}px > 現在のStage1 search {s1_margin}px → 要見直し"
)

lines = [f"対象: {Path(p['def_path']).name}", status, "", "【参考：変位・ひずみ推定値】",
         "  変数          min        max"]
lmap = [("u [px]","u"),("v [px]","v"),("exx [-]","exx"),("eyy [-]","eyy"),
        ("exy [-]","exy"),("e1 [-]","e1"),("γmax [-]","gamma_max"),("ωxy [-]","omega_xy")]
for lbl, key in lmap:
    lo, hi = est[key]
    lines.append(f"  {lbl:<10}  {lo:>8.4f}  {hi:>8.4f}")

result = {
    "rec_search": rec,
    "ok": ok,
    "text": "\\n".join(lines),
    "estimates": {k: list(v) for k, v in est.items()},
}

with open(result_file, "w") as f:
    json.dump(result, f)
'''
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)


@eel.expose
def dic_start_analysis(params: dict):
    """DIC解析をバックグラウンドで開始する"""
    global _dic_params
    _dic_params = params
    threading.Thread(target=_run_dic_analysis, args=(params,), daemon=True).start()


def _run_dic_analysis(params: dict):
    """DIC解析本体をサブプロセスで実行する"""
    import json, tempfile
    param_file = tempfile.mktemp(suffix='.json')
    with open(param_file, 'w') as f:
        json.dump(params, f, default=str)

    script = os.path.join(TOOLS_DIR, "_dic_runner.py")
    _write_dic_runner(script)

    eel.on_tool_started("dic")()
    eel.dic_on_status("解析中...", "ok")()

    _env = os.environ.copy()
    _env['MPLBACKEND'] = 'QtAgg'
    _env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.Popen(
        [PYTHON, script, param_file, os.path.join(TOOLS_DIR, "dic_sem_strain_v58.py")],
        cwd=TOOLS_DIR,
        env=_env,
    )
    _procs["dic"] = proc
    proc.wait()
    _procs.pop("dic", None)

    if proc.returncode == 0:
        eel.on_tool_finished("dic", 0)()
        eel.dic_on_status("解析完了", "ok")()
    else:
        eel.on_tool_finished("dic", proc.returncode)()
        eel.dic_on_status(f"エラー（終了コード {proc.returncode}）", "err")()


def _write_dic_runner(path: str):
    """DIC解析実行用の一時スクリプトを生成する"""
    code = open(__file__.replace('main.py', '_dic_runner_src.py'), 'r', encoding='utf-8').read()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)


@eel.expose
def dic_cancel():
    """DIC解析をキャンセルする"""
    proc = _procs.get("dic")
    if proc:
        proc.terminate()


@eel.expose
def dic_show_trim_preview(params: dict):
    """REF画像にトリミング範囲を重ねて別プロセスのウィンドウで表示する"""
    import subprocess, sys, json

    _script = r"""
import sys, json, os
import numpy as np
import cv2
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

p      = json.loads(sys.argv[1])
path   = p['ref_path']
top    = int(p.get('trim_top',    0))
bottom = int(p.get('trim_bottom', 0))
left   = int(p.get('trim_left',   0))
right  = int(p.get('trim_right',  0))

buf = np.fromfile(path, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
if img is None:
    sys.exit(1)
h, w = img.shape

fig, ax = plt.subplots(figsize=(7, 6))
ax.imshow(img, cmap='gray', vmin=0, vmax=255)
ax.set_xlim(-0.5, w - 0.5)
ax.set_ylim(h - 0.5, -0.5)

def shade(x0, y0, bw, bh):
    ax.add_patch(Rectangle((x0, y0), bw, bh, linewidth=0, facecolor='red', alpha=0.35))

if top    > 0: ax.axhline(y=top-0.5,          color='red',lw=1.5,ls='--'); shade(-0.5,-0.5,      w,       top)
if bottom > 0: ax.axhline(y=h-bottom-0.5,     color='red',lw=1.5,ls='--'); shade(-0.5,h-bottom-0.5, w,  bottom)
if left   > 0: ax.axvline(x=left-0.5,         color='red',lw=1.5,ls='--'); shade(-0.5,-0.5,      left,   h)
if right  > 0: ax.axvline(x=w-right-0.5,      color='red',lw=1.5,ls='--'); shade(w-right-0.5,-0.5,right, h)

rw = max(0, w-left-right)
rh = max(0, h-top-bottom)
ax.set_title(f'{os.path.basename(path)}\nOriginal: {w}x{h}px  ->  Valid area: {rw}x{rh}px', fontsize=9)
ax.axis('off')
fig.tight_layout()
plt.show()
"""

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    subprocess.Popen(
        [sys.executable, '-c', _script, json.dumps(params)],
        env=env,
    )


# ================================================================
# Def EBSD Georef ウィザード用
# ================================================================

@eel.expose
def launch_stress_strain_mapper():
    """Stress-Strain Mapper を起動する（matファイルをダイアログで選択）"""
    mat_path = _tk_filedialog(
        'file',
        'integrated_georef.mat を選択',
        filetypes=[('MAT files', '*.mat'), ('All files', '*.*')],
        initialdir=_work_dir,
    )
    if not mat_path:
        return
    script = os.path.join(TOOLS_DIR, 'stress_strain_mapper_v2.py')
    _env = os.environ.copy()
    _env['QT_API'] = 'PyQt6'
    _env['PYTHONIOENCODING'] = 'utf-8'
    threading.Thread(
        target=lambda: subprocess.Popen(
            [PYTHON, script, '--file', mat_path],
            cwd=TOOLS_DIR, env=_env,
        ).wait(),
        daemon=True,
    ).start()


@eel.expose
def launch_defebsd():
    """Def EBSD Georef ウィザードHTMLを別ウィンドウで開く"""
    eel.start("defebsd_wizard.html", size=(820, 800), block=False)


@eel.expose
def defebsd_browse_file(title: str, filetypes: list, initialdir: str):
    return _tk_filedialog('file', title,
                          filetypes=[tuple(f) for f in filetypes],
                          initialdir=initialdir or _work_dir)


@eel.expose
def defebsd_browse_files(title: str, filetypes: list, initialdir: str):
    """複数ファイルを一括選択して、パスのリストを返す"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    paths = filedialog.askopenfilenames(
        title=title,
        filetypes=[tuple(f) for f in filetypes],
        initialdir=initialdir or _work_dir,
    )
    root.destroy()
    return list(paths)


@eel.expose
def defebsd_browse_dir(title: str, initialdir: str):
    return _tk_filedialog('dir', title, initialdir=initialdir or _work_dir)


@eel.expose
def defebsd_get_labels(georef_xlsx: str):
    """dic_results_georef.xlsx の 'u' シートからラベル一覧を取得する"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(georef_xlsx, read_only=True)
        if 'u' not in wb.sheetnames:
            wb.close()
            return []
        ws = wb['u']
        headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
        wb.close()
        skip = {'subset_id', 'x [px]', 'y [px]'}
        return [h for h in headers if h and h not in skip]
    except Exception:
        return []


@eel.expose
def defebsd_start_batch(params: dict):
    threading.Thread(target=_run_defebsd_batch, args=(params,), daemon=True).start()


def _run_defebsd_batch(params: dict):
    import json, tempfile, re as _re
    param_file = tempfile.mktemp(suffix='.json')
    with open(param_file, 'w', encoding='utf-8') as f:
        json.dump(params, f, ensure_ascii=False)

    script = os.path.join(TOOLS_DIR, 'defebsd_georef_v1.py')
    stage_labels = [s['label'] for s in params.get('stages', [])]

    _env = os.environ.copy()
    _env['MPLBACKEND'] = 'QtAgg'
    _env['PYTHONIOENCODING'] = 'utf-8'
    _env['PYTHONUNBUFFERED'] = '1'

    proc = subprocess.Popen(
        [PYTHON, script, param_file],
        cwd=TOOLS_DIR,
        env=_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    _procs['defebsd'] = proc

    for line in proc.stdout:
        line_s = line.rstrip()
        print(line_s)

        # ステージ開始: [Stage N/M]  label  (file)
        m = _re.match(r'\[Stage \d+/\d+\]\s+(\S+)', line_s)
        if m:
            lbl = m.group(1)
            if lbl in stage_labels:
                try:
                    eel.defebsd_on_stage_status(lbl, '処理中...', 'ok')()
                except Exception:
                    pass

        # ステージ完了: "  Stage 'label' complete."
        m2 = _re.search(r"Stage '(.+?)' complete", line_s)
        if m2:
            lbl = m2.group(1)
            if lbl in stage_labels:
                try:
                    eel.defebsd_on_stage_status(lbl, '完了', 'done')()
                except Exception:
                    pass

    proc.wait()
    _procs.pop('defebsd', None)

    if proc.returncode == 0:
        try:
            eel.defebsd_on_complete(True, '全ステージ完了')()
        except Exception:
            pass
    else:
        try:
            eel.defebsd_on_complete(False, f'エラー（終了コード {proc.returncode}）')()
        except Exception:
            pass


# ================================================================
# EBSD PatRep ウィザード用
# ================================================================

PATREP_DIR = BASE_DIR


@eel.expose
def launch_patrep():
    eel.start("patrep_wizard.html", size=(700, 780), block=False)


@eel.expose
def patrep_browse_dir(title: str, initialdir: str):
    return _tk_filedialog('dir', title, initialdir=initialdir or _work_dir)


@eel.expose
def patrep_get_info(parent_folder: str):
    """親フォルダを検査して nth 名一覧・Phase 情報・tif フォルダ一覧を返す"""
    try:
        import numpy as _np
        from pathlib import Path as _Path
        sys.path.insert(0, TOOLS_DIR)
        from preprocessed_loader import smart_loadmat

        parent = _Path(parent_folder)

        # pre-processed {name}.mat が存在するものを nth として列挙
        mats = sorted(parent.glob("pre-processed *.mat"))
        nth_names = []
        for m in mats:
            name = m.stem.replace("pre-processed ", "")
            if name.lower() == "0th":
                continue
            xlsx = parent / f"pre-processed {name}.xlsx"
            if xlsx.exists():
                nth_names.append(name)

        # 0th .mat から phase 情報を読む
        mat0_cands = sorted(parent.glob("pre-processed 0th*.mat"))
        if not mat0_cands:
            return {"error": "pre-processed 0th*.mat が見つかりません"}

        mat0 = smart_loadmat(str(mat0_cands[0]), variable_names=["phase_index", "phasetxt"])
        phase_idx_map = mat0["phase_index"]
        phase_names_raw = [str(n) for n in mat0["phasetxt"][0]]
        idxs = sorted(set(
            int(v) for v in phase_idx_map.flatten()
            if not (isinstance(v, float) and _np.isnan(v))
        ))
        phases = [
            {"index": i,
             "name": phase_names_raw[i] if i < len(phase_names_raw) else f"Phase{i}"}
            for i in idxs
        ]

        # tif ファイルを含むサブフォルダを列挙
        tif_folders = sorted(
            d.name for d in parent.iterdir()
            if d.is_dir() and any(d.glob("*.tif"))
        )

        return {"nth_names": nth_names, "phases": phases, "tif_folders": tif_folders}

    except Exception as e:
        return {"error": str(e)}


@eel.expose
def patrep_start_batch(params: dict):
    threading.Thread(target=_run_patrep_batch, args=(params,), daemon=True).start()


def _run_patrep_batch(params: dict):
    import json, tempfile, re as _re
    params["patrep_dir"] = PATREP_DIR

    param_file = tempfile.mktemp(suffix=".json")
    with open(param_file, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False)

    script = os.path.join(TOOLS_DIR, "_patrep_runner.py")
    nth_names = params.get("nth_names", [])

    _env = os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"
    _env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [PYTHON, script, param_file],
        cwd=TOOLS_DIR,
        env=_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _procs["patrep"] = proc

    for line in proc.stdout:
        line_s = line.rstrip()
        print(line_s)

        # "Processing: name" → RUNNING
        m = _re.search(r"Processing:\s+(.+)", line_s)
        if m:
            name = m.group(1).strip()
            if name in nth_names:
                try: eel.patrep_on_nth_status(name, "running")()
                except Exception: pass

        # "name: 完了" → DONE
        m2 = _re.search(r"^\s+(.+?):\s+完了", line_s)
        if m2:
            name = m2.group(1).strip()
            if name in nth_names:
                try: eel.patrep_on_nth_status(name, "done")()
                except Exception: pass

        # "ERROR [name]" → ERROR
        m3 = _re.search(r"ERROR \[(.+?)\]", line_s)
        if m3:
            name = m3.group(1).strip()
            if name in nth_names:
                try: eel.patrep_on_nth_status(name, "error")()
                except Exception: pass

    proc.wait()
    _procs.pop("patrep", None)

    if proc.returncode == 0:
        try: eel.patrep_on_complete(True, "全処理完了")()
        except Exception: pass
    else:
        try: eel.patrep_on_complete(False, f"エラー（終了コード {proc.returncode}）")()
        except Exception: pass


# ================================================================
# メイン
# ================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  PineOak v1.0  —  DIC/EBSD Suite")
    print(f"  Tools dir: {TOOLS_DIR}")
    print("=" * 50)
    eel.start(
        "index.html",
        size=(820, 1020),
        port=8765,
        host="localhost",
    )
