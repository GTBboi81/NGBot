@echo off
cd /d "%~dp0"

REM --- Chatwork 認証情報（平文でconfig.yamlに書かないこと） ---
REM 実際のトークン・ルームIDを以下に設定してください
set NGBOT_CHATWORK_TOKEN=
set NGBOT_CHATWORK_ROOM_ID=

call .\venv\Scripts\activate.bat && python main.py