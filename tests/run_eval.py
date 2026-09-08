#!/usr/bin/env python3
"""ゴールデンセットに対してモデルを評価するスクリプト

使い方:
    python tests/run_eval.py --model elyza3
    python tests/run_eval.py --model swallow-v05 --label "Swallow v0.5 Q5_K_M"

仕様:
- `tests/golden_set/{id}_input.txt` を入力
- `{id}_expected.json` が存在する件のみ評価対象（人手レビュー済みのみ）
- 本番と同じプロンプト・サンプリングパラメータ・バリデーションを通す
- 結果を `tests/results/{label}_{timestamp}.json` に保存

評価指標:
- 完全一致率（全フィールド）
- フィールド別精度
- NG理由の混同行列
- 利用回線 N/A 遵守率
- JSONパース失敗率
- 推論時間 p50 / p95 / mean
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ollama  # noqa: E402

import main as ngbot  # noqa: E402

GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden_set"
RESULTS_DIR = PROJECT_ROOT / "tests" / "results"

JSON_FIELDS = [
    "氏名", "郵便番号", "都道府県", "利用回線", "携帯台数",
    "戸建・MS", "固定電話", "ひかりTV", "決裁者・非決裁者",
    "NG理由", "NG理由箇所",
]


class EvalAnalyzer(ngbot.AudioAnalyzer):
    """Whisper初期化をスキップし、LLM分析パスだけ使う評価用サブクラス。"""

    def __init__(self, config: dict, model_override: str | None = None):
        self.config = config
        self.script_dir = str(PROJECT_ROOT)
        self.validator = ngbot.ResultValidator(config)

        ollama_settings = config.get("ollama_settings", {}) or {}
        self._llm_model_name = model_override or str(ollama_settings.get("model_name", "elyza3"))
        self._llm_num_ctx = int(ollama_settings.get("num_ctx", 2048))
        self._llm_num_predict = int(ollama_settings.get("num_predict", 512))
        self._enable_two_stage = bool(ollama_settings.get("enable_two_stage", True))
        self._llm_timeout_sec = int(ollama_settings.get("timeout_sec", 600))
        # 接続先は main.py と同じ解決ロジックを使う。ここで host を渡さないと
        # 環境変数 OLLAMA_HOST が採用され、ゴールデンセット（実通話のPII）が
        # 外部ホストへ送信されうる。
        self._ollama_host = ngbot.resolve_ollama_host(ollama_settings)
        self._ollama_client = ollama.Client(host=self._ollama_host,
                                            timeout=self._llm_timeout_sec)
        self.retry_count = int(config.get("performance_settings", {}).get("retry_on_error", 3))


def load_golden() -> list[tuple[str, str, dict]]:
    """(id, input_text, expected_json) を返す。expected が無い件はスキップ。"""
    items = []
    for inp in sorted(GOLDEN_DIR.glob("*_input.txt")):
        stem = inp.stem.removesuffix("_input")
        exp_path = GOLDEN_DIR / f"{stem}_expected.json"
        if not exp_path.exists():
            continue
        text = inp.read_text(encoding="utf-8")
        expected = json.loads(exp_path.read_text(encoding="utf-8"))
        items.append((stem, text, expected))
    return items


def normalize(v) -> str:
    if v is None:
        return "N/A"
    s = str(v).strip()
    if s == "" or s.lower() in ("none", "null"):
        return "N/A"
    return s


def field_eq(a, b) -> bool:
    return normalize(a) == normalize(b)


def evaluate(predictions: list[dict], expected: list[dict]) -> dict:
    n = len(predictions)
    exact = 0
    field_correct: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    line_na_correct = 0
    line_na_total = 0
    line_na_violation = 0  # 期待がN/Aだったのに何かを出してしまった

    for p, e in zip(predictions, expected):
        all_match = True
        for f in JSON_FIELDS:
            if field_eq(p.get(f), e.get(f)):
                field_correct[f] += 1
            else:
                all_match = False
        if all_match:
            exact += 1
        confusion[normalize(e.get("NG理由"))][normalize(p.get("NG理由"))] += 1
        if normalize(e.get("利用回線")) == "N/A":
            line_na_total += 1
            if normalize(p.get("利用回線")) == "N/A":
                line_na_correct += 1
            else:
                line_na_violation += 1

    return {
        "n": n,
        "exact_match_rate": exact / n if n else 0.0,
        "field_accuracy": {f: field_correct[f] / n if n else 0.0 for f in JSON_FIELDS},
        "ng_reason_confusion": {k: dict(v) for k, v in confusion.items()},
        "line_na_compliance": {
            "n_expected_na": line_na_total,
            "correct_na": line_na_correct,
            "violation": line_na_violation,
            "rate": line_na_correct / line_na_total if line_na_total else None,
        },
    }


def run(model: str, label: str | None) -> None:
    config_path = PROJECT_ROOT / "config.yaml"
    config = ngbot.load_config()
    print(f"Loaded config: {config_path}")

    runner = EvalAnalyzer(config, model_override=model)
    print(f"Model: {runner._llm_model_name}  (num_ctx={runner._llm_num_ctx}, "
          f"num_predict={runner._llm_num_predict}, two_stage={runner._enable_two_stage})")

    golden = load_golden()
    if not golden:
        print(f"No reviewed samples (looked in {GOLDEN_DIR}). "
              "Rename _proposed.json to _expected.json after review.")
        return
    print(f"Evaluating {len(golden)} reviewed samples...")

    predictions: list[dict] = []
    expected: list[dict] = []
    latencies: list[float] = []
    parse_failures = 0
    stage1_rusu_hits = 0

    for i, (rid, text, exp) in enumerate(golden, 1):
        t0 = time.perf_counter()
        try:
            if runner._enable_two_stage and runner.validator.is_likely_rusu(text):
                stage1_rusu_hits += 1
            pred = runner._analyze_with_llm(text)
        except Exception as e:
            print(f"  [{i}/{len(golden)}] {rid}: EXCEPTION {type(e).__name__}: {e}")
            pred = {f: "Error" for f in JSON_FIELDS}
            parse_failures += 1
        dt = time.perf_counter() - t0
        latencies.append(dt)
        predictions.append(pred)
        expected.append(exp)
        ng_pred = pred.get("NG理由", "?")
        ng_exp = exp.get("NG理由", "?")
        mark = "OK" if ng_pred == ng_exp else "NG"
        print(f"  [{i}/{len(golden)}] {rid}: {dt:5.2f}s  NG理由 pred={ng_pred} exp={ng_exp} [{mark}]")

    metrics = evaluate(predictions, expected)
    metrics["latency_sec"] = {
        "mean": statistics.mean(latencies) if latencies else 0,
        "p50": statistics.median(latencies) if latencies else 0,
        "p95": _percentile(latencies, 95),
        "max": max(latencies) if latencies else 0,
    }
    metrics["parse_failures"] = parse_failures
    metrics["stage1_rusu_hits"] = stage1_rusu_hits

    print("\n=== Results ===")
    print(f"Exact match: {metrics['exact_match_rate']:.1%}")
    print(f"Latency mean/p50/p95/max: {metrics['latency_sec']['mean']:.2f}s "
          f"/ {metrics['latency_sec']['p50']:.2f}s / {metrics['latency_sec']['p95']:.2f}s "
          f"/ {metrics['latency_sec']['max']:.2f}s")
    print(f"Stage1 留守ヒット: {stage1_rusu_hits}/{len(golden)} (LLM skipped)")
    print(f"Parse failures: {parse_failures}")
    print("Field accuracy:")
    for f in JSON_FIELDS:
        print(f"  {f}: {metrics['field_accuracy'][f]:.1%}")
    lnc = metrics["line_na_compliance"]
    if lnc["n_expected_na"]:
        print(f"利用回線 N/A 遵守: {lnc['correct_na']}/{lnc['n_expected_na']} "
              f"({lnc['rate']:.1%}, violations={lnc['violation']})")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = (label or model).replace("/", "_").replace(":", "_")
    out = RESULTS_DIR / f"{safe_label}_{ts}.json"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": model,
        "label": label or model,
        "timestamp": ts,
        "metrics": metrics,
        "predictions": predictions,
        "expected": expected,
        "ids": [g[0] for g in golden],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved to {out}")


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Ollama model tag (e.g. elyza3, swallow-v05)")
    parser.add_argument("--label", default=None, help="Label for the results file")
    args = parser.parse_args()
    run(args.model, args.label)


if __name__ == "__main__":
    main()
