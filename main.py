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

CHECK_INTERVAL = 300        # 5 minuti tra controlli
DELAY_BETWEEN_CALLS = 1.1   # sicurezza rate limit

# ================= AUTENTICAZIONE OAUTH =================
auth = OAuth1(
    CONSUMER_KEY,
    CONSUMER_SECRET,
    OAUTH_TOKEN,
    OAUTH_TOKEN_SECRET
)

# ================= STATE =================
# registro ultimo listing per release_id
last_seen = {}  # release_id -> listing_id

# ================= TELEGRAM =================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ Errore Telegram: {e}")

# ================= WANTLIST =================
def get_wantlist():
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    r = requests.get(url, auth=auth)
    r.raise_for_status()
    return r.json()["wants"]

# ================= MARKETPLACE =================
def get_latest_listing(release_id):
    url = "https://api.discogs.com/marketplace/search"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": 1,
        "page": 1
    }
    r = requests.get(url, params=params, auth=auth)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None

# ================= LOOP PRINCIPALE =================
def bot_loop():
    send_telegram("🤖 Discogs Wantlist Notifier avviato e operativo!")
    print("👂 Bot attivo, in ascolto dei nuovi annunci...")

    wants = get_wantlist()

    while True:
        for w in wants:
            rid = w["id"]
            listing = get_latest_listing(rid)
            if not listing:
                continue

            lid = listing["id"]
            if last_seen.get(rid) != lid:
                last_seen[rid] = lid
                msg = (
                    f"🎵 NUOVO ARTICOLO IN WANTLIST!\n\n"
                    f"{listing['title']}\n"
                    f"💰 {listing['price']['value']} {listing['price']['currency']}\n"
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
