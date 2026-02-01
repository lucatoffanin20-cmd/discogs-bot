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

CHECK_INTERVAL = 600  # 10 minuti
MARKETPLACE_CHECK_LIMIT = 5
TEST_RELEASE_ID = 7334987  # ← qui metti l'ID della release da testare

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
        "WantlistWatcher/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT LOOP =================
def bot_loop():
    send_telegram(f"🧪 Bot Discogs TEST avviato – release {TEST_RELEASE_ID}")

    d = init_discogs()

    while True:
        print(f"👂 TEST – Controllo annunci release {TEST_RELEASE_ID}...")

        try:
            results = d.search(
                type="marketplace",
                release_id=TEST_RELEASE_ID,
                sort="listed",
                sort_order="desc",
                per_page=MARKETPLACE_CHECK_LIMIT,
            )

            if not results:
                print("⚠️ Nessun annuncio trovato.")
            else:
                for idx, listing in enumerate(results):
                    data = listing.data
                    price_info = data.get("price")
                    uri = data.get("uri") or data.get("resource_url")

                    if not price_info or not uri:
                        print(f"⚠️ Skipping listing #{idx+1}, price/uri mancanti")
                        continue

                    msg = (
                        f"🧪 TEST Annuncio Discogs\n\n"
                        f"📀 {data.get('title')}\n"
                        f"💰 {price_info.get('value')} {price_info.get('currency')}\n"
                        f"🏷 {data.get('condition')}\n"
                        f"🔗 https://www.discogs.com{uri}"
                    )
                    send_telegram(msg)
                    print("✅ Annuncio inviato")

        except Exception as e:
            print(f"❌ Marketplace error: {e}")

        time.sleep(CHECK_INTERVAL)


# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
