import os
import threading
import time
from datetime import datetime, timedelta
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from flask import Flask, request
import telebot
import logging

# ---------- تنظیمات ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DB_URL")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")  # مثلا: https://your-app.onrender.com

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN معتبر نیست. مقدار درست رو در Environment Render وارد کن.")

if not DB_URL:
    raise ValueError("❌ DB_URL در Environment ست نشده است. باید از Render Postgres استفاده کنی.")

if not RENDER_URL:
    raise ValueError("❌ RENDER_EXTERNAL_URL در Environment ست نشده است. (مثال: https://your-app.onrender.com)")

bot = telebot.TeleBot(BOT_TOKEN)
telebot.logger.setLevel(logging.DEBUG)

# ---------- اتصال به دیتابیس (connection pool) ----------
pool = SimpleConnectionPool(1, 10, DB_URL)

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

# ---------- ایجاد جدول‌ها ----------
with get_conn() as conn:
    with conn.cursor() as cur:
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO NOTHING;", (chat_id,))
    conn.commit()
    cur.close()
    put_conn(conn)
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

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO wallets (user_id, blockchain, address) VALUES (%s, %s, %s);",
                    (chat_id, blockchain.lower(), address))
        conn.commit()
        cur.close()
        put_conn(conn)

        bot.send_message(chat_id, f"✅ کیف پول {address} روی {blockchain} اضافه شد!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطا در افزودن کیف پول: {e}")

# ---------- حلقه سیگنال ----------
last_sent_signals = {}

def fetch_signals(blockchain, address):
    # اینجا می‌تونی API واقعی اضافه کنی
    return f"📡 سیگنال {blockchain.upper()} برای:\n🔗 {address}\n(داده نمونه)"

def signal_loop():
    while True:
        try:
            conn = get_conn()
            cur = conn.cursor()

            cur.execute("SELECT chat_id FROM users;")
            users = [row[0] for row in cur.fetchall()]

            for chat_id in users:
                cur.execute("SELECT id, blockchain, address FROM wallets WHERE user_id=%s;", (chat_id,))
                wallets = cur.fetchall()

                for wallet_id, blockchain, address in wallets:
                    signal_text = fetch_signals(blockchain, address)
                    last_time = last_sent_signals.get((chat_id, wallet_id))

                    if not last_time or datetime.now() - last_time > timedelta(hours=6):
                        try:
                            bot.send_message(chat_id, signal_text)
                            last_sent_signals[(chat_id, wallet_id)] = datetime.now()
                            cur.execute("INSERT INTO signals (wallet_id, signal) VALUES (%s, %s);",
                                        (wallet_id, signal_text))
                            conn.commit()
                        except Exception as e:
                            print(f"[SEND ERROR] {e}")

            # پاک کردن سیگنال‌های قدیمی
            cur.execute("DELETE FROM signals WHERE created_at < NOW() - INTERVAL '24 HOURS';")
            conn.commit()

            cur.close()
            put_conn(conn)

        except Exception as e:
            print(f"[LOOP ERROR] {e}")

        time.sleep(60)

# ---------- Flask + Webhook ----------
app = Flask(__name__)

@app.route("/" + BOT_TOKEN, methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/")
def index():
    return "🤖 Bot is running!", 200

# ---------- اجرای برنامه ----------
if __name__ == "__main__":
    # ست کردن وبهوک
    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")

    print("🚀 Bot starting with webhook...")

    # اجرای حلقه سیگنال در بک‌گراند
    t = threading.Thread(target=signal_loop, daemon=True)
    t.start()

    # اجرای Flask
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
