# =====AMD GPU対応版 音声分析プログラム（Ollama Vulkan版・日時CSV・Chatwork通知）=====
# Const-me/Whisper (main.exe) + Ollama (Vulkan)
#
# 【セットアップ要件】
# 1. Whisperモデル: "ggml-large-v2.bin" または "ggml-medium.bin"
# 2. Ollama: 公式サイトの最新版をインストール
# 3. 環境変数 OLLAMA_VULKAN=1 を設定し、PCを再起動
# 4. config.yaml に chatwork_settings を追加
# 5. pip install requests を実行

import os
import pandas as pd
import ollama
import json
from datetime import datetime, time as dt_time, timedelta
import yaml
import time
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import hashlib
from pathlib import Path
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, List, Tuple, Optional
import queue
import re
import signal
import threading
import sys
import subprocess
import tempfile
import requests  # Chatwork通知用

# --- ロギング設定 ---
class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        try:
            msg = self.format(record)
            # pythonw.exe 起動時は stdout/stderr が None のことがある。
            # tqdm.write はデフォルトで sys.stdout に書くため、None なら静かに捨てる。
            if sys.stdout is None and sys.stderr is None:
                return
            try:
                tqdm.write(msg)
            except (AttributeError, ValueError):
                # tqdm 内部で sys.stdout.write を呼んで失敗したケース
                pass
            self.flush()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            self.handleError(record)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.handlers = []

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")

tqdm_handler = TqdmLoggingHandler()
tqdm_handler.setFormatter(formatter)
logger.addHandler(tqdm_handler)

# 日次ローテーション。当日 + backupCount 日分を保持し、それより古いものは自動削除。
# backupCount=3 → 「audio_analysis.log + .YYYY-MM-DD x 3」= 直近4日分まで残す。
file_handler = TimedRotatingFileHandler(
    "audio_analysis.log",
    when="midnight",
    interval=1,
    backupCount=3,
    encoding="utf-8",
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


# --- Ollama 接続先 ---
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


def resolve_ollama_host(ollama_settings: dict) -> str:
    """Ollama の接続先を解決する。

    host を明示的に渡さないと ollama クライアントは環境変数 OLLAMA_HOST を接続先に
    採用するため、環境汚染や設定ミスで通話文字起こしが外部LLMへ送信されうる。
    外部の Ollama を使う場合のみ config.yaml の ollama_settings.host で明示指定する。
    評価スクリプトなど別のクライアントを作る箇所も必ずこの関数を経由すること。
    """
    host = str((ollama_settings or {}).get("host") or DEFAULT_OLLAMA_HOST)
    if host != DEFAULT_OLLAMA_HOST:
        logger.warning(
            f"Ollama接続先がローカル既定値ではありません: {host} "
            "（通話文字起こしがこの宛先へ送信されます）")
    return host


# --- CSV インジェクション対策 ---
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def escape_csv_formula(value):
    """Excel/LibreOffice で数式として解釈される先頭文字を無害化する。

    先頭の空白類（スペース・タブ・改行等）を除いた最初の文字が = + - @ のセルに
    シングルクォートを付与する。文字列以外（数値・None 等）はそのまま返す。
    読み戻し側は tests/build_golden_set.py の unescape_csv_formula() が対応する。

    既知の限界: 元データ自体が シングルクォート + 数式文字 で始まる場合はエスケープ
    されず、読み戻し時にそのクォートが失われる。日本語の通話文字起こしでは実質発生しない。
    """
    if not isinstance(value, str):
        return value
    if value.lstrip()[:1] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


# --- Chatwork通知クラス ---
class ChatworkNotifier:
    def __init__(self, config: dict):
        cw_settings = config.get("chatwork_settings", {})
        self.enabled = cw_settings.get("enable", False)
        # APIトークンは環境変数のみから取る。config.yaml は Git 追跡下のため、
        # フォールバックを残すと平文トークンを commit する誘因になる。
        self.api_token = os.getenv("NGBOT_CHATWORK_TOKEN", "")
        # ルームIDは秘密情報ではないので config.yaml でも設定できる。
        self.room_id = os.getenv("NGBOT_CHATWORK_ROOM_ID") or str(cw_settings.get("room_id", ""))
        self.api_url = f"https://api.chatwork.com/v2/rooms/{self.room_id}/messages"
        if self.enabled and cw_settings.get("api_token"):
            logger.warning("Chatwork: config.yaml の api_token は使用されません。"
                           "環境変数 NGBOT_CHATWORK_TOKEN を設定し、config.yaml からは削除してください")
        if self.enabled and not self.api_token:
            logger.warning("Chatwork: APIトークン未設定のため通知は無効化されます（環境変数 NGBOT_CHATWORK_TOKEN を設定してください）")
            self.enabled = False
        if self.enabled and not self.room_id:
            logger.warning("Chatwork: ルームID未設定のため通知は無効化されます（環境変数 NGBOT_CHATWORK_ROOM_ID または config.yaml の room_id を設定してください）")
            self.enabled = False

    def send_message(self, message: str):
        if not self.enabled or not self.api_token or not self.room_id:
            return

        headers = {'X-ChatWorkToken': self.api_token}
        params = {'body': message}

        try:
            response = requests.post(self.api_url, headers=headers, data=params)
            if response.status_code == 200:
                logger.info("Chatworkに通知を送信しました。")
            else:
                logger.warning(f"Chatwork送信失敗: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Chatwork接続エラー: {e}")


# --- 進捗管理クラス ---
class ProgressTracker:
    """tqdmの全体進捗表示。コンソール無し環境(pythonw.exe)では disable して安全動作。"""
    def __init__(self):
        self.start_time = time.time()
        self.processed_count = 0
        self.pbar = None
        # pythonw.exe で起動された場合や stdout/stderr が無効な環境では
        # tqdm を disable する（GUIが独自に進捗表示するので問題ない）。
        self._disable = (sys.stdout is None or sys.stderr is None
                         or not hasattr(sys.stderr, "fileno"))

    def start(self, total_files: int):
        try:
            self.pbar = tqdm(total=total_files, desc="全体進捗", unit="file",
                             disable=self._disable)
        except Exception as e:
            logger.warning(f"tqdm 初期化失敗（progress disable）: {e}")
            self.pbar = None

    def update(self, n=1):
        if self.pbar:
            self.processed_count += n
            try:
                self.pbar.update(n)
                self.pbar.set_postfix_str(self.get_stats_str())
            except (AttributeError, ValueError, OSError):
                # disable後でも内部状態によっては書き込みを試みる場合がある
                pass
            
    def get_stats_str(self) -> str:
        elapsed_time = time.time() - self.start_time
        if self.processed_count == 0 or elapsed_time < 1:
            return "計算中..."

        avg_time_per_file = elapsed_time / self.processed_count
        
        now = datetime.now()
        end_of_day = datetime.combine(now.date(), dt_time(23, 59, 59))
        remaining_seconds = (end_of_day - now).total_seconds()
        
        predicted_count = 0
        if avg_time_per_file > 0 and remaining_seconds > 0:
            predicted_count = int(remaining_seconds / avg_time_per_file)
        
        return (f"平均: {avg_time_per_file:.1f}s/件 | "
                f"23:59予測: +{predicted_count}件")

    def set_description(self, desc: str):
        if self.pbar:
            try:
                self.pbar.set_description_str(desc)
            except (AttributeError, ValueError, OSError):
                pass

    def close(self):
        if self.pbar:
            try:
                self.pbar.close()
            except (AttributeError, ValueError, OSError):
                pass


class GracefulKiller:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized: return
        self.kill_now = False
        self.shutdown_requested = False
        self._original_sigint = None
        self._initialized = True

    def setup_handlers(self):
        self._original_sigint = signal.signal(signal.SIGINT, self._exit_gracefully)
        logger.info("中断ハンドラを設定しました（Ctrl+Cで安全に停止）")

    def restore_handlers(self):
        if self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)

    def _exit_gracefully(self, signum, frame):
        if self.shutdown_requested:
            logger.warning("\n⚠️ 強制終了します...")
            sys.exit(1)
        self.shutdown_requested = True
        self.kill_now = True
        tqdm.write("\n🛑 中断要求を受信しました（Ctrl+C）")

    @property
    def should_stop(self) -> bool:
        return self.kill_now


