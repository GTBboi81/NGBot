# NGBot — 通話ログ NG理由 自動分析ボット

光回線の電話営業で「NG（失注）」となった通話音声を、**完全ローカル**で
文字起こし → LLM で構造化抽出 → CSV 出力 → Chatwork 通知する Windows 向けツールです。

- **文字起こし:** ローカル Whisper（Const-me/Whisper CLI もしくは faster-whisper 系）
- **抽出/分類:** ローカル Ollama 上の日本語 LLM（例: ELYZA-Llama-3-8B）
- **出力:** 通話ごとに氏名・都道府県・利用回線・NG理由などを JSON→CSV 化
- **GUI:** ttkbootstrap 製のデスクトップアプリ（スケジュール実行対応）

> 音声の文字起こしと LLM 推論はクラウド API に依存せず、すべて手元の PC / GPU で完結します。
> 外部通信は Chatwork 通知（任意・件数と出力先パスのみ）だけです。詳細は「取り扱うデータと保存先」を参照してください。

---

## ⚠️ このリポジトリに含まれないもの

公開にあたり、以下は**意図的に除外**しています。利用者が各自で用意してください。

| 種別 | 内容 | 入手方法 |
|---|---|---|
| LLM モデル | `*.gguf`（ELYZA-Llama-3-8B 等） | Hugging Face 等から取得し `Modelfile` で Ollama に登録 |
| Whisper モデル | `ggml-*.bin` / CTranslate2 モデル | 各配布元から取得 |
| ffmpeg | `ffmpeg.exe` | 公式サイトから取得しパスを通す |
| 音声データ | 通話 mp3、結果 CSV | — |
| 認証情報 | Chatwork トークン / ルームID | Windows ユーザー環境変数（`setx`）で設定 |
| 実評価データ | 実通話のゴールデンセット（PII） | `tests/build_golden_set.py` で自前構築 |

同梱の `tests/golden_set/sample_*` は**架空の会話**による動作確認用サンプルです。

また、`config.yaml` の NG理由の定義・判定キーワード・Few-shot 例、および回線名（`A社光` 等）は、
すべて**動作確認用のサンプル**に置き換えています。実運用では自社の分類基準とサービス名に差し替えてください。

---

## 取り扱うデータと保存先

本ツールは実通話の個人情報を扱います。**出力は暗号化されません。** 保存先とその中身は次のとおりです。

| 保存先 | 内容 | Git 追跡 |
|---|---|---|
| `<リポジトリ直下>/yyyymmdd_HHMMSS_results.csv` | 電話番号・テナント・文字起こし全文・氏名・郵便番号・都道府県・NG理由・LLM判定根拠 | 除外（`/*.csv`） |
| `.cache/{md5}_main.json` | 上記 CSV 1 行分と同じ内容 | 除外（`.cache/`） |
| `audio_analysis.log` | 処理ファイル名・件数・エラー（通話本文は含まない） | 除外（`*.log`） |
| `tests/golden_set/*` | `build_golden_set.py` で実 CSV から複製した評価データ | 除外（`sample_*` と README のみ追跡） |

運用側で必ず設計してください。

- **保存場所のアクセス権:** 実行フォルダを担当者のみに限定する（NTFS ACL）。共有フォルダ直下には置かない
- **ディスク暗号化:** BitLocker 等を有効にする
- **保持期間と削除:** CSV・`.cache/`・`tests/golden_set/` は自動削除されません。保持期間を決めて定期削除する運用を用意する
- **複製の管理:** `tests/build_golden_set.py` は実 CSV から評価用データを複製します。実行すると PII の保管場所が 1 つ増えることを理解した上で使う

### 「完全ローカル」の範囲

