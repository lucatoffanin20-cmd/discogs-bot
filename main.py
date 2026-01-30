import os
import time
import requests
import threading
from flask import Flask

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

PER_PAGE = 50          # release per pagina wantlist
CHECK_INTERVAL = 600   # 10 minuti in secondi

app = Flask(__name__)
seen_releases = set()  # memorizza release già notificate

# ================= FUNZIONI =================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"⚠️ Errore invio Telegram: {e}")

def safe_get(url, params=None):
    for attempt in range(3):
        try:
            r = requests.get(url, params=params)
            if r.status_code == 429:  # rate limit
                print("⚠️ HTTP 429 ignorato, attendo 5 secondi")
                time.sleep(5)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if r.status_code == 404:
                # Non esiste nessun annuncio → interrompi retry
                return None
            print(f"⚠️ Marketplace error (tentativo {attempt+1}/3): {e}")
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ Errore generico: {e}")
            time.sleep(2)
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

def check_marketplace(release_id):
    url = "https://api.discogs.com/marketplace/search"
    params = {"release_id": release_id, "sort": "listed", "sort_order": "desc", "per_page": 5, "page": 1}
    data = safe_get(url, params)
    if data and "results" in data and data["results"]:
        return True
    return False

def bot_loop():
    global seen_releases
    while True:
        print("👂 Controllo nuovi annunci...")
        wantlist = get_wantlist(DISCOGS_USER)
        print(f"📀 Wantlist caricata: {len(wantlist)} release")
        for rid in wantlist:
            if rid in seen_releases:
                continue
            if check_marketplace(rid):
                send_telegram(f"Nuovo annuncio trovato! Release ID: {rid}")
                seen_releases.add(rid)
        time.sleep(CHECK_INTERVAL)

# ================= AVVIO =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    send_telegram("🤖 Bot avviato e in esecuzione")
    app.run(host="0.0.0.0", port=8080)
