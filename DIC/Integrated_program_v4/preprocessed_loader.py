import os
import glob
import pandas as pd
import scipy.io as sio
import re

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
    return sio.loadmat(path)

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
