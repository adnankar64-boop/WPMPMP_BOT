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
DATABASE_URL = DATABASE_UR ="postgresql://wallet_wpmpmp_user:j9LnormdUlaiWsf36sMTmM79nMXeITRm@dpg-d2dqf0ripnbc739eva90-a/wallet_wpmpmp"

ETHERSCAN_API_KEY = "VZFDUWB3YGQ1YCDKTCU1D6DDSS"
COINGLASS_API_KEY = "7e13609fdaab455c91f77634b271ae1e"

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
        coin_type VARCHAR(10) NOT NULL,
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
        txs = res.json().get("result", [])[:5]
        alerts = []
        for tx in txs:
            eth_value = int(tx["value"]) / 1e18  # تبدیل دقیق Wei به ETH
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
            sol = lamports / 1e9  # تبدیل دقیق lamports به SOL
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
        print("[Coinglass raw response]", res.text)  # برای دیباگ
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

        msg = "📊 سیگنال بازار:\n\n" + ("\n".join(alerts) if alerts else "❌ سیگنال خاصی یافت نشد.")
        for uid in get_all_users():
            try:
                bot.send_message(int(uid), msg)
            except Exception as e:
                print(f"[Telegram send error to {uid}]: {e}")

        time.sleep(SIGNAL_INTERVAL)

def monitor_wallets():
    while True:
        for uid in get_all_users():
            wallets = get_user_wallets(uid)
            for eth in wallets.get("eth", []):
                for alert in get_large_eth_tx(eth):
                    bot.send_message(int(uid), alert)
            for sol in wallets.get("sol", []):
                for alert in get_large_sol_tx(sol):
                    bot.send_message(int(uid), alert)
        time.sleep(CHECK_INTERVAL)

# ---------- اجرای اصلی ----------
if __name__ == "__main__":
    init_db()
    threading.Thread(target=signal_loop, daemon=True).start()
    threading.Thread(target=monitor_wallets, daemon=True).start()
    print("Bot started. Waiting for events...")

    bot.remove_webhook()  # ✅ این خط برای حذف Webhook ضروریه
    bot.polling(none_stop=True)  # ✅ شروع Polling

