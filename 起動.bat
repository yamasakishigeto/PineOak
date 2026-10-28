@echo off
setlocal
rem PineOak 起動スクリプト
rem   .venv（PineOak 専用の Python 環境）が無ければ作成し、
rem   requirements.txt が前回から変わっていればライブラリを入れ直してから main.py を起動する。
rem   Python 本体の環境には一切手を加えない。

cd /d "%~dp0"
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "REQ=%~dp0DIC-EBSD_Suite\requirements.txt"
set "STAMP=%VENV%\requirements.installed.txt"

if exist "%VPY%" goto check_req

echo ==================================================
echo   PineOak 専用の Python 環境(.venv)を作成します
echo   初回のみ数分かかります（インターネット接続が必要）
echo ==================================================
where python >nul 2>nul
if errorlevel 1 (
    echo [エラー] python が見つかりません。
    echo README の「ステップ1: Python のインストール」を確認してください。
    pause
    exit /b 1
)
python -m venv "%VENV%"
if errorlevel 1 (
    echo [エラー] .venv の作成に失敗しました。
    pause
    exit /b 1
)

:check_req
if not exist "%STAMP%" goto install
fc /b "%REQ%" "%STAMP%" >nul 2>nul
if errorlevel 1 goto install
goto run

:install
echo ライブラリをインストールしています...
"%VPY%" -m pip install --disable-pip-version-check -r "%REQ%"
if errorlevel 1 (
    echo.
    echo [エラー] ライブラリのインストールに失敗しました。
    echo インターネット接続を確認して、もう一度 起動.bat を実行してください。
    pause
    exit /b 1
)
copy /y "%REQ%" "%STAMP%" >nul

:run
cd /d "%~dp0DIC-EBSD_Suite"
"%VPY%" main.py
if errorlevel 1 pause
endlocal
