# PineOak DIC/EBSD Suite

SEM画像を用いたDIC（Digital Image Correlation）解析の統合ランチャーです。
通常DIC・EBSDジオリファレンス・Heaviside DIC・Stress-Strain Mapper・Statistical Analysisを一つのGUIから順に実行できます。

---

## 動作環境

- Python 3.13 以上
- Windows 10/11（推奨）

## インストール

```bash
pip install -r requirements.txt
```

## 起動

```bash
py -3.13 main.py
```

---

## ワークフロー

```
1. EBSD PatRep        EBSDパターンの参照パターン置換
        ↓
2. SEM Alignment      SEM画像の位置合わせ・アライメントJSON出力
        ↓
3. Normal DIC         通常DIC計算・ひずみマップ生成（Excel出力）
        ↓
4. EBSD Georef        EBSDデータのジオリファレンス・粒情報割り当て
        ↓
5. Def EBSD Georef    変形後EBSDのジオリファレンス
        ↓
6. Stress-Strain Mapper  応力–ひずみ曲線・シュミット因子・RSS・GROD・m' 粒界マッピング
        ↓
7. Heaviside DIC      不連続変位解析・Heaviside DIC計算
        ↓
8. Statistical Analysis  ひずみ・結晶学的変数の統計解析・ヒストグラム・ステージ変化グラフ
```

作業フォルダを選択してから各ツールを起動してください。
各ツールのファイル選択ダイアログは作業フォルダを初期フォルダとして開きます。

---

## ツール詳細

### 1. SEM Alignment（`sem_align_tool_v3.html`）
複数のSEM画像間の位置ずれをブラウザGUIで補正し、アライメントJSONを出力します。

**入力:** SEM画像（BMP / PNG / TIFF / JPG）
**出力:** `sem_alignment.json`

---

### 2. Normal DIC（`dic_sem_strain_v58.py`）
2段階DIC（粗い探索 → 精密探索）でサブセットごとの変位・ひずみを計算します。

**入力:**
- 参照SEM画像（変形前）
- 変形SEM画像（変形後）
- アライメントJSON（任意）

**出力:** `dic_results.xlsx`（シート: u, v, exx, eyy, exy, e1, gamma_max, omega_xy）

**主なパラメータ:**

| パラメータ | 説明 |
|---|---|
| subset | サブセットサイズ [px]（デフォルト: 31） |
| step | グリッドステップ [px] |
| search | 探索範囲 [px] |
| gauge | ゲージ長さ（ひずみ計算用ステップ数） |
| workers | 並列ワーカー数 |
| NCC閾値 | これ以下の相関値はNaN化 |

設定は `dic_config.txt` に保存・読み込み可能です。

---

### 3. EBSD Georef（`ebsd_georef_v68.py`）
EBSD Grain File（TSL/EDAX OIM Analysis エクスポート）をSEM座標系にジオリファレンスし、
DICサブセットグリッドに結晶粒情報を割り当てます。

**入力:**
- `Grain_File_*.txt`（OIM Analysisエクスポート、11列固定）
- SEM参照画像
- `dic_results.xlsx`（サブセット座標参照用）

**出力:** `ebsd_georef.xlsx`（列: cx, cy, grain_id, phase, phi1, PHI, phi2, IQ, CI）

**処理フロー:**
1. Grain FileからIQマップを生成
2. SEM画像とEBSD IQマップをGUIで並べ表示
3. 対応点を交互クリックで指定（4点以上: 射影変換、3点: アフィン変換）
4. DICグリッドに最近傍EBSD情報を割り当て

---

### 4. Stress-Strain Mapper（`stress_strain_mapper_v2.py`）

DIC と EBSD を統合した応力–ひずみ解析ツールです。

**入力:** `integrated_georef.mat`

**主な機能:**

| 機能 | 内容 |
|---|---|
| 応力–ひずみ曲線 | サブセット・粒・相ごとの曲線表示 |
| Schmid Factor | すべり系指定によるシュミット因子マップ、すべり面トレース表示 |
| RSS | Resolved Shear Stress マップ |
| GROD | Grain Reference Orientation Deviation マップ |
| 主ひずみ方向 | 主ひずみ・主せん断ひずみ方向の計算と表示 |
| m'（Luster–Morris） | 粒界ごとの m' 値によるグラデーション描画、閾値以下/以上フィルタ、カラーマップ選択 |

