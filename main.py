import os
import threading
import requests
from requests_oauthlib import OAuth1
from flask import Flask
from dotenv import load_dotenv
import time
import json

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

# ================= CONFIG =================
CHECK_INTERVAL = 600  # 10 minuti
WANTLIST_PER_PAGE = 50
LAST_SEEN_FILE = "last_seen.json"
REQUEST_DELAY = 0.5  # 0,5 secondi di pausa tra richieste per evitare rate limit

# ================= CARICAMENTO STORICO =================
if os.path.exists(LAST_SEEN_FILE):
    with open(LAST_SEEN_FILE, "r") as f:
        last_seen = json.load(f)
else:
    last_seen = {}

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
    wants = []
    page = 1
    while True:
        url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
        params = {"per_page": WANTLIST_PER_PAGE, "page": page}
        try:
            r = requests.get(url, auth=auth, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            page_wants = data.get("wants", [])
            if not page_wants:
                break
            wants.extend(page_wants)
            if page >= data.get("pagination", {}).get("pages", 0):
                break
            page += 1
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Errore wantlist: {e}")
            time.sleep(5)
            continue
    print(f"📀 Wantlist caricata: {len(wants)} release")
    return wants

# ================= MARKETPLACE =================
def safe_get(url, params=None, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, params=params, auth=auth, timeout=10)
            if r.status_code == 429:  # rate limit
                print("⚠️ HTTP 429 ignorato, attendo 5 secondi")
                time.sleep(5)
                continue
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

def get_latest_listing(release_id):
    url = "https://api.discogs.com/marketplace/search"
    params = {"release_id": release_id, "sort": "listed", "sort_order": "desc", "per_page": 5, "page": 1}
    data = safe_get(url, params)
    results = data.get("results", []) if data else []
    return results[0] if results else None

# ================= BOT LOOP =================
def bot_loop():
    wants = get_wantlist()
    for w in wants:
        rid = w.get("id")
        if rid is None:
            continue
        listing = get_latest_listing(rid)
        time.sleep(REQUEST_DELAY)  # piccolo delay per evitare 429
        if not listing:
            continue
        lid = listing.get("id")
        if lid is None:
            continue
        # Nuovo listing
        if str(rid) not in last_seen or last_seen[str(rid)] != lid:
            last_seen[str(rid)] = lid
            # Salva lo storico persistente
            with open(LAST_SEEN_FILE, "w") as f:
                json.dump(last_seen, f)
            title = listing.get("title", "N/D")
            price = listing.get("price", {}).get("value", "N/D")
            msg = f"🎵 NUOVO ARTICOLO: {title} 💰 {price}\nhttps://www.discogs.com/sell/item/{lid}"
            send_telegram(msg)
    threading.Timer(CHECK_INTERVAL, bot_loop).start()

# ================= FLASK =================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot attivo ✅"

if __name__ == "__main__":
    send_telegram("🤖 Discogs Wantlist Notifier avviato!")
    bot_loop()
    app.run(host="0.0.0.0", port=8080)
