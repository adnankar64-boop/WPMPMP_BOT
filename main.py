import os
import time
import threading
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import telebot
from flask import Flask, request

# ---------- تنظیمات ----------
BOT_TOKEN = "7762972292:AAEkDx853saWRuDpo59TwN_Wa0uW1mY-AIo"

DATABASE_URL =postgresql://wallet_wpmpmp_user:j9LnormdUlaiWsf36sMTmM79nMXeITRm@dpg-d2dqf0ripnbc739eva90-a/wallet_wpmpmp
ETHERSCAN_API_KEY = "VZFDUWB3YGQ1YCDKTCU1D6DDSS"

COINGLASS_API_KEY = "6e5da618d74344f69c0e77ad9b3643c0"

if not BOT_TOKEN or not DATABASE_URL or not ETHERSCAN_API_KEY or not COINGLASS_API_KEY:
    print("⚠️ لطفا متغیرهای محیطی BOT_TOKEN, DATABASE_URL, ETHERSCAN_API_KEY, COINGLASS_API_KEY را تنظیم کنید.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

CHECK_INTERVAL = 600    # هر 10 دقیقه
SIGNAL_INTERVAL = 3600  # هر 1 ساعت

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
        coin_type VARCHAR(10) NOT NULL,  -- 'eth' یا 'sol'
        UNIQUE(user_id, wallet_address),
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
    );
    """)
    conn.commit()
    conn.close()

def add_user_if_not_exists(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (user_id,))
    conn.commit()
    conn.close()

def add_wallet(user_id, wallet, coin_type):
    add_user_if_not_exists(user_id)
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO wallets (user_id, wallet_address, coin_type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
            (user_id, wallet, coin_type)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[DB ADD WALLET ERROR]: {e}")
        return False
    finally:
        conn.close()

def remove_wallet(user_id, wallet):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM wallets WHERE user_id = %s AND wallet_address = %s;", (user_id, wallet))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0

def get_user_wallets(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT wallet_address, coin_type FROM wallets WHERE user_id = %s;", (user_id,))
    rows = cur.fetchall()
    conn.close()
    wallets = {"eth": [], "sol": []}
    for row in rows:
        if row["coin_type"] == "eth":
            wallets["eth"].append(row["wallet_address"])
        elif row["coin_type"] == "sol":
            wallets["sol"].append(row["wallet_address"])
    return wallets

def reset_user_wallets(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM wallets WHERE user_id = %s;", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    rows = cur.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]

# ---------- APIها ----------

def get_eth_balance(wallet):
    try:
        url = "https://api.etherscan.io/api"
        params = {
            "module": "account",
            "action": "balance",
            "address": wallet,
            "tag": "latest",
            "apikey": ETHERSCAN_API_KEY
        }
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        balance = int(data.get("result", 0)) / 1e18
        return balance
    except Exception as e:
        print(f"[ETH BAL ERROR] {e}")
        return 0.0

def get_sol_balance(wallet):
    try:
        url = f"https://public-api.solscan.io/account/{wallet}"
        headers = {"accept": "application/json"}
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        lamports = data.get("lamports", 0)
        return lamports / 1e9
    except Exception as e:
        print(f"[SOL BAL ERROR] {e}")
        return 0.0

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
            eth_value = int(tx["value"]) / 1e10
            if eth_value >= 1:
                alerts.append(
                    f"🚨 تراکنش بزرگ شناسایی شد\n💰 {eth_value:.2f} ETH\n🔗 https://etherscan.io/tx/{tx['hash']}"
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
            sol = lamports / 1e4
            if sol >= 5:
                alerts.append(
                    f"🚨 تراکنش بزرگ شناسایی شد\n💰 {sol:.2f} SOL\n🔗 https://solscan.io/tx/{tx['txHash']}"
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
        res.raise_for_status()
        data = res.json()
        if not data.get("success"):
            print(f"[COINGLASS ERROR] {data.get('message')}")
            return []
        return data.get("data", [])
    except Exception as e:
        print(f"[COINGLASS EXCEPTION] {e}")
        return []

# ---------- حلقه‌ها ----------

def signal_loop():
    while True:
        data = get_long_short_ratios()
        alerts = []
        for item in data:
            symbol = item.get("symbol", "")
            ratio = float(item.get("longShortRatio", 0))
            if ratio > 1.5:
                alerts.append(f"📈 LONG: {symbol} – {ratio:.2f}")
            elif ratio < 0.7:
                alerts.append(f"📉 SHORT: {symbol} – {ratio:.2f}")

        msg = "📊 سیگنال بازار (هر 1 ساعت):\n\n" + ("\n".join(alerts) if alerts else "❌ سیگنال خاصی یافت نشد.")
        user_ids = get_all_users()
        for uid in user_ids:
            try:
                bot.send_message(int(uid), msg)
            except Exception as e:
                print(f"[Telegram send error to {uid}]: {e}")

        time.sleep(SIGNAL_INTERVAL)

def monitor_wallets():
    while True:
        user_ids = get_all_users()
        for uid in user_ids:
            wallets = get_user_wallets(uid)
            for eth in wallets.get("eth", []):
                alerts = get_large_eth_tx(eth)
                for alert in alerts:
                    try:
                        bot.send_message(int(uid), alert)
                    except Exception as e:
                        print(f"[Telegram send error to {uid}]: {e}")

            for sol in wallets.get("sol", []):
                alerts = get_large_sol_tx(sol)
                for alert in alerts:
                    try:
                        bot.send_message(int(uid), alert)
                    except Exception as e:
                        print(f"[Telegram send error to {uid}]: {e}")

        time.sleep(CHECK_INTERVAL)

# ---------- دستورات بات ----------

@bot.message_handler(commands=['start'])
def cmd_start(msg):
    uid = msg.chat.id
    add_user_if_not_exists(uid)
    bot.send_message(uid, "✅ ربات فعال شد.\n📌 آدرس کیف پول خود را ارسال کنید.")

@bot.message_handler(commands=['reset'])
def cmd_reset(msg):
    uid = msg.chat.id
    reset_user_wallets(uid)
    bot.send_message(uid, "♻️ تمام داده‌ها پاک شدند.")

@bot.message_handler(commands=['wallets'])
def cmd_wallets(msg):
    uid = msg.chat.id
    wallets = get_user_wallets(uid)
    if not wallets["eth"] and not wallets["sol"]:
        bot.send_message(uid, "❌ هیچ کیف پولی ثبت نشده.")
        return
    text = "📜 کیف پول‌های ثبت شده:\n\n"
    if wallets["eth"]:
        text += "💎 اتریوم:\n" + "\n".join(wallets["eth"]) + "\n\n"
    if wallets["sol"]:
        text += "🪙 سولانا:\n" + "\n".join(wallets["sol"]) + "\n"
    bot.send_message(uid, text)

@bot.message_handler(commands=['remove'])
def cmd_remove(msg):
    uid = msg.chat.id
    parts = msg.text.strip().split()
    if len(parts) != 2:
        bot.send_message(uid, "❌ فرمت صحیح:\n`/remove [آدرس]`", parse_mode="Markdown")
        return
    addr = parts[1]
    removed = remove_wallet(uid, addr)
    if removed:
        bot.send_message(uid, f"✅ آدرس حذف شد:\n{addr}")
    else:
        bot.send_message(uid, "❌ آدرس یافت نشد.")

@bot.message_handler(commands=['stats'])
def cmd_stats(msg):
    uid = msg.chat.id
    wallets
