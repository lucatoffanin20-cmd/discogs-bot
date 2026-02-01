import os
import time
import threading
import discogs_client
import requests
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
MARKETPLACE_CHECK_LIMIT = 20  # annunci da controllare per release

# ================= FLASK (Railway) =================
app = Flask(__name__)

@app.route("/", methods=["HEAD", "GET"])
def health():
    return "", 200

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    requests.post(url, data=data, timeout=10)

# ================= DISCOGS =================
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
    send_telegram("🧪 Bot Discogs TEST (OAuth marketplace)")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    wantlist = list(user.wantlist)
    release_ids = [w.release.id for w in wantlist]

    print(f"📀 Wantlist caricata: {len(release_ids)} release")

    while True:
        print("👂 TEST – Controllo annunci...")

        for rid in release_ids:
            try:
                results = d.marketplace.search(
                    release_id=rid,
                    sort="listed",
                    sort_order="desc",
                    per_page=MARKETPLACE_CHECK_LIMIT,
                )

                for listing in results:
                    msg = (
                        f"🧪 TEST Annuncio Discogs\n\n"
                        f"📀 {listing.release.title}\n"
                        f"💰 {listing.price.value} {listing.price.currency}\n"
                        f"🏷 {listing.condition}\n"
                        f"🔗 {listing.uri}"
                    )
                    send_telegram(msg)
                    time.sleep(1)

            except Exception as e:
                print(f"⚠️ Errore release {rid}: {e}")
                time.sleep(2)

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
