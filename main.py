import os
import time
import json
import threading
import requests
import discogs_client
from flask import Flask

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

CHECK_INTERVAL = 600  # 10 minuti
SEEN_FILE = "seen.json"
MARKETPLACE_CHECK_LIMIT = 5  # quanti annunci recenti controllare per release

# ================= FLASK (per Railway) =================
app = Flask(__name__)

@app.route("/", methods=["HEAD", "GET"])
def health():
    return "", 200

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    requests.post(url, data=data, timeout=10)

# ================= SEEN STORAGE =================
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ================= DISCOGS CLIENT =================
def init_discogs():
    return discogs_client.Client(
        "WantlistWatcher/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= MARKETPLACE =================
def get_latest_listings(release_id, limit=5):
    url = "https://api.discogs.com/marketplace/search"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": limit,
        "page": 1,
    }
    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        return []

    return r.json().get("results", [])

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🧪 Bot Discogs TEST (senza memoria)")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    wantlist = list(user.wantlist)
    release_ids = [w.release.id for w in wantlist]

    while True:
        print("👂 TEST – Controllo annunci...")

        for rid in release_ids:
            try:
                listings = get_latest_listings(rid)
                for listing in listings:
                    msg = (
                        f"🧪 TEST Annuncio\n\n"
                        f"📀 {listing['title']}\n"
                        f"💰 {listing['price']['value']} {listing['price']['currency']}\n"
                        f"🔗 {listing['uri']}"
                    )
                    send_telegram(msg)
                    time.sleep(1)

            except Exception as e:
                print(f"⚠️ Errore release {rid}: {e}")

        time.sleep(CHECK_INTERVAL)


# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
