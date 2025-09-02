#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import threading
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import telebot
from flask import Flask, request
from datetime import datetime, timedelta

# ----------------------------
# تنظیمات (از env خوانده می‌شود)
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")
HYPERLIQUID_WALLET = os.getenv("HYPERLIQUID_WALLET")  # اختیاری

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN in environment is required")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL in environment is required")

# پارامترهای زمانی
SIGNAL_INTERVAL = int(os.getenv("SIGNAL_INTERVAL", 3600))  # پیش‌فرض هر 1 ساعت
SIGNAL_EXPIRY_HOURS = int(os.getenv("SIGNAL_EXPIRY_HOURS", 24))
API_RETRY = 3
API_TIMEOUT = 12

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ----------------------------
# دیتابیس
# ----------------------------
def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# helper های دیتابیس برای کار با کاربران و کیف پول
def add_user_if_not_exists(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING;", (int(user_id),))
    conn.commit()
    conn.close()

def save_wallet(user_id, wallet_address, coin_type):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO wallets (user_id, wallet_address, coin_type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
                (int(user_id), wallet_address, coin_type))
    conn.commit()
    conn.close()

def remove_wallet(user_id, wallet_address):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM wallets WHERE user_id = %s AND wallet_address = %s;", (int(user_id), wallet_address))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

def get_user_wallets(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT wallet_address, coin_type FROM wallets WHERE user_id = %s;", (int(user_id),))
    rows = cur.fetchall()
    conn.close()
    wallets = {"eth": [], "sol": []}
    for r in rows:
        wallets[r["coin_type"]].append(r["wallet_address"])
    return wallets

def get_all_users():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    rows = cur.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]

# last_signals table helpers
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

def clear_old_signals_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM last_signals WHERE created_at < NOW() - INTERVAL '%s hours';" % SIGNAL_EXPIRY_HOURS)
    conn.commit()
    conn.close()
    print("[DB] Old signals cleared from DB.")

# ----------------------------
# توابع درخواست وب (با retry)
# ----------------------------
def safe_get_json(url, params=None, headers=None):
    last_exc = None
    for attempt in range(API_RETRY):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=API_TIMEOUT)
            # اگر پاسخ JSON قابل پارس نیست، ممکنه خطا باشه
            return resp.json()
        except Exception as e:
            last_exc = e
            print(f"[HTTP ERROR] {url} attempt {attempt+1}/{API_RETRY}: {e}")
            time.sleep(1 + attempt)
    print(f"[HTTP FAILED] {url} => {last_exc}")
    return {}

# ----------------------------
# استخراج سیگنال‌ها از منابع مختلف
# ----------------------------
def get_large_eth_tx(wallet, min_eth=1):
    """Etherscan: خروجی txlist -> بررسی مقدار value"""
    url = "https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "txlist",
        "address": wallet,
        "startblock": 0,
        "endblock": 99999999,
        "sort": "desc",
        "apikey": ETHERSCAN_API_KEY or ""
    }
    data = safe_get_json(url, params=params)
    alerts = []
    for tx in data.get("result", [])[:10]:
        if not isinstance(tx, dict):
            continue
        # برخی ورودی‌ها ممکن است رشته باشند؛ همیشه تبدیل امن انجام می‌دهیم
        try:
            value = int(tx.get("value", 0))
        except Exception:
            continue
        eth_value = value / 1e18
        if eth_value >= min_eth:
            txhash = tx.get("hash") or tx.get("txHash") or "unknown"
            alerts.append(("eth_tx", f"🚨 تراکنش بزرگ ETH\n{eth_value:.4f} ETH\n🔗 https://etherscan.io/tx/{txhash}", txhash))
    print(f"[ETH] {len(alerts)} alerts for {wallet}")
    return alerts

def get_large_sol_tx(wallet, min_sol=5):
    """Solscan public api"""
    url = f"https://public-api.solscan.io/account/transactions?account={wallet}&limit=5"
    data = safe_get_json(url)
    alerts = []
    if isinstance(data, dict) and data.get("error"):
        # بعضی پاسخ‌ها ممکن است خطا باشند
        return alerts
    for tx in data if isinstance(data, list) else []:
        if not isinstance(tx, dict):
            continue
        # بعضی پاسخ‌ها ساختار متفاوتی دارند؛ جستجو برای lamports یا value
        lamports = tx.get("lamport") or tx.get("lamports") or tx.get("value", 0)
        try:
            lamports = int(lamports)
        except Exception:
            lamports = 0
        sol_value = lamports / 1e9
        if sol_value >= min_sol:
            txhash = tx.get("txHash") or tx.get("signature") or "unknown"
            alerts.append(("sol_tx", f"🚨 تراکنش بزرگ SOL\n{sol_value:.4f} SOL\n🔗 https://solscan.io/tx/{txhash}", txhash))
    print(f"[SOL] {len(alerts)} alerts for {wallet}")
    return alerts

