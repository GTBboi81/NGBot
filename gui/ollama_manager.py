"""Ollama サーバの自動起動・停止。

GUI起動時に `ollama serve` をバックグラウンドで立ち上げ、
GUI終了時に自動停止する。既にOllamaが起動中ならそれを再利用。
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from typing import Optional

import urllib.request
import urllib.error


logger = logging.getLogger(__name__)


OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")


def _is_running() -> bool:
    """Ollama API に到達できるか確認。"""
    return _list_models() is not None


def _list_models() -> Optional[list]:
    """Ollama /api/tags を叩いてモデル一覧を取得。失敗時 None。"""
    import json as _json
    host = OLLAMA_HOST
    if "://" not in host:
        host = f"http://{host}"
    try:
        req = urllib.request.Request(f"{host}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status != 200:
                return None
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
            return [m.get("name") for m in (data.get("models") or [])]
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
        return None


class OllamaManager:
    def __init__(self, exe_path: Optional[str] = None):
        resolved = exe_path or shutil.which("ollama")
        if not resolved:
            logger.warning(
                "ollama 実行ファイルが PATH に見つかりません。"
                "Ollamaをインストールするか、環境変数PATHに追加してください。"
            )
            resolved = "ollama"  # 後段でFileNotFoundErrorで明示的に失敗させる
        self.exe_path = resolved
        self._proc: Optional[subprocess.Popen] = None
        self._was_running_externally = False
        logger.info(f"OllamaManager: exe={self.exe_path}, host={OLLAMA_HOST}")

    def start(self, timeout_sec: int = 30) -> bool:
        """Ollama を起動。既に起動中なら何もせず再利用。

        Returns: True なら使用可能（自前起動 or 外部起動の再利用）
        """
        if _is_running():
            self._was_running_externally = True
            logger.info("Ollama は既に起動中のため再利用します")
            return True

        try:
            startupinfo = subprocess.STARTUPINFO() if os.name == "nt" else None
            if os.name == "nt":
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            creationflags = 0
            if os.name == "nt":
                # コンソール窓を出さない + 親プロセスから独立させて kill しやすく
                creationflags = subprocess.CREATE_NO_WINDOW

            logger.info(f"Ollama 起動中: {self.exe_path} serve")
            self._proc = subprocess.Popen(
                [self.exe_path, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            logger.error(f"Ollama 実行ファイルが見つかりません: {self.exe_path}")
            return False
        except Exception as e:
            logger.error(f"Ollama 起動失敗: {e}")
            return False

        # API応答を待つ
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if _is_running():
                logger.info("Ollama API 応答確認: OK")
                return True
            if self._proc.poll() is not None:
                logger.error(f"Ollamaプロセスが即時終了 (returncode={self._proc.returncode})")
                self._proc = None
                return False
            time.sleep(0.5)

        logger.error(f"Ollama 起動タイムアウト ({timeout_sec}秒)")
        self.stop()
        return False

    def stop(self):
        """自前起動した Ollama のみ停止。外部起動分は触らない。"""
        if self._was_running_externally:
            logger.info("Ollama は外部起動なので停止しません")
            return
        if self._proc is None:
            return
        try:
            logger.info("Ollama 停止中")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Ollama terminate タイムアウト → kill")
                self._proc.kill()
                self._proc.wait(timeout=2)
        except Exception as e:
            logger.warning(f"Ollama 停止時の例外: {e}")
        finally:
            self._proc = None

    def is_alive(self) -> bool:
        return _is_running()

    def status_summary(self) -> dict:
        """GUI表示用の詳細ステータス。"""
        models = _list_models()
        return {
            "alive": models is not None,
            "host": OLLAMA_HOST,
            "models": models or [],
            "pid": (self._proc.pid if self._proc and self._proc.poll() is None else None),
            "external": self._was_running_externally,
        }
