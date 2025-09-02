import threading
import time
from datetime import datetime, timedelta
import requests
import psycopg2
from flask import Flask, request
import telebot

# ---------- تنظیمات ----------
BOT_TOKEN = "اینجا توکن خودت را قرار بده"
bot = telebot.TeleBot(BOT_TOKEN)

DB_URL = "postgresql://user:pass@localhost:5432/dbname"  # دیتابیس Postgres
SIGNAL_INTERVAL = 60  # ثانیه
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

# ---------- حلقه سیگنال ----------
last_sent_signals = {}

def signal_loop():
    while True:
        try:
            cur.execute("SELECT chat_id FROM users;")
            users = [row[0] for row in cur.fetchall()]

            for chat_id in users:
                # پیام آزمایشی اولیه
                bot.send_message(chat_id, "🟢 بررسی سیگنال‌ها...")

                # گرفتن کیف پول‌ها
                cur.execute("SELECT id, blockchain, address FROM wallets WHERE user_id=%s;", (chat_id,))
                wallets = cur.fetchall()
                if not wallets:
                    bot.send_message(chat_id, "❌ هیچ کیف پولی ثبت نشده.")
                    continue

                for wallet_id, blockchain, address in wallets:
                    # نمونه سیگنال آزمایشی
                    signal_text = f"📡 سیگنال {blockchain.capitalize()} برای کیف‌پول:\n🔗 {address}\n(داده نمونه)"

                    # جلوگیری از تکرار سیگنال
                    last_time = last_sent_signals.get((chat_id, wallet_id))
                    if not last_time or datetime.now() - last_time > timedelta(hours=24):
                        bot.send_message(chat_id, signal_text)
                        last_sent_signals[(chat_id, wallet_id)] = datetime.now()
                        cur.execute("INSERT INTO signals (wallet_id, signal) VALUES (%s, %s);", (wallet_id, signal_text))
                        conn.commit()
        except Exception as e:
            print(f"[ERROR] signal_loop: {e}")

        # پاک کردن سیگنال‌های قدیمی
        cur.execute("DELETE FROM signals WHERE created_at < NOW() - INTERVAL '24 HOURS';")
        conn.commit()

        time.sleep(SIGNAL_INTERVAL)

# ---------- اجرای بات و حلقه ----------
if __name__ == "__main__":
    print("Starting bot...")
    t = threading.Thread(target=signal_loop, daemon=True)
    t.start()
    bot.infinity_polling()
