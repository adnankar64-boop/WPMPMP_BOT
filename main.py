import os
import sys

# اضافه کردن مسیر پوشه فعلی (برای اینکه فایل اصلی پیدا شود)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from hyperdash_telegram_bot_mtproto_coinglass import main as bot_main

if __name__ == "__main__":
    bot_main()
