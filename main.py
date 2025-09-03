import os
import threading
import time
from datetime import datetime, timedelta
import requests
import psycopg2
from flask import Flask, request
import telebot

# ---------- تنظیمات ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("توکن ربات در Environment Variable با نام BOT_TOKEN تعریف نشده است!")

bot = telebot.TeleBot(BOT_TOKEN)
DB_URL = os.getenv("DB_URL", "postgresql://user:pass@localhost:5432/dbname")  # دیتابیس Postgres
SIGNAL_INTERVAL = 60  # ثانیه

# Threshold نمونه برای سیگنال‌ها
ETH_THRESHOLD = 0.01
SOL_THRESHOLD = 0.01
HYPERLIQUID_THRESHOLD = 0.01

# ---------- اتصال به دیتابیس ----------
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# اطمینان از وجود جداول
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT UNIQUE
);
CREATE TABLE IF NOT EXISTS wallets (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(chat_id),
    blockchain TEXT,
    address TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    wallet_id INT REFERENCES wallets(id),
    signal TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
""")
conn.commit()

# ---------- دستورات تلگرام ----------
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    cur.execute("INSERT INTO users (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO NOTHING;", (chat_id,))
    conn.commit()
    bot.send_message(chat_id, "🟢 پیام آزمایشی اولیه: ربات با موفقیت استارت شد!")

@bot.message_handler(commands=['addwallet'])
def add_wallet(message):
    try:
        parts = message.text.split()
        blockchain, address = parts[1], parts[2]
        chat_id = message.chat.id
        cur.execute("INSERT INTO wallets (user_id, blockchain, address) VALUES (%s, %s, %s);",
                    (chat_id, blockchain.lower(), address))
        conn.commit()
        bot.send_message(chat_id, f"✅ کیف پول {address} روی {blockchain} اضافه شد!")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در افزودن کیف پول: {e}")

# ---------- توابع دریافت سیگنال ----------
def get_eth_signal(address):
    ETHERSCAN_API = os.getenv("ETHERSCAN_API", "")
    if not ETHERSCAN_API:
        return None
    try:
        url = f"https://api.etherscan.io/api?module=account&action=txlist&address={address}&sort=desc&apikey={ETHERSCAN_API}"
        res = requests.get(url).json()
        txs = res.get("result", [])
        for tx in txs[:5]:  # بررسی آخرین ۵ تراکنش
            value = int(tx.get("value", 0)) / 1e18
            if value >= ETH_THRESHOLD:
                return f"📡 سیگنال ETH: تراکنش {value} ETH برای {address}"
    except:
        return None
    return None

def get_sol_signal(address):
    try:
        url = f"https://api.mainnet-beta.solana.com"  # جایگزین API سولانا
        # نمونه سیگنال
        return f"📡 سیگنال SOL برای {address} (داده نمونه)"
    except:
        return None

def get_hyperliquid_signal(address):
    try:
        # نمونه سیگنال Hyperliquid
        return f"📡 سیگنال Hyperliquid برای {address} (داده نمونه)"
    except:
        return None

def get_dexcheck_signal(address):
    DEXCHECK_API_KEY = os.getenv("DEXCHECK_API_KEY", "")
    if not DEXCHECK_API_KEY:
        return None
    try:
        # نمونه درخواست
        return f"📡 سیگنال DexCheck برای {address} (داده نمونه)"
    except:
        return None

def get_coinglass_signal(address):
    try:
        # نمونه سیگنال CoinGlass
        return f"📡 سیگنال CoinGlass برای {address} (داده نمونه)"
    except:
        return None

# ---------- حلقه سیگنال ----------
last_sent_signals = {}

def signal_loop():
    while True:
        try:
            cur.execute("SELECT chat_id FROM users;")
            users = [row[0] for row in cur.fetchall()]

            for chat_id in users:
                cur.execute("SELECT id, blockchain, address FROM wallets WHERE user_id=%s;", (chat_id,))
                wallets = cur.fetchall()
                if not wallets:
                    bot.send_message(chat_id, "❌ هیچ کیف پولی ثبت نشده.")
                    continue

                for wallet_id, blockchain, address in wallets:
                    signal_text = None
                    if blockchain.lower() == "ethereum":
                        signal_text = get_eth_signal(address)
                    elif blockchain.lower() == "solana":
                        signal_text = get_sol_signal(address)
                    elif blockchain.lower() == "hyperliquid":
                        signal_text = get_hyperliquid_signal(address)
                    elif blockchain.lower() == "dexcheck":
                        signal_text = get_dexcheck_signal(address)
                    elif blockchain.lower() == "coinglass":
                        signal_text = get_coinglass_signal(address)

                    if signal_text:
                        last_time = last_sent_signals.get((chat_id, wallet_id))
                        if not last_time or datetime.now() - last_time > timedelta(hours=24):
                            bot.send_message(chat_id, signal_text)
                            last_sent_signals[(chat_id, wallet_id)] = datetime.now()
                            cur.execute("INSERT INTO signals (wallet_id, signal) VALUES (%s, %s);",
                                        (wallet_id, signal_text))
                            conn.commit()
        except Exception as e:
            print(f"[ERROR] signal_loop: {e}")

        # پاک کردن سیگنال‌های قدیمی
        cur.execute("DELETE FROM signals WHERE created_at < NOW() - INTERVAL '24 HOURS';")
        conn.commit()

        time.sleep(SIGNAL_INTERVAL)

# ---------- اجرای بات ----------
if __name__ == "__main__":
    print("Starting bot...")
    t = threading.Thread(target=signal_loop, daemon=True)
    t.start()
    bot.infinity_polling()
