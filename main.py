import os
import threading
import time
from datetime import datetime, timedelta
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager
from flask import Flask, request
import telebot
import logging

# ---------- تنظیمات محیط ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DB_URL", "").strip().replace('"', "").replace("'", "")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN معتبر نیست — مقدار درست را در Environment وارد کنید.")
if not DB_URL:
    raise ValueError("❌ DB_URL ست نشده است.")
if not RENDER_URL:
    raise ValueError("❌ RENDER_EXTERNAL_URL ست نشده است.")

# اطمینان از sslmode=require
if "sslmode" not in DB_URL:
    DB_URL += "&sslmode=require" if "?" in DB_URL else "?sslmode=require"

print(f"🔗 Final DB_URL = [{DB_URL}]")

bot = telebot.TeleBot(BOT_TOKEN)
telebot.logger.setLevel(logging.DEBUG)
app = Flask(__name__)

# ---------- Pool با تنظیمات keepalive ----------
pool = None

def create_pool():
    """ایجاد Connection Pool جدید با تنظیمات پایدار"""
    global pool
    pool = SimpleConnectionPool(
        1, 10,
        dsn=DB_URL,
        sslmode="require",
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3
    )
    print("✅ Connection Pool ساخته شد.")


# ---------- Safe Connection Manager ----------
def safe_get_conn():
    """دریافت اتصال سالم از pool؛ در صورت قطع، pool بازسازی می‌شود."""
    global pool
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        return conn
    except Exception as e:
        print(f"[POOL ERROR] {e} → بازسازی pool ...")
        try:
            pool.closeall()
        except:
            pass
        time.sleep(2)
        create_pool()
        conn = pool.getconn()
        return conn

@contextmanager
def get_db_conn():
    conn = safe_get_conn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


# ---------- ایجاد جداول ----------
def init_db():
    print("🧱 ایجاد یا بررسی جداول دیتابیس ...")
    with get_db_conn() as conn:
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
    print("✅ جداول آماده هستند.")


# ---------- دستورات تلگرام ----------
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO NOTHING;",
                    (chat_id,)
                )
                conn.commit()
        bot.send_message(chat_id, "🟢 ربات فعال شد!\nبرای افزودن کیف پول:\n`/addwallet chain address`", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در ثبت کاربر: {e}")


@bot.message_handler(commands=['addwallet'])
def add_wallet(message):
    try:
        parts = message.text.split()
        if len(parts) < 3:
            bot.send_message(message.chat.id, "❌ فرمت درست: /addwallet chain address")
            return

        blockchain, address = parts[1], parts[2]
        chat_id = message.chat.id

        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO wallets (user_id, blockchain, address) VALUES (%s, %s, %s);",
                    (chat_id, blockchain.lower(), address)
                )
                conn.commit()
        bot.send_message(chat_id, f"✅ کیف پول {address} روی {blockchain.upper()} اضافه شد!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطا در افزودن کیف پول: {e}")


# ---------- حلقه سیگنال ----------
last_sent_signals = {}

def fetch_signals(blockchain, address):
    # داده نمونه (میتوان API واقعی جایگزین کرد)
    return f"📡 سیگنال {blockchain.upper()} برای:\n🔗 {address}\n(نمونه تستی)"


def signal_loop():
    while True:
        try:
            with get_db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT chat_id FROM users;")
                    users = [r[0] for r in cur.fetchall()]

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
                                    cur.execute(
                                        "INSERT INTO signals (wallet_id, signal) VALUES (%s, %s);",
                                        (wallet_id, signal_text)
                                    )
                                    conn.commit()
                                except Exception as e:
                                    print(f"[SEND ERROR] {e}")

                    # حذف سیگنال‌های قدیمی‌تر از ۲۴ ساعت
                    cur.execute("DELETE FROM signals WHERE created_at < NOW() - INTERVAL '24 HOURS';")
                    conn.commit()
        except psycopg2.OperationalError as e:
            print(f"[DB CONNECTION LOST] {e} → retrying in 5s...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"[LOOP ERROR] {e}")

        time.sleep(60)


# ---------- Flask + Webhook ----------
@app.route("/" + BOT_TOKEN, methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
    bot.process_new_updates([update])
    return "OK", 200


@app.route("/")
def index():
    return "🤖 Bot is running!", 200


# ---------- اجرای برنامه ----------
if __name__ == "__main__":
    create_pool()
    init_db()

    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")
    print("🚀 Bot started with webhook:", f"{RENDER_URL}/{BOT_TOKEN}")

    # اجرای حلقه سیگنال در بک‌گراند
    threading.Thread(target=signal_loop, daemon=True).start()

    # اجرای Flask
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
