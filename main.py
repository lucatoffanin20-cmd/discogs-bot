import os
import threading
import requests
from flask import Flask
from dotenv import load_dotenv
import time

load_dotenv()

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER = os.getenv("DISCOGS_USER")

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCOGS_USER]):
    missing = [v for v, val in {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "DISCOGS_USER": DISCOGS_USER
    }.items() if not val]
    print(f"❌ Variabili mancanti o vuote: {missing}")
    exit(1)

# ================= TELEGRAM =================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ Errore invio Telegram: {e}")

# ================= WANTLIST =================
def get_wantlist():
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    try:
        r = requests.get(url)
        r.raise_for_status()
        wants = r.json().get("wants", [])
        print(f"📀 Wantlist caricata: {len(wants)} release")
        return wants
    except requests.exceptions.HTTPError as e:
        print(f"⚠️ Errore wantlist: {e}")
        return []
    except Exception as e:
        print(f"⚠️ Errore wantlist generico: {e}")
        return []

# ================= MARKETPLACE =================
def get_latest_listing(release_id):
    url = "https://api.discogs.com/marketplace/search"
    params = {"release_id": release_id, "sort": "listed", "sort_order": "desc", "per_page": 5, "page": 1}
    retries = 3
    for attempt in range(1, retries+1):
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
            return results[0] if results else None
        except requests.exceptions.HTTPError as e:
            if r.status_code == 502:
                print(f"⚠️ 502 Discogs (tentativo {attempt}/{retries})")
                time.sleep(2)
                continue
            elif r.status_code == 429:
                print("⚠️ HTTP 429 ignorato")
                time.sleep(5)
                continue
            else:
                print(f"⚠️ Marketplace error ({release_id}): {e}")
                return None
        except Exception as e:
            print(f"⚠️ Errore generico marketplace ({release_id}): {e}")
            return None
    return None

# ================= BOT LOOP =================
CHECK_INTERVAL = 180  # ogni 3 minuti
last_seen = {}  # release_id -> listing_id

def bot_loop():
    wants = get_wantlist()
    for w in wants:
        rid = w.get("id")
        listing = get_latest_listing(rid)
        if not listing:
            continue
        lid = listing.get("id")
        if lid is None:
            continue

        old_lid = last_seen.get(rid)
        if old_lid != lid:
            last_seen[rid] = lid
            msg = f"🎵 NUOVO ARTICOLO: {listing.get('title', 'N/D')}\nhttps://www.discogs.com/sell/item/{lid}"
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
