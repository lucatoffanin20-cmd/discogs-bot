import os
import time
import json
import requests
from threading import Thread

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

# ================= CONFIG =================
CHECK_INTERVAL = 600  # 10 minuti
SEEN_LISTINGS_FILE = "seen_listings.json"
PER_PAGE = 50

# ================= AUTENTICAZIONE =================
auth = (OAUTH_TOKEN, OAUTH_TOKEN_SECRET)

# ================= STATO PERSISTENTE =================
if os.path.exists(SEEN_LISTINGS_FILE):
    with open(SEEN_LISTINGS_FILE, "r") as f:
        seen_listings = json.load(f)
else:
    seen_listings = {}

# ================= FUNZIONI =================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})

def safe_get(url, params=None, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, params=params, auth=auth, timeout=10)
            if r.status_code == 429:
                print("⚠️ HTTP 429 ignorato, attendo 5 secondi")
                time.sleep(5)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if r.status_code in [502, 503] and attempt < max_retries:
                print(f"⚠️ Marketplace error (tentativo {attempt}/{max_retries})")
                time.sleep(5)
                continue
            else:
                print(f"⚠️ Marketplace error (tentativo {attempt}/{max_retries}): {e}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Errore rete: {e}")
            time.sleep(5)
    return None

def get_wantlist(user):
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
    return releases

def get_latest_listing(release_id):
    url = f"https://api.discogs.com/marketplace/search"
    params = {"release_id": release_id, "sort": "listed", "sort_order": "desc", "per_page": 1, "page": 1}
    data = safe_get(url, params)
    if not data or "listings" not in data or not data["listings"]:
        return None
    listing = data["listings"][0]
    return listing["id"], listing["price"]["value"], listing["price"]["currency"]

def bot_loop():
    global seen_listings
    while True:
        print("👂 Controllo nuovi annunci...")
        releases = get_wantlist(DISCOGS_USER)
        print(f"📀 Wantlist caricata: {len(releases)} release")
        for rid in releases:
            result = get_latest_listing(rid)
            if not result:
                continue
            listing_id, price, currency = result
            if str(listing_id) not in seen_listings:
                seen_listings[str(listing_id)] = True
                send_telegram(f"Nuovo annuncio: https://www.discogs.com/sell/release/{rid}\nPrezzo: {price} {currency}")
        # Salva stato persistente
        with open(SEEN_LISTINGS_FILE, "w") as f:
            json.dump(seen_listings, f)
        time.sleep(CHECK_INTERVAL)

# ================= MAIN =================
if __name__ == "__main__":
    Thread(target=bot_loop, daemon=True).start()
    from flask import Flask
    app = Flask(__name__)

    @app.route("/", methods=["HEAD"])
    def health():
        return "", 200

    app.run(host="0.0.0.0", port=8080, debug=False)
