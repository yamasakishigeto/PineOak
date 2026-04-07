# PineOak SEM-DIC/HR-EBSD Tools

SEM-DIC と HR-EBSD の実験・解析で使うための Python ツール集です。
SEM画像からのDICひずみ測定、SEM像またはDICマップとEBSDデータとのジオリファレンス、Heviside DICによるすべり帯などの検出、DICサブセットごとの応力–ひずみ曲線の可視化などをサポートします。

---

## フォルダ構成

```
PineOak/
├── DIC-EBSD_Suite/          ← メインの統合プログラム（PineOak DIC/EBSD Suite）
└── 解析実行に必要なデータ (デモ用)/  ← デモ用サンプルデータ
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
| 6 | Stress-Strain curve mapper | 粒ごとの応力–ひずみ曲線の可視化 |
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

## デモ用サンプルデータ

`解析実行に必要なデータ (デモ用)/` に入っています。
PineOak DIC/EBSD Suite の動作確認に使えるサンプルデータ一式です。

| ファイル・フォルダ | 内容 |
|---|---|
| `SEM_images/` | SEM 画像（BMP） |
| `Grain_file_OIM8/` | EBSD グレインファイル |
| `0MPa.mat` | EBSD MAT ファイル（参照状態） |
| `dic_config.txt` | DIC 設定ファイル |
| `dic_results.xlsx` | DIC 解析結果（サンプル） |
| `dic_results_georef.xlsx` | ジオリファレンス済み結果（サンプル） |
| `sem_alignment.json` | SEM 位置合わせ情報（サンプル） |
| `出力データ (デモ)/` | 出力結果のサンプル |

---

## 共通の必要環境

- Python 3.13 以上
- 主なライブラリ：`numpy`, `pandas`, `scipy`, `matplotlib`, `openpyxl`, `eel`

---

## 作者

Shigeto Yamasaki ([@yamasakishigeto](https://github.com/yamasakishigeto))