- 音声の文字起こしと LLM 推論はすべて手元の PC / GPU で実行され、外部 API には送信されません
- Ollama の接続先は `http://127.0.0.1:11434` にコード上で固定されています。環境変数 `OLLAMA_HOST` では変更できません
- **例外は Chatwork 通知のみ**です。有効化した場合、HTTPS で `api.chatwork.com` に送信されます。送信内容は「処理件数」と「出力先ファイルパス」のみで、通話本文・氏名・電話番号は含みません。通知先ルームの閲覧権限と保存期間は運用側で管理してください
- Chatwork 通知が不要な場合は `config.yaml` の `chatwork_settings.enable` を `false` にしてください

---

## セットアップ

```bash
# 1. 依存インストール（GPU 版 torch を含む。環境に合わせて調整）
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 2. Ollama に LLM を登録（例: ELYZA gguf を Modelfile で作成）
#    Modelfile 内の FROM パスをダウンロードした gguf に合わせて編集
ollama create elyza3 -f Modelfile

# 3. 設定ファイルを自分の環境に合わせて編集
#    config.yaml の path_settings / whisper_settings / ollama_settings を編集
```

### 主要な設定（`config.yaml`）

- `path_settings.base_path` … 通話ログ（mp3）ルートフォルダ
- `path_settings.tenant_folders` … 拠点ごとのサブフォルダ名
- `whisper_settings` … Whisper 実行ファイル/モデルのパス
- `ollama_settings.model_name` … 使用する Ollama モデルのタグ（例: `elyza3`）
- `ollama_settings.host` … Ollama の接続先（未指定なら `http://127.0.0.1:11434` に固定。環境変数 `OLLAMA_HOST` では変更できません）
  - GUI の起動判定先だけを変えたい場合は環境変数 `NGBOT_OLLAMA_HOST` を使います（疎通確認専用。通話データの送信先ではありません）
- `extraction_items` / `ng_reason_definitions` … 抽出項目と NG 理由の定義（プロンプトに反映）
- `chatwork_settings` … 通知設定（トークン/ルームIDは環境変数で設定し、この YAML には書かない）

認証情報は設定ファイルに平文で書かず、Windows のユーザー環境変数で渡してください。
`config.yaml` の `chatwork_settings.api_token` / `room_id` は空のままにしてください
（環境変数が空の場合のみフォールバックとして参照されます）。

```bat
setx NGBOT_CHATWORK_TOKEN "＜Chatwork APIトークン＞"
setx NGBOT_CHATWORK_ROOM_ID "＜通知先ルームID＞"
```

`setx` は新しく開いたコマンドプロンプト/GUI から有効になります。
トークンが未設定の場合、Chatwork 通知は自動的に無効化されます（起動時に警告ログが出ます）。

---

## 使い方

```bash
# CLI（ワンショット実行）
run_ngbot.bat
# または
python main.py

# GUI（スケジュール実行・状態表示）
run_ngbot_gui.bat
# または
python gui\app.py
```

---

## 評価（オプション）

ローカル LLM を用意した状態で、ゴールデンセットに対する精度を測れます。

```bash
# 同梱の合成サンプル（sample_*_expected.json）で動作確認
python tests\run_eval.py --model elyza3

# 自前の実データからゴールデンセットを構築（PII のため .gitignore 済み）
python tests\build_golden_set.py --n 100 --since 2026-04-01
```

`run_eval.py` は完全一致率・フィールド別精度・NG理由の混同行列・利用回線 N/A 遵守率・
推論レイテンシ（p50/p95）を出力します。

---

## プロジェクト構成

```
main.py                 … 本体（音声収集→Whisper→LLM→バリデーション→CSV→Chatwork）
config.yaml             … 抽出項目・NG理由定義・各種パスの設定
Modelfile               … Ollama モデル定義（gguf は各自用意）
gui/                    … デスクトップGUI（app / scheduler / runner / status ほか）
cache_manager.py        … 文字起こし結果のキャッシュ
log_analyzer.py         … 実行ログ分析ユーティリティ
tests/                  … 評価・カナリア試験・ゴールデンセット構築スクリプト
```

---

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照してください。
