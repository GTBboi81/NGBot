@echo off
cd /d "%~dp0"

REM --- Chatwork 認証情報 ---
REM トークン/ルームIDは Windows のユーザー環境変数で設定してください。
REM このファイルには絶対に書き込まないでください（Git 追跡下です）。
REM   setx NGBOT_CHATWORK_TOKEN "＜APIトークン＞"
REM   setx NGBOT_CHATWORK_ROOM_ID "＜ルームID＞"
REM setx 実行後は新しいコマンドプロンプトを開き直すと反映されます。

call .env\Scriptsctivate.bat && python main.py