"""シンプルな日次スケジューラ。

専用 daemon スレッドで「次の HH:MM まで 60秒チャンクで寝る → action 実行」を繰り返す。
- shutdown 応答・apply 反映は最大 60秒遅延 (許容)
- PCスリープ復帰時は次の chunk wake で即発火
- action 内例外・_loop 自体の例外いずれもスレッドを殺さない
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional


logger = logging.getLogger(__name__)


class JobScheduler:
    JOB_ID = "ngbot_main_job"

    def __init__(self):
        self._action: Optional[Callable[[], None]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._next_run: Optional[datetime] = None
        self._enabled = False
        # チャンクスリープの長さ (秒)。本番60s固定。テストで1sに下げる用途のみで変更する。
        self._chunk_sec = 60.0

    def set_action(self, action: Callable[[], None]):
        self._action = action

    def apply(self, cfg: dict) -> str:
        if not self._action:
            return "アクション未設定"
        if not cfg or not cfg.get("enabled"):
            self.shutdown()
            return "スケジュール無効"
        try:
            hh, mm = map(int, str(cfg.get("daily_time", "00:00")).split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception as e:
            return f"設定エラー: {e}"

        self._next_run = self._compute_next(datetime.now(), hh, mm)
        self._enabled = True

        if not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="ngbot-scheduler", daemon=True
            )
            self._thread.start()
        return f"次回実行: {self.next_run_time()}"

    def shutdown(self):
        self._stop.set()
        t = self._thread
        self._enabled = False
        self._next_run = None
        if t and t.is_alive():
            t.join(timeout=2)
        self._thread = None

    def next_run_time(self) -> Optional[str]:
        return self._next_run.strftime("%Y-%m-%d %H:%M:%S") if self._next_run else None

    def is_enabled(self) -> bool:
        return self._enabled and self._thread is not None and self._thread.is_alive()

    def _loop(self):
        logger.info("scheduler thread started")
        try:
            while not self._stop.is_set():
                try:
                    target = self._next_run
                    if target is None:
                        # 不在 (shutdown レース等) → 1秒待って再チェック
                        if self._stop.wait(1):
                            return
                        continue
                    remaining = (target - datetime.now()).total_seconds()
                    if remaining > 0:
                        # チャンクスリープ。stop で早期復帰、PCスリープからの wake もここで catchup
                        if self._stop.wait(timeout=min(self._chunk_sec, remaining)):
                            return
                        continue
                    # 発火
                    logger.info(f"スケジュール発火: target={target.strftime('%Y-%m-%d %H:%M:%S')}")
                    try:
                        if self._action:
                            self._action()
                    except Exception:
                        logger.exception("schedule action raised; continuing")
                    # 次回時刻: 発火 target の翌日。複数日 PCスリープで取り残されてたら最新に集約
                    self._next_run = target + timedelta(days=1)
                    now = datetime.now()
                    while self._next_run <= now:
                        self._next_run += timedelta(days=1)
                except Exception:
                    # _loop 内部の予期せぬ例外でスレッド全体を死なせない。UI が「次回実行: ...」と
                    # 嘘をつくサイレント死を防ぐ (レビュー指摘 B#7)。
                    logger.exception("scheduler loop unexpected error; backoff 30s")
                    if self._stop.wait(30):
                        return
        finally:
            logger.info("scheduler thread stopped")

    @staticmethod
    def _compute_next(now: datetime, hh: int, mm: int) -> datetime:
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return t if t > now else t + timedelta(days=1)
