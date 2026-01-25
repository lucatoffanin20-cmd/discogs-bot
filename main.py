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

auth = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, OAUTH_TOKEN, OAUTH_TOKEN_SECRET)

CHECK_INTERVAL = 300  # ogni 5 minuti
last_seen = {}  # release_id -> {"listing_id": id, "price": valore}

# ================= TELEGRAM =================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

# ================= WANTLIST =================
def get_wantlist():
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    try:
        r = requests.get(url, auth=auth)
        r.raise_for_status()
        return r.json().get("wants", [])
    except:
        return []

# ================= MARKETPLACE =================
def get_latest_listing(release_id):
    url = "https://api.discogs.com/marketplace/search"
    params = {"release_id": release_id, "sort": "listed", "sort_order": "desc", "per_page": 1, "page": 1}
    try:
        r = requests.get(url, params=params, auth=auth)
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0] if results else None
    except:
        return None

# ================= BOT LOOP =================
def bot_task():
    wants = get_wantlist()
    for w in wants:
        rid = w.get("id")
        listing = get_latest_listing(rid)
        if not listing:
            continue
        lid = listing.get("id")
        if lid is None:
            continue
        new_price = listing.get('price', {}).get('value')
        old = last_seen.get(rid)

        # Nuovo listing
        if old is None or old['listing_id'] != lid:
            last_seen[rid] = {"listing_id": lid, "price": new_price}
            msg = f"🎵 NUOVO ARTICOLO: {listing.get('title', 'N/D')} 💰 {new_price}\nhttps://www.discogs.com/sell/item/{lid}"
            send_telegram(msg)
        # Prezzo cambiato
        elif old['price'] != new_price:
            last_seen[rid]['price'] = new_price
            msg = f"💰 Prezzo aggiornato: {listing.get('title', 'N/D')} - Nuovo prezzo {new_price}\nhttps://www.discogs.com/sell/item/{lid}"
            send_telegram(msg)

    # Richiama il task ogni CHECK_INTERVAL secondi
    threading.Timer(CHECK_INTERVAL, bot_task).start()

# ================= FLASK =================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot attivo ✅"

if __name__ == "__main__":
    send_telegram("🤖 Discogs Wantlist Notifier avviato!")
    bot_task()  # avvia loop in background
    app.run(host="0.0.0.0", port=8080)
