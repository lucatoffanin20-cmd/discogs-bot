import os
import time
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

CHECK_INTERVAL = 300  # 5 minuti

# 🔴 TEST MODE
TEST_RELEASES = [1496650]  # release con annunci attivi

# ================= FLASK =================
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
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
        "DiscogsTestBot/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🧪 BOT TEST avviato")

    d = init_discogs()
    print(f"🧪 TEST MODE – release controllate: {TEST_RELEASES}")

    while True:
        print("👂 Controllo annunci...")

        for rid in TEST_RELEASES:
            try:
                results = d.search(
                    type="marketplace",
                    release_id=rid,
                    per_page=3,
                )

                if not results:
                    print(f"⚠️ Nessun annuncio per release {rid}")
                    continue

                for listing in results:
                    listing_id = listing.id
                    link = f"https://www.discogs.com/sell/item/{listing_id}"

                    msg = (
                        f"🧪 TEST Annuncio Discogs\n\n"
                        f"📀 {listing.title}\n"
                        f"🔗 {link}"
                    )

                    send_telegram(msg)
                    print("✅ Notifica inviata")
                    return  # 🔴 stop dopo il primo annuncio (test)

            except Exception as e:
                print(f"❌ Errore release {rid}: {e}")

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
