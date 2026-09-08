"""End-to-end 統合検証。GUI の Tk mainloop / runner と組み合わせた挙動を確認する。

検証項目:
  I1: スケジューラ発火 → Tk after() ディスパッチ → action が main thread で実行される
  I2: runner.start で擬似遅い AudioAnalyzer を回し、runner.request_stop で X秒以内に終了
  I3: GUI _scheduled_action から runner.start が確かに呼ばれる (mock化したrunner)
  I4: 起動直後の _restore_scheduler_if_running 経路 (state.json enabled=true)
"""
from __future__ import annotations
import sys
import time
import threading
import tkinter as tk
from datetime import datetime, timedelta

sys.path.insert(0, ".")


# ===== I1: Scheduler -> Tk after dispatch (本物の mainloop) =====
def i1_scheduler_to_tk():
    """実 GUI と同じく root.mainloop() を本スレッドで動かして、
    スケジューラスレッドから root.after() で main thread 経路をテスト。"""
    from gui.scheduler import JobScheduler

    root = tk.Tk()
    root.withdraw()
    fired = threading.Event()
    main_thread_id = threading.get_ident()
    fire_thread_id = []
    after_call_error = []
    sched_fired = threading.Event()

    def on_main():
        fire_thread_id.append(threading.get_ident())
        fired.set()
        root.quit()  # mainloop を抜ける

    def scheduled_action():
        sched_fired.set()
        try:
            root.after(0, on_main)
        except Exception as e:
            after_call_error.append(repr(e))
            # mainloop も抜ける
            try:
                root.after(0, root.quit)
            except Exception:
                pass

    sched = JobScheduler()
    sched._chunk_sec = 1.0
    sched.set_action(scheduled_action)
    sched.apply({"enabled": True, "daily_time": "00:00"})
    sched._next_run = datetime.now() + timedelta(seconds=2)

    # タイムアウト用 watchdog (10秒で root.quit)
    def watchdog():
        time.sleep(10)
        try:
            root.after(0, root.quit)
        except Exception:
            pass
    threading.Thread(target=watchdog, daemon=True).start()

    # 実 GUI と同じく mainloop を本スレッドで動かす
    root.mainloop()

    sched.shutdown()
    try:
        root.destroy()
    except tk.TclError:
        pass

    assert sched_fired.is_set(), "I1 FAIL: スケジューラ自体が発火しなかった"
    if after_call_error:
        raise AssertionError(
            f"I1 FAIL: root.after が別スレッドから動かない: {after_call_error[0]}"
            " ← これは本番GUIで silent に発火失敗する重大バグ"
        )
    assert fired.is_set(), "I1 FAIL: on_main が main thread で実行されなかった"
    assert fire_thread_id[0] == main_thread_id, \
        f"I1 FAIL: on_main が main thread でない (期待: {main_thread_id}, 実: {fire_thread_id})"
    print(f"  [I1 PASS] スケジューラスレッド→root.after()→main thread 経路 OK")


# ===== I2: runner.request_stop で擬似分析が即停止 =====
def i2_runner_stop_responsive():
    from gui.runner import AnalyzerRunner
    import main as ngbot_main

    # 元の AudioAnalyzer を退避
    original = ngbot_main.AudioAnalyzer

    class SlowAnalyzer:
        """run_once を 600秒走らせるが should_stop で即抜ける擬似版"""
        def __init__(self, config, progress_callback=None, target_date=None):
            self.progress_callback = progress_callback
            self.config = config

        def run_once(self):
            # 進捗イベントを発火しながら 600秒待つ
            if self.progress_callback:
                self.progress_callback({"event": "start", "total": 1000, "tenants": ["mock"]})
            for i in range(1200):  # 600秒 (0.5s × 1200)
                if ngbot_main.graceful_killer.should_stop:
                    if self.progress_callback:
                        self.progress_callback({"event": "finish", "processed": i,
                                                "total": 1000, "interrupted": True,
                                                "csv_path": None})
                    import pandas as pd
                    return pd.DataFrame(), i, True, None
                time.sleep(0.5)
            import pandas as pd
            return pd.DataFrame(), 1200, False, None

    ngbot_main.AudioAnalyzer = SlowAnalyzer
    try:
        runner = AnalyzerRunner()
        # 確実なクリーン状態にする
        ngbot_main.graceful_killer.kill_now = False
        ngbot_main.graceful_killer.shutdown_requested = False

        ok = runner.start()
        assert ok, "I2 FAIL: runner.start が False"
        time.sleep(2)  # SlowAnalyzer が稼働開始
        assert runner.is_running(), "I2 FAIL: runner が起動していない"

        t0 = time.time()
        runner.request_stop()
        # ポーリングで終了確認
        deadline = t0 + 10
        while runner.is_running():
            if time.time() > deadline:
                assert False, f"I2 FAIL: runner が10秒経っても停止しない"
            time.sleep(0.1)
        elapsed = time.time() - t0
        assert elapsed < 5, f"I2 FAIL: 停止に {elapsed:.2f}s かかった (目標 <5s)"
        print(f"  [I2 PASS] runner stop 応答 {elapsed*1000:.0f}ms")
    finally:
        ngbot_main.AudioAnalyzer = original
        ngbot_main.graceful_killer.kill_now = False
        ngbot_main.graceful_killer.shutdown_requested = False


