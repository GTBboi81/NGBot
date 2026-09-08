#!/usr/bin/env python3
"""デバッグ: AudioAnalyzer の _get_unprocessed_files が 0件を返す原因を切り分ける。

5/19 0:00 のスケジュール発火で全テナント 0件で完了した事象を再現/調査。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import main as ngbot  # noqa: E402

TARGET_DATE_STR = "2026-05-18"


def main() -> None:
    cfg = ngbot.load_config()
    base_path = cfg["path_settings"]["base_path"]
    tenants = cfg["path_settings"]["tenant_folders"]
    print(f"base_path = {base_path!r}")
    print(f"base_path exists? {os.path.isdir(base_path)}")
    print(f"tenants = {tenants}")
    print(f"target_date_str = {TARGET_DATE_STR}")
    target_date = datetime.strptime(TARGET_DATE_STR, "%Y-%m-%d").date()

    # 1) target_date 正規化を検証
    norm = ngbot.AudioAnalyzer._normalize_target_date(TARGET_DATE_STR)
    print(f"_normalize_target_date('{TARGET_DATE_STR}') = {norm!r}")
    print(f"matches expected? {norm == target_date}")
    print()

    # 2) 1ファイル名で正規表現を検証
    sample = "20260518_09000000000_01.mp3"
    extracted = ngbot.AudioAnalyzer._extract_date_from_filename(sample)
    print(f"_extract_date_from_filename('{sample}') = {extracted!r}")
    print(f"matches target? {extracted == target_date}")
    print()

    # 3) 各テナントのファイル一覧と target_date 一致件数
    print("=== per-tenant scan ===")
    for t in tenants:
        audio_dir = os.path.join(base_path, t)
        if not os.path.isdir(audio_dir):
            print(f"[{t}] DIR_MISSING: {audio_dir}")
            continue
        try:
            all_files = os.listdir(audio_dir)
        except Exception as e:
            print(f"[{t}] LISTDIR_ERROR: {e}")
            continue
        all_mp3 = [f for f in all_files
                   if f.lower().endswith(".mp3") and os.path.isfile(os.path.join(audio_dir, f))]
        matched = [f for f in all_mp3 if ngbot.AudioAnalyzer._extract_date_from_filename(f) == target_date]
        print(f"[{t}] total_files={len(all_files)} mp3={len(all_mp3)} match_{TARGET_DATE_STR}={len(matched)}")
        if matched:
            print(f"      example match: {matched[0]}")
        elif all_mp3:
            date_dist = Counter(ngbot.AudioAnalyzer._extract_date_from_filename(f) for f in all_mp3[:500])
            top = date_dist.most_common(5)
            print(f"      date dist (first 500): {top}")
            none_count = sum(1 for f in all_mp3[:200] if ngbot.AudioAnalyzer._extract_date_from_filename(f) is None)
            print(f"      none_date_in_first_200: {none_count}")

    # 4) AudioAnalyzer 実体経由でも検証（target_date 引数あり）
    print("\n=== AudioAnalyzer 経由 (target_date='2026-05-18') ===")
    try:
        analyzer = ngbot.AudioAnalyzer(cfg, target_date=TARGET_DATE_STR)
        print(f"analyzer.target_date = {analyzer.target_date!r}")
        for t in tenants:
            files = analyzer._get_unprocessed_files(t)
            print(f"  _get_unprocessed_files('{t}') -> {len(files)} files")
    except Exception as e:
        print(f"AudioAnalyzer init failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
