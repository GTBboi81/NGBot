#!/usr/bin/env python3
"""ゴールデンセット構築スクリプト

直近の日次CSV (yyyymmdd_*_results.csv) から層化サンプリングで N 件を抽出し、
tests/golden_set/{id}_input.txt と tests/golden_set/{id}_proposed.json を出力する。

proposed.json は「現行 elyza3 Q4_K_M が返した既存出力」をそのまま正解候補として置く。
ユーザーが目視レビューして必要に応じて修正し、`_expected.json` にリネームすれば
評価スクリプト（次タスク）で読まれる。

使い方:
    python tests/build_golden_set.py --n 100 --since 2026-04-01
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "tests" / "golden_set"

# main.py の extraction_items と同じ順序
JSON_FIELDS = [
    "氏名", "郵便番号", "都道府県", "利用回線", "携帯台数",
    "戸建・MS", "固定電話", "ひかりTV", "決裁者・非決裁者",
    "NG理由", "NG理由箇所",
]

NG_REASON_CATS = [
    "留守", "今忙NG", "アプローチNG", "料金NG", "家族反対",
    "提供確認NG", "既契約NG", "面倒NG", "不信NG",
    "申込意思無NG", "その他",
]

CSV_FILE_PATTERN = re.compile(r"^(\d{8})_\d+_results\.csv$")


def length_bucket(text: str) -> str:
    n = len(text or "")
    if n <= 50:
        return "XS"
    if n <= 200:
        return "S"
    if n <= 800:
        return "M"
    if n <= 2000:
        return "L"
    return "XL"


def discover_csvs(since: datetime) -> list[Path]:
    out = []
    for p in PROJECT_ROOT.glob("*_results.csv"):
        m = CSV_FILE_PATTERN.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            continue
        if d >= since:
            out.append(p)
    return sorted(out)


def make_row_id(row: dict, csv_name: str) -> str:
    key = f"{csv_name}|{row.get('電話番号','')}|{row.get('テナント','')}|{row.get('文字起こしテキスト','')[:80]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def proposed_json(row: dict) -> dict:
    out = {"reasoning": row.get("判定根拠", "") or ""}
    for f in JSON_FIELDS:
        out[f] = row.get(f, "") or "N/A"
    return out


def _open_csv(p: Path):
    """CSVファイルを文字コード自動判定で開く。Shift_JIS(cp932)/UTF-8/UTF-8-SIG対応。"""
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            with p.open("r", encoding=enc, newline="") as f:
                _ = f.read(4096)
            return p.open("r", encoding=enc, newline="")
        except UnicodeDecodeError:
            continue
    return p.open("r", encoding="cp932", errors="replace", newline="")


def load_rows(csvs: list[Path]) -> list[dict]:
    rows: dict[str, dict] = {}
    for p in csvs:
        try:
            with _open_csv(p) as f:
                reader = csv.DictReader(f)
                for r in reader:
                    text = (r.get("文字起こしテキスト") or "").strip()
                    if not text:
                        continue
                    if (r.get("NG理由") or "").strip() == "":
                        continue
                    r["_src_csv"] = p.name
                    rid = make_row_id(r, p.name)
                    rows[rid] = r
        except Exception as e:
            print(f"[warn] failed to read {p.name}: {e}")
    return list(rows.values())


def stratified_sample(rows: list[dict], n: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    by_cat: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        cat = (r.get("NG理由") or "その他").strip() or "その他"
        if cat not in NG_REASON_CATS:
            cat = "その他"
        bucket = length_bucket(r.get("文字起こしテキスト", ""))
        by_cat[(cat, bucket)].append(r)

    # 各 (cat, bucket) からまず最低1件、残りは比例配分
    total = sum(len(v) for v in by_cat.values())
    if total == 0:
        return []
    selected: list[dict] = []
    quotas: dict[tuple[str, str], int] = {}
    for key, items in by_cat.items():
        proportional = max(1, round(n * len(items) / total))
        quotas[key] = min(proportional, len(items))

    while sum(quotas.values()) > n:
        # 最大のものから1減らす
        k = max(quotas, key=lambda x: (quotas[x], len(by_cat[x])))
        if quotas[k] <= 1:
            break
        quotas[k] -= 1

    while sum(quotas.values()) < n:
        # 余裕のあるカテゴリから1増やす
        candidates = [k for k, q in quotas.items() if q < len(by_cat[k])]
        if not candidates:
            break
        k = rng.choice(candidates)
        quotas[k] += 1

    for key, items in by_cat.items():
        q = quotas.get(key, 0)
        if q <= 0:
            continue
        picked = rng.sample(items, min(q, len(items)))
        selected.extend(picked)

    rng.shuffle(selected)
    return selected[:n]


def write_set(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, r in enumerate(rows, 1):
        rid = f"{i:03d}_{make_row_id(r, r.get('_src_csv',''))[:6]}"
        text = (r.get("文字起こしテキスト") or "").strip()
        (OUT_DIR / f"{rid}_input.txt").write_text(text, encoding="utf-8")
        with (OUT_DIR / f"{rid}_proposed.json").open("w", encoding="utf-8") as f:
            json.dump(proposed_json(r), f, ensure_ascii=False, indent=2)
        manifest.append({
            "id": rid,
            "src_csv": r.get("_src_csv", ""),
            "電話番号": r.get("電話番号", ""),
            "テナント": r.get("テナント", ""),
            "NG理由_AI": r.get("NG理由", ""),
            "char_len": len(text),
            "length_bucket": length_bucket(text),
            "reviewed": False,
        })
    with (OUT_DIR / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    by_reason: dict[str, int] = defaultdict(int)
    by_bucket: dict[str, int] = defaultdict(int)
    for m in manifest:
        by_reason[m["NG理由_AI"]] += 1
        by_bucket[m["length_bucket"]] += 1
    print(f"=== Wrote {len(manifest)} samples to {OUT_DIR} ===")
    print("By NG理由:")
    for k in NG_REASON_CATS:
        if by_reason.get(k):
            print(f"  {k}: {by_reason[k]}")
    print("By length_bucket:")
    for k in ["XS", "S", "M", "L", "XL"]:
        if by_bucket.get(k):
            print(f"  {k}: {by_bucket[k]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--since", type=str, default="2026-04-01",
                        help="YYYY-MM-DD: only use CSVs on/after this date")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    since_dt = datetime.strptime(args.since, "%Y-%m-%d")
    csvs = discover_csvs(since_dt)
    print(f"Found {len(csvs)} CSVs since {args.since}:")
    for p in csvs:
        print(f"  {p.name}")
    rows = load_rows(csvs)
    print(f"Loaded {len(rows)} unique rows with non-empty NG理由")
    if not rows:
        print("No rows. Try --since earlier.")
        return
    picked = stratified_sample(rows, args.n, seed=args.seed)
    write_set(picked)


if __name__ == "__main__":
    main()
