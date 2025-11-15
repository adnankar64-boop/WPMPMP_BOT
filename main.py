import os
import sys

# اضافه کردن مسیر پوشه فعلی (برای اینکه فایل اصلی پیدا شود)
sys.path.append(os.path.dirname(__file__))

from hyperdash_telegram_bot_mtproto_coinglass import main

if __name__ == "__main__":
    main()
