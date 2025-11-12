"""
Entry point for Telegram Signal Bot
"""

import os
import sys

# مسیر فایل اصلی پروژه
sys.path.append(os.path.dirname(__file__))

from hyperdash_telegram_bot_mtproto_coinglass import main

if __name__ == "__main__":
    print("🚀 Starting Telegram Signal Bot ...")
    print(f"BOT_TOKEN: {'✅ set' if os.environ.get('BOT_TOKEN') else '❌ not set'}")
    print(f"COINGLASS_API_KEY: {'✅ set' if os.environ.get('COINGLASS_API_KEY') else '❌ not set'}")
    print(f"PROXY_URL: {os.environ.get('PROXY_URL', '(empty)')}")
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped manually.")