def get_long_short_coinglass():
    """Coinglass public endpoint (may vary). Returns list of alerts."""
    url = "https://open-api.coinglass.com/public/v2/longShortRatio"
    headers = {"coinglassSecret": COINGLASS_API_KEY} if COINGLASS_API_KEY else {}
    data = safe_get_json(url, headers=headers)
    alerts = []
    # data might be { "success": True, "data": [...] }
    for item in data.get("data", []) if isinstance(data.get("data", []), list) else []:
        try:
            symbol = item.get("symbol")
            ratio = float(item.get("longShortRatio") or item.get("ratio") or 0)
            if ratio > 1.5:
                sig_text = f"📈 COINGLASS LONG: {symbol} – {ratio:.2f}"
                alerts.append(("coinglass_long", sig_text, f"coinglass-{symbol}-{ratio}"))
            elif ratio < 0.7:
                sig_text = f"📉 COINGLASS SHORT: {symbol} – {ratio:.2f}"
                alerts.append(("coinglass_short", sig_text, f"coinglass-{symbol}-{ratio}"))
        except Exception:
            continue
    print(f"[Coinglass] {len(alerts)} alerts")
    return alerts

def get_hyperliquid_signals():
    """نمونه‌ی خواندن از Hyperliquid — مسیر و پارامتر واقعی ممکن است متفاوت باشد"""
    if not HYPERLIQUID_WALLET:
        return []
    # This is an example endpoint; adjust if Hyperliquid API differs
    url = f"https://api.hyperliquid.xyz/v1/user/positions?wallet={HYPERLIQUID_WALLET}"
    data = safe_get_json(url)
    alerts = []
    for pos in data.get("positions", []) if isinstance(data.get("positions", []), list) else []:
        try:
            size = float(pos.get("size", 0))
            symbol = pos.get("symbol", "")
            side = pos.get("side", "")
            if size > 0:
                sig_id = f"hyper-{symbol}-{side}-{int(size)}"
                sig_text = f"📊 Hyperliquid {side.upper()} {symbol} – {size:.2f}"
                alerts.append(("hyper", sig_text, sig_id))
        except Exception:
            continue
    print(f"[Hyperliquid] {len(alerts)} alerts")
    return alerts

# ----------------------------
# ارسال سیگنال‌ها (با جلوگیری از تکرار)
# ----------------------------
def send_alerts_to_all(alerts):
    """alerts: list of tuples (kind, text, unique_id)"""
    if not alerts:
        print("[LOOP] no alerts to send")
        return
    for kind, text, uid in alerts:
        # uid را برای تکرار استفاده می‌کنیم؛ اگر در DB بود نادیده می‌کنیم
        if not uid:
            uid = f"misc-{hash(text)}"
        if is_signal_sent(uid):
            print(f"[SKIP] already sent: {uid}")
            continue
        # send to all users who have at least one wallet (or to all users; here send to all users)
        users = get_all_users()
        if not users:
            print("[LOOP] no registered users to send alerts")
            # still save to avoid re-sending many times (optional)
            save_signal(uid)
            continue
        for u in users:
            try:
                bot.send_message(int(u), f"📊 سیگنال بازار:\n\n{text}")
            except Exception as e:
                print(f"[Telegram send error to {u}]: {e}")
        save_signal(uid)
        print(f"[SENT] {uid}")

# ----------------------------
# signal loop (thread)
# ----------------------------
def signal_loop():
    print("[LOOP] signal_loop started")
    while True:
        try:
            # cleanup DB signals older than expiry
            clear_old_signals_db()

            collected = []
            # coinglass
            collected.extend(get_long_short_coinglass())

            # hyperliquid
            collected.extend(get_hyperliquid_signals())

            # wallets per user
            users = get_all_users()
            for uid in users:
                w = get_user_wallets(uid)
                for eth in w.get("eth", []):
                    collected.extend(get_large_eth_tx(eth, min_eth=1))
                for sol in w.get("sol", []):
                    collected.extend(get_large_sol_tx(sol, min_sol=5))

            # send non-duplicates
            send_alerts_to_all(collected)
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
        time.sleep(SIGNAL_INTERVAL)

