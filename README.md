# PineOak SEM-DIC/HR-EBSD Tools

SEM-DIC と HR-EBSD の実験・解析で使うための Python ツール集です。
SEM画像からのDICひずみ測定、SEM像またはDICマップとEBSDデータとのジオリファレンス、応力–ひずみ曲線の可視化などをサポートします。

---

## フォルダ構成

```
PineOak/
├── DIC/
│   └── Integrated_program_v4/   ← メインの統合プログラム（DIC Suite v4）
├── GeoReferencer/               ← DIC–EBSD ジオリファレンスツール（旧バージョン）
├── EBSD PatRep/                 ← EBSD パターン置換ツール
├── stress_strain_mapper/        ← 応力–ひずみ曲線マッパー
└── mat_to_excel_batch_exporter_250828.py
```

---

## DIC Suite v4（メイン）

`DIC/Integrated_program_v4/` に入っている統合解析プログラムです。
GUI（ブラウザベース）から以下の解析を一括で実行できます。

| モジュール | 内容 |
|---|---|
| SEM-DIC | SEM 画像を使ったサブセットベースのひずみ測定 |
| Heaviside DIC | すべり帯などの不連続変形の検出・可視化 |
| EBSD ジオリファレンス | EBSD グレインマップを SEM 座標系に位置合わせ |
| SEM 位置合わせ | 複数ステージの SEM 画像の位置合わせ |

### 起動方法
```bash
cd DIC/Integrated_program_v4
python main.py
```
→ ブラウザが開き、GUI から各モジュールを選んで実行できます。

### 必要なライブラリ
```bash
pip install -r DIC/Integrated_program_v4/requirements.txt
```

---

## GeoReferencer

`GeoReferencer/` に入っている DIC–EBSD ジオリファレンスツールです（旧バージョン）。
DIC Suite v4 に同機能が統合されているため、現在はレガシー用途です。

---

## EBSD PatRep

`EBSD PatRep/` に入っている EBSD パターン置換ツールです。

- **pattern_replacer_allpoints_batch_250709.py**
  複数フォルダのデータをまとめて処理し、EBSD パターンを参照と置き換えます。

```bash
python "EBSD PatRep/pattern_replacer_allpoints_batch_250709.py"
```

---

## 応力–ひずみ曲線マッパー

`stress_strain_mapper/` に入っています。
Excel ファイルを読み込み、粒ごとの散布図と応力–ひずみ曲線を同時に表示します。

```bash
python stress_strain_mapper/stress_strain_mapper_250828.py
```

---

## mat → Excel 変換

複数の `.mat` ファイルをまとめて Excel に変換します。

```bash
python mat_to_excel_batch_exporter_250828.py
```

---

## 共通の必要環境

- Python 3.13 以上
- 主なライブラリ：`numpy`, `pandas`, `scipy`, `matplotlib`, `openpyxl`, `eel`

---

## 作者

Shigeto Yamasaki ([@yamasakishigeto](https://github.com/yamasakishigeto))
