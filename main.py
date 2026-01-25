print("🤖 Discogs Wantlist Notifier avviato")

import os
import time
import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

# ================= CONFIG =================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

# Intervalli
CHECK_INTERVAL = 300        # controllo wantlist ogni 5 minuti
DELAY_BETWEEN_CALLS = 1.2   # sicurezza rate limit Discogs

# ================= CONTROLLI =================
if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, CONSUMER_KEY, CONSUMER_SECRET, OAUTH_TOKEN, OAUTH_TOKEN_SECRET, DISCOGS_USER]):
    missing = [v for v, val in {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "CONSUMER_KEY": CONSUMER_KEY,
        "CONSUMER_SECRET": CONSUMER_SECRET,
        "OAUTH_TOKEN": OAUTH_TOKEN,
        "OAUTH_TOKEN_SECRET": OAUTH_TOKEN_SECRET,
        "DISCOGS_USER": DISCOGS_USER
    }.items() if not val]
    print(f"❌ Variabili mancanti o vuote: {missing}")
    exit(1)

# ================= AUTENTICAZIONE OAUTH =================
auth = OAuth1(
    CONSUMER_KEY,
    CONSUMER_SECRET,
    OAUTH_TOKEN,
    OAUTH_TOKEN_SECRET
)

# ================= STATO =================
last_seen = {}  # release_id -> ultimo listing_id visto

# ================= TELEGRAM =================
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram non configurato correttamente")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
        if r.status_code != 200:
            print(f"⚠️ Errore Telegram: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"⚠️ Errore Telegram: {e}")

# ================= WANTLIST =================
def get_wantlist():
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    try:
        r = requests.get(url, auth=auth)
        r.raise_for_status()
        return r.json().get("wants", [])
    except Exception as e:
        print(f"⚠️ Errore get_wantlist: {e}")
        return []

# ================= MARKETPLACE =================
def get_latest_listing(release_id):
    if release_id is None:
        return None
    url = "https://api.discogs.com/marketplace/search"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": 1,
        "page": 1
    }
    try:
        r = requests.get(url, params=params, auth=auth)
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0] if results else None
    except Exception as e:
        print(f"⚠️ Errore get_latest_listing ({release_id}): {e}")
        return None

# ================= LOOP PRINCIPALE =================
def bot_loop():
    send_telegram("🤖 Discogs Wantlist Notifier avviato e operativo!")
    print("👂 Bot attivo, in ascolto dei nuovi annunci...")

    wants = get_wantlist()

    while True:
        for w in wants:
            rid = w.get("id")
            listing = get_latest_listing(rid)
            if not listing:
                continue

            lid = listing.get("id")
            if lid is None:
                continue

            if last_seen.get(rid) != lid:
                last_seen[rid] = lid
                msg = (
                    f"🎵 NUOVO ARTICOLO IN WANTLIST!\n\n"
                    f"{listing.get('title', 'N/D')}\n"
                    f"💰 {listing.get('price', {}).get('value', 'N/D')} "
                    f"{listing.get('price', {}).get('currency', '')}\n"
                    f"📦 Condizione: {listing.get('condition', 'N/D')}\n"
                    f"🔗 https://www.discogs.com/sell/item/{lid}"
                )
                send_telegram(msg)
                print(f"✅ Notifica inviata: {lid}")

            time.sleep(DELAY_BETWEEN_CALLS)

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    bot_loop()
