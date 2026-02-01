import os
import time
import json
import threading
import requests
import discogs_client
from flask import Flask
from datetime import datetime

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

CHECK_INTERVAL = 600  # 10 minuti
MARKETPLACE_CHECK_LIMIT = 5
SEEN_FILE = "seen.json"

# 🧪 TEST
TEST_MODE = False            # True = invia solo 1 annuncio e stop
TEST_ONLY_FIRST_RELEASE = False

# ================= FLASK (Railway) =================
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "", 200

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    requests.post(url, data=data, timeout=10)

# ================= SEEN =================
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

# ================= MARKETPLACE (REST PURO) =================
def get_latest_listings(release_id, limit=5):
    url = "https://api.discogs.com/marketplace/search"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": limit,
        "page": 1,
    }

    headers = {
        "User-Agent": "WantlistWatcher/1.0",
        "Authorization": (
            f'OAuth oauth_consumer_key="{CONSUMER_KEY}", '
            f'oauth_token="{OAUTH_TOKEN}", '
            f'oauth_signature_method="PLAINTEXT", '
            f'oauth_signature="{CONSUMER_SECRET}&{OAUTH_TOKEN_SECRET}"'
        ),
    }

    r = requests.get(url, params=params, headers=headers, timeout=20)
    if r.status_code != 200:
        print("❌ Marketplace error:", r.status_code)
        return []

    return r.json().get("results", [])

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🤖 Bot Discogs AVVIATO")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    wantlist = list(user.wantlist)
    release_ids = [w.release.id for w in wantlist]

    print(f"📀 Wantlist caricata: {len(release_ids)} release")

    seen = load_seen()

    while True:
        print("👂 Controllo nuovi annunci...")

        for idx, rid in enumerate(release_ids):

            if TEST_ONLY_FIRST_RELEASE and idx > 0:
                break

            try:
                listings = get_latest_listings(rid, MARKETPLACE_CHECK_LIMIT)

                for l in listings:
                    listing_id = str(l["id"])

                    if listing_id in seen:
                        continue

                    seen.add(listing_id)
                    save_seen(seen)

                    msg = (
                        f"🆕 Nuovo annuncio Discogs\n\n"
                        f"📀 {l['title']}\n"
                        f"💰 {l['price']['value']} {l['price']['currency']}\n"
                        f"🏷 {l['condition']}\n"
                        f"🔗 {l['uri']}"
                    )

                    send_telegram(msg)
                    print("✅ Annuncio inviato:", listing_id)
                    time.sleep(2)

                    if TEST_MODE:
                        return  # 🔴 STOP immediato in test

            except Exception as e:
                print(f"⚠️ Errore release {rid}: {e}")
                time.sleep(2)

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
