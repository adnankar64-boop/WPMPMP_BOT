import os
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import Flask
import telebot

# ==============================
# 📌 تنظیمات
# ==============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "Your_Telegram_Bot_Token")
CHAT_ID = os.getenv("CHAT_ID", "Your_Chat_ID")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "Your_Etherscan_API_Key")
SOLANA_API_URL = "https://public-api.solscan.io/account/transactions?account="
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY", "Your_Coinglass_API_Key")

# کیف پول‌ها
ETH_WALLETS = ["0x1234567890abcdef1234567890abcdef12345678"]
SOL_WALLETS = ["So11111111111111111111111111111111111111112"]

# Hyperliquid wallet (نمونه)
HYPER_WALLETS = ["0xc66b1916a8355f422dde8d7227a85fa6a72137ec283cdcad93785cb6cf931e7b"]

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# حافظه سیگنال‌ها
last_sent_signals = {}

# ==============================
# 📌 توابع کمکی
# ==============================

def clean_old_signals():
    """پاک کردن سیگنال‌های قدیمی‌تر از 24 ساعت"""
    now = datetime.now()
    for key in list(last_sent_signals.keys()):
        if now - last_sent_signals[key] > timedelta(hours=24):
            del last_sent_signals[key]

def already_sent(signal_id):
    """بررسی اینکه سیگنال تکراری است یا نه"""
    clean_old_signals()
    if signal_id in last_sent_signals:
        return True
    last_sent_signals[signal_id] = datetime.now()
    return False

# ==============================
# 📌 سیگنال‌های اتریوم
# ==============================
def get_large_eth_tx(wallet, min_value=10):
    url = f"https://api.etherscan.io/api?module=account&action=txlist&address={wallet}&sort=desc&apikey={ETHERSCAN_API_KEY}"
    alerts = []
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        txs = data.get("result", [])
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            eth_value = int(tx.get("value", 0)) / 1e18
            if eth_value >= min_value:
                sig_id = tx.get("hash")
                if already_sent(sig_id):
                    continue
                alerts.append(
                    f"🟢 تراکنش بزرگ روی اتریوم:\n"
                    f"🔹 مقدار: {eth_value:.2f} ETH\n"
                    f"📤 از: {tx.get('from')}\n"
                    f"📥 به: {tx.get('to')}\n"
                    f"🔗 https://etherscan.io/tx/{sig_id}"
                )
    except Exception as e:
        print(f"[ETH ERROR] {e}")
    return alerts

# ==============================
# 📌 سیگنال‌های سولانا
# ==============================
def get_large_solana_tx(wallet, min_value=1000):
    url = f"{SOLANA_API_URL}{wallet}&limit=1"
    alerts = []
    try:
        r = requests.get(url, timeout=10)
        txs = r.json()
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            lamports = tx.get("lamports", 0)
            sol_value = lamports / 1e9
            if sol_value >= min_value:
                sig_id = tx.get("txHash")
                if already_sent(sig_id):
                    continue
                alerts.append(
                    f"🟢 تراکنش بزرگ روی سولانا:\n"
                    f"🔹 مقدار: {sol_value:.2f} SOL\n"
                    f"🔗 https://solscan.io/tx/{sig_id}"
                )
    except Exception as e:
        print(f"[SOLANA ERROR] {e}")
    return alerts

# ==============================
# 📌 سیگنال‌های کوین‌گلاس
# ==============================
def get_coinglass_signal():
    url = "https://open-api.coinglass.com/api/pro/v1/futures/longShort_rate?exchange=binance&period=1h"
    alerts = []
    try:
        headers = {"coinglassSecret": COINGLASS_API_KEY}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if not data or "data" not in data:
            return []
        for item in data["data"]:
            symbol = item.get("symbol")
            long_ratio = item.get("longRate", 0)
            short_ratio = item.get("shortRate", 0)
            sig_id = f"coinglass-{symbol}-{long_ratio}-{short_ratio}"
            if already_sent(sig_id):
                continue
            alerts.append(
                f"📊 سیگنال کوین‌گلاس ({symbol}):\n"
                f"🟢 Long: {long_ratio}%\n"
                f"🔴 Short: {short_ratio}%"
            )
    except Exception as e:
        print(f"[COINGLASS ERROR] {e}")
    return alerts

# ==============================
# 📌 سیگنال‌های هایپرلیکویید (نمونه ساده)
# ==============================
def get_hyper_signals(wallets):
    alerts = []
    for wallet in wallets:
        try:
            # این فقط یک نمونه است (باید با API واقعی Hyperliquid جایگزین شود)
            sig_id = f"hyper-{wallet}-{int(time.time()//3600)}"
            if already_sent(sig_id):
                continue
            alerts.append(
                f"📡 سیگنال Hyperliquid برای کیف‌پول:\n"
                f"🔗 {wallet}\n"
                f"(داده نمونه)"
            )
        except Exception as e:
            print(f"[HYPER ERROR] {e}")
    return alerts

# ==============================
# 📌 حلقه اصلی سیگنال
# ==============================
def signal_loop():
    while True:
        try:
            alerts = []

            # اتریوم
            for w in ETH_WALLETS:
                alerts.extend(get_large_eth_tx(w))

            # سولانا
            for w in SOL_WALLETS:
                alerts.extend(get_large_solana_tx(w))

            # کوین‌گلاس
            alerts.extend(get_coinglass_signal())

            # هایپرلیکویید
            alerts.extend(get_hyper_signals(HYPER_WALLETS))

            # ارسال پیام‌ها
            for msg in alerts:
                bot.send_message(CHAT_ID, f"📊 سیگنال بازار:\n\n{msg}")
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
        time.sleep(60)

# ==============================
# 📌 دستورات ربات
# ==============================
@bot.message_handler(commands=["start"])
def start_message(message):
    bot.reply_to(message, "🟢 پیام آزمایشی: ربات در حال کار است!")

# ==============================
# 📌 اجرا
# ==============================
if __name__ == "__main__":
    print("✅ Bot restarted successfully and is live on Render!")

    # پیام آزمایشی اولیه
    try:
        bot.send_message(CHAT_ID, "🟢 پیام آزمایشی اولیه: ربات با موفقیت استارت شد!")
    except Exception as e:
        print(f"[STARTUP MSG ERROR] {e}")

    # اجرای حلقه سیگنال در رشته جداگانه
    threading.Thread(target=signal_loop, daemon=True).start()

    # اجرای Flask (برای Render لازم است)
    @app.route("/")
    def home():
        return "Bot is running!"

    app.run(host="0.0.0.0", port=10000)
