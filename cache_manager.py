#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
キャッシュ管理ツール
"""

import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


class CacheManager:
    """キャッシュ管理クラス"""
    
    def __init__(self, cache_dir=".cache"):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.cache_dir = os.path.join(self.script_dir, cache_dir)
    
    def get_cache_info(self):
        """キャッシュ情報を取得"""
        if not os.path.exists(self.cache_dir):
            return {
                'exists': False,
                'count': 0,
                'total_size': 0,
                'oldest': None,
                'newest': None
            }
        
        cache_files = [f for f in os.listdir(self.cache_dir) if f.endswith(('.pkl', '.json'))]
        
        if not cache_files:
            return {
                'exists': True,
                'count': 0,
                'total_size': 0,
                'oldest': None,
                'newest': None
            }
        
        total_size = 0
        oldest_time = None
        newest_time = None
        
        for cache_file in cache_files:
            file_path = os.path.join(self.cache_dir, cache_file)
            
            # ファイルサイズ
            total_size += os.path.getsize(file_path)
            
            # ファイルの更新時刻
            mtime = os.path.getmtime(file_path)
            mtime_dt = datetime.fromtimestamp(mtime)
            
            if oldest_time is None or mtime_dt < oldest_time:
                oldest_time = mtime_dt
            
            if newest_time is None or mtime_dt > newest_time:
                newest_time = mtime_dt
        
        return {
            'exists': True,
            'count': len(cache_files),
            'total_size': total_size,
            'oldest': oldest_time,
            'newest': newest_time
        }
    
    def display_info(self):
        """キャッシュ情報を表示"""
        print("\n" + "="*60)
        print("キャッシュ情報")
        print("="*60)
        
        info = self.get_cache_info()
        
        if not info['exists']:
            print(f"保存場所: {self.cache_dir}")
            print("状態: キャッシュフォルダが存在しません")
            print("\nℹ️ プログラムを一度実行すると自動作成されます")
            return
        
        if info['count'] == 0:
            print(f"保存場所: {self.cache_dir}")
            print("状態: キャッシュファイルがありません")
            return
        
        # サイズを読みやすく表示
        size_mb = info['total_size'] / (1024 * 1024)
        size_gb = size_mb / 1024
        
        if size_gb >= 1:
            size_str = f"{size_gb:.2f} GB"
        else:
            size_str = f"{size_mb:.1f} MB"
        
        # 経過日数を計算
        now = datetime.now()
        oldest_days = (now - info['oldest']).days if info['oldest'] else 0
        newest_days = (now - info['newest']).days if info['newest'] else 0
        
        print(f"保存場所: {self.cache_dir}")
        print(f"キャッシュファイル数: {info['count']}件")
        print(f"総サイズ: {size_str}")
        
        if info['oldest']:
            print(f"最古のキャッシュ: {info['oldest'].strftime('%Y-%m-%d %H:%M')} ({oldest_days}日前)")
        
        if info['newest']:
            print(f"最新のキャッシュ: {info['newest'].strftime('%Y-%m-%d %H:%M')} ({newest_days}日前)")
        
        print()
    
    def clear_all(self, confirm=True):
        """すべてのキャッシュを削除"""
        info = self.get_cache_info()
        
        if not info['exists'] or info['count'] == 0:
            print("\n❌ 削除するキャッシュがありません")
            return False
        
        if confirm:
            print(f"\n⚠️ {info['count']}件のキャッシュファイルを削除します")
            response = input("本当に削除しますか？ (yes/no): ")
            if response.lower() not in ['yes', 'y']:
                print("キャンセルしました")
                return False
        
        try:
            shutil.rmtree(self.cache_dir)
            print(f"\n✅ {info['count']}件のキャッシュを削除しました")
            return True
        except Exception as e:
            print(f"\n❌ エラー: {e}")
            return False
    
    def clean_old_cache(self, days=7, confirm=True):
        """古いキャッシュを削除"""
        if not os.path.exists(self.cache_dir):
            print("\n❌ キャッシュフォルダが存在しません")
            return False
        
        cache_files = [f for f in os.listdir(self.cache_dir) if f.endswith(('.pkl', '.json'))]
        
        if not cache_files:
            print("\n❌ 削除するキャッシュがありません")
            return False
        
        cutoff_time = datetime.now() - timedelta(days=days)
        old_files = []
        
        for cache_file in cache_files:
            file_path = os.path.join(self.cache_dir, cache_file)
            mtime = os.path.getmtime(file_path)
            mtime_dt = datetime.fromtimestamp(mtime)
            
            if mtime_dt < cutoff_time:
                old_files.append(file_path)
        
        if not old_files:
            print(f"\n✅ {days}日以上古いキャッシュはありません")
            return False
        
        if confirm:
            print(f"\n⚠️ {len(old_files)}件の古いキャッシュ（{days}日以上）を削除します")
            response = input("本当に削除しますか？ (yes/no): ")
            if response.lower() not in ['yes', 'y']:
                print("キャンセルしました")
                return False
        
        deleted_count = 0
        for file_path in old_files:
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                print(f"警告: {os.path.basename(file_path)} の削除に失敗 - {e}")
        
        print(f"\n✅ {deleted_count}件の古いキャッシュを削除しました")
        return True
    
    def interactive_menu(self):
        """対話型メニュー"""
        while True:
            print("\n" + "="*60)
            print("キャッシュ管理ツール")
            print("="*60)
            print("1. キャッシュ情報を表示")
            print("2. 全キャッシュを削除")
            print("3. 古いキャッシュを削除（7日以上）")
            print("4. 古いキャッシュを削除（30日以上）")
            print("5. 終了")
            print("="*60)
            
            choice = input("\n選択してください (1-5): ").strip()
            
            if choice == '1':
                self.display_info()
            elif choice == '2':
                self.clear_all()
            elif choice == '3':
                self.clean_old_cache(days=7)
            elif choice == '4':
                self.clean_old_cache(days=30)
            elif choice == '5':
                print("\n終了します")
                break
            else:
                print("\n❌ 無効な選択です")


def main():
    """メイン関数"""
    manager = CacheManager()
    
    if len(sys.argv) == 1:
        # 引数なし: 対話モード
        manager.interactive_menu()
    elif sys.argv[1] == '--info':
        # 情報表示
        manager.display_info()
    elif sys.argv[1] == '--clear':
        # 全削除
        manager.clear_all(confirm=False)
    elif sys.argv[1] == '--clean':
        # 古いキャッシュ削除
        if len(sys.argv) > 2:
            try:
                days = int(sys.argv[2])
                manager.clean_old_cache(days=days, confirm=False)
            except ValueError:
                print("❌ エラー: 日数は数値で指定してください")
        else:
            manager.clean_old_cache(days=7, confirm=False)
    elif sys.argv[1] == '--help':
        # ヘルプ
        print("""
キャッシュ管理ツール

使い方:
  python cache_manager.py              # 対話モード
  python cache_manager.py --info       # キャッシュ情報を表示
  python cache_manager.py --clear      # 全キャッシュを削除
  python cache_manager.py --clean [日数] # 古いキャッシュを削除（デフォルト7日）
  python cache_manager.py --help       # ヘルプ表示

例:
  python cache_manager.py --info       # 情報表示
  python cache_manager.py --clear      # 全削除
  python cache_manager.py --clean 7    # 7日以上古いキャッシュを削除
  python cache_manager.py --clean 30   # 30日以上古いキャッシュを削除
""")
    else:
        print(f"❌ 不明なオプション: {sys.argv[1]}")
        print("ヘルプを表示するには: python cache_manager.py --help")


if __name__ == "__main__":
    main()