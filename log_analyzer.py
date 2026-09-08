#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ログ解析ツール - 処理状況と停止原因の特定
"""

import os
import re
from datetime import datetime
from collections import defaultdict
import sys

class LogAnalyzer:
    """ログファイルを解析して処理状況を確認"""
    
    def __init__(self, log_file="audio_analysis.log"):
        self.log_file = log_file
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_path = os.path.join(self.script_dir, log_file)
    
    def analyze(self):
        """ログ全体を解析"""
        if not os.path.exists(self.log_path):
            print(f"❌ ログファイルが見つかりません: {self.log_path}")
            return
        
        print(f"\n{'='*70}")
        print(f"ログ解析レポート - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}\n")
        
        # ログファイルを読み込み
        with open(self.log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if not lines:
            print("❌ ログファイルが空です")
            return
        
        # 基本統計
        self._print_basic_stats(lines)
        
        # 処理状況
        self._print_processing_status(lines)
        
        # エラー分析
        self._print_error_analysis(lines)
        
        # 警告分析
        self._print_warning_analysis(lines)
        
        # 最後の処理
        self._print_last_processing(lines)
        
        # 停止原因の推測
        self._print_stop_reason(lines)
        
        # パフォーマンス統計
        self._print_performance_stats(lines)
    
    def _print_basic_stats(self, lines):
        """基本統計情報"""
        print("📊 基本統計")
        print("-" * 70)
        
        # 開始・終了時刻
        first_line = lines[0]
        last_line = lines[-1]
        
        first_time = self._extract_timestamp(first_line)
        last_time = self._extract_timestamp(last_line)
        
        print(f"開始時刻: {first_time if first_time else '不明'}")
        print(f"最終ログ: {last_time if last_time else '不明'}")
        
        if first_time and last_time:
            try:
                start = datetime.strptime(first_time, "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
                duration = end - start
                print(f"実行時間: {duration}")
            except:
                pass
        
        print(f"総ログ行数: {len(lines)}")
        print()
    
    def _print_processing_status(self, lines):
        """処理状況"""
        print("📁 処理状況")
        print("-" * 70)
        
        # テナントごとの処理状況
        tenant_pattern = r"テナント '(.+?)' の処理"
        processed_pattern = r"処理完了: (.+?)\.mp3"
        tenant_complete_pattern = r"テナント '(.+?)': (\d+)/(\d+)件を正常に処理"
        
        current_tenant = None
        tenant_files = defaultdict(list)
        tenant_stats = {}
        
        for line in lines:
            # テナント開始
            match = re.search(tenant_pattern, line)
            if match:
                current_tenant = match.group(1)
            
            # ファイル処理完了
            match = re.search(processed_pattern, line)
            if match and current_tenant:
                tenant_files[current_tenant].append(match.group(1))
            
            # テナント完了
            match = re.search(tenant_complete_pattern, line)
            if match:
                tenant = match.group(1)
                processed = int(match.group(2))
                total = int(match.group(3))
                tenant_stats[tenant] = (processed, total)
        
        if tenant_stats:
            for tenant, (processed, total) in tenant_stats.items():
                rate = (processed / total * 100) if total > 0 else 0
                status = "✅" if processed == total else "⚠️"
                print(f"{status} {tenant}: {processed}/{total}件 ({rate:.1f}%)")
        
        if tenant_files and not tenant_stats:
            # 統計がない場合は処理済みファイル数を表示
            for tenant, files in tenant_files.items():
                print(f"📂 {tenant}: {len(files)}件処理")
        
        if not tenant_stats and not tenant_files:
            print("ℹ️ テナント処理情報が見つかりません")
        
        print()
    
    def _print_error_analysis(self, lines):
        """エラー分析"""
        print("❌ エラー分析")
        print("-" * 70)
        
        errors = [line for line in lines if 'ERROR' in line]
        
        if not errors:
            print("✅ エラーなし")
        else:
            print(f"⚠️ エラー発生: {len(errors)}件\n")
            
            # エラーの分類
            error_types = defaultdict(list)
            for error in errors:
                if 'LLM分析エラー' in error:
                    error_types['LLM分析エラー'].append(error)
                elif '文字起こしエラー' in error:
                    error_types['文字起こしエラー'].append(error)
                elif 'ファイル処理エラー' in error:
                    error_types['ファイル処理エラー'].append(error)
                elif 'キャッシュ' in error:
                    error_types['キャッシュエラー'].append(error)
                else:
                    error_types['その他のエラー'].append(error)
            
            for error_type, error_list in error_types.items():
                print(f"  {error_type}: {len(error_list)}件")
            
            # 最新のエラーを表示
            print("\n最新のエラー（最大5件）:")
            for error in errors[-5:]:
                print(f"  {error.strip()}")
        
        print()
    
    def _print_warning_analysis(self, lines):
        """警告分析"""
        print("⚠️ 警告分析")
        print("-" * 70)
        
        warnings = [line for line in lines if 'WARNING' in line]
        
        if not warnings:
            print("✅ 警告なし")
        else:
            print(f"警告発生: {len(warnings)}件\n")
            
            # 警告の分類
            warning_types = defaultdict(int)
            for warning in warnings:
                if 'NG理由箇所' in warning:
                    warning_types['NG理由箇所の補正'] += 1
                elif '固定電話' in warning:
                    warning_types['固定電話の補正'] += 1
                elif 'ひかりTV' in warning:
                    warning_types['ひかりTVの補正'] += 1
                elif '戸建・MS' in warning:
                    warning_types['戸建・MSの補正'] += 1
                elif '携帯台数' in warning:
                    warning_types['携帯台数の補正'] += 1
                elif 'キャッシュ' in warning:
                    warning_types['キャッシュ関連'] += 1
                else:
                    warning_types['その他の警告'] += 1
            
            for warning_type, count in sorted(warning_types.items(), 
                                             key=lambda x: x[1], reverse=True):
                print(f"  {warning_type}: {count}件")
        
        print()
    
    def _print_last_processing(self, lines):
        """最後に処理したファイル"""
        print("📄 最後の処理")
        print("-" * 70)
        
        # 最後に処理完了したファイル
        processed_pattern = r"処理完了: (.+?)\.mp3"
        last_processed = None
        
        for line in reversed(lines):
            match = re.search(processed_pattern, line)
            if match:
                last_processed = match.group(1)
                timestamp = self._extract_timestamp(line)
                print(f"最後に完了: {last_processed}.mp3")
                if timestamp:
                    print(f"完了時刻: {timestamp}")
                break
        
        if not last_processed:
            print("ℹ️ 処理完了ファイルが見つかりません")
        
        print()
    
    def _print_stop_reason(self, lines):
        """停止原因の推測"""
        print("🔍 停止原因の推測")
        print("-" * 70)
        
        last_50_lines = lines[-50:]
        
        # パターン検出
        reasons = []
        
        # エラーによる停止
        if any('ERROR' in line for line in last_50_lines):
            reasons.append("❌ エラーが発生しています")
            # 最後のエラーを表示
            for line in reversed(last_50_lines):
                if 'ERROR' in line:
                    print(f"最後のエラー: {line.strip()}")
                    break
        
        # 予期せぬ例外
        if any('Exception' in line or 'Traceback' in line for line in last_50_lines):
            reasons.append("💥 予期せぬ例外が発生しています")
        
        # メモリ不足
        if any('memory' in line.lower() or 'MemoryError' in line for line in last_50_lines):
            reasons.append("💾 メモリ不足の可能性があります")
        
        # ネットワークエラー
        if any('Connection' in line or 'Network' in line or 'timeout' in line.lower() 
               for line in last_50_lines):
            reasons.append("🌐 ネットワークエラーの可能性があります")
        
        # 正常終了
        if any('全処理完了' in line or '全ての処理が完了' in line for line in last_50_lines):
            reasons.append("✅ 正常に完了しています")
        
        # CUDA/GPUエラー
        if any('CUDA' in line or 'GPU' in line for line in last_50_lines):
            reasons.append("🎮 GPU/CUDAエラーの可能性があります")
        
        # 処理中断の可能性
        if not reasons:
            # 最後のログのタイムスタンプを確認
            last_timestamp = self._extract_timestamp(lines[-1])
            if last_timestamp:
                print(f"最終ログ時刻: {last_timestamp}")
                print("⏸️ 処理が途中で停止した可能性があります")
                print("\n考えられる原因:")
                print("  - プロセスの強制終了")
                print("  - システムのシャットダウン/再起動")
                print("  - ネットワーク接続の切断")
                print("  - Ollamaサービスの停止")
                print("  - リソース不足（メモリ/ディスク）")
        else:
            for reason in reasons:
                print(reason)
        
        print()
    
    def _print_performance_stats(self, lines):
        """パフォーマンス統計"""
        print("⚡ パフォーマンス統計")
        print("-" * 70)
        
        # 処理時間のパターン
        time_pattern = r"処理完了:.*?\(文字起こし: ([\d.]+)s, 分析: ([\d.]+)s\)"
        
        transcribe_times = []
        analysis_times = []
        
        for line in lines:
            match = re.search(time_pattern, line)
            if match:
                transcribe_times.append(float(match.group(1)))
                analysis_times.append(float(match.group(2)))
        
        if transcribe_times:
            print(f"処理済みファイル数: {len(transcribe_times)}件")
            print(f"\n文字起こし時間:")
            print(f"  平均: {sum(transcribe_times)/len(transcribe_times):.2f}秒")
            print(f"  最小: {min(transcribe_times):.2f}秒")
            print(f"  最大: {max(transcribe_times):.2f}秒")
            
            print(f"\n分析時間:")
            print(f"  平均: {sum(analysis_times)/len(analysis_times):.2f}秒")
            print(f"  最小: {min(analysis_times):.2f}秒")
            print(f"  最大: {max(analysis_times):.2f}秒")
            
            total_times = [t + a for t, a in zip(transcribe_times, analysis_times)]
            print(f"\n合計処理時間:")
            print(f"  平均: {sum(total_times)/len(total_times):.2f}秒/件")
            print(f"  推定総時間: {sum(total_times)/60:.1f}分")
        else:
            print("ℹ️ パフォーマンスデータが見つかりません")
        
        print()
    
    def _extract_timestamp(self, line):
        """ログ行からタイムスタンプを抽出"""
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        if match:
            return match.group(1)
        return None
    
    def get_summary(self):
        """簡易サマリーを返す"""
        if not os.path.exists(self.log_path):
            return "ログファイルが見つかりません"
        
        with open(self.log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if not lines:
            return "ログファイルが空です"
        
        error_count = len([l for l in lines if 'ERROR' in l])
        warning_count = len([l for l in lines if 'WARNING' in l])
        
        # 処理完了数
        processed_pattern = r"処理完了: (.+?)\.mp3"
        processed_count = len([l for l in lines if re.search(processed_pattern, l)])
        
        # 正常終了チェック
        completed = any('全処理完了' in l or '全ての処理が完了' in l for l in lines)
        
        summary = f"""
【処理サマリー】
- 処理ファイル数: {processed_count}件
- エラー: {error_count}件
- 警告: {warning_count}件
- 状態: {'✅ 正常完了' if completed else '⚠️ 未完了/中断'}
"""
        return summary


def main():
    """メイン関数"""
    analyzer = LogAnalyzer()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == '--summary':
            print(analyzer.get_summary())
        elif sys.argv[1] == '--help':
            print("""
ログ解析ツール

使い方:
  python log_analyzer.py          # 詳細解析
  python log_analyzer.py --summary # 簡易サマリー
  python log_analyzer.py --help    # ヘルプ表示
""")
        else:
            analyzer.analyze()
    else:
        analyzer.analyze()


if __name__ == "__main__":
    main()