import os
import sys
import os
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone


# اضافه کردن مسیر پوشه فعلی
sys.path.append(os.path.dirname(__file__))

from hyperdash_telegram_bot_mtproto_coinglass import main

if __name__ == "__main__":
    main()
