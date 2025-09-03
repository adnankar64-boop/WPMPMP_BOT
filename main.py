import threading
import time
from datetime import datetime, timedelta
import requests
import psycopg2
import telebot

# ---------- تنظیمات ----------
BOT_TOKEN = "اینجا_توکن_بات"
ETHERSCAN_API_KEY = "اینجا_کلید_Etherscan"
DEXCHECK_API_KEY = "اینجا_کلید_DexCheck"
COINGLASS_API_KEY = "اینجا_کلید_Coinglass"

DB_URL = "postgresql://user:pass@localhost:5432/dbname"  # دیتابیس Postgres
SIGNAL_INTERVAL = 60  # ثانیه
ETH_THRESHOLD = 1 * 10**18     # فقط بالای 1 ETH
SOL_THRESHOLD = 50             # فقط بالای 50 SOL
DEXCHECK_THRESHOLD = 100000    # فقط معاملات نهنگ‌ها بالای 100k$

bot = telebot.TeleBot(BOT_TOKEN)

# ---------- اتصال به دیتابیس ----------
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

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
    wallet_id INT,
    uniq_key TEXT,
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
    bot.send_message(chat_id, "🟢 ربات استارت شد!\n➕ با /addwallet کیف‌پول اضافه کن.\n📋 با /listwallets لیست کیف‌پول‌ها رو ببین.\n❌ با /removewallet کیف‌پول حذف کن.")

@bot.message_handler(commands=['addwallet'])
def add_wallet(message):
    try:
        parts = message.text.split()
        blockchain, address = parts[1].lower(), parts[2]
        chat_id = message.chat.id
        cur.execute("INSERT INTO wallets (user_id, blockchain, address) VALUES (%s, %s, %s);",
                    (chat_id, blockchain, address))
        conn.commit()
        bot.send_message(chat_id, f"✅ کیف پول {address} روی {blockchain} اضافه شد!")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا: {e}")

@bot.message_handler(commands=['listwallets'])
def list_wallets(message):
    chat_id = message.chat.id
    cur.execute("SELECT blockchain, address FROM wallets WHERE user_id=%s;", (chat_id,))
    rows = cur.fetchall()
    if not rows:
        bot.send_message(chat_id, "❌ هیچ کیف‌پولی ثبت نشده است.")
    else:
        msg = "📋 لیست کیف‌پول‌های شما:\n\n"
        for chain, addr in rows:
            msg += f"🔗 {chain.upper()} → `{addr}`\n"
        bot.send_message(chat_id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['removewallet'])
def remove_wallet(message):
    try:
        parts = message.text.split()
        blockchain, address = parts[1].lower(), parts[2]
        chat_id = message.chat.id
        cur.execute("DELETE FROM wallets WHERE user_id=%s AND blockchain=%s AND address=%s;",
                    (chat_id, blockchain, address))
        conn.commit()
        bot.send_message(chat_id, f"🗑️ کیف پول {address} روی {blockchain} حذف شد!")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا: {e}")

# ---------- توابع API ----------
def get_eth_transactions(address):
    url = "https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "sort": "desc",
        "apikey": ETHERSCAN_API_KEY
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        return r.json().get("result", []) if r.status_code == 200 else []
    except:
        return []

def get_sol_transactions(address):
    url = f"https://public-api.solscan.io/account/tokens?account={address}"
    try:
        r = requests.get(url, timeout=10)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def get_dexcheck_whales():
    url = "https://api.dexcheck.ai/v1/whales/trades"
    headers = {"x-api-key": DEXCHECK_API_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def get_hyperliquid_data():
    url = "https://api.hyperliquid.xyz/info"
    try:
        r = requests.get(url, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

def get_coinglass_data(symbol="BTC"):
    url = f"https://open-api.coinglass.com/api/pro/v1/futures/openInterest?symbol={symbol}"
    headers = {"coinglassSecret": COINGLASS_API_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

# ---------- ارسال سیگنال ----------
def send_signal(chat_id, wallet_id, uniq_key, text):
    cur.execute("SELECT 1 FROM signals WHERE uniq_key=%s AND created_at > NOW() - INTERVAL '24 HOURS';",
                (uniq_key,))
    if cur.fetchone():
        return
    bot.send_message(chat_id, text)
    cur.execute("INSERT INTO signals (wallet_id, uniq_key, signal) VALUES (%s, %s, %s);",
                (wallet_id, uniq_key, text))
    conn.commit()

# ---------- حلقه سیگنال ----------
def signal_loop():
    last_hyper_sent = datetime.now() - timedelta(minutes=10)

    while True:
        try:
            cur.execute("SELECT chat_id FROM users;")
            users = [row[0] for row in cur.fetchall()]

            for chat_id in users:
                cur.execute("SELECT id, blockchain, address FROM wallets WHERE user_id=%s;", (chat_id,))
                wallets = cur.fetchall()

                for wallet_id, blockchain, address in wallets:
                    if blockchain == "eth":
                        txs = get_eth_transactions(address)
                        if txs:
                            tx = txs[0]
                            if int(tx['value']) > ETH_THRESHOLD:
                                uniq = f"eth_{tx['hash']}"
                                msg = f"🟣 تراکنش بزرگ Ethereum\n🔗 {address}\n💰 مقدار: {int(tx['value'])/10**18} ETH"
                                send_signal(chat_id, wallet_id, uniq, msg)

                    elif blockchain == "sol":
                        txs = get_sol_transactions(address)
                        if txs:
                            balance = sum([float(t.get("tokenAmount", 0)) for t in txs])
                            if balance > SOL_THRESHOLD:
                                uniq = f"sol_{datetime.now().timestamp()}"
                                msg = f"🟡 تراکنش بزرگ Solana\n🔗 {address}\n💰 موجودی: {balance} SOL"
                                send_signal(chat_id, wallet_id, uniq, msg)

                # --- DexCheck ---
                whales = get_dexcheck_whales()
                if whales and isinstance(whales, list):
                    for w in whales[:5]:
                        try:
                            usd = float(w.get("amountUsd", 0))
                            if usd > DEXCHECK_THRESHOLD:
                                uniq = f"dex_{w.get('txHash')}"
                                msg = f"🐋 Whale Trade (DexCheck)\n🪙 {w.get('symbol')} | 💵 {usd}$"
                                send_signal(chat_id, 0, uniq, msg)
                        except:
                            continue

                # --- Hyperliquid (هر 5 دقیقه یکبار) ---
                if datetime.now() - last_hyper_sent > timedelta(minutes=5):
                    hyper = get_hyperliquid_data()
                    if hyper:
                        uniq = f"hyper_{datetime.now().strftime('%H%M')}"
                        msg = f"🌐 Hyperliquid Update\n📊 {str(hyper)[:200]}..."
                        send_signal(chat_id, 0, uniq, msg)
                        last_hyper_sent = datetime.now()

                # --- Coinglass ---
                cg = get_coinglass_data("BTC")
                if cg and cg.get("data"):
                    oi = cg['data'][0].get("openInterest", 0)
                    if float(oi) > 1000000000:
                        uniq = f"cg_{datetime.now().strftime('%H%M')}"
                        msg = f"📈 Coinglass BTC OI\n💰 {oi}"
                        send_signal(chat_id, 0, uniq, msg)

        except Exception as e:
            print("⚠️ Loop error:", e)

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