graceful_killer = GracefulKiller()


class ResultValidator:
    def __init__(self, config: dict):
        self.config = config
        validation_settings = config.get("validation_settings", {})
        
        line_validation = validation_settings.get("line_validation", {})
        self.customer_confirmation_keywords = line_validation.get(
            "customer_confirmation_keywords",
            ["はい", "そうです", "そうですね", "ええ", "間違いない", "使ってます"]
        )
        self.appointer_question_patterns = line_validation.get(
            "appointer_question_patterns",
            ["ご利用でお間違いないでしょうか", "ご利用で間違いない", "のご利用で"]
        )
        
        rusu_validation = validation_settings.get("rusu_validation", {})
        self.short_text_threshold = rusu_validation.get("short_text_threshold", 30)
        self.customer_response_keywords = rusu_validation.get(
            "customer_response_keywords",
            ["はい", "いいえ", "結構です", "いりません", "大丈夫です", "忙しい", "もしもし"]
        )
        self.rusu_keywords = rusu_validation.get(
            "rusu_keywords",
            ["留守", "留守番電話", "録音", "メッセージ", "応答できません"]
        )
        
        self.line_names = [line["name"] for line in config.get("line_definitions", [])]
    
    def validate_and_correct(self, extracted_data: dict, conversation_text: str) -> dict:
        validated = extracted_data.copy()
        
        if self._is_transcription_failed(conversation_text):
            validated["利用回線"] = "N/A"
            validated["NG理由"] = "留守"
            validated["NG理由箇所"] = "N/A"
            return self._clean_all_fields(validated)
        
        validated = self._validate_rusu(validated, conversation_text)
        validated = self._validate_line(validated, conversation_text)
        validated = self._validate_ng_location(validated, conversation_text)
        
        return self._clean_all_fields(validated)

    def _clean_all_fields(self, data: dict) -> dict:
        cleaned = {}
        for k, v in data.items():
            if isinstance(v, bool):
                cleaned[k] = "有" if v else "N/A"
            elif isinstance(v, str):
                if v.lower() == "true":
                    cleaned[k] = "有" 
                elif v.lower() == "false":
                    cleaned[k] = "N/A"
                elif v.lower() in ["none", "null", "n/a"]:
                    cleaned[k] = "N/A"
                else:
                    cleaned[k] = v
            elif v is None:
                cleaned[k] = "N/A"
            else:
                cleaned[k] = str(v)
        return cleaned
    
    def _is_transcription_failed(self, text: str) -> bool:
        if not text:
            return True
        return any(p in text for p in ["文字起こし失敗", "文字起こしエラー", "実行エラー", "タイムアウト"])

    def is_likely_rusu(self, text: str) -> bool:
        """ルールベースで留守確定できるか判定（L-4 用の高速フィルタ）。

        判定条件:
        1. 文字起こし失敗系のマーカーを含む
        2. 顧客応答キーワードを一切含まない、かつ
           (a) 文字数 < short_text_threshold もしくは
           (b) 既知の留守キーワードを含む
        """
        if not text:
            return True
        if self._is_transcription_failed(text):
            return True

        if any(kw in text for kw in self.customer_response_keywords):
            return False

        if len(text) < self.short_text_threshold:
            return True
        if any(kw in text for kw in self.rusu_keywords):
            return True
        return False
    
    def _validate_rusu(self, data: dict, text: str) -> dict:
        if len(text) < self.short_text_threshold and not any(kw in text for kw in self.customer_response_keywords):
            data["NG理由"] = "留守"
            data["利用回線"] = "N/A"
            return data
        return data
    
    def _validate_line(self, data: dict, text: str) -> dict:
        current_line = data.get("利用回線", "N/A")
        if current_line == "N/A" or not current_line:
            return data
        
        if not any(line in text for line in self.line_names if line != "N/A"):
            data["利用回線"] = "N/A"
            return data
        
        if any(p in text for p in self.appointer_question_patterns) and not any(kw in text for kw in self.customer_confirmation_keywords):
            data["利用回線"] = "N/A"
        
        return data
    
    def _validate_ng_location(self, data: dict, text: str) -> dict:
        ng_location = data.get("NG理由箇所", "")
        if ng_location and ng_location != "N/A" and ng_location not in text:
            data["NG理由箇所"] = "N/A"
        return data


