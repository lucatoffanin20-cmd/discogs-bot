import os
import time
import json
import requests
from requests_oauthlib import OAuth1
from threading import Thread
from flask import Flask

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

PER_PAGE = 50          # release per pagina
CHECK_INTERVAL = 600   # 10 minuti
SEEN_FILE = "seen_listings.json"

# ============== FUNZIONI UTILI ==============
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        r = requests.post(url, data=payload)
        r.raise_for_status()
    except Exception as e:
        print(f"⚠️ Errore Telegram: {e}")

def safe_get(url, params=None):
    """Richiesta GET con OAuth1 e gestione errori."""
    auth = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, OAUTH_TOKEN, OAUTH_TOKEN_SECRET)
    try:
        r = requests.get(url, params=params, auth=auth)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        if r.status_code == 429:
            print("⚠️ HTTP 429 ignorato, attendo 5 secondi")
            time.sleep(5)
            return safe_get(url, params)
        elif r.status_code == 404:
            print(f"⚠️ 404: {url}")
            return None
        else:
            print(f"⚠️ HTTP error: {e}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Request error: {e}")
        return None

def get_wantlist(user):
    """Recupera tutte le release della wantlist di un utente."""
    releases = []
    page = 1
    while True:
        url = f"https://api.discogs.com/users/{user}/wants"
        params = {"per_page": PER_PAGE, "page": page}
        data = safe_get(url, params)
        if not data or "wants" not in data or not data["wants"]:
            break
        releases.extend([w["basic_information"]["id"] for w in data["wants"]])
        if page >= data.get("pagination", {}).get("pages", 1):
            break
        page += 1
    print(f"📀 Wantlist caricata: {len(releases)} release")
    return releases

def get_latest_listing(release_id):
    """Prende l'annuncio più recente di una release."""
    url = f"https://api.discogs.com/marketplace/search"
    params = {"release_id": release_id, "sort": "listed", "sort_order": "desc", "per_page": 1, "page": 1}
    data = safe_get(url, params)
    if data and "listings" in data and data["listings"]:
        return data["listings"][0]["id"], data["listings"][0]["price"]["value"]
    return None, None

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return json.load(f)
    return {}

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f)

# ============== BOT LOOP ==============
def bot_loop():
    seen_listings = load_seen()
    wantlist = get_wantlist(DISCOGS_USER)
    if not wantlist:
        print("⚠️ Wantlist vuota o non caricabile.")
        return
    while True:
        for release_id in wantlist:
            listing_id, price = get_latest_listing(release_id)
            if listing_id and listing_id not in seen_listings:
                seen_listings[listing_id] = True
                save_seen(seen_listings)
                send_telegram(f"📢 Nuovo annuncio!\nRelease ID: {release_id}\nListing ID: {listing_id}\nPrezzo: {price}")
                print(f"📢 Nuovo annuncio per release {release_id} trovato!")
            time.sleep(1)  # piccola pausa per non sovraccaricare
        print(f"👂 Controllo completato, prossimo controllo tra {CHECK_INTERVAL//60} minuti")
        time.sleep(CHECK_INTERVAL)

# ============== FLASK SERVER PER UPTIME ROBOT ==============
app = Flask(__name__)

@app.route("/", methods=["HEAD", "GET"])
def home():
    return "Bot attivo", 200

# ============== MAIN ==============
if __name__ == "__main__":
    Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