---

### 5. Statistical Analysis（`stats_analysis_v1.py`）

解析済みデータの統計解析モジュールです。

**入力:** `integrated_georef.mat`

**主な機能:**

| 機能 | 内容 |
|---|---|
| ヒストグラム | ひずみ・結晶学的変数の分布表示、ビン幅自動統一 |
| ステージ変化グラフ | 各変数のステージ（負荷レベル）依存性プロット |
| Quality Filter | CI / IQ / ZNCC / PK height / MAE によるデータフィルタリング |
| Region Filter | 相・粒界近傍でのデータ絞り込み |
| 軸範囲指定 | 各グラフの最大・最小値を手動設定 |

---

### 6. Heaviside DIC（`heaviside_dic_v81.py`）
すべり帯などの不連続変位場をHeaviside基底関数で解析します。

**入力:**
- `dic_results.xlsx`（Normal DIC出力）
- SEM画像フォルダ
- `ebsd_georef.xlsx`（粒情報、任意）

**出力:**
- `heaviside_dic_results.xlsx`
- 結果確認GUI（SEM画像 + ひずみマップ + 不連続線の重ね合わせ表示）

**GUI操作:**

| 操作 | 機能 |
|---|---|
| ベース画像選択 | ひずみ成分（u/v/exx/eyy等）またはSEM画像を背景に選択 |
| カラーマップ選択 | ベース・不連続線それぞれのカラーマップを変更 |
| vmin / vmax | カラースケールの範囲を手動設定 |
| ←/→ キー | Heavisideしきい値の微調整 |
| PNG保存 | 現在の表示をPNG出力 |

---

## ファイル構成

```
DIC-EBSD_Suite/
├── main.py                                    # 統合ランチャー（Eel + Tkinter）
├── index.html                                 # ランチャーUI
├── dic_wizard.html                            # Normal DICウィザードUI
├── ebsd_wizard.html                           # EBSD Georefウィザードui
├── heaviside_wizard.html                      # Heaviside DICウィザードUI
├── sem_align_tool_v3.html                     # SEM位置合わせUI
├── defebsd_wizard.html                        # Def EBSD Georefウィザードui
├── patrep_wizard.html                         # EBSD PatRepウィザードUI
├── dic_sem_strain_v58.py                      # Normal DIC解析エンジン
├── ebsd_georef_v68.py                         # EBSDジオリファレンスエンジン
├── heaviside_dic_v81.py                       # Heaviside DIC解析エンジン
├── defebsd_georef_v1.py                       # 変形後EBSDジオリファレンスエンジン
├── pattern_replacer_allpoints_batch_250709.py # EBSD PatRep解析エンジン
├── reference_search_module_allpoints_250709.py# PatRep参照探索モジュール
├── preprocessed_loader.py                     # 前処理済みデータローダー
├── visualize_grain_map_overlay_250709.py      # 粒マップ重ね合わせ可視化
├── stress_strain_mapper_v2.py                 # Stress-Strain Mapper（PyQt6 GUI）
├── stress_strain_calc.py                      # 応力–ひずみ計算関数
├── gb_definitions.py                          # 粒界定義（SpecialBoundary / m'）
├── gb_settings_dialog.py                      # 粒界設定ダイアログ
├── stats_analysis_v1.py                       # Statistical Analysis（PyQt6 GUI）
├── _dic_runner.py                             # DIC実行管理
├── _dic_runner_src.py                         # DIC解析サブプロセス用スクリプト
├── _patrep_runner.py                          # PatRep実行管理
└── requirements.txt
```

---

## 依存ライブラリ

| ライブラリ | 用途 | 必須 |
|---|---|---|
| numpy | 数値計算 | ✓ |
| scipy | 補間・空間演算 | ✓ |
| opencv-python | 画像処理・NCC | ✓ |
| scikit-image | Hough変換 | ✓ |
| matplotlib | グラフ描画 | ✓ |
| openpyxl | Excel入出力 | ✓ |
| PyQt6 | EBSDジオリファレンスGUI | ✓ |
| eel | ランチャーUI（HTML↔Python連携） | ✓ |
| joblib | 並列処理（DIC高速化） | 任意 |
| tqdm | 進捗バー | 任意 |
| japanize-matplotlib | 日本語フォント自動設定 | 任意 |

<!-- test -->