class WhisperGPUBatch:
    """Const-me/Whisper CLI (main.exe) を直接使用するクラス"""
    
    def __init__(self, config: dict):
        whisper_settings = config.get("whisper_settings", {})
        self.exe_path = whisper_settings.get("exe_path", "C:/cli/main.exe")
        self.model_path = whisper_settings.get("model_path", "C:/whisper-models/ggml-medium.bin")

        # T-1: ドメイン用語のinitial prompt。回線名・業界用語を事前に渡すことで
        # 音の近い語への誤認識（例:「えーしゃひかり」→「A社光」）を抑制する。
        # 設定で上書き可能。指定なければ line_definitions から自動構築。
        custom_prompt = whisper_settings.get("initial_prompt")
        if custom_prompt:
            self.initial_prompt = str(custom_prompt)
        else:
            line_names = [l["name"] for l in config.get("line_definitions", [])
                          if l.get("name") and l["name"] != "N/A"]
            base_terms = ["光回線", "インターネット", "アポインター", "営業電話",
                          "継続確認", "プラン", "料金"]
            self.initial_prompt = "、".join(line_names + base_terms) + "。"

        if not os.path.exists(self.exe_path):
            raise FileNotFoundError(f"Whisper実行ファイルが見つかりません: {self.exe_path}")
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"モデルファイルが見つかりません: {self.model_path}")

        # initial_prompt は内部的に約224トークン上限。日本語1文字≒1.5トークン目安で
        # 概ね150文字超で警告。実害が出るのは200文字超あたりから。
        if len(self.initial_prompt) > 150:
            logger.warning(
                f"Whisper initial_prompt が長すぎる可能性 ({len(self.initial_prompt)}文字)。"
                "224トークン上限超過で先頭が切られる場合があります"
            )

        # 停止ボタン押下で即 kill するため Popen ハンドルを保持する。
        self._current_proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()

        logger.info(f"WhisperGPU CLI 初期化: OK (prompt={self.initial_prompt[:60]}...)")

    def terminate(self):
        """停止要求時に外部から呼ぶ。実行中の Whisper プロセスを即 kill する。"""
        with self._proc_lock:
            p = self._current_proc
        if p and p.poll() is None:
            try:
                p.kill()
                logger.info("Whisper subprocess killed (stop request)")
            except Exception as e:
                logger.warning(f"Whisper kill 失敗: {e}")

    def _parse_srt(self, srt_content: str) -> str:
        lines = []
        for line in srt_content.splitlines():
            l = line.strip()
            if l and not l.isdigit() and '-->' not in l:
                lines.append(l)
        return "".join(lines)

    def transcribe_batch(self, audio_paths: List[str]) -> Dict[str, str]:
        if not audio_paths:
            return {}
        
        results = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_files_map = {}
            for i, p in enumerate(audio_paths):
                if not os.path.exists(p):
                    logger.warning(f"ファイル消失によりスキップ: {p}")
                    results[p] = "（ファイル消失）"
                    continue

                ext = os.path.splitext(p)[1] or ".mp3"
                temp_path = os.path.join(temp_dir, f"{i:04d}{ext}")
                try:
                    shutil.copy2(p, temp_path)
                    temp_files_map[temp_path] = p
                except Exception as e:
                    logger.error(f"コピー失敗: {p} -> {e}")
                    results[p] = f"（ファイルアクセスエラー）"

            target_temp_paths = list(temp_files_map.keys())
            if not target_temp_paths:
                return results

            cmd = [self.exe_path, "-m", self.model_path, "-l", "ja", "-osrt"]
            # T-1: ドメイン用語の初期プロンプト注入（空文字なら付与しない）
            if self.initial_prompt:
                cmd += ["--prompt", self.initial_prompt]
            cmd += list(target_temp_paths)
            
            try:
                timeout_sec = 300 + (120 * len(target_temp_paths))
                startupinfo = subprocess.STARTUPINFO() if os.name == 'nt' else None
                if os.name == 'nt':
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                # Popen + ポーリングで should_stop に即応答。1秒以内に kill 可能。
                # stdout/stderr は使わないので DEVNULL に流して pipe-fill deadlock を防ぐ。
                deadline = time.time() + timeout_sec
                with self._proc_lock:
                    self._current_proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        startupinfo=startupinfo,
                    )
                process = self._current_proc

                killed_by_stop = False
                timed_out = False
                while True:
                    rc = process.poll()
                    if rc is not None:
                        break
                    if graceful_killer.should_stop:
                        try:
                            process.kill()
                            logger.info("Whisper subprocess killed (stop request)")
                        except Exception as e:
                            logger.warning(f"Whisper kill 失敗: {e}")
                        killed_by_stop = True
                        # kill 後、終了を確認
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                        break
                    if time.time() > deadline:
                        try:
                            process.kill()
                        except Exception:
                            pass
                        timed_out = True
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                        break
                    time.sleep(0.5)

                with self._proc_lock:
                    self._current_proc = None

                if killed_by_stop:
                    for p in temp_files_map.values():
                        results[p] = "（停止要求により中断）"
                    return results
                if timed_out:
                    logger.error(f"Whisper処理タイムアウト ({len(target_temp_paths)}件)")
                    for p in temp_files_map.values():
                        results[p] = "（タイムアウト）"
                    return results

                if process.returncode != 0:
                    logger.warning(
                        f"Whisper返却コード異常: Code={process.returncode} "
                        f"(batch={len(temp_files_map)}件)。個別SRT生成結果で継続"
                    )

                for temp_p, orig_p in temp_files_map.items():
                    candidates = [temp_p + ".srt", os.path.splitext(temp_p)[0] + ".srt"]
                    found_srt = None
                    for c in candidates:
                        if os.path.exists(c):
                            found_srt = c
                            break

                    if found_srt:
                        try:
                            with open(found_srt, 'r', encoding='utf-8-sig', errors='replace') as f:
                                srt_text = self._parse_srt(f.read())
                            if srt_text:
                                transcription = srt_text
                            else:
                                transcription = "（音声なし/無音）"
                        except Exception as e:
                            logger.warning(f"SRT読込失敗 {os.path.basename(orig_p)}: {e}")
                            transcription = "（結果読込エラー）"
                    else:
                        # SRT未生成 → returncodeに応じてエラー種別を区別
                        if process.returncode != 0:
                            transcription = f"（実行エラー: Code {process.returncode}）"
                        else:
                            transcription = "（文字起こし失敗）"

                    results[orig_p] = transcription

            except Exception as e:
                # タイムアウト/停止は Popen ループ内で処理済み。ここは Popen 生成失敗等
                logger.error(f"Whisper実行例外: {e}")
                with self._proc_lock:
                    self._current_proc = None
                for p in temp_files_map.values():
                    results[p] = f"（実行例外）"

        return results


