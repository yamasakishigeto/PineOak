import pandas as pd
import scipy.io as sio
import numpy as np
import re


def smart_loadmat(path, variable_names=None):
    """
    .mat ファイルを読み込む。ファイルヘッダーで形式を判定し、
    v5/v7.2 は scipy、v7.3（HDF5）は h5py を使う。

    v5 ファイルでも char 型変数の読み込みに失敗することがあるため、
    variable_names を指定して数値変数だけ読む際に有効。
    """
    # ファイルヘッダーで MATLAB バージョンを判定
    with open(path, 'rb') as f:
        header = f.read(128)

    if b'MATLAB 7.3' in header:
        # v7.3 (HDF5) 形式 → h5py で読む
        import h5py
        result = {}
        with h5py.File(path, 'r') as f:
            keys = list(f.keys()) if variable_names is None else [k for k in variable_names if k in f]
            for key in keys:
                if key.startswith('#'):
                    continue
                try:
                    item = f[key]
                    if not isinstance(item, h5py.Dataset):
                        continue
                    arr = item[()]
                    if arr.dtype.kind in ('f', 'i', 'u') and arr.ndim >= 2:
                        # MATLAB は列優先、h5py は行優先で読むため転置が必要
                        arr = arr.T
                    elif arr.dtype == object:
                        # セル配列（文字列など）: 各要素は HDF5 オブジェクト参照
                        shape = arr.shape
                        strings = []
                        for ref in arr.flat:
                            try:
                                chars = f[ref][()]
                                s = ''.join(chr(int(c)) for c in chars.flat)
                            except Exception:
                                s = ''
                            strings.append(s)
                        arr = np.array(strings, dtype=object).reshape(shape).T
                    result[key] = arr
                except Exception:
                    pass
        return result
    else:
        # v5/v7.2 形式 → scipy で読む
        # variable_names を指定すると、不要な変数（char 型など）を読み飛ばせる
        return sio.loadmat(path, variable_names=variable_names)

def get_value_by_label(df: pd.DataFrame, label: str):
    """
    Search the first column for a cell that loosely matches the given label,
    ignoring case, spaces, and underscores, and return the adjacent cell value.
    """
    # normalize the target label
    label_norm = re.sub(r"[\s_]+", "", label).lower()
    # iterate through first column
    for idx, cell in df.iloc[:, 0].items():
        if pd.isna(cell):
            continue
        cell_norm = re.sub(r"[\s_]+", "", str(cell)).lower()
        if label_norm in cell_norm:
            return df.iloc[idx, 1]
    raise KeyError(f"Label not found in first column: {label}")
