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

SIGNAL_INTERVAL = 3600  # ارسال سیگنال هر 1 ساعت

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

def get_users_with_wallets():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT u.user_id
        FROM users u
        JOIN wallets w ON u.user_id = w.user_id
    """)
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
        txs = res.json().get("result", [])[:5]
        alerts = []
        for tx in txs:
            eth_value = int(tx["value"]) / 1e18
            if eth_value >= 1:
                alerts.append(f"🚨 تراکنش بزرگ شناسایی شد\n💰 {eth_value:.2f} ETH\n🔗 https://etherscan.io/tx/{tx['hash']}")
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
            if sol >= 5:
                alerts.append(f"🚨 تراکنش بزرگ شناسایی شد\n💰 {sol:.2f} SOL\n🔗 https://solscan.io/tx/{tx['txHash']}")
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
        if not data.get("success"):
            print(f"[COINGLASS ERROR] {data.get('message')}")
            return []
        return data.get("data", [])
    except Exception as e:
        print(f"[COINGLASS EXCEPTION] {e}")
        return []

# ---------- حلقه سیگنال بهینه ----------
last_sent_signals = set()

def signal_loop():
    global last_sent_signals
    print("[LOOP] signal_loop started")
    while True:
        alerts = []

        # ---------- Coinglass ----------
        data = get_long_short_ratios()
        for item in data:
            symbol = item.get("symbol", "")
            ratio = float(item.get("longShortRatio", 0))
            if ratio > 1.5:
                alert = f"📈 LONG: {symbol} – {ratio:.2f}"
            elif ratio < 0.7:
                alert = f"📉 SHORT: {symbol} – {ratio:.2f}"
            else:
                continue
            if alert not in last_sent_signals:
                alerts.append(alert)
                last_sent_signals.add(alert)

        # ---------- Wallet Transactions ----------
        for uid in get_users_with_wallets():
            wallets = get_user_wallets(uid)
            for eth_wallet in wallets["eth"]:
                for alert in get_large_eth_tx(eth_wallet):
                    if alert not in last_sent_signals:
                        alerts.append(alert)
                        last_sent_signals.add(alert)
            for sol_wallet in wallets["sol"]:
                for alert in get_large_sol_tx(sol_wallet):
                    if alert not in last_sent_signals:
                        alerts.append(alert)
                        last_sent_signals.add(alert)

        # ---------- ارسال پیام ----------
        if alerts:
            msg = "📊 سیگنال بازار:\n\n" + "\n".join(alerts)
            for uid in get_users_with_wallets():
                try:
                    bot.send_message(chat_id=int(uid), text=msg)
                except Exception as e:
                    print(f"[Telegram send error to {uid}]: {e}")
        else:
            print("[LOOP] هیچ سیگنالی جدیدی یافت نشد.")

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
    for uid in get_users_with_wallets():
        try:
            bot.send_message(chat_id=int(uid), text="✅ Bot restarted successfully and is live on Render!")
        except:
            pass
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
