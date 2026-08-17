"""MAT v5 読み取り。

数値変数は scipy（高速・安定）、文字列/セル変数は自前パーサで読む。
scipy は非 ASCII を含む char 変数で `buffer is too small` を出すため
（日本語パスで解析した .mat がこれに該当）、その部分だけ自前で処理する。
"""
import struct, zlib, os
import scipy.io as sio

_TYPES = {1, 2, 3, 4, 5, 6, 7, 9, 12, 13, 14, 15, 16, 17, 18}
_ESZ = {1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 4, 9: 8, 12: 8, 13: 8, 16: 1, 17: 2, 18: 4}


def _tag(b, o):
    w = struct.unpack_from('<I', b, o)[0]
    if (w >> 16) & 0xFFFF:                       # small data element
        return (w & 0xFFFF, (w >> 16) & 0xFFFF, o + 4, 8)
    n = struct.unpack_from('<I', b, o + 4)[0]
    return (w, n, o + 8, 8 + n + (-n) % 8)


def _hdr(b, do, end):
    o, p = do, []
    while o < end and len(p) < 4:
        dt, n, dof, tot = _tag(b, o)
        p.append((dt, n, dof, o, tot))
        o += tot
    if len(p) < 3:
        return None
    cls = struct.unpack_from('<I', b, p[0][2])[0] & 0xFF
    nd = p[1][1] // 4
    dims = struct.unpack_from('<' + 'i' * nd, b, p[1][2])
    name = b[p[2][2]:p[2][2] + p[2][1]].decode('latin1')
    return cls, dims, name, p


def _decode_char(b, p):
    dt, n, dof = p[3][0], p[3][1], p[3][2]
    raw = b[dof:dof + n]
    return raw.decode('utf-16-le', 'replace') if _ESZ.get(dt, 1) == 2 else raw.decode('utf-8', 'replace')


def read_string_vars(path, want):
    """char / cell-of-char 変数を自前で読む。{name: str | [str, ...]} を返す。"""
    raw = open(path, 'rb').read()
    o, out = 128, {}
    while o < len(raw) - 8 and len(out) < len(want):
        dt, n, dof, tot = _tag(raw, o)
        d = base = None
        if dt == 15:
            try:
                d = zlib.decompress(raw[dof:dof + n])
                dt2, n2, dof2, _ = _tag(d, 0)
                if dt2 == 14:
                    base = (dof2, min(dof2 + n2, len(d)))
                else:
                    d = None
            except Exception:
                d = None
        elif dt == 14:
            d, base = raw, (dof, dof + n)
        if d is not None and base:
            h = _hdr(d, *base)
            if h:
                cls, dims, name, p = h
                if name in want:
                    if cls == 4:
                        out[name] = _decode_char(d, p)
                    elif cls == 1:
                        cnt, lst = 1, []
                        for x in dims:
                            cnt *= x
                        o2 = p[2][3] + p[2][4]
                        for _ in range(cnt):
                            if o2 >= base[1]:
                                break
                            t2, n2, d2, tt = _tag(d, o2)
                            if t2 == 14:
                                h2 = _hdr(d, d2, d2 + n2)
                                if h2:
                                    lst.append(_decode_char(d, h2[3]))
                            o2 += tt
                        out[name] = lst
        nxt_nopad, nxt_pad = dof + n, o + tot

        def _ok(q):
            if q + 8 > len(raw):
                return False
            t = struct.unpack_from('<I', raw, q)[0]
            return ((t & 0xFFFF) if (t >> 16) else t) in _TYPES

        o = nxt_nopad if (dt == 15 and _ok(nxt_nopad)) else nxt_pad
    return out


def read_numeric(path, names):
    """数値変数を scipy で読む（char を避けるため variable_names を必ず指定）。"""
    out = {}
    for v in names:
        try:
            r = sio.loadmat(path, struct_as_record=False, squeeze_me=True, variable_names=[v])
            if v in r:
                out[v] = r[v]
        except Exception:
            pass
    return out


def stage_name(path):
    """.mat の projectname からステージ名を得る（ファイル名の命名規則に依存しない）。"""
    s = read_string_vars(path, {'projectname'}).get('projectname', '')
    return os.path.splitext(str(s).strip())[0] if s else None