class AudioAnalyzer:
    """音声ファイルの文字起こしと分析を行うクラス"""

    # ファイル名から日付(YYYYMMDD)を抽出する正規表現。
    # 想定する形式（いずれも対応）:
    #   "20260422_09000000000_01.mp3"  ← 日付が先頭 (現行のNGログ命名)
    #   "09000000000_20251120.mp3"     ← 日付が末尾 (旧形式)
    #   "09000000000_20251120_1530.mp3"
    # 8桁数字を `_` または ファイル先頭・拡張子境界 で囲まれた位置から抽出する。
    _FILENAME_DATE_RE = re.compile(r"(?:^|_)(\d{8})(?:_|\.|$)")

    def __init__(self, config: dict, progress_callback=None, target_date=None):
        """
        progress_callback: Optional[Callable[[dict], None]]
            GUIなど外部からの進捗監視用。{event, ...} 形式のdictを送る。
            event: 'start' | 'file_done' | 'tenant_done' | 'finish' | 'error'
        target_date: Optional[Union[str, date]]
            指定日(YYYY-MM-DD or date)のファイルのみ処理対象。Noneなら全件。
            ファイル名 "<num>_YYYYMMDD..." から日付を抽出して照合する。
        """
        self.config = config
        self.progress_callback = progress_callback
        self.target_date = self._normalize_target_date(target_date)
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.cache_dir = os.path.join(self.script_dir, ".cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.validator = ResultValidator(config)
        self.notifier = ChatworkNotifier(config)

        perf_settings = config.get("performance_settings", {})
        self.batch_size = perf_settings.get("batch_size", 5)
        self.max_workers = perf_settings.get("max_workers", 5)
        self.enable_cache = perf_settings.get("enable_cache", True)
        self.retry_count = perf_settings.get("retry_on_error", 3)

        # Ollama LLMオプション。通話ログは平均1000文字以下なので2048で十分。
        ollama_settings = config.get("ollama_settings", {}) or {}
        self._llm_model_name = str(ollama_settings.get("model_name", "elyza3"))
        self._llm_num_ctx = int(ollama_settings.get("num_ctx", 2048))
        self._llm_num_predict = int(ollama_settings.get("num_predict", 512))
        self._enable_two_stage = bool(ollama_settings.get("enable_two_stage", True))
        # HTTPタイムアウト。Ollamaが固まった時にここで例外を出して次回スケジュールをブロックさせない。
        # デフォルト10分。長尺ログでもこの時間内に応答が無ければ異常と判断する。
        self._llm_timeout_sec = int(ollama_settings.get("timeout_sec", 600))
        self._ollama_host = resolve_ollama_host(ollama_settings)
        self._ollama_client = ollama.Client(host=self._ollama_host,
                                            timeout=self._llm_timeout_sec)
        logger.info(f"Ollama接続先: {self._ollama_host}")

        continuous_settings = config.get("continuous_mode", {})
        self.continuous_enabled = continuous_settings.get("enable", False)
        self.check_interval = continuous_settings.get("check_interval_seconds", 60)
        self.max_iterations = continuous_settings.get("max_iterations", 0)

        correction_settings = config.get("text_correction", {})
        self.enable_correction = correction_settings.get("enable", True)

        self.whisper = WhisperGPUBatch(config)
        
        # --- CSVファイル名に日時を入れる ---
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._realtime_csv_path = os.path.join(self.script_dir, f"{timestamp}_results.csv")
        self._realtime_results = []
        self._realtime_lock = threading.Lock()
        self._save_interval = perf_settings.get("save_interval", 1)

        logger.info(f"AudioAnalyzer 初期化完了")
        logger.info(f"出力ファイル: {self._realtime_csv_path}")
        if self.target_date:
            logger.info(f"対象日フィルタ: {self.target_date.isoformat()} のファイルのみ処理")

    @staticmethod
    def _normalize_target_date(value):
        """str/date/None を date|None に正規化。"""
        if value is None or value == "":
            return None
        from datetime import date as _date
        if isinstance(value, _date):
            return value
        s = str(value).strip()
        # 受け入れ可能な形式: YYYY-MM-DD, YYYYMMDD, YYYY/MM/DD
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        logger.warning(f"target_date を解釈できません: {value!r} (フィルタ無効)")
        return None

    @classmethod
    def _extract_date_from_filename(cls, filename: str):
        r"""ファイル名から date を抽出。失敗時 None。

        複数の8桁数字がある場合は **valid date として解釈できるもの** を優先する。
        年範囲(2000-2099)に収まる "YYYYMMDD" を有効とみなす。
        電話番号(11桁)中の偶然の8桁シーケンスを誤検出しないよう、
        境界付き(`(?:^|_)...(?:_|\.|$)`)でマッチさせている。
        """
        matches = cls._FILENAME_DATE_RE.findall(filename)
        if not matches:
            return None
        for token in matches:
            try:
                d = datetime.strptime(token, "%Y%m%d").date()
                # 妥当な年範囲かチェック（2000-2099に限定して誤マッチを防ぐ）
                if 2000 <= d.year <= 2099:
                    return d
            except ValueError:
                continue
        return None

    def _matches_target_date(self, filename: str) -> bool:
        """対象日フィルタが有効なときのみ判定。Noneなら常にTrue。"""
        if self.target_date is None:
            return True
        d = self._extract_date_from_filename(filename)
        if d is None:
            # 日付抽出不能なファイルは「対象外」として除外（安全側）
            return False
        return d == self.target_date

    def _notify(self, event: str, **kwargs):
        """進捗コールバックを呼ぶ。コールバック側の例外でメイン処理を止めない。"""
        if not self.progress_callback:
            return
        try:
            payload = {"event": event, **kwargs}
            self.progress_callback(payload)
        except Exception as e:
            logger.warning(f"progress_callback例外: {e}")

    def _get_unprocessed_files(self, tenant_folder: str) -> List[str]:
        path_settings = self.config.get("path_settings", {})
        base_path = path_settings.get("base_path")
        audio_dir = os.path.join(base_path, tenant_folder)

        if not os.path.isdir(audio_dir):
            return []

        try:
            all_mp3 = [f for f in os.listdir(audio_dir)
                       if f.lower().endswith(".mp3") and os.path.isfile(os.path.join(audio_dir, f))]
        except Exception as e:
            logger.error(f"ファイルリスト取得エラー ({tenant_folder}): {e}")
            return []

        if self.target_date is None:
            return all_mp3

        # 対象日フィルタ適用
        matched = [f for f in all_mp3 if self._matches_target_date(f)]
        skipped = len(all_mp3) - len(matched)
        if skipped > 0:
            logger.info(
                f"テナント '{tenant_folder}': 対象日 {self.target_date.isoformat()} で "
                f"{len(matched)}件抽出 / {skipped}件スキップ"
            )
        return matched

    def _add_result_and_save(self, result: dict) -> bool:
        if result is None:
            return False
        with self._realtime_lock:
            self._realtime_results.append(result)
            if len(self._realtime_results) % self._save_interval == 0:
                return self._save_realtime_csv()
        return True

    def _save_realtime_csv(self) -> bool:
        if not self._realtime_results:
            return True
        
        max_retries = 5
        for i in range(max_retries):
            try:
                df = pd.DataFrame(self._realtime_results)
                base_columns = ["電話番号", "テナント"]
                text_columns = ["文字起こしテキスト（生）", "文字起こしテキスト", "校正適用"] if self.enable_correction else ["文字起こしテキスト"]
                extracted_columns = [item["name"] for item in self.config.get("extraction_items", [])]
                # 判定根拠(LLMのreasoning)は最終列に配置
                trailing_columns = ["判定根拠"]

                target_columns = base_columns + text_columns + extracted_columns + trailing_columns
                existing_columns = [col for col in target_columns if col in df.columns]
                
                df = df.reindex(columns=existing_columns)
                # Excel で開く運用のため、数式として解釈されうるセルを無害化する
                df = df.apply(lambda s: s.map(escape_csv_formula))
                df.to_csv(self._realtime_csv_path, index=False, encoding="shift_jis", errors="replace")
                return True
            except PermissionError:
                if i < max_retries - 1:
                    logger.warning(f"CSV書き込み待機中... (Excelを閉じてください) {i+1}/{max_retries}")
                    time.sleep(2)
                else:
                    logger.error(f"CSV保存失敗: ファイルがロックされています: {self._realtime_csv_path}")
            except Exception as e:
                logger.error(f"CSV保存エラー: {e}")
                break
        return False

    def _finalize_realtime_csv(self) -> Optional[str]:
        with self._realtime_lock:
            has_results = bool(self._realtime_results)
        if has_results:
            self._save_realtime_csv()
        return self._realtime_csv_path

    def _reset_realtime_results(self):
        with self._realtime_lock:
            self._realtime_results = []

    def _get_file_hash(self, file_path: str) -> str:
        hasher = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            logger.warning(f"ハッシュ計算失敗 {file_path}: {e}")
            return "error_hash"

    def _load_from_cache(self, file_path: str) -> Optional[dict]:
        if not self.enable_cache:
            return None
        if not os.path.exists(file_path):
            return None

        file_hash = self._get_file_hash(file_path)
        if file_hash == "error_hash":
            return None

        # キャッシュは JSON のみを読む。
        # 旧形式(.pkl)は pickle.load() が任意コード実行になりうるため読み込まない。
        # 既存の .pkl はキャッシュミス扱いとなり、対象ファイルは再処理される。
        json_path = os.path.join(self.cache_dir, f"{file_hash}_main.json")

        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"JSONキャッシュ読込失敗 {os.path.basename(json_path)}: {e}")

        return None

    def _save_to_cache(self, file_path: str, data: dict):
        if not self.enable_cache or not os.path.exists(file_path):
            return
        file_hash = self._get_file_hash(file_path)
        if file_hash == "error_hash":
            return
        cache_path = os.path.join(self.cache_dir, f"{file_hash}_main.json")
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"キャッシュ保存失敗 {os.path.basename(cache_path)}: {e}")

    def _correct_transcription(self, raw_text: str) -> Tuple[str, bool]:
        if not self.enable_correction or not raw_text:
            return raw_text, False
        # 表記ゆれの補正表。ここはサンプルで、実運用では扱うサービス名に合わせて定義する。
        replacements = {"えーしゃひかり": "A社光", "びーしゃひかり": "B社ひかり"}
        corrected = raw_text
        for old, new in replacements.items():
            corrected = corrected.replace(old, new)
        return corrected, corrected != raw_text

    def _generate_prompts(self, conversation_text: str) -> Tuple[str, str]:
        config = self.config
        line_choices = [line["name"] for line in config.get("line_definitions", [])]
        ng_reasons = [reason["name"] for reason in config.get("ng_reason_definitions", [])]

        prompt_opt = config.get("prompt_optimization", {}) or {}
        use_few_shot = bool(prompt_opt.get("use_few_shot", True))
        use_cot = bool(prompt_opt.get("use_chain_of_thought", True))

        # --- Few-shot例: config.yaml の ng_examples を取り込む ---
        few_shot_block = ""
        if use_few_shot:
            examples = config.get("ng_examples", []) or []
            if examples:
                lines = ["【Few-shot 判定例】"]
                for i, ex in enumerate(examples, 1):
                    lines.append(
                        f"\n例{i}:\n"
                        f"  会話: 「{ex.get('text', '')}」\n"
                        f"  → NG理由: {ex.get('ng_reason', 'N/A')}\n"
                        f"  → NG理由箇所: {ex.get('ng_location', 'N/A')}\n"
                        f"  → 利用回線: {ex.get('line', 'N/A')}\n"
                        f"  → 根拠: {ex.get('explanation', '')}"
                    )
                few_shot_block = "\n".join(lines) + "\n"

        # --- Chain-of-Thought の段階指示 ---
        cot_block = ""
        if use_cot:
            cot_block = """
【判定の手順（必ず以下の順で考えること）】
Step1: 会話ログ全体を読み、顧客の発言（応答）が存在するか確認する
       → 顧客発言がない/留守電アナウンスのみ → NG理由は「留守」で確定。利用回線も「N/A」
Step2: 顧客発言がある場合、アポインターの趣旨説明（料金/プラン/サービス内容）が完了しているか判定
       → 説明前の拒否 → アプローチNG候補
       → 説明後の拒否 → 申込意思無NG/料金NG/不信NG等の具体的理由を検討
Step3: 利用回線について、顧客が明確に肯定した発言があるか確認
       → アポインターの質問のみ・顧客の肯定なし → 「N/A」
Step4: NG理由箇所は会話ログから「顧客の発言」をそのまま抜き出す（アポインター発言は不可）
Step5: 上記の思考過程を reasoning に簡潔に記述する（150文字以内）
"""

        system_prompt = f"""あなたは公平で客観的なコールセンター分析官です。
提供された「光回線営業の通話ログ」から情報を抽出し、JSON形式で出力してください。

【抽出項目ルール】
1. 固定電話: "有", "無", "N/A" のいずれか。（true/falseは禁止）
2. 利用回線: 顧客が明確に肯定した場合のみ出力。推測は厳禁。不明なら"N/A"。
3. NG理由箇所: 必ず会話ログ中に存在する顧客の発言をそのまま引用すること。

【NG理由の判定基準（優先順）】
1. 留守: 留守電、発信音のみ。顧客が「もしもし」等一言でも話せば留守ではない。
2. 今忙NG: 「忙しい」「仕事中」「運転中」「準備中」「バタバタ」等の時間的理由。
3. アプローチNG: 挨拶直後・趣旨説明前に「結構です」「いりません」と断られた場合。
4. 料金NG/家族反対/提供確認NG/既契約NG/面倒NG/不信NG: 具体的理由が会話に出ている場合。
5. 申込意思無NG: 説明後に理由なく断られた、または現状維持希望（「今のままでいい」等）。
6. その他: 上記いずれにも明確に該当しない特殊ケース。
{cot_block}
{few_shot_block}
【選択肢リスト】
回線: {', '.join(line_choices)}
NG理由: {', '.join(ng_reasons)}
"""

        user_prompt = f"""以下の会話ログを分析してください。

【会話ログ】
{conversation_text}

【出力フォーマット(JSON)】
{{
  "reasoning": "判定の根拠（思考プロセス）",
  "氏名": "",
  "郵便番号": "",
  "都道府県": "",
  "利用回線": "",
  "携帯台数": "",
  "戸建・MS": "",
  "固定電話": "",
  "ひかりTV": "",
  "決裁者・非決裁者": "",
  "NG理由": "",
  "NG理由箇所": ""
}}
"""
        return system_prompt, user_prompt

    def _build_rusu_result(self, reason_text: str = "ルール判定: 顧客応答なし→留守確定") -> dict:
        """留守確定時のresult dictを生成（LLM呼び出しなし）。"""
        items = self.config.get("extraction_items", []) or []
        result = {item["name"]: "N/A" for item in items}
        result["NG理由"] = "留守"
        result["NG理由箇所"] = "N/A"
        result["利用回線"] = "N/A"
        result["reasoning"] = reason_text
        return result

    def _analyze_with_llm(self, text: str) -> dict:
        """Ollama (Vulkan) を使用して分析（JSONエラー対策強化版）。

        L-4: 留守確定パターンはルールベースで先に判定し、LLM呼び出しを省略。
        ELYZA-8B 1コール ~3-10秒 × 留守率30%程度のケースで全体30-40%短縮見込み。
        """
        # --- Stage 1: ルールベース留守判定（LLM省略） ---
        if self._enable_two_stage and self.validator.is_likely_rusu(text):
            logger.debug("Stage1で留守確定 → LLMスキップ")
            # R-4対応: Stage1結果も _clean_all_fields を通して通常経路と整合させる
            rusu = self._build_rusu_result()
            return self.validator.validate_and_correct(rusu, text)

        # --- Stage 2: LLM詳細分析 ---
        system_prompt, user_prompt = self._generate_prompts(text)
        model_name = self._llm_model_name

        last_error_kind = "Error"  # 最終失敗時のラベル (タイムアウトなら "Timeout")
        for attempt in range(self.retry_count):
            try:
                if graceful_killer.should_stop:
                    return {}

                response = self._ollama_client.chat(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    format="json",
                    # keep_alive: モデルをメモリに常駐 (24時間)。
                    # アイドル後のコールドリロード (数十秒) を排除する。
                    keep_alive="24h",
                    options={
                        "temperature": 0.0,  # 確実性を高めるため0.0推奨
                        "num_predict": self._llm_num_predict,
                        "num_ctx": self._llm_num_ctx,
                    },
                )
                
                content = response["message"]["content"]
                
                # --- JSON抽出とパース処理の強化 ---
                try:
                    # 1. まずそのままパースを試みる
                    extracted_data = json.loads(content)
                except json.JSONDecodeError:
                    # 2. 失敗した場合、Markdown記号や余計な文字を除去して再試行
                    # 最も外側の {} を探す正規表現
                    json_match = re.search(r'\{[\s\S]*\}', content)
                    if json_match:
                        json_str = json_match.group(0)
                        try:
                            extracted_data = json.loads(json_str)
                        except json.JSONDecodeError as json_err:
                            # それでもダメならリトライへ
                            logger.warning(f"JSON文法エラー (試行 {attempt+1}/{self.retry_count}): {json_err}")
                            logger.debug(f"不正なJSON内容: {json_str[:200]}...") # 冒頭200文字をログに残す
                            time.sleep(0.5) # 少し待ってからリトライ
                            continue
                    else:
                        # JSONの形すらしていない場合
                        logger.warning(f"JSONが見つかりません (試行 {attempt+1}/{self.retry_count})")
                        time.sleep(0.5)
                        continue

                # 成功したらバリデーションを通す
                return self.validator.validate_and_correct(extracted_data, text)

            except Exception as e:
                # タイムアウト/接続失敗系は Ollama が落ちている可能性が高い。
                # retry × timeout で合計待ち時間が爆発する (例: 600s × 3 = 30分) のを防ぐため
                # 即 fail-fast でループを抜ける。
                ename = type(e).__name__
                is_timeout = ("Timeout" in ename) or ("ConnectError" in ename) or isinstance(e, TimeoutError)
                if is_timeout:
                    last_error_kind = "Timeout"
                    logger.error(
                        f"LLM接続/タイムアウト失敗 ({ename}): Ollama の応答なし or キュー待ち過大。"
                        " max_workers と OLLAMA_NUM_PARALLEL を揃えてください"
                    )
                    break
                last_error_kind = "Error"
                if attempt == self.retry_count - 1:
                    logger.error(f"LLM分析 最終失敗: {e}")
                else:
                    logger.warning(f"LLM通信/不明なエラー (試行 {attempt+1}/{self.retry_count}): {e}")
                time.sleep(0.5)

        # 全てのリトライに失敗した場合 (タイムアウト or その他で区別)
        return {item["name"]: last_error_kind for item in self.config.get("extraction_items", [])}

    def _analyze_single(self, item: Tuple[str, str, str, str]) -> Optional[dict]:
        file_path, filename, tenant_folder, raw_text = item
        try:
            if graceful_killer.should_stop:
                return None

            phone_number = os.path.splitext(filename)[0].split("_")[0]

            corrected_text, was_corrected = self._correct_transcription(raw_text)
            extracted_data = self._analyze_with_llm(corrected_text)

            # reasoning(LLMの判定根拠) は別カラム「判定根拠」として保持しデバッグ性を高める
            reasoning_text = ""
            if isinstance(extracted_data, dict) and "reasoning" in extracted_data:
                reasoning_text = str(extracted_data.pop("reasoning") or "")

            result = {
                "テナント": tenant_folder,
                "電話番号": phone_number,
                "文字起こしテキスト（生）": raw_text if self.enable_correction else None,
                "文字起こしテキスト": corrected_text,
                "校正適用": "はい" if was_corrected else "いいえ",
                **extracted_data,
                "判定根拠": reasoning_text or "N/A",
            }

            self._save_to_cache(file_path, result)
            return result
        except Exception as e:
            logger.error(f"分析エラー {filename}: {e}")
            return None

    def process_tenant_folder(self, tenant_folder: str, tracker: ProgressTracker) -> Tuple[List[dict], bool]:
        """
        Whisper⇄LLM パイプライン化版。
          - Producer スレッド: Whisper を逐次バッチ処理し、結果を queue (maxsize=1) に積む
          - Consumer (本関数 = メインスレッド): キャッシュヒット適用 + LLM分析を ThreadPoolExecutor で実行
          - これにより バッチN+1 の Whisper と バッチN の LLM が並走する
          - ストップ要求時は Producer/Consumer 双方が graceful_killer.should_stop を見て即終了
        """
        path_settings = self.config.get("path_settings", {})
        base_path = path_settings.get("base_path")
        processed_folder_name = path_settings.get("processed_folder_name", "分析済み")

        audio_dir = os.path.join(base_path, tenant_folder)
        processed_dir = os.path.join(audio_dir, processed_folder_name)
        os.makedirs(processed_dir, exist_ok=True)

        audio_files = self._get_unprocessed_files(tenant_folder)
        if not audio_files:
            return [], False

        results: List[dict] = []
        interrupted = False
        total_batches = (len(audio_files) + self.batch_size - 1) // self.batch_size

        # Producer ↔ Consumer をつなぐキュー。maxsize=1 で「1バッチ先読み」(メモリ節約)
        pipe: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=1)
        producer_done = threading.Event()

        def _producer():
            """Whisperバッチを逐次走らせて pipe に積む。"""
            try:
                for batch_idx, batch_start in enumerate(
                    range(0, len(audio_files), self.batch_size), start=1
                ):
                    if graceful_killer.should_stop:
                        return
                    batch_files_all = audio_files[batch_start:batch_start + self.batch_size]

                    uncached_items: List[Tuple[str, str]] = []
                    cache_hit_items: List[Tuple[str, str, dict]] = []
                    missing_count = 0
                    for filename in batch_files_all:
                        if graceful_killer.should_stop:
                            return
                        path = os.path.join(audio_dir, filename)
                        if not os.path.exists(path):
                            missing_count += 1
                            continue
                        cached = self._load_from_cache(path)
                        if cached:
                            cached["テナント"] = tenant_folder
                            cache_hit_items.append((path, filename, cached))
                        else:
                            uncached_items.append((path, filename))

                    if not uncached_items and not cache_hit_items and missing_count == 0:
                        continue

                    transcriptions: Dict[str, str] = {}
                    whisper_elapsed = 0.0
                    if uncached_items:
                        self._notify("batch_phase",
                                     tenant=tenant_folder, phase="whisper",
                                     batch_idx=batch_idx, batch_total=total_batches,
                                     count=len(uncached_items),
                                     files=[f for _, f in uncached_items],
                                     cache_hits=len(cache_hit_items))
                        tracker.set_description(
                            f"テナント: {tenant_folder} ({len(uncached_items)}件を文字起こし中)"
                        )
                        whisper_t0 = time.time()
                        transcriptions = self.whisper.transcribe_batch(
                            [p for p, _ in uncached_items]
                        )
                        whisper_elapsed = time.time() - whisper_t0

                    if graceful_killer.should_stop:
                        return

                    # 1バッチ分を pipe へ送る。Consumer が止まっている場合に永久ブロック
                    # しないよう timeout 付きループで should_stop を監視する。
                    item_to_put = {
                        "batch_idx": batch_idx,
                        "uncached": uncached_items,
                        "cache_hits": cache_hit_items,
                        "transcriptions": transcriptions,
                        "whisper_sec": whisper_elapsed,
                        "missing_count": missing_count,
                    }
                    while not graceful_killer.should_stop:
                        try:
                            pipe.put(item_to_put, timeout=1)
                            break
                        except queue.Full:
                            continue
                    if graceful_killer.should_stop:
                        return
            except Exception:
                logger.exception("Whisper producer 例外")
            finally:
                producer_done.set()
                # sentinel: Consumer 側のループ終了に使う
                try:
                    pipe.put_nowait(None)
                except queue.Full:
                    # Consumer がこの後 get するので、追加 None は捨ててよい (どうせ break する)
                    pass

        producer_thread = threading.Thread(
            target=_producer, name=f"whisper-producer-{tenant_folder}", daemon=True
        )
        producer_thread.start()

        # === Consumer ループ ===
        try:
            while True:
                if graceful_killer.should_stop:
                    interrupted = True
                    break

                try:
                    item = pipe.get(timeout=1)
                except queue.Empty:
                    if producer_done.is_set() and pipe.empty():
                        break
                    continue
                if item is None:  # sentinel
                    break

                # キャッシュヒットを同期適用
                for path, filename, cached in item["cache_hits"]:
                    results.append(cached)
                    self._add_result_and_save(cached)
                    if os.path.exists(path):
                        try:
                            shutil.move(path, os.path.join(processed_dir, filename))
                        except Exception as e:
                            logger.warning(f"移動失敗(キャッシュ済): {filename} - {e}")
                    tracker.update(1)
                    self._notify("file_done", filename=filename, tenant=tenant_folder, ok=True,
                                 ng_reason=cached.get("NG理由", "N/A"),
                                 cached=True, elapsed_sec=0.0)

                # 存在しなかったファイルの分カウント補正
                for _ in range(item.get("missing_count", 0)):
                    tracker.update(1)

                uncached = item["uncached"]
                if not uncached:
                    continue

                analysis_items = []
                for path, filename in uncached:
                    if not os.path.exists(path):
                        logger.warning(f"分析前ファイル消失: {filename}")
                        tracker.update(1)
                        continue
                    text = item["transcriptions"].get(path, "（文字起こし失敗）")
                    analysis_items.append((path, filename, tenant_folder, text))

                if not analysis_items:
                    continue

                self._notify("batch_phase",
                             tenant=tenant_folder, phase="llm",
                             batch_idx=item["batch_idx"], batch_total=total_batches,
                             count=len(analysis_items),
                             files=[f for _, f, _, _ in analysis_items],
                             whisper_sec=round(item["whisper_sec"], 1))
                tracker.set_description(
                    f"テナント: {tenant_folder} ({len(analysis_items)}件をLLM分析中)"
                )

                file_start_times: Dict[str, float] = {}
                # 「with ThreadPoolExecutor」だと __exit__ で shutdown(wait=True) が呼ばれ
                # in-flight LLM (最大10分) を待ってしまう。停止ボタン応答性のため明示管理に。
                executor = ThreadPoolExecutor(max_workers=self.max_workers)
                try:
                    def _submit_with_time(it):
                        _, fname, _, _ = it
                        file_start_times[fname] = time.time()
                        return executor.submit(self._analyze_single, it)
                    futures = {_submit_with_time(it): it for it in analysis_items}

                    for future in as_completed(futures):
                        if graceful_killer.should_stop:
                            interrupted = True
                            break
                        it = futures[future]
                        path, filename, _, _ = it
                        llm_elapsed = round(
                            time.time() - file_start_times.get(filename, time.time()), 1
                        )
                        try:
                            result = future.result()
                            if result:
                                results.append(result)
                                self._add_result_and_save(result)
                                if os.path.exists(path):
                                    try:
                                        shutil.move(
                                            path, os.path.join(processed_dir, filename)
                                        )
                                    except Exception as e:
                                        logger.warning(f"ファイル移動失敗: {e}")
                                self._notify("file_done", filename=filename,
                                             tenant=tenant_folder, ok=True,
                                             ng_reason=result.get("NG理由", "N/A"),
                                             cached=False, elapsed_sec=llm_elapsed)
                            else:
                                self._notify("file_done", filename=filename,
                                             tenant=tenant_folder, ok=False,
                                             cached=False, elapsed_sec=llm_elapsed)
                        except Exception as e:
                            logger.error(f"並列処理エラー {filename}: {e}")
                            self._notify("file_done", filename=filename,
                                         tenant=tenant_folder, ok=False, error=str(e),
                                         cached=False, elapsed_sec=llm_elapsed)
                        tracker.update(1)
                finally:
                    # in-flight は待たない (orphan されるが各 thread は cache に保存するので結果は次回拾える)
                    executor.shutdown(wait=False, cancel_futures=True)

                if interrupted:
                    break
        finally:
            # Producer の終了を待つ。stop 要求済みなら Whisper subprocess は kill されるので速く返る。
            producer_thread.join(timeout=10)

        if graceful_killer.should_stop:
            interrupted = True
        return results, interrupted

    def run_once(self) -> Tuple[pd.DataFrame, int, bool, Optional[str]]:
        path_settings = self.config.get("path_settings", {})
        tenant_folders = path_settings.get("tenant_folders", [])
        
        # run_onceごとにresultsをクリア（メモリ圧迫を防ぐ）。
        # CSVは各起動時にtimestamp付きの新規ファイルを作るので、この時点のクリアはI/Oに影響しない。
        self._reset_realtime_results()
        
        all_results = []
        was_interrupted = False

        total_files_to_process = 0
        all_tenant_files = {}
        for tenant_folder in tenant_folders:
            files = self._get_unprocessed_files(tenant_folder)
            if files:
                all_tenant_files[tenant_folder] = files
                total_files_to_process += len(files)

        if total_files_to_process == 0:
            logger.info("処理対象の新しいファイルはありません。")
            self._notify("finish", processed=0, total=0, interrupted=False, csv_path=None)
            return pd.DataFrame(), 0, False, None

        self._notify("start", total=total_files_to_process, tenants=list(all_tenant_files.keys()))

        tracker = ProgressTracker()
        tracker.start(total_files_to_process)

        try:
            for tenant_folder in tenant_folders:
                if graceful_killer.should_stop:
                    was_interrupted = True
                    break

                if tenant_folder in all_tenant_files:
                    self._notify("tenant_start", tenant=tenant_folder,
                                 file_count=len(all_tenant_files[tenant_folder]))
                    tenant_results, interrupted = self.process_tenant_folder(tenant_folder, tracker)
                    all_results.extend(tenant_results)
                    self._notify("tenant_done", tenant=tenant_folder,
                                 processed=len(tenant_results))
                    if interrupted:
                        was_interrupted = True
                        break
        finally:
            tracker.close()

        final_csv = self._finalize_realtime_csv()
        self._notify("finish", processed=len(all_results),
                     total=total_files_to_process,
                     interrupted=was_interrupted,
                     csv_path=final_csv)
        
        # 処理完了通知（ファイルがあった場合のみ）
        if total_files_to_process > 0 and not was_interrupted:
            msg = f"[音声分析完了]\n処理件数: {len(all_results)}件\n出力先: {final_csv}"
            self.notifier.send_message(msg)

        if not all_results:
            return pd.DataFrame(), 0, was_interrupted, final_csv
        return pd.DataFrame(all_results), len(all_results), was_interrupted, final_csv

    def run_continuous(self):
        logger.info("=" * 60)
        logger.info("連続監視モード開始")
        logger.info("=" * 60)
        iteration = 0
        while True:
            if graceful_killer.should_stop:
                break
            iteration += 1
            logger.info(f"\n【第{iteration}回チェック】 {datetime.now().strftime('%H:%M:%S')}")
            
            df, processed, interrupted, csv_path = self.run_once()

            if interrupted:
                logger.info("処理が中断されました。")
                break
            
            if processed > 0:
                logger.info(f"今回のチェックで {processed} 件の処理が完了しました。")
            else:
                logger.info("新しいファイルは見つかりませんでした。")
            
            if self.max_iterations > 0 and iteration >= self.max_iterations:
                logger.info("最大実行回数に達したため終了します。")
                break
            
            logger.info(f"次のチェックまで{self.check_interval}秒待機...")
            for _ in range(self.check_interval):
                if graceful_killer.should_stop:
                    break
                time.sleep(1)
        logger.info("連続監視モード終了")


def load_config() -> dict:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError("config.yaml が見つかりません。")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    graceful_killer.setup_handlers()
    try:
        config = load_config()
        logger.info("=" * 60)
        logger.info("音声分析プログラム（日時CSV・Chatwork通知版）")
        logger.info("=" * 60)
        analyzer = AudioAnalyzer(config)
        if analyzer.continuous_enabled:
            analyzer.run_continuous()
        else:
            df, total, interrupted, csv = analyzer.run_once()
            if not interrupted:
                logger.info("すべての処理が完了しました。")
    except Exception as e:
        logger.error(f"致命的なエラーが発生しました: {e}", exc_info=True)
        sys.exit(1)
    finally:
        graceful_killer.restore_handlers()
        logger.info("プログラムを終了します。")


if __name__ == "__main__":
    main()