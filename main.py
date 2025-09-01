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
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz"
HYPERLIQUID_WALLET = os.environ.get("HYPERLIQUID_WALLET")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

SIGNAL_INTERVAL = 3600  # هر 1 ساعت
API_RETRY = 3
API_TIMEOUT = 15

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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS last_signals (
        id SERIAL PRIMARY KEY,
        signal_text TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def is_signal_sent(signal_text):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM last_signals WHERE signal_text = %s;", (signal_text,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def save_signal(signal_text):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO last_signals (signal_text) VALUES (%s) ON CONFLICT DO NOTHING;", (signal_text,))
    conn.commit()
    conn.close()

def clear_old_signals():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM last_signals WHERE created_at < NOW() - INTERVAL '24 hours';")
    conn.commit()
    conn.close()
    print("[DB] Old signals cleared.")

# ---------- توابع کمکی برای Retry ----------
def safe_get(url, params=None, headers=None):
    for attempt in range(API_RETRY):
        try:
            return requests.get(url, params=params, headers=headers, timeout=API_TIMEOUT).json()
        except Exception as e:
            print(f"[Retry {attempt+1}/{API_RETRY}] Error fetching {url}: {e}")
            time.sleep(2)
    return {}

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
    txs = safe_get(url, params).get("result", [])[:5]
    alerts = []
    for tx in txs:
        eth_value = int(tx.get("value", 0)) / 1e18
        if eth_value >= 1:
            alerts.append(f"🚨 ETH تراکنش بزرگ:\n💰 {eth_value:.2f} ETH\n🔗 https://etherscan.io/tx/{tx.get('hash')}")
    print(f"[ETH] {len(alerts)} alerts from {wallet}")
    return alerts

def get_large_sol_tx(wallet):
    url = f"https://public-api.solscan.io/account/transactions?account={wallet}&limit=5"
    headers = {"accept": "application/json"}
    txs = safe_get(url, headers=headers)
    alerts = []
    for tx in txs:
        lamports = tx.get("lamport", 0)
        sol = lamports / 1e9
        if sol >= 5:
            alerts.append(f"🚨 SOL تراکنش بزرگ:\n💰 {sol:.2f} SOL\n🔗 https://solscan.io/tx/{tx.get('txHash')}")
    print(f"[SOL] {len(alerts)} alerts from {wallet}")
    return alerts

def get_long_short_ratios():
    url = "https://open-api.coinglass.com/public/v2/longShortRatio"
    headers = {"coinglassSecret": COINGLASS_API_KEY}
    data = safe_get(url, headers=headers)
    if not data.get("success", False):
        print(f"[Coinglass ERROR] {data.get('message')}")
        return []
    print(f"[Coinglass] {len(data.get('data', []))} symbols fetched")
    return data.get("data", [])

def get_hyperliquid_signals():
    url = f"{HYPERLIQUID_API_URL}/v1/user/positions?wallet={HYPERLIQUID_WALLET}"
    data = safe_get(url)
    signals = []
    for pos in data.get("positions", []):
        size = float(pos.get("size", 0))
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        if size > 0:
            signals.append(f"📊 Hyperliquid {side.upper()} {symbol} – {size:.2f}")
    print(f"[Hyperliquid] {len(signals)} signals fetched")
    return signals

# ---------- پیام‌های بات ----------
@bot.message_handler(commands=["start"])
def handle_start(message):
    user_id = message.chat.id
    add_user_if_not_exists(user_id)
    bot.reply_to(message, "سلام! ربات فعال شد ✅\n\n📢 پیام آزمایشی: ربات در حال کار است!")

# ---------- حلقه سیگنال ----------
def signal_loop():
    print("[LOOP] signal_loop started")
    while True:
        clear_old_signals()
        alerts = []

        # Coinglass
        for item in get_long_short_ratios():
            symbol = item.get("symbol", "")
            ratio = float(item.get("longShortRatio", 0))
            if ratio > 1.5:
                alerts.append(f"📈 LONG: {symbol} – {ratio:.2f}")
            elif ratio < 0.7:
                alerts.append(f"📉 SHORT: {symbol} – {ratio:.2f}")

        # Hyperliquid
        alerts.extend(get_hyperliquid_signals())

        # Ethereum & Solana wallets
        for uid in get_all_users():
            wallets = get_user_wallets(uid)
            for eth_wallet in wallets["eth"]:
                alerts.extend(get_large_eth_tx(eth_wallet))
            for sol_wallet in wallets["sol"]:
                alerts.extend(get_large_sol_tx(sol_wallet))

        # حذف سیگنال‌های تکراری
        final_alerts = []
        for alert in alerts:
            if not is_signal_sent(alert):
                final_alerts.append(alert)
                save_signal(alert)

        msg = "📊 سیگنال بازار:\n\n" + ("\n".join(final_alerts) if final_alerts else "❌ سیگنالی یافت نشد.")
        for uid in get_all_users():
            try:
                bot.send_message(int(uid), msg)
            except Exception as e:
                print(f"[Telegram send error to {uid}]: {e}")

        print(f"[LOOP] Sent {len(final_alerts)} new alerts")
        time.sleep(SIGNAL_INTERVAL)

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
    threading.Thread(target=signal_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
