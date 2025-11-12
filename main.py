"""
Signal bot (HyperDash, DexScreener, DeBank, Hyperliquid, CoinGlass) via MTProto->SOCKS
Added: CoinGlass integration (uses CoinGlass REST API). Store this file as
hyperdash_telegram_bot_mtproto_coinglass.py and run with Python 3.9+.

Note: set COINGLASS_API_KEY to your API key (already inserted from your message),
check your CoinGlass plan for endpoint access and rate-limits.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from telegram import Bot, Update
from telegram.ext import CommandHandler, MessageHandler, Filters, Updater, CallbackContext
from telegram.utils.request import Request
from telegram.error import TelegramError

# ---------------- CONFIG ----------------
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "7762972292:AAEOjINaOiWzyJ0zJjrjjvtdTl6Wg51vCC8")

# PROXY: if you deploy to a public VPS you usually don't need a local SOCKS proxy.
# Set PROXY_URL to empty string to disable proxy usage.
PROXY_URL = os.environ.get("PROXY_URL", "")  # e.g. "socks5h://127.0.0.1:1080" or empty
PROXIES_REQUESTS = {}
if PROXY_URL:
    PROXIES_REQUESTS = {"http": PROXY_URL, "https": PROXY_URL}

# sources
HYPERDASH_BASE = "https://hyperdash.info"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/search?q="
DEBANK_API = "https://api.debank.com/user/total_balance?id="
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

# CoinGlass config (use environment variable for secret)
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY", "5790b41304464fc784f7f825e7e9dc04")
COINGLASS_BASE = "https://open-api-v4.coinglass.com"

# timing & thresholds
POLL_INTERVAL = 300  # seconds (5 minutes)
MIN_POSITION_VALUE_USD = 10.0
WALLETS_FILE = "wallets.json"
AUTHORIZED_CHATS_FILE = "authorized_chats.json"
REQUEST_TIMEOUT = 12

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("signal_bot")

# ---------------- sessions ----------------
def make_session(proxies: Optional[Dict[str, str]] = None) -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=(500,502,503,504))
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    if proxies:
        s.proxies.update(proxies)
    s.headers.update({"User-Agent": "SignalBot/1.0"})
    return s

BASE_SESSION = make_session(PROXIES_REQUESTS)

# ---------------- Telegram bot init (use Request with proxy) ----------------
request_obj = Request(proxy_url=PROXY_URL, connect_timeout=10.0, read_timeout=15.0)
bot = Bot(token=BOT_TOKEN, request=request_obj)
updater = Updater(bot=bot, use_context=True)
dispatcher = updater.dispatcher

# ---------------- storage ----------------
def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.error("read %s failed: %s", path, e)
        return default

def _write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("write %s failed: %s", path, e)

def load_wallets() -> List[str]:
    data = _read_json(WALLETS_FILE, [])
    if isinstance(data, list):
        return [w.lower() for w in data]
    return []

def save_wallets(wallets: List[str]):
    _write_json(WALLETS_FILE, wallets)

def load_authorized_chats() -> Set[int]:
    data = _read_json(AUTHORIZED_CHATS_FILE, [])
    try:
        return set(int(x) for x in data)
    except Exception:
        return set()

def save_authorized_chats(chats: Set[int]):
    _write_json(AUTHORIZED_CHATS_FILE, list(chats))

authorized_chats: Set[int] = load_authorized_chats()

def authorize_chat(chat_id: int):
    if chat_id not in authorized_chats:
        authorized_chats.add(chat_id)
        save_authorized_chats(authorized_chats)
    return True

# ---------------- command handlers ----------------
def cmd_add(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    authorize_chat(chat_id)
    if not context.args:
        update.message.reply_text("Usage: /add <wallet_address>")
        return
    addr = context.args[0].strip().lower()
    wallets = load_wallets()
    if addr in wallets:
        update.message.reply_text("آدرس قبلا وجود دارد.")
    else:
        wallets.append(addr)
        save_wallets(wallets)
        update.message.reply_text(f"آدرس {addr} اضافه شد ✅")

def cmd_remove(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    authorize_chat(chat_id)
    if not context.args:
        update.message.reply_text("Usage: /remove <wallet_address>")
        return
    addr = context.args[0].strip().lower()
    wallets = load_wallets()
    if addr in wallets:
        wallets.remove(addr)
        save_wallets(wallets)
        update.message.reply_text(f"آدرس {addr} حذف شد ✅")
    else:
        update.message.reply_text("آدرس یافت نشد.")

def cmd_list(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    authorize_chat(chat_id)
    wallets = load_wallets()
    if wallets:
        update.message.reply_text("فهرست کیف‌پول‌ها:\n" + "\n".join(wallets))
    else:
        update.message.reply_text("لیست خالی است.")

def cmd_status(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    authorize_chat(chat_id)
    wallets = load_wallets()
    update.message.reply_text(f"Bot running. Poll interval: {POLL_INTERVAL}s\nFollowed wallets: {len(wallets)}\nProxy: {PROXY_URL}")

def cmd_test(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    authorize_chat(chat_id)
    if not context.args:
        update.message.reply_text("Usage: /test <wallet_address>")
        return
    addr = context.args[0].strip().lower()
    update.message.reply_text(f"Testing {addr} — checking sources...")
    results = []
    try:
        h = fetch_from_hyperliquid(addr)
        results.append(("Hyperliquid", bool(h)))
    except Exception as e:
        results.append(("Hyperliquid", f"err:{e}"))
    try:
        hd_html = fetch_trader_page_hyperdash(addr)
        results.append(("HyperDash", bool(hd_html)))
    except Exception as e:
        results.append(("HyperDash", f"err:{e}"))
    try:
        ds = fetch_from_dexscreener(addr)
        results.append(("DexScreener", bool(ds)))
    except Exception as e:
        results.append(("DexScreener", f"err:{e}"))
    try:
        db = fetch_from_debank(addr)
        results.append(("DeBank", bool(db)))
    except Exception as e:
        results.append(("DeBank", f"err:{e}"))
    try:
        cg = fetch_from_coinglass(addr)
        results.append(("CoinGlass", bool(cg)))
    except Exception as e:
        results.append(("CoinGlass", f"err:{e}"))
    text = "\n".join(f"{k}: {v}" for k, v in results)
    update.message.reply_text("Test results:\n" + text)

dispatcher.add_handler(CommandHandler("add", cmd_add, pass_args=True))
dispatcher.add_handler(CommandHandler("remove", cmd_remove, pass_args=True))
dispatcher.add_handler(CommandHandler("list", cmd_list))
dispatcher.add_handler(CommandHandler("status", cmd_status))
dispatcher.add_handler(CommandHandler("test", cmd_test, pass_args=True))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, lambda u,c: None))

# ---------------- fetchers ----------------
def fetch_trader_page_hyperdash(address: str) -> Optional[str]:
    url = f"{HYPERDASH_BASE}/trader/{address}"
    try:
        s = make_session(PROXIES_REQUESTS)
        r = s.get(url, timeout=REQUEST_TIMEOUT)
        if r.ok:
            return r.text
    except Exception as e:
        logger.debug("HyperDash fetch err %s", e)
    return None

def extract_json_from_html(html: str) -> Optional[Dict[str, Any]]:
    if not html:
        return None
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">\s*(\{.*?\})\s*</script>', html, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', html, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None

def parse_trader_state(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not state:
        return None
    trader = None
    if "props" in state and "pageProps" in state["props"] and "trader" in state["props"]["pageProps"]:
        trader = state["props"]["pageProps"]["trader"]
    elif "trader" in state:
        trader = state["trader"]
    else:
        for k,v in state.items():
            if isinstance(v, dict) and "positions" in v:
                trader = v
                break
    if not trader:
        return None
    positions = []
    raw_positions = trader.get("positions") or []
    if isinstance(raw_positions, dict):
        raw_positions = list(raw_positions.values())
    for p in raw_positions:
        try:
            symbol = p.get("symbol") or p.get("asset") or p.get("market")
            size_usd = float(p.get("notional") or p.get("sizeUsd") or p.get("size") or 0)
            side = p.get("side") or ("long" if p.get("isLong") else "short" if p.get("isShort") else "")
            positions.append({"symbol": symbol, "size_usd": size_usd, "side": side, "opened_at": p.get("openedAt")})
        except Exception:
            continue
    address = trader.get("address") or trader.get("wallet") or trader.get("id")
    return {"address": address, "positions": positions, "raw": trader}

def fetch_from_dexscreener(address: str) -> Optional[Dict[str, Any]]:
    try:
        s = make_session(PROXIES_REQUESTS)
        r = s.get(DEXSCREENER_API + address, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        pairs = data.get("pairs") or []
        pos = []
        for p in pairs:
            try:
                liquidity_usd = float((p.get("liquidity") or {}).get("usd") or 0)
            except Exception:
                liquidity_usd = 0
            if liquidity_usd >= MIN_POSITION_VALUE_USD:
                pos.append({"symbol": (p.get("baseToken") or {}).get("symbol"), "size_usd": liquidity_usd, "side": "liquidity"})
        if pos:
            return {"address": address, "positions": pos, "source": "dexscreener"}
    except Exception as e:
        logger.debug("Dex error %s", e)
    return None

def fetch_from_debank(address: str) -> Optional[Dict[str, Any]]:
    try:
        s = make_session(PROXIES_REQUESTS)
        r = s.get(DEBANK_API + address, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        total = float((data.get("data") or {}).get("total_usd_value") or 0)
        if total >= MIN_POSITION_VALUE_USD:
            return {"address": address, "positions":[{"symbol":"PORTFOLIO","size_usd":total,"side":"hold"}], "source":"debank"}
    except Exception as e:
        logger.debug("Debank err %s", e)
    return None

def fetch_from_hyperliquid(address: str) -> Optional[Dict[str, Any]]:
    try:
        s = make_session(PROXIES_REQUESTS)
        payload = {"type":"clearinghouseState","user": address}
        r = s.post(HYPERLIQUID_API, json=payload, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            return None
        data = r.json()
        positions = []
        for key in ("openPositions", "assetPositions", "positions"):
            raw = data.get(key)
            if raw:
                for p in raw:
                    try:
                        sym = p.get("asset") or p.get("symbol") or p.get("market")
                        size = float(p.get("notional") or p.get("sizeUsd") or p.get("size") or 0)
                        side = p.get("side") or p.get("direction") or ("long" if p.get("position",{}).get("szi",0) > 0 else "short")
                        if size >= MIN_POSITION_VALUE_USD:
                            positions.append({"symbol": sym, "size_usd": size, "side": side})
                    except Exception:
                        continue
                if positions:
                    return {"address": address, "positions": positions, "source":"hyperliquid"}
        return None
    except Exception as e:
        logger.debug("Hyperliquid err %s", e)
    return None

# ---------------- CoinGlass fetcher ----------------
def fetch_from_coinglass(address: str) -> Optional[Dict[str, Any]]:
    """
    Try multiple CoinGlass endpoints that can contain wallet/position info:
      - /api/hyperliquid/position (query param user=address)
      - /api/exchange/assets (list of assets for exchange wallets)
    CoinGlass requires header: CG-API-KEY
    Returns similar structure: {address, positions:[{symbol,size_usd,side}], source:'CoinGlass'}
    """
    headers = {"CG-API-KEY": COINGLASS_API_KEY, "Accept": "application/json"}
    s = make_session(PROXIES_REQUESTS)
    try:
        # 1) hyperliquid position (for derivatives/hyperliquid users)
        url_hl = f"{COINGLASS_BASE}/api/hyperliquid/position"
        r = s.get(url_hl, params={"user": address}, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.ok:
            data = r.json()
            if data.get("code") in (0, "0") and data.get("data"):
                lst = data["data"].get("list") or []
                positions = []
                for item in lst:
                    # CoinGlass returns 'user' field; match it
                    user = (item.get("user") or "").lower()
                    if user != address.lower():
                        continue
                    try:
                        pos_size = float(item.get("position_value_usd") or item.get("position_size") or item.get("margin_balance") or 0)
                    except Exception:
                        pos_size = 0
                    if abs(pos_size) >= MIN_POSITION_VALUE_USD:
                        symbol = item.get("symbol") or item.get("asset")
                        # position_size may be negative for short
                        side = "long" if float(item.get("position_size") or 0) > 0 else "short" if float(item.get("position_size") or 0) < 0 else "unknown"
                        positions.append({"symbol": symbol, "size_usd": abs(pos_size), "side": side})
                if positions:
                    return {"address": address, "positions": positions, "source": "CoinGlass(Hyperliquid)"}
        # 2) exchange assets (for on-chain/exchange wallets)
        url_ex = f"{COINGLASS_BASE}/api/exchange/assets"
        r2 = s.get(url_ex, params={"wallet_address": address}, headers=headers, timeout=REQUEST_TIMEOUT)
        if r2.ok:
            data2 = r2.json()
            if data2.get("code") in (0, "0") and data2.get("data"):
                positions = []
                for item in data2.get("data"):
                    try:
                        bal_usd = float(item.get("balance_usd") or item.get("balance") or 0)
                    except Exception:
                        bal_usd = 0
                    if bal_usd >= MIN_POSITION_VALUE_USD:
                        sym = item.get("symbol") or item.get("assets_name")
                        positions.append({"symbol": sym, "size_usd": bal_usd, "side": "hold"})
                if positions:
                    return {"address": address, "positions": positions, "source": "CoinGlass(ExchangeAssets)"}
    except Exception as e:
        logger.debug("CoinGlass err %s", e)
    return None

# ---------------- event detection & poller ----------------
class StateStore:
    def __init__(self):
        self.store: Dict[str, Any] = {}
    def get(self, addr: str):
        return self.store.get(addr.lower())
    def update(self, addr: str, snap: Dict[str, Any]):
        self.store[addr.lower()] = snap

state_store = StateStore()

def detect_events(addr: str, snapshot: Dict[str, Any]) -> List[str]:
    events: List[str] = []
    prev = state_store.get(addr) or {"positions": []}
    prev_positions = prev.get("positions", [])
    now_positions = snapshot.get("positions", [])

    prev_map = {(p.get("symbol"), (p.get("side") or "").lower()): p for p in prev_positions}

    for p in now_positions:
        sym = p.get("symbol")
        side = (p.get("side") or "").lower()
        size = float(p.get("size_usd") or 0)
        key = (sym, side)
        if key not in prev_map:
            if size >= MIN_POSITION_VALUE_USD:
                events.append(f"New position ({snapshot.get('source','Unknown')}): {sym} {side.upper()} ${size:.0f}")
        else:
            prev_size = float(prev_map[key].get("size_usd") or 0)
            if size > prev_size * 1.05 and size >= MIN_POSITION_VALUE_USD:
                events.append(f"Size increase ({snapshot.get('source','Unknown')}): {sym} {side.upper()} -> ${size:.0f}")

    for pp in prev_positions:
        sym = pp.get("symbol")
        side = (pp.get("side") or "").lower()
        found = False
        for p in now_positions:
            if p.get("symbol") == sym and (p.get("side") or "").lower() == side:
                found = True
                break
        if not found:
            # either closed or direction changed
            for p in now_positions:
                if p.get("symbol") == sym and (p.get("side") or "").lower() != side:
                    events.append(f"Direction change ({snapshot.get('source','Unknown')}): {sym} {side} -> {p.get('side')}")
                    found = True
                    break
            if not found:
                events.append(f"Closed ({snapshot.get('source','Unknown')}): {sym} {side}")

    state_store.update(addr, snapshot)
    return events

def fetch_from_sources_in_order(addr: str) -> Optional[Dict[str, Any]]:
    # 1) HyperDash
    html = fetch_trader_page_hyperdash(addr)
    if html:
        st = extract_json_from_html(html)
        if st:
            parsed = parse_trader_state(st)
            if parsed and parsed.get("positions"):
                parsed["source"] = "HyperDash"
                return parsed
    # 2) CoinGlass (try for exchange wallet or hyperliquid positions)
    cg = fetch_from_coinglass(addr)
    if cg:
        cg["source"] = cg.get("source","CoinGlass")
        return cg
    # 3) DexScreener
    ds = fetch_from_dexscreener(addr)
    if ds:
        ds["source"] = ds.get("source","DexScreener")
        return ds
    # 4) DeBank
    db = fetch_from_debank(addr)
    if db:
        db["source"] = "DeBank"
        return db
    # 5) Hyperliquid API
    hl = fetch_from_hyperliquid(addr)
    if hl:
        hl["source"] = "Hyperliquid"
        return hl
    return None

def send_signal_to_chats(text: str):
    targets = list(authorized_chats)
    if not targets:
        logger.info("No authorized chats to send signals to. Signal:\n%s", text)
        return
    for cid in targets:
        try:
            bot.send_message(chat_id=cid, text=text, parse_mode="Markdown")
        except TelegramError as e:
            logger.error("send to %s failed: %s", cid, e)

def poll_wallet(addr: str):
    parsed = fetch_from_sources_in_order(addr)
    if not parsed:
        logger.debug("no data for %s", addr)
        return
    events = detect_events(addr, parsed)
    if events:
        ts = datetime.now(timezone.utc).astimezone().isoformat()
        for e in events:
            text = f"⚡ سیگنال — کیف‌پول: `{addr}`\n{e}\n_source: {parsed.get('source','unknown')}\n_time: {ts}_"
            logger.info("SIGNAL: %s", text)
            send_signal_to_chats(text)

def poller_thread():
    logger.info("Poller started. Interval %s seconds", POLL_INTERVAL)
    while True:
        wallets = load_wallets()
        if wallets:
            for w in wallets:
                try:
                    poll_wallet(w)
                except Exception as ex:
                    logger.error("poll error %s: %s", w, ex)
        time.sleep(POLL_INTERVAL)

# ---------------- main ----------------
def main():
    # Start poller thread
    threading.Thread(target=poller_thread, daemon=True).start()
    logger.info("Starting bot polling (proxy socks -> %s)", PROXY_URL)
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
