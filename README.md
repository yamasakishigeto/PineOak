# PineOak SEM-DIC/HR-EBSD Tools

SEM-DIC と HR-EBSD の実験・解析で使うための Python ツール集です。
SEM画像からのDICひずみ測定、SEM像またはDICマップとEBSDデータとのジオリファレンス、Heaviside DICによるすべり帯などの検出、DICサブセットごとの応力–ひずみ曲線の可視化などをサポートします。

---

## フォルダ構成

```
PineOak/
├── DIC-EBSD_Suite/       ← メインの統合プログラム（PineOak DIC/EBSD Suite）
├── Demo_input_data/      ← デモ用入力データ
└── Demo_output_data/     ← デモ用出力データ（各Stepの実行結果サンプル）
```

---

## PineOak DIC/EBSD Suite（メイン）

`DIC-EBSD_Suite/` に入っている統合解析プログラムです。
GUI（ブラウザベース）から以下の解析を一括で実行できます。

| Step | モジュール | 内容 |
|---|---|---|
| 1 | EBSD PatRep | EBSD パターンの参照パターン置換 |
| 2 | SEM Alignment | 複数ステージの SEM 画像の位置合わせ |
| 3 | Normal DIC | SEM 画像を使ったサブセットベースのひずみ測定 |
| 4 | EBSD Georef | EBSD グレインマップを SEM 座標系に位置合わせ |
| 5 | Def EBSD Georef | 変形後 EBSD グレインマップのジオリファレンス |
| 6 | Stress-Strain Mapper | サブセット・粒・相ごとの応力–ひずみ曲線の可視化 |
| 7 | Heaviside DIC | すべり帯などの不連続変形の検出・可視化 |

### 起動方法
```bash
cd DIC-EBSD_Suite
python main.py
```
→ ブラウザが開き、GUI から各モジュールを選んで実行できます。

### 必要なライブラリ
```bash
pip install -r DIC-EBSD_Suite/requirements.txt
```

---

## デモ用データ

### 入力データ（`Demo_input_data/`）

| ファイル・フォルダ | 内容 |
|---|---|
| `SEM_images/` | SEM 画像（BMP、各負荷ステージ） |
| `Grain_file_OIM8/` | EBSD グレインファイル（OIM8形式、各ステージ） |
| `0MPa.mat` | HR-EBSD MAT ファイル（CrossCourt出力、参照状態） |

### 出力データ（`Demo_output_data/`）

各 Step の実行結果サンプルです。

| フォルダ | 対応Step | 内容 |
|---|---|---|
| `2_SEM_Alignment_X750/` | Step 2 | SEM位置合わせ結果（JSON・GIF） |
| `3_Normal_DIC_X750/` | Step 3 | DIC解析結果（xlsx・ひずみマップPNG） |
| `4_EBSD_Georef_X750/` | Step 4 | EBSDジオリファレンス結果（xlsx・PNG） |
| `5_Def_EBSD_Georef_X750/` | Step 5 | 変形後EBSDジオリファレンス結果（integrated_georef.mat・PNG） |
| `7_Heaviside_DIC_X750_1100MPa/` | Step 7 | Heaviside DIC結果（xlsx・PNG） |

---

## 共通の必要環境

- Python 3.13 以上
- 主なライブラリ：`numpy`, `pandas`, `scipy`, `matplotlib`, `openpyxl`, `eel`, `PyQt6`

---

## 作者

Shigeto Yamasaki ([@yamasakishigeto](https://github.com/yamasakishigeto))
