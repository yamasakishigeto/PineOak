# PineOak SEM-DIC/HR-EBSD Tools

SEM-DIC と HR-EBSD の実験・解析で使うための Python ツール集です。
SEM画像からのDICひずみ測定、SEM像またはDICマップとEBSDデータとのジオリファレンス、Heviside DICによるすべり帯などの検出、DICサブセットごとの応力–ひずみ曲線の可視化などをサポートします。

---

## フォルダ構成

```
PineOak/
├── Integrated_program_v4/          ← メインの統合プログラム（PineOak DIC/EBSD Suite）
├── stress_strain_mapper/           ← 応力–ひずみ曲線マッパー
└── 解析実行に必要なデータ (デモ用)/  ← デモ用サンプルデータ
```

---

## PineOak DIC/EBSD Suite（メイン）

`Integrated_program_v4/` に入っている統合解析プログラムです。
GUI（ブラウザベース）から以下の解析を一括で実行できます。

| モジュール | 内容 |
|---|---|
| SEM-DIC | SEM 画像を使ったサブセットベースのひずみ測定 |
| Heaviside DIC | すべり帯などの不連続変形の検出・可視化 |
| EBSD ジオリファレンス | EBSD グレインマップを SEM 座標系に位置合わせ |
| EBSD PatRep | EBSD パターンの参照パターン置換 |
| SEM 位置合わせ | 複数ステージの SEM 画像の位置合わせ |

### 起動方法
```bash
cd Integrated_program_v4
python main.py
```
→ ブラウザが開き、GUI から各モジュールを選んで実行できます。

### 必要なライブラリ
```bash
pip install -r Integrated_program_v4/requirements.txt
```

---

## 応力–ひずみ曲線マッパー

`stress_strain_mapper/` に入っています。
Excel ファイルを読み込み、粒ごとの散布図と応力–ひずみ曲線を同時に表示します。

```bash
python stress_strain_mapper/stress_strain_mapper_250828.py
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
