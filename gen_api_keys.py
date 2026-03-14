#!/usr/bin/env python3
"""生成 Polymarket CLOB API 密钥"""

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

PRIVATE_KEY = os.environ["POLYMARKET_PRIVATE_KEY"]
HOST = "https://clob.polymarket.com"
CHAIN_ID = POLYGON  # 137

def main():
    print("🔑 正在初始化 CLOB 客户端...")
    client = ClobClient(HOST, key=PRIVATE_KEY, chain_id=CHAIN_ID)
    
    print(f"📍 钱包地址: {client.get_address()}")
    
    print("⚙️  正在生成/派生 API 密钥...")
    api_creds = client.create_or_derive_api_key()
    
    print("\n✅ API 密钥生成成功！")
    print(f"API Key:        {api_creds.api_key}")
    print(f"API Secret:     {api_creds.api_secret}")
    print(f"API Passphrase: {api_creds.api_passphrase}")

if __name__ == "__main__":
    main()
