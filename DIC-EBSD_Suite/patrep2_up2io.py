"""EDAX .up2 パターンファイルの読み書き。

レイアウト: [ヘッダ(可変長)][pattern 0][pattern 1]...
  byte 0-3   version
  byte 4-7   width
  byte 8-11  height
  byte 12-15 最初のパターンへのオフセット   ← ここを読むので決め打ちしない
各パターンは width*height*2 バイト（16bit）、無圧縮・固定長。
"""
import struct, os, shutil, glob

_HDR = struct.Struct('<4I')


class Up2:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.version, self.w, self.h, self.off = _HDR.unpack(f.read(16))
        self.bpp = self.w * self.h * 2
        self.size = os.path.getsize(path)
        n, rem = divmod(self.size - self.off, self.bpp)
        self.n, self.rem = n, rem

    def __repr__(self):
        return (f"Up2({os.path.basename(self.path)} v{self.version} {self.w}x{self.h} "
                f"header={self.off}B n={self.n}" + (f" 端数{self.rem}B" if self.rem else "") + ")")

    def compatible(self, other):
        return (self.version, self.w, self.h, self.bpp) == (other.version, other.w, other.h, other.bpp)

    def offset(self, idx):
        return self.off + idx * self.bpp

    def read_pattern(self, idx):
        with open(self.path, 'rb') as f:
            f.seek(self.offset(idx))
            return f.read(self.bpp)


def patch(src_up2, dst_path, pairs, backup_dir=None, verify=True, log=print):
    """dst_path の pattern を src_up2 の pattern で上書きする。

    pairs : [(dst_index, src_index), ...]   いずれも 0 基準
    """
    src, dst = src_up2, Up2(dst_path)
    if not src.compatible(dst):
        raise ValueError(f"src と dst の形式が違います: {src} / {dst}")
    bad = [(d, s) for d, s in pairs if not (0 <= d < dst.n and 0 <= s < src.n)]
    if bad:
        raise ValueError(f"範囲外の index が {len(bad)} 件: {bad[:5]}")
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)

    with open(src.path, 'rb') as fs, open(dst_path, 'r+b') as ft:
        for di, si in pairs:
            fs.seek(src.offset(si))
            buf = fs.read(src.bpp)
            if len(buf) != src.bpp:
                raise IOError(f"src#{si} を読み切れません")
            if backup_dir:
                ft.seek(dst.offset(di))
                with open(os.path.join(backup_dir, f"orig_{di}.bin"), 'wb') as fb:
                    fb.write(ft.read(dst.bpp))
            ft.seek(dst.offset(di))
            ft.write(buf)
    log(f"    {len(pairs)} パターン書き込み")

    if verify:
        ng = 0
        with open(src.path, 'rb') as fs, open(dst_path, 'rb') as ft:
            for di, si in pairs:
                fs.seek(src.offset(si)); ft.seek(dst.offset(di))
                if fs.read(src.bpp) != ft.read(dst.bpp):
                    ng += 1
        if ng:
            raise IOError(f"検証失敗: {ng} / {len(pairs)} 件が不一致")
        log(f"    検証OK ({len(pairs)}/{len(pairs)} バイト一致)")
    if os.path.getsize(dst_path) != dst.size:
        raise IOError("ファイルサイズが変化しました")


def restore(dst_path, backup_dir, log=print):
    """orig_patterns/ の退避パターンを書き戻し、コピーを未差し替えの状態に戻す。

    しきい値を変えて再実行したとき、前回だけで差し替えた点が残らないようにする。
    書き戻したファイルは削除する（次の patch が改めて退避を作る）。
    """
    files = sorted(glob.glob(os.path.join(backup_dir, 'orig_*.bin')))
    if not files:
        return 0
    dst = Up2(dst_path)
    with open(dst_path, 'r+b') as ft:
        for f in files:
            buf = open(f, 'rb').read()
            if len(buf) != dst.bpp:
                raise IOError(f"退避ファイルのサイズが合いません: {os.path.basename(f)}")
            ft.seek(dst.offset(int(os.path.basename(f)[5:-4])))
            ft.write(buf)
    for f in files:
        os.remove(f)
    log(f"    前回の差し替え {len(files)} 件を元に戻しました")
    return len(files)


def _copy_if_needed(src, out_dir, allow_reuse, log):
    if not (src and os.path.exists(src)):
        return None, False
    dst = os.path.join(out_dir, os.path.basename(src))
    if allow_reuse and os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
        log(f"    既存を再利用 {os.path.basename(src)}")
        return dst, True
    log(f"    コピー {os.path.basename(src)} ({os.path.getsize(src)/1024**3:.2f} GB)")
    shutil.copy2(src, dst)
    return dst, False


def prepare_output(src_osc, src_up2_path, out_dir, allow_reuse=True, log=print):
    """out_dir に .osc と .up2 を元の名前のままコピーする。

    戻り値 (osc_path, up2_path, up2を再利用したか)
    allow_reuse=False なら既存の .up2 コピーがあっても作り直す。
    .osc は書き換えないので常に再利用してよい。
    """
    os.makedirs(out_dir, exist_ok=True)
    osc, _ = _copy_if_needed(src_osc, out_dir, True, log)
    up2, reused = _copy_if_needed(src_up2_path, out_dir, allow_reuse, log)
    return osc, up2, reused
