import os
import sys
import threading
from flask import Flask

# اضافه کردن مسیر پوشه فعلی برای پیدا کردن فایل اصلی
sys.path.append(os.path.dirname(__file__))

# ایمپورت main از فایل اصلی ربات
from hyperdash_telegram_bot_mtproto_coinglass import main as bot_main

# ---- Flask fake server for Render free tier ----
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running successfully!", 200

def run_flask():
    # Render نیاز دارد یک پورت باز باشد:
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---- Main Runner ----
if __name__ == "__main__":
    # اجرای Flask در یک Thread
    threading.Thread(target=run_flask, daemon=True).start()

    # اجرای ربات تلگرام
    bot_main()
