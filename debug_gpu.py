#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPU検出デバッグスクリプト
"""

import sys

print("=" * 60)
print("GPU検出デバッグ")
print("=" * 60)

# 1. PyTorch確認
print("\n【1. PyTorch確認】")
try:
    import torch
    print(f"PyTorchバージョン: {torch.__version__}")
    print(f"CUDA利用可能: {torch.cuda.is_available()}")
    print(f"CUDAバージョン: {torch.version.cuda if torch.cuda.is_available() else 'N/A'}")
    
    if torch.cuda.is_available():
        print(f"CUDAデバイス数: {torch.cuda.device_count()}")
        for i in range(torch.cuda.device_count()):
            print(f"  デバイス{i}: {torch.cuda.get_device_name(i)}")
            props = torch.cuda.get_device_properties(i)
            print(f"    メモリ: {props.total_memory / 1024**3:.1f} GB")
    else:
        print("⚠️ torch.cuda.is_available() = False")
        print("\n考えられる原因:")
        print("  1. PyTorchがCPU版でインストールされている")
        print("  2. CUDAドライバがインストールされていない")
        print("  3. CUDAバージョンとPyTorchの互換性問題")
        
except ImportError as e:
    print(f"❌ PyTorchがインストールされていません: {e}")

# 2. faster-whisper確認
print("\n【2. faster-whisper確認】")
try:
    from faster_whisper import WhisperModel
    print("✅ faster-whisperがインストールされています")
    
    # CTranslate2のCUDA対応確認
    try:
        import ctranslate2
        print(f"CTranslate2バージョン: {ctranslate2.__version__}")
        
        # CUDAサポート確認
        cuda_devices = ctranslate2.get_cuda_device_count()
        print(f"CTranslate2 CUDAデバイス数: {cuda_devices}")
        
        if cuda_devices == 0:
            print("⚠️ CTranslate2がCUDAを検出できていません")
            print("\n考えられる原因:")
            print("  1. ctranslate2がCPU版でインストールされている")
            print("  2. 再インストールが必要: pip install ctranslate2 --force-reinstall")
        
    except Exception as e:
        print(f"CTranslate2確認エラー: {e}")
    
except ImportError as e:
    print(f"❌ faster-whisperがインストールされていません: {e}")

# 3. NVIDIA SMI確認
print("\n【3. NVIDIA SMI確認】")
import subprocess
try:
    result = subprocess.run(['nvidia-smi'], capture_output=True, text=True)
    if result.returncode == 0:
        print("✅ nvidia-smiが実行可能です")
        # ドライババージョンとCUDAバージョンを抽出
        lines = result.stdout.split('\n')
        for line in lines[:10]:
            print(f"  {line}")
    else:
        print("❌ nvidia-smiの実行に失敗しました")
        print(result.stderr)
except FileNotFoundError:
    print("❌ nvidia-smiが見つかりません（NVIDIAドライバがインストールされていない可能性）")
except Exception as e:
    print(f"❌ エラー: {e}")

# 4. 環境変数確認
print("\n【4. 関連環境変数】")
import os
cuda_vars = ['CUDA_VISIBLE_DEVICES', 'CUDA_HOME', 'CUDA_PATH', 'LD_LIBRARY_PATH']
for var in cuda_vars:
    value = os.environ.get(var, '(未設定)')
    print(f"  {var}: {value}")

# 5. 修正提案
print("\n【5. 修正提案】")
print("=" * 60)

try:
    import torch
    if not torch.cuda.is_available():
        print("""
PyTorchがCUDAを認識していません。以下を試してください:

1. PyTorchの再インストール（CUDA対応版）:
   pip uninstall torch torchvision torchaudio
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

2. CTranslate2の再インストール:
   pip uninstall ctranslate2 faster-whisper
   pip install ctranslate2 faster-whisper

3. 環境変数の設定（必要に応じて）:
   set CUDA_VISIBLE_DEVICES=0
""")
    else:
        print("✅ PyTorchはCUDAを認識しています")
        
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() == 0:
                print("""
⚠️ CTranslate2がCUDAを認識していません。

CTranslate2の再インストールを試してください:
   pip uninstall ctranslate2 faster-whisper
   pip install ctranslate2 faster-whisper --force-reinstall
""")
            else:
                print("✅ CTranslate2もCUDAを認識しています")
        except:
            pass
            
except:
    pass

print("\n" + "=" * 60)
print("デバッグ完了")
print("=" * 60)