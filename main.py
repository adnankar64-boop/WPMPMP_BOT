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

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

CHECK_INTERVAL = 600    # هر 10 دقیقه
SIGNAL_INTERVAL = 60    # برای تست سریع، بعدا 3600

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

# ---------- API ها ----------
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
        txs = res.json().get("result", [])[:5]
        alerts = []
        for tx in txs:
            eth_value = int(tx["value"]) / 1e18
            if eth_value >= 0.01:  # تست سریع، بعدا می‌توان به 1 برگرداند
                alerts.append(
                    f"🚨 تراکنش بزرگ ETH شناسایی شد\n💰 {eth_value:.4f} ETH\n🔗 https://etherscan.io/tx/{tx['hash']}"
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
        alerts = []
        for tx in txs:
            lamports = tx.get("lamport", 0)
            sol = lamports / 1e9
            if sol >= 0.1:  # تست سریع، بعدا 5
                alerts.append(
                    f"🚨 تراکنش بزرگ SOL شناسایی شد\n💰 {sol:.4f} SOL\n🔗 https://solscan.io/tx/{tx['txHash']}"
                )
        return alerts
    except Exception as e:
        print(f"[SOL TX ERROR] {e}")
        return []

def get_long_short_ratios():
    url = "https://open-api.coinglass.com/public/v2/longShortRatio"
    headers = {"coinglassSecret": COINGLASS_API_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        data = res.json()
        print("[DEBUG] Coinglass response:", data)
        if not data.get("success"):
            print(f"[COINGLASS ERROR] {data.get('message')}")
            return []
        return data.get("data", [])
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

        # ---------- Coinglass ----------
        data = get_long_short_ratios()
        for item in data:
            symbol = item.get("symbol", "")
            ratio = float(item.get("longShortRatio", 0))
            if ratio > 1.5:
                alerts.append(f"📈 LONG: {symbol} – {ratio:.2f}")
            elif ratio < 0.7:
                alerts.append(f"📉 SHORT: {symbol} – {ratio:.2f}")

        # ---------- Wallet Transactions ----------
        for uid in get_all_users():
            wallets = get_user_wallets(uid)

            for eth_wallet in wallets["eth"]:
                alerts.extend(get_large_eth_tx(eth_wallet))

            for sol_wallet in wallets["sol"]:
                alerts.extend(get_large_sol_tx(sol_wallet))

        # ---------- پیام آزمایشی ----------
        alerts.append("🟢 پیام آزمایشی: ربات در حال کار است!")

        # ---------- ارسال پیام ----------
        msg = "📊 سیگنال بازار:\n\n" + ("\n".join(alerts) if alerts else "❌ سیگنالی یافت نشد.")
        for uid in get_all_users():
            try:
                bot.send_message(chat_id=int(uid), text=msg)
            except Exception as e:
                print(f"[Telegram send error to {uid}]: {e}")

        time.sleep(SIGNAL_INTERVAL)

def start_signal_thread():
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

# ---------- اجرای برنامه ----------
if __name__ == "__main__":
    init_db()
    start_signal_thread()
    print("[BOT] Sending startup message")
    for uid in get_all_users():
        try:
            bot.send_message(chat_id=int(uid), text="✅ Bot restarted successfully and is live on Render!")
        except:
            pass
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
