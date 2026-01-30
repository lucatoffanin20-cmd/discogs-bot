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

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🤖 Bot Discogs avviato")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    try:
        wantlist = list(user.wantlist)
        release_ids = [w.id for w in wantlist]  # ✅ FIX QUI
        print(f"📀 Wantlist caricata: {len(release_ids)} release")
    except Exception as e:
        print(f"❌ Errore wantlist: {e}")
        return

    seen = load_seen()

    while True:
        print("👂 Controllo nuovi annunci...")

        for rid in release_ids:
            try:
                listings = d.search(
                    release_id=rid,
                    type="marketplace",
                    sort="listed",
                    sort_order="desc",
                )

                if not listings:
                    continue

                listing = listings[0]
                listing_id = str(listing.id)

                if listing_id in seen:
                    continue

                seen.add(listing_id)
                save_seen(seen)

                msg = (
                    f"🆕 Nuovo annuncio Discogs\n\n"
                    f"📀 {listing.release.title}\n"
                    f"💰 {listing.price['value']} {listing.price['currency']}\n"
                    f"🏷 {listing.condition}\n"
                    f"🔗 {listing.uri}"
                )

                send_telegram(msg)
                time.sleep(2)

            except Exception as e:
                print(f"⚠️ Errore release {rid}: {e}")
                time.sleep(2)

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
