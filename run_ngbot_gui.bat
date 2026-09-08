@echo off
rem NGBot GUI 起動スクリプト（デスクトップショートカットからの呼び出し用）
cd /d "%~dp0"

rem --- Chatwork 認証情報（環境変数で設定。空の場合は通知が無効化されます） ---
if not defined NGBOT_CHATWORK_TOKEN set NGBOT_CHATWORK_TOKEN=
if not defined NGBOT_CHATWORK_ROOM_ID set NGBOT_CHATWORK_ROOM_ID=

rem --- Ollama 高速化設定 (Phase 1) ---
rem OLLAMA_NUM_PARALLEL: LLM 並列推論数。max_workers と整合させる。
rem   4 を推奨 (8B Q4_K_M + num_ctx=2048 でVRAM 約 7-8GB 必要)。
rem   VRAM 余裕があれば 6 or 8 に上げると更に速い。
if not defined OLLAMA_NUM_PARALLEL set OLLAMA_NUM_PARALLEL=4
rem KV cache を q8_0 で量子化してVRAM節約 (推論速度はほぼ変わらず)
if not defined OLLAMA_KV_CACHE_TYPE set OLLAMA_KV_CACHE_TYPE=q8_0
rem モデルロード後は最大24時間メモリに常駐 (アイドル後の再ロード回避)
if not defined OLLAMA_KEEP_ALIVE set OLLAMA_KEEP_ALIVE=24h

rem --- venv 有効化して GUI 起動 ---
rem pythonw.exe で起動するとコンソール窓が出ません
call ".\venv\Scripts\activate.bat"
start "" pythonw.exe "%~dp0gui\app.py"
