"""NGBot GUI メインアプリケーション（単一画面レイアウト）。

スクショ準拠のレイアウト:
  ┌─────────────────────────────┬──────────────────────────────────┐
  │ スケジュール (1日1回)         │ 実行進捗（テナント別）             │
  │   実行時刻  HH:MM             │   [tenant1] ━━━━━━━━ 12/30 待機 │
  │   [スケジュール開始/停止]      │   [tenant2] ━━━━━     5/20 実行中│
  │   次回実行: ...               │   ...                            │
  │                              │                                  │
  │ 即時実行                      │ ログ                              │
  │   対象日 [____] [前日に戻す]    │   [16:45:16] [INFO] ...          │
  │   [即時実行] [停止]            │   ...                            │
  │                              │                                  │
  │ 対象テナント                   │                                  │
  │   ☑ A  ☑ B  ☑ C              │                                  │
  │   [すべて選択] [すべて解除]      │                                  │
  └─────────────────────────────┴──────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tkinter as tk
from datetime import datetime, date, timedelta
from tkinter import messagebox
from typing import Optional


# pythonw.exe 起動時は sys.stdout/sys.stderr が None になり、
# tqdm.write() 等が AttributeError: 'NoneType' object has no attribute 'write' で落ちる。
# main.py を import する前に安全なシンクへ差し替える。
class _NullStream:
    def write(self, *a, **kw): return 0
    def flush(self, *a, **kw): pass
    def isatty(self): return False
    def fileno(self): raise OSError("no fileno in pythonw")


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import PRIMARY, SUCCESS, DANGER, INFO, SECONDARY
    USE_TTKB = True
except ImportError:
    import tkinter.ttk as tb
    PRIMARY = SUCCESS = DANGER = INFO = SECONDARY = "TButton"
    USE_TTKB = False

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_THIS_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from gui import config_io  # noqa: E402
from gui.runner import AnalyzerRunner  # noqa: E402
from gui.scheduler import JobScheduler  # noqa: E402
from gui.ollama_manager import OllamaManager  # noqa: E402
from main import ChatworkNotifier  # noqa: E402  # main は LOG_PATH 定義より下で再 import 済み

LOG_PATH = os.path.join(_PARENT_DIR, "audio_analysis.log")
STATE_PATH = os.path.join(_PARENT_DIR, ".gui_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ngbot.gui")
# pythonw だと stderr が /dev/null なので basicConfig 経由ではログがディスクに残らない。
# main.py 側で設定済の TimedRotatingFileHandler を借りて、スケジュール発火/スキップ等を
# audio_analysis.log へ書けるようにする (今回のバグ調査で原因特定不能になった反省)。
import main as _ngbot_main  # noqa: E402
for _h in _ngbot_main.logger.handlers:
    if _h not in logger.handlers:  # 再 import 時の二重登録防止
        logger.addHandler(_h)
logger.setLevel(logging.INFO)


def _btn(parent, text, cmd, style=PRIMARY, width=None):
    """ttkbootstrap の有無に応じてボタンを生成するヘルパ。"""
    kwargs = {"text": text, "command": cmd}
    if width:
        kwargs["width"] = width
    if USE_TTKB:
        kwargs["bootstyle"] = style
    return tb.Button(parent, **kwargs)


# =====================================================================
# メインアプリ
# =====================================================================
class NGBotApp:
    def __init__(self):
        self.runner = AnalyzerRunner()
        self.scheduler = JobScheduler()
        self.scheduler.set_action(self._scheduled_action)
        self.ollama = OllamaManager()
        # Chatwork通知。スケジュール発火/スキップ/エラーをここからも飛ばす。
        # 設定読込失敗時は通知無効化（必須機能ではないため例外を握りつぶしてGUI起動を優先）。
        try:
            self._cw = ChatworkNotifier(config_io.load())
        except Exception as e:
            logger.warning(f"ChatworkNotifier初期化失敗: {e}")
            self._cw = None

        if USE_TTKB:
            self.root = tb.Window(themename="cosmo", title="NGBot 音声分析")
        else:
            self.root = tk.Tk()
            self.root.title("NGBot 音声分析")
        self.root.geometry("1200x720")

        # テナント別進捗管理
        self._tenant_widgets: dict[str, dict] = {}  # tenant -> {bar, lbl_count, lbl_state, lbl_now}
        self._tenant_progress: dict[str, dict] = {}  # tenant -> {processed, total}
        self._target_tenants_vars: dict[str, tk.BooleanVar] = {}
        self._run_start_time: Optional[datetime] = None  # 起動時刻 (バックアップ用)
        # 直近30件の file_done 時刻。ETA を sliding window で計算する
        # (起動直後のキャッシュヒットによる過大評価を抑える)
        self._recent_done_times: list[datetime] = []

        self._build_layout()
        self._load_state()
        self._start_ollama_async()
        self._poll_events()
        self._restore_scheduler_if_running()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------- レイアウト構築 -----------------------------------------------

    def _build_layout(self):
        # 全体: 左右2ペイン + 下部ステータスバー
        wrapper = tb.Frame(self.root)
        wrapper.pack(fill="both", expand=True)

        outer = tb.Frame(wrapper, padding=8)
        outer.pack(fill="both", expand=True)

        outer.columnconfigure(0, weight=1, uniform="col")
        outer.columnconfigure(1, weight=2, uniform="col")
        outer.rowconfigure(0, weight=1)

        left = tb.Frame(outer)
        right = tb.Frame(outer)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_left(left)
        self._build_right(right)

        # 下部 Ollama ステータスバー
        status_bar = tb.Frame(wrapper, padding=(8, 4))
        status_bar.pack(fill="x", side="bottom")
        self.lbl_ollama = tb.Label(status_bar, text="Ollama: 確認中...",
                                   foreground="gray", font=("Yu Gothic UI", 9))
        self.lbl_ollama.pack(side="left")

    def _build_left(self, parent):
        # スケジュール
        f_sch = tb.Labelframe(parent, text="スケジュール (1日1回)", padding=8)
        f_sch.pack(fill="x", pady=(0, 8))
        row = tb.Frame(f_sch); row.pack(anchor="w")
        tb.Label(row, text="実行時刻:").pack(side="left")
        self.var_hh = tk.StringVar(value="00")
        self.var_mm = tk.StringVar(value="00")
        tk.Spinbox(row, from_=0, to=23, width=3, textvariable=self.var_hh, format="%02.0f").pack(side="left", padx=2)
        tb.Label(row, text="時").pack(side="left")
        tk.Spinbox(row, from_=0, to=59, width=3, textvariable=self.var_mm, format="%02.0f").pack(side="left", padx=2)
        tb.Label(row, text="分").pack(side="left")

        # 対象日: 実行日の何日前を処理対象にするか。
        # 1=前日(デフォルト)、2=おととい。仮想環境用PC3 からの転送遅延で 0件取りこぼしが
        # 発生する場合は 2 以上に。値はスケジュール開始時に config.yaml に保存される。
        row_d = tb.Frame(f_sch); row_d.pack(anchor="w", pady=(4, 0))
        tb.Label(row_d, text="対象日:").pack(side="left")
        try:
            _gui_sch_init = config_io.load().get("gui_schedule", {}) or {}
            _days_ago_init = int(_gui_sch_init.get("target_days_ago", 1))
            if _days_ago_init < 0:
                _days_ago_init = 1
        except Exception:
            _days_ago_init = 1
        self.var_days_ago = tk.StringVar(value=str(_days_ago_init))
        tk.Spinbox(row_d, from_=0, to=30, width=3, textvariable=self.var_days_ago).pack(side="left", padx=2)
        tb.Label(row_d, text="日前を処理 (1=前日, 2=おととい)").pack(side="left")

        tb.Label(f_sch, text="翌日0時運用なら 00:00", foreground="gray").pack(anchor="w", pady=(2, 4))

        btns = tb.Frame(f_sch); btns.pack(anchor="w", pady=2)
        _btn(btns, "スケジュール開始", self.on_schedule_start, SUCCESS).pack(side="left", padx=2)
        _btn(btns, "スケジュール停止", self.on_schedule_stop, SECONDARY).pack(side="left", padx=2)

        self.lbl_next = tb.Label(f_sch, text="次回実行: -", foreground="navy")
        self.lbl_next.pack(anchor="w", pady=(4, 2))

        warn = "※ 開始後はウィンドウを閉じず、PCをスリープ/シャットダウンしないでください"
        tb.Label(f_sch, text=warn, foreground="firebrick", font=("Yu Gothic UI", 9)).pack(anchor="w")

        # 即時実行
        f_now = tb.Labelframe(parent, text="即時実行", padding=8)
        f_now.pack(fill="x", pady=(0, 8))
        row2 = tb.Frame(f_now); row2.pack(anchor="w")
        tb.Label(row2, text="対象日:").pack(side="left")
        self.var_target_date = tk.StringVar(value=date.today().isoformat())
        tb.Entry(row2, textvariable=self.var_target_date, width=14).pack(side="left", padx=4)
        _btn(row2, "前日に戻す", self.on_prev_day, SECONDARY).pack(side="left", padx=2)

        btns2 = tb.Frame(f_now); btns2.pack(anchor="w", pady=4)
        self.btn_run = _btn(btns2, "即時実行", self.on_run_now, SUCCESS, width=12)
        self.btn_run.pack(side="left", padx=2)
        self.btn_stop = _btn(btns2, "停止", self.on_stop, DANGER, width=10)
        self.btn_stop.pack(side="left", padx=2)
        self.btn_stop.configure(state="disabled")

        self.var_show_browser = tk.BooleanVar(value=False)
        tb.Checkbutton(f_now, text="ブラウザを表示する (HEADLESS=False)",
                       variable=self.var_show_browser).pack(anchor="w", pady=2)

        # 対象テナント
        f_tenants_outer = tb.Labelframe(parent, text="対象テナント", padding=8)
        f_tenants_outer.pack(fill="x")
        self.f_tenants = tb.Frame(f_tenants_outer)
        self.f_tenants.pack(fill="x", anchor="w")
        self._build_tenant_checkboxes()

        op = tb.Frame(f_tenants_outer)
        op.pack(anchor="w", pady=(6, 0))
        _btn(op, "すべて選択", self.on_tenants_select_all, SECONDARY).pack(side="left", padx=2)
        _btn(op, "すべて解除", self.on_tenants_clear_all, SECONDARY).pack(side="left", padx=2)
        _btn(op, "テナント編集...", self.on_edit_tenants, INFO).pack(side="left", padx=2)

    def _build_tenant_checkboxes(self):
        # 既存のチェックボックスを破棄
        for w in self.f_tenants.winfo_children():
            if isinstance(w, tb.Checkbutton):
                w.destroy()

        cfg = config_io.load()
        tenants = (cfg.get("path_settings") or {}).get("tenant_folders", []) or []

        self._target_tenants_vars = {}
        # 3列グリッド
        for i, t in enumerate(tenants):
            v = tk.BooleanVar(value=True)
            self._target_tenants_vars[t] = v
            cb = tb.Checkbutton(self.f_tenants, text=t, variable=v)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=2)

    def _build_right(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        # 実行進捗（テナント別）
        f_prog = tb.Labelframe(parent, text="実行進捗（テナント別）", padding=8)
        f_prog.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        # 全体サマリ (件数・速度・ETA)
        self.lbl_eta = tb.Label(f_prog, text="待機中", font=("Yu Gothic UI", 10, "bold"),
                                foreground="navy")
        self.lbl_eta.pack(anchor="w", pady=(0, 4))
        self.f_prog_inner = tb.Frame(f_prog)
        self.f_prog_inner.pack(fill="both", expand=True)
        self._build_tenant_progress_rows()

        # ログ
        f_log = tb.Labelframe(parent, text="ログ", padding=8)
        f_log.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.txt_log = tk.Text(f_log, wrap="none", font=("Consolas", 9), height=12)
        sb = tb.Scrollbar(f_log, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set, state="disabled")
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _build_tenant_progress_rows(self):
        # 既存行を破棄
        for w in self.f_prog_inner.winfo_children():
            w.destroy()
        self._tenant_widgets = {}

        cfg = config_io.load()
        tenants = (cfg.get("path_settings") or {}).get("tenant_folders", []) or []

        self.f_prog_inner.columnconfigure(1, weight=1)
        # ヘッダ
        tb.Label(self.f_prog_inner, text="テナント", font=("Yu Gothic UI", 9, "bold"))\
            .grid(row=0, column=0, sticky="w", padx=4)
        tb.Label(self.f_prog_inner, text="進捗", font=("Yu Gothic UI", 9, "bold"))\
            .grid(row=0, column=1, sticky="w", padx=4)
        tb.Label(self.f_prog_inner, text="件数", font=("Yu Gothic UI", 9, "bold"))\
            .grid(row=0, column=2, padx=4)
        tb.Label(self.f_prog_inner, text="状態", font=("Yu Gothic UI", 9, "bold"))\
            .grid(row=0, column=3, padx=4)
        tb.Label(self.f_prog_inner, text="現在の処理", font=("Yu Gothic UI", 9, "bold"))\
            .grid(row=0, column=4, sticky="w", padx=4)

        for i, t in enumerate(tenants, start=1):
            tb.Label(self.f_prog_inner, text=t, font=("Yu Gothic UI", 10, "bold"))\
                .grid(row=i, column=0, sticky="w", padx=4, pady=3)

            if USE_TTKB:
                bar = tb.Progressbar(self.f_prog_inner, mode="determinate",
                                     bootstyle="info-striped", maximum=1, value=0)
            else:
                bar = tb.Progressbar(self.f_prog_inner, mode="determinate", maximum=1, value=0)
            bar.grid(row=i, column=1, sticky="ew", padx=4, pady=3)

            lbl_count = tb.Label(self.f_prog_inner, text="--/--", width=10)
            lbl_count.grid(row=i, column=2, padx=4)
            lbl_state = tb.Label(self.f_prog_inner, text="待機", width=10)
            lbl_state.grid(row=i, column=3, padx=4)
            # 現在の処理ステージ表示。最新の batch_phase / file_done を反映
            lbl_now = tb.Label(self.f_prog_inner, text="-", width=44, anchor="w",
                               foreground="gray")
            lbl_now.grid(row=i, column=4, sticky="w", padx=4)

            self._tenant_widgets[t] = {"bar": bar, "lbl_count": lbl_count,
                                        "lbl_state": lbl_state, "lbl_now": lbl_now}
            self._tenant_progress[t] = {"processed": 0, "total": 0}

    # ------- 状態保存 ----------------------------------------------------

    def _load_state(self):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            sch = state.get("schedule") or {}
            if sch.get("hh") is not None: self.var_hh.set(f"{int(sch['hh']):02d}")
            if sch.get("mm") is not None: self.var_mm.set(f"{int(sch['mm']):02d}")
            self._scheduler_was_running = bool(sch.get("enabled", False))
            for t, on in (state.get("target_tenants") or {}).items():
                if t in self._target_tenants_vars:
                    self._target_tenants_vars[t].set(bool(on))
        except FileNotFoundError:
            self._scheduler_was_running = False
        except Exception as e:
            logger.warning(f"GUI状態の読み込み失敗: {e}")
            self._scheduler_was_running = False

    def _save_state(self):
        state = {
            "schedule": {
                "hh": int(self.var_hh.get() or 0),
                "mm": int(self.var_mm.get() or 0),
                "enabled": self.scheduler.is_enabled(),
            },
            "target_tenants": {t: v.get() for t, v in self._target_tenants_vars.items()},
        }
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"GUI状態の保存失敗: {e}")

    def _restore_scheduler_if_running(self):
        if getattr(self, "_scheduler_was_running", False):
            self._append_log("[INFO] 前回終了時にスケジューラが動作中だったため自動再開します")
            self.on_schedule_start(silent=True)

    # ------- Ollama -------------------------------------------------------

    def _start_ollama_async(self):
        """非ブロッキングで Ollama 起動。詳細ステータスを表示する。"""
        import threading
        def worker():
            self._append_log("[INFO] Ollama 起動確認中...")
            ok = self.ollama.start()
            status = self.ollama.status_summary()
            if not ok or not status["alive"]:
                self._append_log("[ERROR] Ollama 起動失敗。'ollama serve' を手動起動してください")
                self._set_ollama_status_label("Ollama: ✕ 未起動", "firebrick")
                return

            origin = "外部プロセスを再利用" if status["external"] else f"自動起動 (PID={status['pid']})"
            models = status["models"]
            self._append_log(f"[INFO] Ollama: 利用可能 host={status['host']} ({origin})")
            self._append_log(f"[INFO] Ollama 登録モデル: {', '.join(models) or '（なし）'}")

            # 既定モデル(elyza3) の存在確認
            cfg = config_io.load()
            wanted = (cfg.get("ollama_settings") or {}).get("model_name", "elyza3")
            ok_model = any(m.split(":")[0] == wanted or m == wanted for m in models)
            if not ok_model:
                self._append_log(f"[WARN] 既定モデル '{wanted}' が見つかりません。"
                                 f"`ollama pull {wanted}` を実行してください")
                self._set_ollama_status_label(
                    f"Ollama: △ {status['host']} (model '{wanted}' 未導入)", "darkorange")
            else:
                tag = "外部" if status["external"] else f"PID={status['pid']}"
                self._set_ollama_status_label(
                    f"Ollama: ● 稼働中 {status['host']} [{tag}] models={len(models)}", "darkgreen")
        threading.Thread(target=worker, daemon=True).start()

    def _set_ollama_status_label(self, text: str, color: str):
        """メイン画面の Ollama ステータス表示を更新（メインスレッドで）。"""
        def _apply():
            if hasattr(self, "lbl_ollama"):
                self.lbl_ollama.configure(text=text, foreground=color)
        try:
            self.root.after(0, _apply)
        except Exception:
            pass

    # ------- ボタンハンドラ ------------------------------------------------

    def on_schedule_start(self, silent=False):
        try:
            hh = int(self.var_hh.get())
            mm = int(self.var_mm.get())
            assert 0 <= hh < 24 and 0 <= mm < 60
        except (ValueError, AssertionError):
            messagebox.showerror("NGBot", "実行時刻が不正です (0-23時 / 0-59分)")
            return
        # daily 固定運用なので mode/interval_minutes/cron_expr は書かない (旧APScheduler名残)
        try:
            days_ago = int(self.var_days_ago.get())
            if days_ago < 0:
                days_ago = 1
        except (ValueError, AttributeError):
            days_ago = 1
        sch = {"enabled": True, "daily_time": f"{hh:02d}:{mm:02d}", "target_days_ago": days_ago}
        config_io.update(["gui_schedule"], sch)
        msg = self.scheduler.apply(sch)
        nxt = self.scheduler.next_run_time()
        self.lbl_next.configure(text=f"次回実行: {nxt or '-'}")
        self._append_log(f"[INFO] スケジュール開始 実行時刻={hh:02d}:{mm:02d} "
                         f"tenants={self._selected_tenants()}")
        if nxt:
            try:
                wait_sec = (datetime.strptime(nxt, "%Y-%m-%d %H:%M:%S") - datetime.now()).total_seconds()
                self._append_log(f"[INFO] 次回実行まで {int(wait_sec)} 秒待機 ({nxt})")
            except Exception:
                pass
        self._notify_cw(f"スケジュール開始 {hh:02d}:{mm:02d} 次回={nxt or '-'}")
        if not silent:
            messagebox.showinfo("NGBot", f"スケジュールを開始しました\n{msg}")
        self._save_state()

    def on_schedule_stop(self):
        self.scheduler.apply({"enabled": False})
        config_io.update(["gui_schedule", "enabled"], False)
        self.lbl_next.configure(text="次回実行: -")
        self._append_log("[INFO] スケジュール停止")
        self._notify_cw("スケジュール停止")
        self._save_state()

    def on_prev_day(self):
        try:
            d = datetime.strptime(self.var_target_date.get(), "%Y-%m-%d").date()
        except ValueError:
            d = date.today()
        self.var_target_date.set((d - timedelta(days=1)).isoformat())

    def on_run_now(self):
        if self.runner.is_running():
            messagebox.showinfo("NGBot", "既に実行中です")
            return
        sel = self._selected_tenants()
        if not sel:
            messagebox.showwarning("NGBot", "対象テナントを1つ以上選択してください")
            return

        target = self._validated_target_date()
        if target is False:  # 入力あり・不正
            return

        # 進捗UIをリセット
        for t in self._tenant_widgets:
            self._reset_tenant_row(t)

        ok = self.runner.start(tenant_filter=sel, target_date=target)
        if not ok:
            messagebox.showinfo("NGBot", "起動できませんでした")
            return
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        date_msg = f"date={target}" if target else "全件"
        self._append_log(f"[INFO] 即時実行を開始 tenants={sel} {date_msg}")

    def _validated_target_date(self):
        """対象日入力を検証。空ならNone(全件)、有効ならYYYY-MM-DD文字列、
        不正ならエラー表示してFalseを返す。"""
        s = (self.var_target_date.get() or "").strip()
        if not s:
            return None
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
            return d.isoformat()
        except ValueError:
            messagebox.showerror("NGBot", f"対象日の形式が不正です: {s!r}\nYYYY-MM-DD で入力してください")
            return False

    def on_stop(self):
        self.runner.request_stop()
        # 即時UIフィードバック: ボタン無効化 + lbl_eta に「停止中」表示
        # Whisper subprocess は <1秒で kill、in-flight LLM は orphan (最大10分背景で完走)
        self.btn_stop.configure(state="disabled")
        if hasattr(self, "lbl_eta"):
            self.lbl_eta.configure(
                text="停止処理中... (Whisper即停止、LLM最大10分は裏で完走)",
                foreground="firebrick",
            )
        self._append_log("[INFO] 停止要求を送信")

    def on_tenants_select_all(self):
        for v in self._target_tenants_vars.values():
            v.set(True)

    def on_tenants_clear_all(self):
        for v in self._target_tenants_vars.values():
            v.set(False)

    def on_edit_tenants(self):
        """簡易テナント編集ダイアログ（カンマ区切り）。"""
        from tkinter import simpledialog
        cfg = config_io.load()
        cur = (cfg.get("path_settings") or {}).get("tenant_folders", [])
        s = simpledialog.askstring("テナント編集",
                                   "テナント名をカンマ区切りで入力",
                                   initialvalue=", ".join(cur), parent=self.root)
        if s is None:
            return
        new_list = [t.strip() for t in s.split(",") if t.strip()]
        cfg.setdefault("path_settings", {})["tenant_folders"] = new_list
        config_io.save(cfg)
        self._append_log(f"[INFO] テナント更新: {new_list}")
        self._build_tenant_checkboxes()
        self._build_tenant_progress_rows()

    def _selected_tenants(self) -> list[str]:
        return [t for t, v in self._target_tenants_vars.items() if v.get()]

    # ------- イベント処理 --------------------------------------------------

    def _poll_events(self):
        for ev in self.runner.drain_events():
            self._handle(ev)
        nxt = self.scheduler.next_run_time()
        if nxt:
            self.lbl_next.configure(text=f"次回実行: {nxt}")
        self.root.after(500, self._poll_events)

    def _reset_tenant_row(self, tenant: str):
        w = self._tenant_widgets.get(tenant)
        if not w:
            return
        w["bar"].configure(maximum=1, value=0)
        w["lbl_count"].configure(text="--/--")
        w["lbl_state"].configure(text="待機")
        if "lbl_now" in w:
            w["lbl_now"].configure(text="-", foreground="gray")
        self._tenant_progress[tenant] = {"processed": 0, "total": 0}

    def _set_tenant_state(self, tenant: str, state: str, color: str = "black"):
        w = self._tenant_widgets.get(tenant)
        if not w:
            return
        w["lbl_state"].configure(text=state, foreground=color)

    def _set_tenant_now(self, tenant: str, text: str, color: str = "black"):
        w = self._tenant_widgets.get(tenant)
        if not w or "lbl_now" not in w:
            return
        w["lbl_now"].configure(text=text[:60], foreground=color)

    def _update_tenant_count(self, tenant: str):
        w = self._tenant_widgets.get(tenant)
        if not w:
            return
        p = self._tenant_progress.get(tenant, {"processed": 0, "total": 0})
        w["bar"].configure(maximum=max(1, p["total"]), value=p["processed"])
        w["lbl_count"].configure(text=f"{p['processed']}/{p['total']}")

    def _update_global_progress(self):
        """全テナント合計の進捗と ETA を計算して lbl_eta に表示。

        rate は直近 30件の file_done を sliding window で算出する。
        起動直後のキャッシュヒット連発による ETA 過小評価を防ぐ。
        """
        total = sum(p.get("total", 0) for p in self._tenant_progress.values())
        done = sum(p.get("processed", 0) for p in self._tenant_progress.values())
        if total == 0:
            return
        # rate 計算: 直近30件の時刻スパンから算出。サンプル不足時は全体平均で代替。
        win = self._recent_done_times[-30:]
        if len(win) >= 2:
            span = (win[-1] - win[0]).total_seconds()
            rate = (len(win) - 1) / span if span > 0 else 0
        elif self._run_start_time and done > 0:
            elapsed = max(1.0, (datetime.now() - self._run_start_time).total_seconds())
            rate = done / elapsed
        else:
            rate = 0
        remaining = max(0, total - done)
        eta_sec = remaining / rate if rate > 0 else 0
        rate_min = rate * 60
        eta_min = int(eta_sec / 60)
        eta_hh, eta_mm = divmod(eta_min, 60)
        msg = f"{done}/{total} 件 | {rate_min:.1f}件/分 | 残り約 {eta_hh}h{eta_mm:02d}m"
        if hasattr(self, "lbl_eta"):
            self.lbl_eta.configure(text=msg)

    def _handle(self, ev: dict):
        e = ev.get("event")
        if e == "start":
            self._run_start_time = datetime.now()
            self._append_log(f"[INFO] 処理開始: 全{ev.get('total', 0)}件")
        elif e == "tenant_start":
            t = ev.get("tenant", "")
            self._tenant_progress[t] = {"processed": 0, "total": ev.get("file_count", 0)}
            self._update_tenant_count(t)
            self._set_tenant_state(t, "実行中", "blue")
            self._set_tenant_now(t, f"待機中... {ev.get('file_count')}件", "gray")
            self._append_log(f"[INFO] テナント開始: {t} ({ev.get('file_count')}件)")
        elif e == "batch_phase":
            t = ev.get("tenant", "")
            phase = ev.get("phase", "?")
            idx = ev.get("batch_idx", 0)
            tot = ev.get("batch_total", 0)
            cnt = ev.get("count", 0)
            if phase == "whisper":
                cache = ev.get("cache_hits", 0)
                txt = f"Whisper {idx}/{tot}: {cnt}件" + (f" (キャッシュ{cache})" if cache else "")
                color = "darkblue"
            elif phase == "llm":
                whs = ev.get("whisper_sec", 0)
                txt = f"LLM分析 {idx}/{tot}: {cnt}件 (Whisper {whs}s)"
                color = "darkmagenta"
            else:
                txt = f"{phase} {idx}/{tot}"
                color = "black"
            self._set_tenant_now(t, txt, color)
        elif e == "file_done":
            t = ev.get("tenant", "")
            if t in self._tenant_progress:
                self._tenant_progress[t]["processed"] += 1
                self._update_tenant_count(t)
            # ETA計算用にタイムスタンプ蓄積 (キャッシュヒットは rate 過大化するので除外)
            if not ev.get("cached", False):
                self._recent_done_times.append(datetime.now())
                if len(self._recent_done_times) > 60:
                    self._recent_done_times = self._recent_done_times[-60:]
            # NG理由が留守以外で出てきたら一行ログに残す。
            ng = ev.get("ng_reason", "N/A")
            cached = ev.get("cached", False)
            elapsed = ev.get("elapsed_sec")
            if not ev.get("ok", True):
                err = ev.get("error", "")
                self._append_log(f"[NG-ERR] {ev.get('filename','')} ({t}) {err}")
            elif cached:
                # cache hit はノイズになるので log は省略
                pass
            elif ng and ng != "N/A" and ng != "留守":
                self._append_log(f"[NG] {ev.get('filename','')} ({t}) {ng} [{elapsed}s]")
            self._update_global_progress()
        elif e == "tenant_done":
            t = ev.get("tenant", "")
            self._set_tenant_state(t, "完了", "green")
            self._set_tenant_now(t, f"完了 {ev.get('processed')}件", "darkgreen")
            self._append_log(f"[INFO] テナント完了: {t} → {ev.get('processed')}件")
        elif e == "finish":
            interrupted = ev.get("interrupted")
            self._append_log(f"[INFO] 全処理終了: {ev.get('processed')}件 中断={interrupted}")
            self.btn_run.configure(state="normal")
            self.btn_stop.configure(state="disabled")
        elif e == "runner_error":
            self._append_log(f"[ERROR] {ev.get('type')}: {ev.get('message')}")
            self.btn_run.configure(state="normal")
            self.btn_stop.configure(state="disabled")
        elif e == "rejected":
            self._append_log(f"[WARN] 要求拒否: {ev.get('reason')}")
        elif e == "stop_requested":
            self._append_log("[INFO] 停止要求受信")
        elif e == "runner_starting":
            self._append_log("[INFO] Runner起動")
        elif e == "runner_finished":
            pass

    def _append_log(self, line: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"{ts} {line}\n")
        self.txt_log.see("end")
        if int(self.txt_log.index("end-1c").split(".")[0]) > 1500:
            self.txt_log.delete("1.0", "200.0")
        self.txt_log.configure(state="disabled")

    def _notify_cw(self, message: str):
        """Chatwork通知。ネットワーク不調で GUI を巻き込まないよう例外は握りつぶす。"""
        if not self._cw:
            return
        try:
            self._cw.send_message(f"[NGBot] {message}")
        except Exception as e:
            logger.warning(f"chatwork通知失敗: {e}")

    # ------- スケジュール発火 ---------------------------------------------

    def _scheduled_action(self):
        def _on_main():
            # logger.info も使ってファイルログ(audio_analysis.log)にも残す。
            # GUI Text widget だけだと再起動で消える=今回のバグ調査が困難になったため。
            logger.info("スケジュール発火")
            if self.runner.is_running():
                msg = "スケジュール発火スキップ: 既に実行中 (前回ジョブがハング中の可能性)"
                self._append_log(f"[WARN] {msg}")
                logger.warning(msg)
                self._notify_cw(msg)
                return
            sel = self._selected_tenants()
            if not sel:
                msg = "スケジュール発火スキップ: 対象テナントが空"
                self._append_log(f"[WARN] {msg}")
                logger.warning(msg)
                self._notify_cw(msg)
                return
            # スケジュール発火時は GUI 入力を無視し、config の target_days_ago に従う。
            # target_days_ago=1 (デフォルト)で「実行日の前日」、2 で「おととい」を対象。
            # 仮想環境用PC3 からの転送遅延で 0件取りこぼしが発生した場合は 2 以上を推奨。
            try:
                gui_sch = config_io.load().get("gui_schedule", {}) or {}
                days_ago = int(gui_sch.get("target_days_ago", 1))
                if days_ago < 0:
                    days_ago = 1
            except Exception as e:
                logger.warning(f"target_days_ago 読み込み失敗、デフォルト1を使用: {e}")
                days_ago = 1
            target = (date.today() - timedelta(days=days_ago)).isoformat()
            self._append_log(f"[INFO] スケジュール発火: 即時実行を開始 tenants={sel} date={target}")
            logger.info(f"スケジュール発火: 即時実行を開始 tenants={sel} date={target}")
            self._notify_cw(f"スケジュール発火 対象日={target} tenants={sel}")
            for t in self._tenant_widgets:
                self._reset_tenant_row(t)
            self.runner.start(tenant_filter=sel, target_date=target)
            self.btn_run.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        try:
            self.root.after(0, _on_main)
        except Exception as e:
            logger.warning(f"scheduled_action ディスパッチ失敗: {e}")

    # ------- 終了処理 ----------------------------------------------------

    def _on_close(self):
        if self.runner.is_running():
            if not messagebox.askyesno("NGBot", "実行中です。停止して終了しますか？"):
                return
            self.runner.request_stop()
        self._save_state()
        self.scheduler.shutdown()
        self.ollama.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    NGBotApp().run()


if __name__ == "__main__":
    main()
