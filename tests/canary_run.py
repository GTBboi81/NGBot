#!/usr/bin/env python3
"""カナリア試験: 本物のテナントフォルダに触れず、サンプル N件を隔離ワークスペースで処理する。

使い方:
    python tests/canary_run.py --tenant example1 --n 50
    python tests/canary_run.py --tenant example1 --n 50 --date 20260517

副作用:
- tests/canary_workspace/<tenant>/ にmp3をコピー（元ファイルはそのまま）
- 処理後、コピーは tests/canary_workspace/<tenant>/分析済み/ に移動（本番フォルダには影響なし）
- CSV: プロジェクトrootの {timestamp}_results.csv に通常通り出力後、tests/results/canary_*.csv にリネーム
- Chatwork通知は強制OFF
- .cache はプロジェクト共通（Whisper転写の再利用が効くため）
"""
from __future__ import annotations

import argparse
import copy
import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from main import load_config, AudioAnalyzer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="example1")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--date", default=None,
                        help="YYYYMMDD: ファイル名先頭の日付がこれのものに限定")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workspace",
                        default=str(PROJECT_ROOT / "tests" / "canary_workspace"))
    parser.add_argument("--max-workers", type=int, default=2,
                        help="Ollamaサーバの並列度を絞る（本番=4、軽試験=2推奨）")
    args = parser.parse_args()

    cfg = copy.deepcopy(load_config())
    src_base = Path(cfg["path_settings"]["base_path"])
    src_dir = src_base / args.tenant

    workspace = Path(args.workspace)
    canary_tenant_dir = workspace / args.tenant
    canary_tenant_dir.mkdir(parents=True, exist_ok=True)

    # 既存の workspace 内 mp3 と「分析済み」を念のため空に
    for old in canary_tenant_dir.glob("*.mp3"):
        old.unlink()
    proc = canary_tenant_dir / "分析済み"
    if proc.exists():
        for old in proc.glob("*"):
            try:
                old.unlink()
            except IsADirectoryError:
                pass

    # サンプリング
    rng = random.Random(args.seed)
    pool = []
    for p in src_dir.glob("*.mp3"):
        if not p.is_file():
            continue
        if args.date and not p.name.startswith(args.date + "_"):
            continue
        pool.append(p)
    if not pool:
        print(f"No mp3 matched in {src_dir} (date filter={args.date})")
        return
    sample = rng.sample(pool, min(args.n, len(pool)))
    print(f"Sampling {len(sample)} / {len(pool)} mp3 (date filter={args.date or 'any'})")

    for src in sample:
        shutil.copy2(src, canary_tenant_dir / src.name)
    print(f"Copied to {canary_tenant_dir}")

    # 設定上書き
    cfg["path_settings"]["base_path"] = str(workspace)
    cfg["path_settings"]["tenant_folders"] = [args.tenant]
    cfg["chatwork_settings"]["enable"] = False
    cfg["performance_settings"]["max_workers"] = args.max_workers

    model = cfg["ollama_settings"]["model_name"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n=== Canary run started @ {ts} ===")
    print(f"  model       = {model}")
    print(f"  tenant      = {args.tenant}")
    print(f"  n           = {len(sample)}")
    print(f"  max_workers = {args.max_workers}")
    print(f"  workspace   = {workspace}")
    print("---")

    analyzer = AudioAnalyzer(cfg)
    df, total, interrupted, csv_path = analyzer.run_once()

    print(f"\n=== Done ===")
    print(f"  total      = {total}")
    print(f"  interrupted= {interrupted}")
    print(f"  source CSV = {csv_path}")

    # CSV を tests/results/ に名前変えて保存
    if csv_path and Path(csv_path).exists():
        dst = PROJECT_ROOT / "tests" / "results" / f"canary_{model.replace(':','_')}_{ts}.csv"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(csv_path, dst)
        print(f"  saved as   = {dst}")

    # 簡易サマリ
    if df is not None and len(df) > 0:
        print(f"\nNG理由 分布 ({len(df)} 件):")
        if "NG理由" in df.columns:
            print(df["NG理由"].value_counts().to_string())
        if "利用回線" in df.columns:
            na = (df["利用回線"].fillna("N/A").astype(str) == "N/A").mean() * 100
            print(f"\n利用回線 N/A率: {na:.1f}%")


if __name__ == "__main__":
    main()
