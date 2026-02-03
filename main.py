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
DISCOGS_USER = os.getenv("DISCOGS_USER")

CHECK_INTERVAL = 300          # 5 minuti per test/produzione
MARKETPLACE_CHECK_LIMIT = 5  # quanti listing recenti controllare

# 🔴 TEST MODE
TEST_MODE = True
TEST_RELEASES = [1496650]  # ID release da testare

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
        "DiscogsNotifierTest/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🧪 Bot Discogs TEST avviato")

    d = init_discogs()

    # SOLO TEST RELEASE
    release_ids = TEST_RELEASES
    print(f"🧪 TEST MODE – release controllate: {release_ids}")

    while True:
        print("👂 Controllo annunci...")

        for rid in release_ids:
            try:
                results = d.search(
                    type="marketplace",
                    release_id=rid,
                    sort="listed",
                    sort_order="desc",
                    per_page=MARKETPLACE_CHECK_LIMIT,
                )

                if not results:
                    print(f"⚠️ Nessun annuncio per release {rid}")
                    continue

                for listing in results:
                    # 🔑 LINK robusto: usa listing.uri sempre disponibile
                    uri = getattr(listing, "uri", None)
                    if not uri:
                        print("⚠️ Listing senza uri, skip")
                        continue

                    msg = (
                        f"🧪 TEST – Annuncio Discogs trovato\n\n"
                        f"📀 {listing.title}\n"
                        f"🏷 {listing.condition}\n"
                        f"🔗 https://www.discogs.com{uri}"
                    )

                    send_telegram(msg)
                    print("✅ Annuncio inviato correttamente")
                    return  # STOP DOPO IL PRIMO listing in test

            except discogs_client.exceptions.HTTPError as e:
                print(f"❌ HTTP error release {rid}: {e}")
            except Exception as e:
                print(f"❌ Errore release {rid}: {e}")

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
