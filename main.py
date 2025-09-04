import os
import threading
import time
from datetime import datetime, timedelta
import requests
import psycopg2
from flask import Flask, request
import telebot

# ---------- تنظیمات از Environment ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DB_URL")

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN معتبر نیست. مقدار درست رو در Environment Render وارد کن.")

if not DB_URL:
    raise ValueError("❌ DB_URL در Environment ست نشده است. باید از Render Postgres استفاده کنی.")

bot = telebot.TeleBot(BOT_TOKEN)

# ---------- اتصال به دیتابیس ----------
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# ساخت جدول‌ها
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
    bot.send_message(chat_id, "🟢 ربات با موفقیت استارت شد!\nبرای افزودن کیف پول: /addwallet chain address")

@bot.message_handler(commands=['addwallet'])
def add_wallet(message):
    try:
        parts = message.text.split()
        if len(parts) < 3:
            bot.send_message(message.chat.id, "❌ فرمت درست: /addwallet chain address")
            return

        blockchain, address = parts[1], parts[2]
        chat_id = message.chat.id
        cur.execute("INSERT INTO wallets (user_id, blockchain, address) VALUES (%s, %s, %s);",
                    (chat_id, blockchain.lower(), address))
        conn.commit()
        bot.send_message(chat_id, f"✅ کیف پول {address} روی {blockchain} اضافه شد!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطا در افزودن کیف پول: {e}")

# ---------- حلقه سیگنال ----------
last_sent_signals = {}

def fetch_signals(blockchain, address):
    """
    اینجا می‌تونی API های واقعی (Etherscan, Solana, DexCheck, ...) رو صدا بزنی.
    فعلا داده نمونه برمی‌گردونه.
    """
    return f"📡 سیگنال {blockchain.upper()} برای:\n🔗 {address}\n(داده نمونه)"

def signal_loop():
    while True:
        try:
            cur.execute("SELECT chat_id FROM users;")
            users = [row[0] for row in cur.fetchall()]

            for chat_id in users:
                cur.execute("SELECT id, blockchain, address FROM wallets WHERE user_id=%s;", (chat_id,))
                wallets = cur.fetchall()

                for wallet_id, blockchain, address in wallets:
                    signal_text = fetch_signals(blockchain, address)

                    # جلوگیری از ارسال تکراری
                    last_time = last_sent_signals.get((chat_id, wallet_id))
                    if not last_time or datetime.now() - last_time > timedelta(hours=6):
                        try:
                            bot.send_message(chat_id, signal_text)
                            last_sent_signals[(chat_id, wallet_id)] = datetime.now()
                            cur.execute("INSERT INTO signals (wallet_id, signal) VALUES (%s, %s);", (wallet_id, signal_text))
                            conn.commit()
                        except Exception as e:
                            print(f"[SEND ERROR] {e}")
        except Exception as e:
            print(f"[LOOP ERROR] {e}")

        # پاک کردن سیگنال‌های قدیمی
        cur.execute("DELETE FROM signals WHERE created_at < NOW() - INTERVAL '24 HOURS';")
        conn.commit()

        time.sleep(60)  # هر ۶۰ ثانیه بررسی

# ---------- اجرای بات ----------
if __name__ == "__main__":
    print("🚀 Bot starting...")
    t = threading.Thread(target=signal_loop, daemon=True)
    t.start()
    bot.infinity_polling()
