#!/usr/bin/env python3
"""tests/results/*.json を集約して比較レポートを作る

使い方:
    python tests/compare_results.py
    python tests/compare_results.py --top 5            # 直近の上位5件のみ
    python tests/compare_results.py --weights default  # 採用判定スコアを出す

各 result.json は run_eval.py が出力した形式。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "tests" / "results"

# Task #7 の決定マトリクス重み (合計1.0)
DEFAULT_WEIGHTS = {
    "exact_match_rate": 0.30,
    "ng_reason_accuracy": 0.25,
    "line_na_compliance": 0.20,
    "json_success_rate": 0.10,
    "speed_score": 0.10,  # ベースライン比の逆数を正規化
    "vram_score": 0.05,   # 現状は手入力。スクリプトでは1.0固定。
}


def load_results() -> list[dict]:
    files = sorted(RESULTS_DIR.glob("*.json"))
    out = []
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["_file"] = p.name
            out.append(data)
        except Exception as e:
            print(f"[warn] failed to read {p.name}: {e}")
    return out


def derive_metrics(r: dict) -> dict:
    m = r.get("metrics", {})
    n = m.get("n", 0)
    # NG理由精度: 混同行列から対角線
    confusion = m.get("ng_reason_confusion", {})
    correct = sum(confusion.get(k, {}).get(k, 0) for k in confusion)
    total = sum(sum(v.values()) for v in confusion.values())
    ng_acc = correct / total if total else 0.0
    return {
        "label": r.get("label", r.get("model", "?")),
        "n": n,
        "exact": m.get("exact_match_rate", 0.0),
        "ng_reason_acc": ng_acc,
        "line_na_rate": (m.get("line_na_compliance", {}) or {}).get("rate") or 0.0,
        "json_success": 1.0 - (m.get("parse_failures", 0) / max(n, 1)),
        "p50_s": (m.get("latency_sec", {}) or {}).get("p50", 0.0),
        "p95_s": (m.get("latency_sec", {}) or {}).get("p95", 0.0),
        "mean_s": (m.get("latency_sec", {}) or {}).get("mean", 0.0),
        "stage1_rusu": m.get("stage1_rusu_hits", 0),
    }


def score(rows: list[dict], baseline_p95: float, weights: dict) -> list[dict]:
    for r in rows:
        speed = baseline_p95 / r["p95_s"] if r["p95_s"] else 0.0
        speed = max(0.0, min(1.5, speed))  # クリップ
        r["speed_score"] = speed
        r["vram_score"] = 1.0
        r["total"] = (
            weights["exact_match_rate"] * r["exact"]
            + weights["ng_reason_accuracy"] * r["ng_reason_acc"]
            + weights["line_na_compliance"] * r["line_na_rate"]
            + weights["json_success_rate"] * r["json_success"]
            + weights["speed_score"] * min(r["speed_score"], 1.0)
            + weights["vram_score"] * r["vram_score"]
        )
    return rows


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no results)")
        return
    headers = ["Label", "n", "Exact", "NG理由", "回線N/A", "JSON OK", "p50", "p95", "Stage1留守", "Score"]
    print(f"{'Label':<28} {'n':>3} {'Exact':>6} {'NG理由':>7} {'回線N/A':>7} {'JSON':>6} {'p50':>6} {'p95':>6} {'留守':>4} {'Score':>6}")
    print("-" * 100)
    for r in rows:
        print(f"{r['label'][:27]:<28} {r['n']:>3} "
              f"{r['exact']*100:>5.1f}% {r['ng_reason_acc']*100:>6.1f}% "
              f"{r['line_na_rate']*100:>6.1f}% {r['json_success']*100:>5.1f}% "
              f"{r['p50_s']:>5.2f}s {r['p95_s']:>5.2f}s "
              f"{r['stage1_rusu']:>4} {r.get('total', 0)*100:>5.1f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=0, help="latest N results only (0=all)")
    parser.add_argument("--weights", choices=["default"], default="default")
    parser.add_argument("--baseline-label", default=None,
                        help="label substring to mark as baseline for speed scoring")
    args = parser.parse_args()

    raw = load_results()
    if args.top:
        raw = raw[-args.top:]
    rows = [derive_metrics(r) for r in raw]
    if not rows:
        print(f"No results in {RESULTS_DIR}")
        return

    # baseline_p95 を決める
    if args.baseline_label:
        baseline = next((r for r in rows if args.baseline_label in r["label"]), rows[0])
    else:
        baseline = rows[0]
    baseline_p95 = baseline["p95_s"] or 1.0
    print(f"Baseline (for speed score): {baseline['label']}  p95={baseline_p95:.2f}s\n")

    weights = DEFAULT_WEIGHTS
    rows = score(rows, baseline_p95, weights)
    rows_sorted = sorted(rows, key=lambda r: r.get("total", 0), reverse=True)
    print_table(rows_sorted)

    print("\n採用判定:")
    if not rows_sorted:
        return
    best = rows_sorted[0]
    baseline_row = next((r for r in rows if r["label"] == baseline["label"]), best)
    gain = (best.get("total", 0) - baseline_row.get("total", 0)) * 100
    print(f"  最高: {best['label']} (Score {best.get('total',0)*100:.1f})")
    print(f"  ベースライン比: {gain:+.1f} pt")
    if gain >= 3.0:
        print("  → 採用検討の閾値 (+3pt) を満たす")
    else:
        print("  → +3pt 未達。現状維持を推奨")


if __name__ == "__main__":
    main()
