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

# 🔴 MODALITÀ TEST
TEST_MODE = True
TEST_RELEASES = [7334987, 1502804]  # ID release da testare

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
    send_telegram("🧪 BOT TEST – controllo listing.data robusto")

    d = init_discogs()
    release_ids = TEST_RELEASES if TEST_MODE else []

    print(f"📀 Controllo release: {release_ids}")

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
                    print(f"⚠️ Nessun annuncio trovato per release {rid}")
                    continue

                for idx, listing in enumerate(results, start=1):
                    data = getattr(listing, 'data', {})
                    title = data.get('title', 'N/D')
                    uri = data.get('uri') or data.get('resource_url', 'N/D')
                    price_data = data.get('price')
                    price_str = f"{price_data['value']} {price_data['currency']}" if price_data else "Prezzo N/D"
                    condition = getattr(listing, 'condition', 'N/D')

                    msg = (
                        f"🧪 TEST Annuncio Discogs\n\n"
                        f"📀 {title}\n"
                        f"💰 {price_str}\n"
                        f"🏷 {condition}\n"
                        f"🔗 https://www.discogs.com{uri}"
                    )

                    send_telegram(msg)
                    print(f"✅ Listing #{idx} inviato per release {rid}")
                    return  # STOP dopo il primo listing trovato per test

            except Exception as e:
                print(f"❌ Marketplace error release {rid}: {e}")

        time.sleep(CHECK_INTERVAL)


# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
