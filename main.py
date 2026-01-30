import os
import time
import requests
from requests_oauthlib import OAuth1
from flask import Flask
from dotenv import load_dotenv
import threading

load_dotenv()

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

auth = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, OAUTH_TOKEN, OAUTH_TOKEN_SECRET)

CHECK_INTERVAL = 180  # 3 minuti
MAX_RETRIES = 3
RETRY_DELAY = 5

last_seen = {}  # release_id -> listing_id

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

# ================= REQUEST SICURA =================
def safe_get(url, params=None):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, auth=auth, timeout=15)

            # 404 = nessun annuncio → NON è un errore
            if r.status_code == 404:
                return None

            r.raise_for_status()
            return r

        except requests.exceptions.HTTPError:
            if r.status_code == 502:
                print(f"⚠️ 502 Discogs (tentativo {attempt}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY)
            else:
                print(f"⚠️ HTTP {r.status_code} ignorato")
                return None
        except Exception as e:
            print(f"❌ Errore rete: {e}")
            return None
    return None

# ================= WANTLIST =================
def get_wantlist():
    wants = []
    page = 1

    while True:
        url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
        params = {
            "page": page,
            "per_page": 50
        }

        r = safe_get(url, params)
        if not r:
            break

        data = r.json()
        page_wants = data.get("wants", [])
        wants.extend(page_wants)

        pagination = data.get("pagination", {})
        pages = pagination.get("pages", 1)

        if page >= pages:
            break

        page += 1
        time.sleep(1)  # anti rate-limit

    print(f"📀 Wantlist caricata: {len(wants)} release")
    return wants

# ================= MARKETPLACE =================
def get_latest_listing(release_id):
    url = "https://api.discogs.com/marketplace/search"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": 5,   # <-- CRITICO
        "page": 1
    }

    r = safe_get(url, params)
    if not r:
        return None

    results = r.json().get("results", [])
    if not results:
        return None

    # prende davvero il più recente
    results.sort(key=lambda x: x.get("id", 0), reverse=True)
    return results[0]


# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🤖 Discogs Wantlist Notifier attivo (check ogni 3 min)")
    while True:
        print("👂 Controllo nuovi annunci...")
        wants = get_wantlist()

        for w in wants:
            rid = w.get("id")
            listing = get_latest_listing(rid)
            if not listing:
                continue

            lid = listing.get("id")
            title = listing.get("title", "N/D")

            if last_seen.get(rid) != lid:
                last_seen[rid] = lid
                msg = (
                    f"🎵 NUOVO ARTICOLO:\n"
                    f"{title}\n"
                    f"https://www.discogs.com/sell/item/{lid}"
                )
                send_telegram(msg)

        time.sleep(CHECK_INTERVAL)

# ================= FLASK =================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot attivo ✅"

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