# ----------------------------
# تلگرام handlers
# ----------------------------
@bot.message_handler(commands=["start"])
def handle_start(message):
    uid = message.chat.id
    add_user_if_not_exists(uid)
    bot.reply_to(message,
                 "سلام 👋\nربات فعال شد.\n\nدستورات:\n/addwallet eth <0x...>\n/addwallet sol <SOL_ADDRESS>\n/removewallet <ADDRESS>\n/mywallets")
    # print chat id to logs (helps set CHAT_ID if needed)
    print(f"[TELEGRAM] /start from {uid}")

@bot.message_handler(commands=["addwallet"])
def handle_addwallet(message):
    uid = message.chat.id
    add_user_if_not_exists(uid)
    parts = message.text.strip().split()
    if len(parts) == 3:
        coin = parts[1].lower()
        addr = parts[2].strip()
    elif len(parts) == 2:
        addr = parts[1].strip()
        if addr.startswith("0x") and len(addr) == 42:
            coin = "eth"
        else:
            coin = "sol"
    else:
        bot.reply_to(message, "فرمت درست: /addwallet eth 0x... یا /addwallet sol <address> یا /addwallet 0x...")
        return

    if coin not in ("eth", "sol"):
        bot.reply_to(message, "نوع کیف‌پول باید eth یا sol باشد.")
        return
    # basic validation
    if coin == "eth" and not addr.startswith("0x"):
        bot.reply_to(message, "آدرس ETH معتبر نیست.")
        return
    save_wallet(uid, addr, coin)
    bot.reply_to(message, f"✅ کیف‌پول ثبت شد: {addr} ({coin})")
    print(f"[WALLET] user {uid} added {addr} ({coin})")

@bot.message_handler(commands=["removewallet"])
def handle_removewallet(message):
    uid = message.chat.id
    parts = message.text.strip().split()
    if len(parts) != 2:
        bot.reply_to(message, "فرمت: /removewallet <address>")
        return
    addr = parts[1].strip()
    ok = remove_wallet(uid, addr)
    if ok:
        bot.reply_to(message, f"✅ کیف‌پول حذف شد: {addr}")
    else:
        bot.reply_to(message, "❌ آن آدرس برای شما ثبت نشده است.")

@bot.message_handler(commands=["mywallets"])
def handle_mywallets(message):
    uid = message.chat.id
    w = get_user_wallets(uid)
    if not w["eth"] and not w["sol"]:
        bot.reply_to(message, "شما هیچ کیف‌پولی ثبت نکرده‌اید.")
        return
    msg = "کیف‌پول‌های ثبت‌شده:\n"
    if w["eth"]:
        msg += "\n🔷 ETH:\n" + "\n".join(w["eth"])
    if w["sol"]:
        msg += "\n🟡 SOL:\n" + "\n".join(w["sol"])
    bot.reply_to(message, msg)

@bot.message_handler(func=lambda m: True)
def handle_unknown(message):
    bot.reply_to(message, "دستور شناخته‌شده نیست. /start برای راهنمایی")

# ----------------------------
# Webhook route + set webhook
# ----------------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

def set_webhook():
    # prefer RENDER_EXTERNAL_HOSTNAME env (Render provides it)
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not host:
        print("[WEBHOOK] RENDER_EXTERNAL_HOSTNAME not set; skipping setWebhook")
        return
    webhook_url = f"https://{host}/{BOT_TOKEN}"
    resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", params={"url": webhook_url}, timeout=10)
    try:
        print("[WEBHOOK] set response:", resp.json())
    except Exception:
        print("[WEBHOOK] set response code:", resp.status_code)

# ----------------------------
# اجرای برنامه
# ----------------------------
if __name__ == "__main__":
    print("Starting bot...")
    init_db()
    # ست وبهوک
    set_webhook()
    # استارت حلقه سیگنال در ترد جدا
    t = threading.Thread(target=signal_loop, daemon=True)
    t.start()
    # ارسال پیام استارت به تمام کاربران (اختیاری، می‌توان حذف شود)
    try:
        users = get_all_users()
        for u in users:
            try:
                bot.send_message(int(u), "🟢 پیام آزمایشی اولیه: ربات با موفقیت استارت شد!")
            except Exception as e:
                print(f"[STARTUP MSG ERROR] user {u}: {e}")
    except Exception:
        pass
    # اجرا Flask
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
