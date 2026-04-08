# PineOak SEM-DIC/HR-EBSD Tools

SEM-DIC と HR-EBSD の実験・解析で使うための Python ツール集です。
SEM画像からのDICひずみ測定、SEM像またはDICマップとEBSDデータとのジオリファレンス、Heaviside DICによるすべり帯などの検出、DICサブセットごとの応力–ひずみ曲線の可視化などをサポートします。


## PineOak DIC/EBSD Suite（メイン）

`DIC-EBSD_Suite/` に入っている統合解析プログラムです。
GUI（ブラウザベース）から以下の解析を実行できます。

| Step | モジュール | 内容 |
|---|---|---|
| 1 | EBSD PatRep | EBSD パターンの参照パターン置換 |
| 2 | SEM Alignment | 複数ステージの SEM 画像の位置合わせ |
| 3 | Normal DIC | SEM 画像を使ったサブセットベースのひずみ測定 |
| 4 | EBSD Georef | EBSD グレインマップを SEM 座標系に位置合わせ |
| 5 | Def EBSD Georef | 変形後 EBSD グレインマップのジオリファレンス |
| 6 | Stress-Strain Mapper | サブセット・粒・相ごとの応力–ひずみ曲線の可視化 |
| 7 | Heaviside DIC | すべり帯などの不連続変形の検出・可視化 |

---

## デモ用データ (Inconel X-750 の実験データと解析結果)

### 入力データ（`Demo_input_data`）

