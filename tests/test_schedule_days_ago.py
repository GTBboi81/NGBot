#!/usr/bin/env python3
"""gui_schedule.target_days_ago の解決ロジックを GUI を起動せず検証。

_scheduled_action 内のコードと同じ計算を再現する。
config.yaml の target_days_ago を変えて再実行すれば反映確認できる。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gui import config_io  # noqa: E402


def resolve_target_date(today: date | None = None) -> tuple[date, int, dict]:
    """gui/app.py の _scheduled_action と同じ解決ロジック。"""
    today = today or date.today()
    gui_sch = config_io.load().get("gui_schedule", {}) or {}
    try:
        days_ago = int(gui_sch.get("target_days_ago", 1))
        if days_ago < 0:
            days_ago = 1
    except (TypeError, ValueError):
        days_ago = 1
    return today - timedelta(days=days_ago), days_ago, gui_sch


def main() -> None:
    today = date.today()
    target, days_ago, gui_sch = resolve_target_date(today)
    print("config.yaml gui_schedule:")
    for k, v in gui_sch.items():
        print(f"  {k}: {v}")
    print()
    print(f"今日:           {today.isoformat()}")
    print(f"target_days_ago: {days_ago}")
    print(f"target_date:     {target.isoformat()}  ← スケジュール発火時に処理される対象日")

    # シミュレーション: 各 days_ago 値の場合の対象日
    print("\nシミュレーション (もし target_days_ago を変えたら):")
    for d in [0, 1, 2, 3, 7]:
        print(f"  days_ago={d} -> {today - timedelta(days=d)}")


if __name__ == "__main__":
    main()
