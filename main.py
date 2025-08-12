import os
import time
import json
import threading
import requests
import telebot
from flask import Flask, request

# ---------------- تنظیمات ----------------
BOT_TOKEN = "7762972292:AAEkDx853saWRuDpo59TwN_Wa0uW1mY-AIo"
ETHERSCAN_API_KEY = "VZFDUWB3YGQ1YCDKTCU1D6DDSS"
COINGLASS_API_KEY = "6e5da618d74344f69c0e77ad9b3643c0"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

DATA_FILE = "data.json"
CHECK_INTERVAL = 600         # هر 10 دقیقه بررسی تراکنش
SIGNAL_INTERVAL = 3600       # هر 1 ساعت بررسی سیگنال

# ---------------- توابع ذخیره/خواندن ----------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

user_data = load_data()

# ---------------- صفحه اصلی ----------------
@app.route('/')
def home():
    return "✅ Whale + Signal Bot is Running"

# ---------------- هندلر وبهوک ----------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200

# ---------------- توابع کمکی ----------------
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
        res = requests.get(url, params=params)
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
        res = requests.get(url, headers=headers)
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
        res = requests.get(url, params=params)
        txs = res.json().get("result", [])[:5]
        alerts = []
        for tx in txs:
            eth_value = int(tx["value"]) / 1e18
            if eth_value >= 10:
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
        res = requests.get(url, headers=headers)
        txs = res.json()
        alerts = []
        for tx in txs:
            lamports = tx.get("lamport", 0)
            sol = lamports / 1e9
            if sol >= 10:
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
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()
        if not data.get("success"):
            print(f"[COINGLASS ERROR] {data.get('message')}")
            return []
        return data.get("data", [])
    except Exception as e:
        print(f"[COINGLASS EXCEPTION] {e}")
        return []

# ---------------- لوپ‌ها ----------------
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
        for uid in list(user_data.keys()):
            try:
                bot.send_message(int(uid), msg)
            except Exception as e:
                print(f"[Telegram send error to {uid}]: {e}")

        time.sleep(SIGNAL_INTERVAL)

def monitor_wallets():
    while True:
        for uid, wallets in list(user_data.items()):
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

# ---------------- دستورات ربات ----------------
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    uid = str(msg.chat.id)
    if uid not in user_data:
        user_data[uid] = {"eth": [], "sol": []}
        save_data(user_data)
    bot.send_message(msg.chat.id, "✅ ربات فعال شد.\n📌 آدرس کیف پول خود را ارسال کنید.")

@bot.message_handler(commands=['reset'])
def cmd_reset(msg):
    uid = str(msg.chat.id)
    user_data[uid] = {"eth": [], "sol": []}
    save_data(user_data)
    bot.send_message(msg.chat.id, "♻️ تمام داده‌ها پاک شدند.")

@bot.message_handler(commands=['wallets'])
def cmd_wallets(msg):
    uid = str(msg.chat.id)
    wallets = user_data.get(uid, {"eth": [], "sol": []})
    if not wallets["eth"] and not wallets["sol"]:
        bot.send_message(msg.chat.id, "❌ هیچ کیف پولی ثبت نشده.")
        return
    text = "📜 کیف پول‌های ثبت شده:\n\n"
    if wallets["eth"]:
        text += "💎 اتریوم:\n" + "\n".join(wallets["eth"]) + "\n\n"
    if wallets["sol"]:
        text += "🪙 سولانا:\n" + "\n".join(wallets["sol"]) + "\n"
    bot.send_message(msg.chat.id, text)

@bot.message_handler(commands=['remove'])
def cmd_remove(msg):
    uid = str(msg.chat.id)
    parts = msg.text.strip().split()
    if len(parts) != 2:
        bot.send_message(msg.chat.id, "❌ فرمت صحیح:\n`/remove [آدرس]`", parse_mode="Markdown")
        return
    addr = parts[1]
    removed = False
    if addr in user_data.get(uid, {}).get("eth", []):
        user_data[uid]["eth"].remove(addr)
        removed = True
    elif addr in user_data.get(uid, {}).get("sol", []):
        user_data[uid]["sol"].remove(addr)
        removed = True
    if removed:
        save_data(user_data)
        bot.send_message(msg.chat.id, f"✅ آدرس حذف شد:\n{addr}")
    else:
        bot.send_message(msg.chat.id, "❌ آدرس یافت نشد.")

@bot.message_handler(commands=['stats'])
def cmd_stats(msg):
    uid = str(msg.chat.id)
    wallets = user_data.get(uid, {"eth": [], "sol": []})
    text = "📊 موجودی کیف پول‌ها:\n\n"
    for eth in wallets.get("eth", []):
        bal = get_eth_balance(eth)
        text += f"💎 ETH: `{eth}`\n💰 {bal:.4f} ETH\n\n"
    for sol in wallets.get("sol", []):
        bal = get_sol_balance(sol)
        text += f"🪙 SOL: `{sol}`\n💰 {bal:.4f} SOL\n\n"
    bot.send_message(msg.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: True)
def handle_new_wallet(msg):
    uid = str(msg.chat.id)
    text = msg.text.strip()
    if text.startswith("0x") and len(text) == 42:
        user_data.setdefault(uid, {"eth": [], "sol": []})
        if text not in user_data[uid]["eth"]:
            user_data[uid]["eth"].append(text)
            save_data(user_data)
            bot.send_message(msg.chat.id, f"✅ آدرس ETH ثبت شد:\n{text}")
        else:
            bot.send_message(msg.chat.id, "⚠️ این آدرس قبلاً ثبت شده.")
        return
    if len(text) >= 32 and not text.startswith("0x"):
        user_data.setdefault(uid, {"eth": [], "sol": []})
        if text not in user_data[uid]["sol"]:
            user_data[uid]["sol"].append(text)
            save_data(user_data)
            bot.send_message(msg.chat.id, f"✅ آدرس SOL ثبت شد:\n{text}")
        else:
            bot.send_message(msg.chat.id, "⚠️ این آدرس قبلاً ثبت شده.")
        return
    bot.send_message(msg.chat.id, "❌ فرمت آدرس صحیح نیست.")

# ---------------- اجرای برنامه ----------------
if __name__ == "__main__":
    # ست کردن وبهوک
    bot.remove_webhook()
    bot.set_webhook(url=f"https://wpmpmp-bot.onrender.com/{BOT_TOKEN}")

    # اجرای حلقه‌های بررسی
    threading.Thread(target=monitor_wallets, daemon=True).start()
    threading.Thread(target=signal_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