**Google Drive で公開中:** [Demo_input_data をダウンロード](https://drive.google.com/drive/folders/1pKHcwHPwMw2f66rzmWhUP6fhfWnmFe73?usp=sharing)

各 Step の入力データがフォルダごとにまとめられています。このデータを使って一連の解析を試すことができます。

| フォルダ | 対応Step | 内容 |
|---|---|---|
| `1_EBSD_PatRep_X750_750MPa/` | Step 1 | EBSD up2 ファイル、前処理済みデータ（.mat・.xlsx） |
| `2_SEM_Alignment_X750/` | Step 2 | SEM 画像フォルダ（各負荷ステージの BMP） |
| `3_Normal_DIC_X750/` | Step 3 | SEM 画像フォルダ、位置合わせ JSON |
| `4_EBSD_Georef_X750/` | Step 4 | SEM 参照画像（BMP）、EBSD グレインファイル（.txt）、HR-EBSD MAT ファイル（.mat）、DIC 結果（.xlsx） |
| `5_Def_EBSD_Georef_X750/` | Step 5 | EBSD グレインファイルフォルダ（各ステージ）、CrossCourt4 MAT フォルダ、DIC 結果（.xlsx） |
| `6_Stress-Strain_curve_mapper_X750/` | Step 6 | 統合ジオリファレンスデータ（integrated_georef.mat） |
| `7_Heaviside_DIC_X750/` | Step 7 | SEM 画像フォルダ、DIC 設定（.txt）、DIC 結果（.xlsx）、位置合わせ JSON |
| `osc_files/` | — | 各負荷ステージの EBSD .osc ファイル |

### 出力データ（`Demo_output_data`）

**Google Drive で公開中:** [Demo_output_data をダウンロード](https://drive.google.com/drive/folders/1s9MS0SGDzwkB5C_UjhfM0k93Gigm19y9?usp=sharing)

各 Step の実行結果サンプルです。Demo_input_dataでの試行がうまくいっていれば同様の結果になるはずです。

| フォルダ | 対応Step | 内容 |
|---|---|---|
| `1_EBSD_PatRep_X750_750MPa/` | Step 1 | PatRep 結果（CSV・マッチングマップ PNG） |
| `2_SEM_Alignment_X750/` | Step 2 | 位置合わせ結果（JSON・確認用 GIF） |
| `3_Normal_DIC_X750/` | Step 3 | DIC 解析結果（xlsx・ひずみマップ ZIP） |
| `4_EBSD_Georef_X750/` | Step 4 | EBSDジオリファレンス結果（xlsx・マップ画像 ZIP） |
| `5_Def_EBSD_Georef_X750/` | Step 5 | 各ステージの制御点 JSON・統合ジオリファレンスデータ（integrated_georef.mat） |
| `6_Stress-Strain_curve_mapper_X750/` | Step 6 | 応力–ひずみ曲線マッパーのスクリーンショット・派生マップ PNG |
| `7_Heaviside_DIC_X750_1100MPa/` | Step 7 | Heaviside DIC 結果（xlsx・可視化 PNG） |

---

## セットアップと起動方法

### ステップ1: Python のインストール（初回のみ）

1. [https://www.python.org/downloads/](https://www.python.org/downloads/) を開く
2. 「Download Python 3.13.x」をクリックしてダウンロード
3. インストーラーを起動し、**「Add Python to PATH」に必ずチェックを入れてから** Install Now をクリック

### ステップ2: プログラムのダウンロードと配置（初回のみ）

**推奨インストール先: `C:\tools\PineOak\`**

パスに日本語やスペースが含まれると誤動作することがあるため、シンプルなパスに置くことをおすすめします。
OneDrive と同期しているフォルダも避けてください。

1. GitHubのページ上部にある緑色の「**Code**」ボタンをクリック → 「**Download ZIP**」
2. ダウンロードした ZIP を右クリック → 「すべて展開」
3. 展開すると **`PineOak-main`** というフォルダができるので、**`PineOak`** にリネーム（右クリック → 「名前の変更」）
4. リネームしたフォルダを `C:\tools\` の中に移動（`C:\tools\` フォルダが無ければ先に作成）

### ステップ3: 必要なライブラリのインストール（初回のみ）

1. スタートメニューで「**コマンドプロンプト**」を検索して開く
2. 以下をそのままコピーして貼り付け、Enter：

```
pip install -r C:\tools\PineOak\DIC-EBSD_Suite\requirements.txt
```

### ステップ4: プログラムの起動（毎回）

コマンドプロンプトで以下を入力して Enter：

```
cd C:\tools\PineOak\DIC-EBSD_Suite
python main.py
```

→ ブラウザが自動で開き、GUI から各モジュールを選んで実行できます。

---

## プログラムのアップデート方法

バグ修正や機能追加があったとき、以下の方法でプログラムを最新版に更新できます。

### 方法A：git を使う方法（推奨・手軽）

**初回だけ：git のインストール**

1. [https://git-scm.com/download/win](https://git-scm.com/download/win) を開く
2. 「Click here to download」をクリックしてインストーラーをダウンロード
3. インストーラーを起動し、すべて「Next」でインストール（設定変更不要）
4. インストール後、コマンドプロンプトを**一度閉じて開き直す**

**初回だけ：ZIPからgit管理に切り替える**

`C:\tools\PineOak\` にすでにプログラムが入っている場合、以下を**1行ずつ**実行：

```
cd C:\tools\PineOak
git init
git remote add origin https://github.com/yamasakishigeto/PineOak.git
git fetch
git reset --hard origin/main
git branch --set-upstream-to=origin/main main
```

> ※ 自分で変更していたファイルは上書きされます。設定ファイル（`dic_config.txt` など）は事前にバックアップしてください。

**2回目以降のアップデート（普段使い）**

コマンドプロンプトで以下を実行するだけです：

```
cd C:\tools\PineOak
git pull
```

「Already up to date.」と表示されれば最新版です。更新があった場合はファイル名が表示されます。

---

### 方法B：ZIP を再ダウンロードする方法

1. GitHubページの「**Code**」→「**Download ZIP**」で最新版をダウンロード
2. ZIP を展開し、中の `PineOak-main\DIC-EBSD_Suite\` フォルダの中身を `C:\tools\PineOak\DIC-EBSD_Suite\` に**上書きコピー**
3. データファイルや自分で保存した設定ファイルは上書きしないよう注意

---

## 共通の必要環境

- Python 3.13 以上
- 主なライブラリ：`numpy`, `pandas`, `scipy`, `matplotlib`, `openpyxl`, `eel`, `PyQt6`

---

## ライセンス / License

- **プログラム（コード） / Code**: [MIT License](LICENSE)
- **デモデータ / Demo data**: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — 使用時は作者（Shigeto Yamasaki）のクレジットを表記してください。/ Please credit the author (Shigeto Yamasaki) when using this data.

---

## 作者

Shigeto Yamasaki ([@yamasakishigeto](https://github.com/yamasakishigeto))
