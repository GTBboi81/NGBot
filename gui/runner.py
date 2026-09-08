"""AudioAnalyzerをGUIスレッドから安全に起動・進捗ブリッジするRunner。

設計:
- GUIメインスレッドはTkinterのため、Analyzerは別スレッドで実行する。
- AudioAnalyzer.progress_callback で受け取る dict を queue.Queue に積む。
- GUI側は after() で queue を定期ポーリングし、UIを更新する。
- 多重起動防止: is_running() で実行中なら新規要求を拒否。
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from typing import Callable, Optional

# 親ディレクトリ(main.py のある場所)をimport pathに追加
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_THIS_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import main as ngbot_main  # noqa: E402  AudioAnalyzer/load_config/graceful_killer


logger = logging.getLogger(__name__)


class AnalyzerRunner:
    """AudioAnalyzer を別スレッドで実行する管理オブジェクト。"""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._event_queue: "queue.Queue[dict]" = queue.Queue()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    # ------- public API ------------------------------------------------------

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, on_event: Optional[Callable[[dict], None]] = None,
              tenant_filter: Optional[list] = None,
              target_date: Optional[str] = None) -> bool:
        """非同期で AudioAnalyzer.run_once() を実行。

        既に実行中なら False を返す。on_event が指定された場合、
        progress_callback の payload をその関数にも転送する。
        tenant_filter が指定されればconfig.yamlを書き換えずにメモリ上のconfigを差し替える。
        target_date が指定されればその日付のファイルのみ処理（YYYY-MM-DD推奨）。
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._event_queue.put({"event": "rejected", "reason": "already_running"})
                return False
            self._stop_event.clear()
            ngbot_main.graceful_killer.kill_now = False
            ngbot_main.graceful_killer.shutdown_requested = False
            self._thread = threading.Thread(
                target=self._run_safely,
                args=(on_event, tenant_filter, target_date),
                daemon=True,
            )
            self._thread.start()
            return True

    def request_stop(self):
        """GracefulKiller経由で停止要求を出す。実行中スレッドはチェックポイントで終了。"""
        self._stop_event.set()
        ngbot_main.graceful_killer.kill_now = True
        ngbot_main.graceful_killer.shutdown_requested = True
        self._event_queue.put({"event": "stop_requested"})

    def drain_events(self) -> list[dict]:
        """GUIスレッドから定期的に呼び、たまったイベントを取り出す。"""
        events = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    # ------- internal --------------------------------------------------------

    def _push_event(self, payload: dict, on_event: Optional[Callable[[dict], None]]):
        self._event_queue.put(payload)
        if on_event is not None:
            try:
                on_event(payload)
            except Exception as e:  # コールバックの不具合でRunnerを止めない
                logger.warning(f"on_event例外: {e}")

    def _run_safely(self, on_event: Optional[Callable[[dict], None]],
                    tenant_filter: Optional[list] = None,
                    target_date: Optional[str] = None):
        try:
            config = ngbot_main.load_config()
            # GUI管理下では continuous_mode は常にOFF扱い
            if "continuous_mode" in config:
                config["continuous_mode"]["enable"] = False
            # メモリ上だけテナントを絞り込み（config.yaml は触らない）
            if tenant_filter is not None:
                config.setdefault("path_settings", {})
                config["path_settings"]["tenant_folders"] = list(tenant_filter)

            analyzer = ngbot_main.AudioAnalyzer(
                config,
                progress_callback=lambda ev: self._push_event(ev, on_event),
                target_date=target_date,
            )
            self._push_event({"event": "runner_starting"}, on_event)
            # DataFrameは使わないので破棄してメモリ節約
            _df, total, interrupted, csv_path = analyzer.run_once()
            del _df
            self._push_event({
                "event": "runner_finished",
                "processed": int(total),
                "interrupted": bool(interrupted),
                "csv_path": csv_path,
            }, on_event)
        except FileNotFoundError as e:
            self._push_event({"event": "runner_error", "type": "FileNotFound", "message": str(e)}, on_event)
        except Exception as e:
            logger.exception("Runner unhandled exception")
            self._push_event({"event": "runner_error", "type": e.__class__.__name__, "message": str(e)}, on_event)
        finally:
            with self._lock:
                self._thread = None
