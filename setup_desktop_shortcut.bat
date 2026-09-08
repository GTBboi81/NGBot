@echo off
rem ============================================================
rem NGBot 初回セットアップ: 依存ライブラリ導入 + デスクトップ
rem ショートカット作成。これをダブルクリック1回で完了する。
rem ============================================================
setlocal
cd /d "%~dp0"

echo [1/3] venv 有効化と依存ライブラリ導入
if not exist ".\venv\Scripts\activate.bat" (
    echo   [ERROR] venv が見つかりません。先に Python venv を作成してください
    pause
    exit /b 1
)
call ".\venv\Scripts\activate.bat"
pip install --quiet ttkbootstrap apscheduler ruamel.yaml
if errorlevel 1 (
    echo   [ERROR] 依存ライブラリの導入に失敗しました
    pause
    exit /b 1
)

echo [2/3] デスクトップショートカット作成
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0create_shortcut.ps1"
if errorlevel 1 (
    echo   [ERROR] ショートカット作成に失敗しました
    pause
    exit /b 1
)

echo [3/3] 完了
echo デスクトップの "NGBot" アイコンをダブルクリックして起動してください。
pause
endlocal
