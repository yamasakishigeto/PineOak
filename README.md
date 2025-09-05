# PIneOak SEM-DIC/HR-EBSD Tools

このリポジトリには、SEM-DIC と HR-EBSD の実験で使うための Python スクリプトをまとめています。  
データの変換、グラフ表示、EBSD パターンの処理などをサポートします。

---

## 入っているプログラム

### EBSD PatRep フォルダ
- **pattern_replacer_allpoints_batch_250709.py**  
  複数フォルダのデータをまとめて処理し、EBSD パターンを参照と置き換えます。結果を CSV に出力し、画像も自動でコピー＆置換します。最後にグレインマップを表示します。  

- **preprocessed_loader.py**  
  前処理済みの Excel や mat ファイルを読み込むための補助スクリプトです。  

- **reference_search_module_allpoints_250709.py**  
  参照点を探したり、最も近いパターンを見つけるためのモジュールです。  

- **visualize_grain_map_overlay_250709.py**  
  グレインマップを読み込み、結果を重ねて表示するツールです。  

### ルートにあるスクリプト
- **mat_to_excel_batch_exporter_250828.py**  
  複数の `.mat` ファイルをまとめて読み込み、Excel (`.xlsx`) に変換します。  

- **stress_strain_mapper_250828.py**  
  Excel ファイルを読み込み、粒ごとの散布図と応力–ひずみ曲線を同時に表示できるツールです。クリックやホバーで点を選んでグラフが更新されます。  

---

## 必要な環境
- Python 3.9 以上  
- 使うライブラリ（pip でインストールできます）：  
  `numpy`, `pandas`, `scipy`, `matplotlib`, `openpyxl`, `tkinter`（標準で入っています）

---

## 使い方の例

### 1) mat ファイルを Excel に変換
```bash
python mat_to_excel_batch_exporter_250828.py
```
→ ダイアログでフォルダを選び、変換したい変数を選びます。

### 2) 応力–ひずみ曲線を表示
```bash
python stress_strain_mapper_250828.py --mode click
```
→ Excel ファイルを指定すると、粒の位置と応力–ひずみ曲線が表示されます。  

### 3) EBSD パターン置換
```bash
python "EBSD PatRep/pattern_replacer_allpoints_batch_250709.py"
```
→ 0th フォルダと nth フォルダを選ぶと、自動的に処理と可視化が行われます。  

---

## データについて
- 実験データ（tif や mat など数 GB 以上の大きなファイル）は GitHub にアップロードしないでください。  
- 代わりに PC の `data/` フォルダなどで管理するのがおすすめです。  

---

## 作者
- 作成者: Shigeto Yamasaki (@yamasakishigeto)  
