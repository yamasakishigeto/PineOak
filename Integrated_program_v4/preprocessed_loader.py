import os
import glob
import pandas as pd
import scipy.io as sio
import numpy as np
import re


def smart_loadmat(path, variable_names=None):
    """
    .mat ファイルを読み込む。scipy（v5/v7.2）と h5py（v7.3/HDF5）の両方に対応。
    """
    # まず scipy で試みる（v5/v7.2 形式）
    try:
        return sio.loadmat(path, variable_names=variable_names)
    except TypeError:
        pass  # v7.3 形式の場合は TypeError が出るので h5py にフォールバック

    # v7.3 (HDF5) として読み込む
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
                    # scipy と同じ (1, n) 形状に合わせる
                    arr = np.array(strings, dtype=object).reshape(shape).T
                result[key] = arr
            except Exception:
                pass
    return result

def load_preprocessed_xlsx(folder: str, nth: str, **read_excel_kwargs) -> pd.DataFrame:
    pattern = os.path.join(folder, f"pre-processed {nth}*.xlsx")
    candidates = glob.glob(pattern)
    if not candidates:
        raise FileNotFoundError(f"No matching Excel file for pattern: {pattern}")
    path = sorted(candidates)[0]
    return pd.read_excel(path, **read_excel_kwargs)

def load_preprocessed_mat(folder: str, nth: str) -> dict:
    pattern = os.path.join(folder, f"pre-processed {nth}*.mat")
    candidates = glob.glob(pattern)
    if not candidates:
        raise FileNotFoundError(f"No matching MAT file for pattern: {pattern}")
    path = sorted(candidates)[0]
    return smart_loadmat(path)

def get_value_by_label(df: pd.DataFrame, label: str):
    """
    Search the first column for a cell that loosely matches the given label,
    ignoring case, spaces, and underscores, and return the adjacent cell value.
    """
    # normalize the target label
    label_norm = re.sub(r"[\s_]+", "", label).lower()
    # iterate through first column
    for idx, cell in df.iloc[:, 0].astype(str).items():
        cell_norm = re.sub(r"[\s_]+", "", cell).lower()
        if label_norm in cell_norm:
            return df.iloc[idx, 1]
    raise KeyError(f"Label not found in first column: {label}")
