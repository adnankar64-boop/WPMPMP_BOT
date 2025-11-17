# ---------------- IMPORTS ----------------
import os
import sys
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from telegram import Bot, Update
from telegram.ext import (
    CommandHandler,
    Updater,
    CallbackContext
)
from telegram.utils.request import Request


# ---------------- CONFIG ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY")
PROXY_URL = os.environ.get("PROXY_URL", "")

if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is not set.")
if not COINGLASS_API_KEY:
    raise RuntimeError("Environment variable COINGLASS_API_KEY is not set.")

PROXIES_REQUESTS = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
WALLETS_FILE = os.environ.get("WALLETS_FILE", "wallets.json")
AUTHORIZED_CHATS_FILE = os.environ.get("AUTHORIZED_CHATS_FILE", "authorized_chats.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("signal_bot")


# ---------------- sessions ----------------
def make_session(proxies=None):
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=(500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=100)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    if proxies:
        s.proxies.update(proxies)
    return s


BASE_SESSION = make_session(PROXIES_REQUESTS)


# ---------------- Telegram bot ----------------
request_obj = Request(connect_timeout=10.0, read_timeout=15.0)
bot = Bot(token=BOT_TOKEN, request=request_obj)
updater = Updater(bot=bot, use_context=True)
dispatcher = updater.dispatcher


# ---------------- storage ----------------
def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default


def _write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"write {path} failed: {e}")


def load_wallets():
    data = _read_json(WALLETS_FILE, [])
    return [w.lower() for w in data] if isinstance(data, list) else []


def save_wallets(wallets):
    _write_json(WALLETS_FILE, wallets)


def load_authorized_chats():
    data = _read_json(AUTHORIZED_CHATS_FILE, [])
    try:
        return set(int(x) for x in data)
    except:
        return set()


def save_authorized_chats(chats):
    _write_json(AUTHORIZED_CHATS_FILE, list(chats))


authorized_chats = load_authorized_chats()


def authorize_chat(chat_id):
    if chat_id not in authorized_chats:
        authorized_chats.add(chat_id)
        save_authorized_chats(authorized_chats)
    return True


# ---------------- command handlers ----------------
def cmd_add(update: Update, context: CallbackContext):
    authorize_chat(update.effective_chat.id)
    if not context.args:
        update.message.reply_text("Usage: /add <wallet>")
        return

    addr = context.args[0].lower()
    wallets = load_wallets()

    if addr in wallets:
        update.message.reply_text("آدرس موجود است.")
    else:
        wallets.append(addr)
        save_wallets(wallets)
        update.message.reply_text(f"آدرس {addr} اضافه شد.")


def cmd_remove(update: Update, context: CallbackContext):
    authorize_chat(update.effective_chat.id)
    if not context.args:
        update.message.reply_text("Usage: /remove <wallet>")
        return

    addr = context.args[0].lower()
    wallets = load_wallets()

    if addr in wallets:
        wallets.remove(addr)
        save_wallets(wallets)
        update.message.reply_text("حذف شد.")
    else:
        update.message.reply_text("وجود ندارد.")


def cmd_list(update: Update, context: CallbackContext):
    authorize_chat(update.effective_chat.id)
    wallets = load_wallets()
    update.message.reply_text("\n".join(wallets) if wallets else "لیست خالی است.")


def cmd_status(update: Update, context: CallbackContext):
    authorize_chat(update.effective_chat.id)
    update.message.reply_text(f"Bot OK\nWallets: {len(load_wallets())}\nInterval: {POLL_INTERVAL}s")


def cmd_test(update: Update, context: CallbackContext):
    authorize_chat(update.effective_chat.id)
    update.message.reply_text("test running...")


dispatcher.add_handler(CommandHandler("add", cmd_add, pass_args=True))
dispatcher.add_handler(CommandHandler("remove", cmd_remove, pass_args=True))
dispatcher.add_handler(CommandHandler("list", cmd_list))
dispatcher.add_handler(CommandHandler("status", cmd_status))
dispatcher.add_handler(CommandHandler("test", cmd_test, pass_args=True))


# ---------------- POLLER THREAD ----------------
def poller_thread():
    while True:
        try:
            wallets = load_wallets()
            logger.info(f"Polling wallets... count={len(wallets)}")

            # (اینجا بعداً می‌تونی APIها را اضافه کنی)

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.error(f"poller_thread error: {e}")
            time.sleep(5)


# ---------------- main ----------------
def main():
    threading.Thread(target=poller_thread, daemon=True).start()

    logger.info("Starting bot polling ...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
