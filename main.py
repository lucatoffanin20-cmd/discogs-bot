import os
import threading
import requests
from requests_oauthlib import OAuth1
from flask import Flask
from dotenv import load_dotenv
import time

load_dotenv()

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

if not all([
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    CONSUMER_KEY,
    CONSUMER_SECRET,
    OAUTH_TOKEN,
    OAUTH_TOKEN_SECRET,
    DISCOGS_USER
]):
    print("❌ ERRORE: una o più variabili d'ambiente mancano")
    exit(1)

auth = OAuth1(
    CONSUMER_KEY,
    CONSUMER_SECRET,
    OAUTH_TOKEN,
    OAUTH_TOKEN_SECRET
)

CHECK_INTERVAL = 300  # 5 minuti
last_seen = {}  # chiave: release_id_listing_id -> prezzo

# ================= TELEGRAM =================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "disable_web_page_preview": True
            },
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ Errore Telegram: {e}")

# ================= WANTLIST =================
def get_wantlist():
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    try:
        r = requests.get(url, auth=auth, timeout=15)
        r.raise_for_status()
        return r.json().get("wants", [])
    except Exception as e:
        print(f"⚠️ Errore wantlist: {e}")
        return []

# ================= MARKETPLACE =================
def get_latest_listings(release_id, limit=5):
    url = "https://api.discogs.com/marketplace/search"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": limit,
        "page": 1
    }
    try:
        r = requests.get(url, params=params, auth=auth, timeout=15)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"⚠️ Marketplace error ({release_id}): {e}")
        return []

# ================= BOT LOOP =================
def bot_task():
    print("👂 Controllo nuovi annunci...")
    wants = get_wantlist()

    for w in wants:
        release_id = w.get("id")
        if not release_id:
            continue

        listings = get_latest_listings(release_id, limit=5)

        for listing in listings:
            listing_id = listing.get("id")
            if not listing_id:
                continue

            price = listing.get("price", {}).get("value")
            key = f"{release_id}_{listing_id}"

            old_price = last_seen.get(key)

            # Nuovo annuncio
            if old_price is None:
                last_seen[key] = price
                msg = (
                    f"🎵 NUOVO ANNUNCIO\n"
                    f"{listing.get('title', 'N/D')}\n"
                    f"💰 Prezzo: {price}\n"
                    f"https://www.discogs.com/sell/item/{listing_id}"
                )
                send_telegram(msg)

            # Cambio prezzo
            elif old_price != price:
                last_seen[key] = price
                msg = (
                    f"💰 CAMBIO PREZZO\n"
                    f"{listing.get('title', 'N/D')}\n"
                    f"Nuovo prezzo: {price}\n"
                    f"https://www.discogs.com/sell/item/{listing_id}"
                )
                send_telegram(msg)

        # micro pausa per evitare burst
        time.sleep(1)

    threading.Timer(CHECK_INTERVAL, bot_task).start()

# ================= FLASK =================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot Discogs attivo ✅"

@app.route("/ping")
def ping():
    return "pong"

if __name__ == "__main__":
    send_telegram("🤖 Discogs Wantlist Notifier avviato!")
    bot_task()
    app.run(host="0.0.0.0", port=8080)
