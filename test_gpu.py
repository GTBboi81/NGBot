# test_gpu.py

import ollama
import sys

# -----------------------------------------------------
# ★★★ テストしたいモデル名に書き換えてください ★★★
# -----------------------------------------------------
# 例: "llama3:latest", "elyza3" など
model_name = "elyza3"
# -----------------------------------------------------


print("--- Ollama Vulkan GPU 認識テスト開始 ---")

try:
    # 1. Ollamaサーバーに接続できるかチェック
    print("\n[ステップ1] Ollamaサーバーへの接続テスト...")
    ollama.list()
    print("[成功] Ollamaサーバーに接続できました。")

    # 2. 指定したモデルが存在するかチェック
    print(f"\n[ステップ2] モデル '{model_name}' の存在確認...")
    ollama.show(model_name)
    print(f"[成功] モデル '{model_name}' が見つかりました。")

    # 3. 簡単な推論を実行
    print("\n[ステップ3] GPUを使った推論テストを実行します...")
    print("  >> タスクマネージャーの「GPU」→「専用GPUメモリ」を確認してください。")
    print("  >> メモリ使用量が増えれば成功です。")

    response = ollama.chat(
        model=model_name,
        messages=[
            {'role': 'user', 'content': '自己紹介をしてください。あなたの知識はいつまでですか？'},
        ]
    )
    
    print("\n[成功] 推論が完了しました。")
    print("\n--- 応答 ---")
    print(response['message']['content'])
    print("------------")

    print("\n--- テスト終了 ---")
    print("タスクマネージャーでGPUメモリが使用されたか最終確認してください。")

except ollama.ResponseError as e:
    print(f"\n[エラー] Ollamaからエラーが返されました (ステータスコード: {e.status_code})")
    if e.status_code == 404:
        print(f"  >> モデル '{model_name}' が見つかりません。")
        print(f"  >> PowerShellで `ollama pull {model_name}` または `ollama create` を実行してください。")
    else:
        print(f"  >> エラー詳細: {e.error}")

except Exception as e:
    print(f"\n[致命的エラー] Ollamaサーバーに接続できませんでした。")
    print(f"  >> エラー詳細: {e}")
    print("  >> Ollamaが起動しているか確認してください。")
    print("  >> 環境変数 OLLAMA_VULKAN=1 を設定後、PCを再起動しましたか？")