import os
import time
import threading
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import telebot
from flask import Flask, request

# ---------- تنظیمات ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY")
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY")

# آدرس وبهوک:
# 1) اگر خودت WEBHOOK_URL را ست کنی، همان استفاده می‌شود.
# 2) در غیر این صورت، اگر RENDER_EXTERNAL_URL موجود باشد از آن + /<BOT_TOKEN> ساخته می‌شود.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
if not WEBHOOK_URL:
    _render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if _render_url and BOT_TOKEN:
        WEBHOOK_URL = _render_url.rstrip("/") + "/" + BOT_TOKEN

if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN تنظیم نشده است.")
if not DATABASE_URL:
    raise RuntimeError("Environment variable DATABASE_URL تنظیم نشده است.")

bot = telebot.TeleBot(BOT_TOKEN)  # می‌تونی parse_mode="HTML" هم بدی در صورت نیاز
app = Flask(__name__)

CHECK_INTERVAL = 600    # هر 10 دقیقه (فعلاً استفاده نشده؛ برای توسعه‌های بعدی)
SIGNAL_INTERVAL = 10  # فقط برای تست


# ---------- دیتابیس ----------
def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallets (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        wallet_address TEXT NOT NULL,
        coin_type VARCHAR(10) NOT NULL,
        UNIQUE(user_id, wallet_address),
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
    );
    """)
    conn.commit()
    conn.close()
    print("[DB] Tables ensured.")

def add_user_if_not_exists(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    rows = cur.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]

def get_user_wallets(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT wallet_address, coin_type FROM wallets WHERE user_id = %s;", (user_id,))
    rows = cur.fetchall()
    conn.close()
    wallets = {"eth": [], "sol": []}
    for row in rows:
        wallets[row["coin_type"]].append(row["wallet_address"])
    return wallets

# ---------- APIها ----------
def get_large_eth_tx(wallet):
    url = "https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "txlist",
        "address": wallet,
        "startblock": 0,
        "endblock": 99999999,
        "sort": "desc",
        "apikey": ETHERSCAN_API_KEY
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        data = res.json()
        txs = data.get("result", [])[:5] if isinstance(data, dict) else []
        alerts = []
        for tx in txs:
            # بعضی وقت‌ها value رشته‌ای خیلی بزرگه
            try:
                eth_value = int(tx.get("value", "0")) / 1e18
            except Exception:
                eth_value = 0.0
            if eth_value >= 1:
                alerts.append(
                    f"🚨 تراکنش بزرگ شناسایی شد\n💰 {eth_value:.2f} ETH\n🔗 https://etherscan.io/tx/{tx.get('hash')}"
                )
        return alerts
    except Exception as e:
        print(f"[ETH TX ERROR] {e}")
        return []

def get_large_sol_tx(wallet):
    url = f"https://public-api.solscan.io/account/transactions?account={wallet}&limit=5"
    headers = {"accept": "application/json"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        txs = res.json()
        if not isinstance(txs, list):
            return []
        alerts = []
        for tx in txs:
            lamports = tx.get("lamport", 0) or 0
            sol = lamports / 1e9
            if sol >= 5:
                alerts.append(
                    f"🚨 تراکنش بزرگ شناسایی شد\n💰 {sol:.2f} SOL\n🔗 https://solscan.io/tx/{tx.get('txHash')}"
                )
        return alerts
    except Exception as e:
        print(f"[SOL TX ERROR] {e}")
        return []

def get_long_short_ratios():
    url = "https://open-api.coinglass.com/public/v2/longShortRatio"
    headers = {"coinglassSecret": COINGLASS_API_KEY or ""}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        data = res.json()
        print("[DEBUG] Coinglass response:", data)
        # ساختار پاسخ Coinglass ممکنه تغییر کنه؛ با محافظه‌کاری برخورد می‌کنیم
        if isinstance(data, dict):
            ok = data.get("success")
            if ok is False:
                print(f"[COINGLASS ERROR] {data.get('message')}")
                return []
            return data.get("data", []) or []
        return []
    except Exception as e:
        print(f"[COINGLASS EXCEPTION] {e}")
        return []

# ---------- پیام‌های بات ----------
@bot.message_handler(commands=["start"])
def handle_start(message):
    user_id = message.chat.id
    add_user_if_not_exists(user_id)
    bot.reply_to(message, "سلام! به ربات خوش آمدی 😊\n\nبرای افزودن کیف پول:\n/addwallet eth 0x...\nیا فقط:\n/addwallet 0x...\n\nبرای حذف کیف پول:\n/removewallet 0x...\n\nبرای دیدن کیف پول‌ها:\n/mywallets")

@bot.message_handler(commands=["addwallet"])
def handle_add_wallet(message):
    try:
        user_id = message.chat.id
        add_user_if_not_exists(user_id)
        parts = message.text.strip().split()

        if len(parts) == 2:
            wallet_address = parts[1]
            if wallet_address.startswith("0x") and len(wallet_address) == 42:
                coin_type = "eth"
            elif len(wallet_address) >= 32:
                coin_type = "sol"
            else:
                bot.reply_to(message, "❌ نوع کیف پول مشخص نیست.")
                return
        elif len(parts) == 3:
            coin_type = parts[1].lower()
            wallet_address = parts[2]
            if coin_type not in ["eth", "sol"]:
                bot.reply_to(message, "❌ نوع کیف پول فقط می‌تواند eth یا sol باشد.")
                return
        else:
            bot.reply_to(message, "❌ فرمت اشتباه.")
            return

        if coin_type == "eth" and not wallet_address.startswith("0x"):
            bot.reply_to(message, "❌ آدرس ETH اشتباه است.")
            return
        if coin_type == "sol" and len(wallet_address) < 20:
            bot.reply_to(message, "❌ آدرس SOL معتبر نیست.")
            return

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO wallets (user_id, wallet_address, coin_type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
            (user_id, wallet_address, coin_type)
        )
        conn.commit()
        conn.close()

        bot.reply_to(message, f"✅ کیف پول ثبت شد:\n{wallet_address} ({coin_type.upper()})")
    except Exception as e:
        print(f"[ADD WALLET ERROR] {e}")
        bot.reply_to(message, "❌ خطا در افزودن کیف پول.")

@bot.message_handler(commands=["removewallet"])
def handle_remove_wallet(message):
    try:
        user_id = message.chat.id
        parts = message.text.strip().split()
        if len(parts) == 2:
            wallet_address = parts[1]
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM wallets WHERE user_id = %s AND wallet_address = %s;", (user_id, wallet_address))
            deleted = cur.rowcount
            conn.commit()
            conn.close()

            if deleted:
                bot.reply_to(message, f"✅ کیف پول حذف شد:\n{wallet_address}")
            else:
                bot.reply_to(message, "❌ این کیف پول برای شما ثبت نشده است.")
        else:
            bot.reply_to(message, "❌ فرمت اشتباه.")
    except Exception as e:
        print(f"[REMOVE WALLET ERROR] {e}")
        bot.reply_to(message, "❌ خطا در حذف کیف پول.")

@bot.message_handler(commands=["mywallets"])
def handle_my_wallets(message):
    user_id = message.chat.id
    wallets = get_user_wallets(user_id)
    if not wallets["eth"] and not wallets["sol"]:
        bot.reply_to(message, "شما هنوز هیچ کیف پولی ثبت نکرده‌اید.")
        return

    msg = "💼 کیف پول‌های ثبت‌شده:\n"
    if wallets["eth"]:
        msg += "\n🔷 ETH:\n" + "\n".join(wallets["eth"])
    if wallets["sol"]:
        msg += "\n🟡 SOL:\n" + "\n".join(wallets["sol"])

    bot.reply_to(message, msg)

@bot.message_handler(func=lambda message: True)
def handle_unknown(message):
    bot.reply_to(message, "❓ دستور ناشناخته است.")

# ---------- حلقه سیگنال ----------
def signal_loop():
    print("[LOOP] signal_loop started")
    while True:
        alerts = []

        # نسبت لانگ/شورت
        data = get_long_short_ratios()
        for item in data:
            try:
                symbol = item.get("symbol", "")
                ratio = float(item.get("longShortRatio", 0))
                if ratio > 1.5:
                    alerts.append(f"📈 LONG: {symbol} – {ratio:.2f}")
                elif ratio < 0.7:
                    alerts.append(f"📉 SHORT: {symbol} – {ratio:.2f}")
            except Exception:
                continue

        # تراکنش‌های بزرگ والت‌های ثبت‌شده
        users = get_all_users()
        for uid in users:
            print(f"[LOOP] Checking wallets for user {uid}")
            wallets = get_user_wallets(uid)

            for eth_wallet in wallets["eth"]:
                eth_alerts = get_large_eth_tx(eth_wallet)
                alerts.extend(eth_alerts)

            for sol_wallet in wallets["sol"]:
                sol_alerts = get_large_sol_tx(sol_wallet)
                alerts.extend(sol_alerts)

        print(f"[LOOP] Sending {len(alerts)} alerts")
        msg = "📊 سیگنال بازار:\n\n" + ("\n".join(alerts) if alerts else "❌ سیگنالی یافت نشد.")
        for uid in get_all_users():
            try:
                bot.send_message(int(uid), msg)
            except Exception as e:
                print(f"[Telegram send error to {uid}]: {e}")

        time.sleep(SIGNAL_INTERVAL)

def start_signal_thread():
    """اجرای حلقه سیگنال در یک ترد جدا."""
    t = threading.Thread(target=signal_loop, daemon=True)
    t.start()
    print("[THREAD] signal_loop thread started")

# ---------- وبهوک ----------
@app.route('/' + BOT_TOKEN, methods=['POST'])
def getMessage():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/")
def index():
    return "Bot is running!", 200

@app.route("/healthz")
def healthz():
    return "ok", 200

def ensure_webhook():
    """وبهوک تلگرام را اگر لازم باشد ست/به‌روز می‌کند."""
    if not WEBHOOK_URL:
        print("[WEBHOOK] WEBHOOK_URL/RENDER_EXTERNAL_URL تعریف نشده؛ وبهوک ست نمی‌شود.")
        return

    try:
        info = bot.get_webhook_info()
        current = getattr(info, "url", "") if info else ""
    except Exception as e:
        print(f"[WEBHOOK] get_webhook_info error: {e}")
        current = ""

    if current != WEBHOOK_URL:
        try:
            bot.remove_webhook()
        except Exception:
            pass
        try:
            ok = bot.set_webhook(url=WEBHOOK_URL)
            print(f"[WEBHOOK] set_webhook -> {ok}, url={WEBHOOK_URL}")
        except Exception as e:
            print(f"[WEBHOOK] set_webhook error: {e}")
    else:
        print(f"[WEBHOOK] already set: {WEBHOOK_URL}")

# ---------- اجرای برنامه ----------
if __name__ == "__main__":
    import threading

    # اجرای لوپ سیگنال‌ها در یک ترد جداگانه
    signal_thread = threading.Thread(target=signal_loop, daemon=True)
    signal_thread.start()
    print("[THREAD] signal_loop thread started")

    # اطلاع‌رسانی شروع شدن ربات
    try:
        bot.send_message(chat_id=160584976, text="✅ Bot restarted successfully and is live on Render!")
    except Exception as e:
        print(f"[ERROR] Could not send startup message: {e}")

    # اجرای Flask
    app.run(host="0.0.0.0", port=10000)