# ===== I3: gui/app の _scheduled_action から runner.start が呼ばれる =====
def i3_scheduled_action_calls_runner():
    """直接 GUI app をインポートはせず、構造的に確認 (Tk窓が増えるのを避けるため)"""
    import inspect
    import gui.app as gapp
    src = inspect.getsource(gapp.NGBotApp._scheduled_action)
    # 必須要素
    assert "self.runner.start" in src, "I3 FAIL: runner.start 呼び出しがない"
    assert "self.runner.is_running" in src, "I3 FAIL: 多重起動チェックがない"
    assert "date.today() - timedelta(days=1)" in src, "I3 FAIL: 前日固定ロジックがない"
    assert "self._notify_cw" in src, "I3 FAIL: Chatwork通知がない"
    assert "logger.info" in src or "logger.warning" in src, "I3 FAIL: file logger 出力がない"
    print("  [I3 PASS] _scheduled_action 構造 (runner.start + 前日固定 + Chatwork通知)")


# ===== I4: 起動時の _restore_scheduler_if_running ロジック =====
def i4_restore_on_startup():
    """state.json に enabled=true があれば自動で apply されるか (構造的検証)"""
    import inspect
    import gui.app as gapp
    src = inspect.getsource(gapp.NGBotApp._restore_scheduler_if_running)
    assert "_scheduler_was_running" in src
    assert "on_schedule_start" in src
    assert "silent=True" in src
    # _load_state で _scheduler_was_running が enabled から設定される
    src2 = inspect.getsource(gapp.NGBotApp._load_state)
    assert "_scheduler_was_running" in src2 and "enabled" in src2
    print("  [I4 PASS] 起動時自動再開ロジック確認")


# ===== I5: GUI on_stop の即時UIフィードバック =====
def i5_on_stop_immediate_feedback():
    import inspect
    import gui.app as gapp
    src = inspect.getsource(gapp.NGBotApp.on_stop)
    assert "self.runner.request_stop" in src
    assert "self.btn_stop.configure" in src and 'state="disabled"' in src
    assert "停止処理中" in src and "lbl_eta" in src
    print("  [I5 PASS] on_stop で即時UI反映 (ボタン無効化 + 停止中表示)")


# ===== I6: Whisper Popen kill と AudioAnalyzer.process_tenant_folder の統合 =====
def i6_whisper_kill_in_pipeline():
    """Producer thread 内の Whisper が should_stop で kill される構造確認"""
    import inspect
    import main as m
    src = inspect.getsource(m.AudioAnalyzer.process_tenant_folder)
    assert "_producer" in src, "I6 FAIL: producer 関数がない"
    assert "graceful_killer.should_stop" in src, "I6 FAIL: should_stop チェックがない"
    src_w = inspect.getsource(m.WhisperGPUBatch.transcribe_batch)
    assert "Popen" in src_w and "process.kill()" in src_w
    assert "graceful_killer.should_stop" in src_w
    print("  [I6 PASS] Whisper Popen kill 構造 (Producer + WhisperGPUBatch)")


# ===== I7: ThreadPoolExecutor が wait=False で抜ける =====
def i7_executor_shutdown_no_wait():
    import inspect
    import main as m
    src = inspect.getsource(m.AudioAnalyzer.process_tenant_folder)
    # 'with ThreadPoolExecutor(' (関数呼び出し形式) が無く、明示的な finally + shutdown(wait=False)
    assert "shutdown(wait=False" in src, "I7 FAIL: shutdown(wait=False) がない"
    assert "with ThreadPoolExecutor(" not in src, \
        "I7 FAIL: with ThreadPoolExecutor(...) が残っている (in-flight LLM を待ってしまう)"
    print("  [I7 PASS] ThreadPoolExecutor 明示 shutdown(wait=False)")


if __name__ == "__main__":
    print("=== NGBot 統合検証 ===")
    tests = [
        ("I3 _scheduled_action 構造", i3_scheduled_action_calls_runner),
        ("I4 起動時自動再開", i4_restore_on_startup),
        ("I5 on_stop 即時UI", i5_on_stop_immediate_feedback),
        ("I6 Whisper kill 構造", i6_whisper_kill_in_pipeline),
        ("I7 Executor shutdown(wait=False)", i7_executor_shutdown_no_wait),
        ("I2 runner stop 応答", i2_runner_stop_responsive),
        ("I1 Scheduler→Tk→main thread", i1_scheduler_to_tk),
    ]
    passed, failed = 0, []
    for name, fn in tests:
        print(f"\n--- {name} ---")
        try:
            fn(); passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}"); failed.append(name)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"  ERROR: {type(e).__name__}: {e}"); failed.append(name)
    print(f"\n=== RESULT: {passed}/{len(tests)} passed ===")
    if failed: print(f"FAILED: {failed}"); sys.exit(1)
    sys.exit(0)
