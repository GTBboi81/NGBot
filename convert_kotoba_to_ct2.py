#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kotoba-whisperをCTranslate2形式に変換するスクリプト

使用方法:
  python convert_kotoba_to_ct2.py

必要なパッケージ:
  pip install ctranslate2 transformers[torch] huggingface_hub
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


def check_dependencies():
    """必要なパッケージがインストールされているか確認"""
    missing = []
    
    try:
        import ctranslate2
        print(f"✅ ctranslate2: {ctranslate2.__version__}")
    except ImportError:
        missing.append("ctranslate2")
    
    try:
        import transformers
        print(f"✅ transformers: {transformers.__version__}")
    except ImportError:
        missing.append("transformers")
    
    try:
        import torch
        print(f"✅ torch: {torch.__version__}")
        if torch.cuda.is_available():
            print(f"   CUDA available: {torch.cuda.get_device_name(0)}")
    except ImportError:
        missing.append("torch")
    
    if missing:
        print(f"\n❌ 以下のパッケージをインストールしてください:")
        print(f"   pip install {' '.join(missing)}")
        return False
    
    return True


def convert_model(
    model_name: str = "kotoba-tech/kotoba-whisper-v2.2",
    output_dir: str = "./kotoba-whisper-ct2",
    quantization: str = "float16",
    force: bool = False
):
    """
    Whisperモデルを CTranslate2 形式に変換
    
    Args:
        model_name: HuggingFace のモデル名またはローカルパス
        output_dir: 変換後のモデルを保存するディレクトリ
        quantization: 量子化タイプ (float32, float16, int8, int8_float16)
        force: 既存のディレクトリを上書きするか
    """
    output_path = Path(output_dir)
    
    # 既存チェック
    if output_path.exists() and not force:
        print(f"⚠️ 出力ディレクトリが既に存在します: {output_dir}")
        print("   --force オプションで上書きできます")
        return output_dir
    
    print(f"\n{'='*60}")
    print(f"モデル変換開始")
    print(f"{'='*60}")
    print(f"入力モデル: {model_name}")
    print(f"出力先: {output_dir}")
    print(f"量子化: {quantization}")
    print(f"{'='*60}\n")
    
    # ct2-transformers-converter コマンドを使用
    cmd = [
        sys.executable, "-m", "ctranslate2.converters.transformers",
        "--model", model_name,
        "--output_dir", output_dir,
        "--quantization", quantization,
        "--copy_files", "tokenizer.json", "preprocessor_config.json",
    ]
    
    if force:
        cmd.append("--force")
    
    print(f"実行コマンド: {' '.join(cmd)}\n")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"\n{'='*60}")
        print(f"✅ 変換完了!")
        print(f"{'='*60}")
        print(f"出力先: {output_dir}")
        print(f"\nconfig.yaml での設定方法:")
        print(f"  whisper_settings:")
        print(f"    backend: 'ctranslate2'")
        print(f"    custom_model_path: '{output_dir}'")
        print(f"{'='*60}")
        return output_dir
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 変換に失敗しました: {e}")
        
        # 代替方法を試す
        print("\n代替方法を試行中...")
        try:
            return convert_model_alternative(model_name, output_dir, quantization, force)
        except Exception as e2:
            print(f"❌ 代替方法も失敗: {e2}")
            return None


def convert_model_alternative(
    model_name: str,
    output_dir: str,
    quantization: str,
    force: bool
):
    """
    代替の変換方法（直接APIを使用）
    """
    import ctranslate2
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    
    print(f"\nモデルをダウンロード中: {model_name}")
    
    # モデルとプロセッサをロード
    model = WhisperForConditionalGeneration.from_pretrained(model_name)
    processor = WhisperProcessor.from_pretrained(model_name)
    
    print("CTranslate2形式に変換中...")
    
    # 変換
    converter = ctranslate2.converters.TransformersConverter(model_name)
    converter.convert(
        output_dir,
        quantization=quantization,
        force=force
    )
    
    # プロセッサファイルをコピー
    processor.save_pretrained(output_dir)
    
    print(f"✅ 変換完了: {output_dir}")
    return output_dir


def verify_model(model_path: str):
    """変換されたモデルを検証"""
    print(f"\n{'='*60}")
    print(f"モデル検証")
    print(f"{'='*60}")
    
    try:
        from faster_whisper import WhisperModel
        
        print(f"モデルをロード中: {model_path}")
        model = WhisperModel(model_path, device="cpu", compute_type="int8")
        
        print("✅ モデルのロードに成功しました")
        print("\n検証テスト:")
        print("  - モデルファイルが正しく読み込めます")
        print("  - faster-whisper で使用可能です")
        
        return True
        
    except Exception as e:
        print(f"❌ 検証に失敗: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="kotoba-whisper を CTranslate2 形式に変換"
    )
    parser.add_argument(
        "--model",
        default="kotoba-tech/kotoba-whisper-v2.2",
        help="変換するモデル名 (default: kotoba-tech/kotoba-whisper-v2.2)"
    )
    parser.add_argument(
        "--output",
        default="./kotoba-whisper-ct2",
        help="出力ディレクトリ (default: ./kotoba-whisper-ct2)"
    )
    parser.add_argument(
        "--quantization",
        choices=["float32", "float16", "int8", "int8_float16"],
        default="float16",
        help="量子化タイプ (default: float16)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存のディレクトリを上書き"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="変換後にモデルを検証"
    )
    
    args = parser.parse_args()
    
    print("="*60)
    print("kotoba-whisper CTranslate2 変換ツール")
    print("="*60)
    
    # 依存関係チェック
    print("\n依存関係を確認中...")
    if not check_dependencies():
        sys.exit(1)
    
    # モデル変換
    output_path = convert_model(
        model_name=args.model,
        output_dir=args.output,
        quantization=args.quantization,
        force=args.force
    )
    
    if output_path is None:
        sys.exit(1)
    
    # 検証
    if args.verify:
        if not verify_model(output_path):
            sys.exit(1)
    
    print("\n✅ 全ての処理が完了しました")


if __name__ == "__main__":
    main()