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
SEEN_FILE = "seen.json"
MARKETPLACE_CHECK_LIMIT = 5  # quanti annunci recenti controllare
DAILY_REPORT_HOUR = 22  # ora in cui inviare il riepilogo giornaliero

# ================= FLASK =================
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "", 200

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"❌ Errore invio Telegram: {e}")

# ================= SEEN STORAGE =================
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

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
    send_telegram("🤖 Bot Discogs avviato")

    d = init_discogs()
    try:
        user = d.user(DISCOGS_USER)
        wantlist = list(user.wantlist)
        release_ids = [w.release.id for w in wantlist]
        print(f"📀 Wantlist caricata: {len(release_ids)} release")
    except Exception as e:
        print(f"❌ Errore fetching wantlist: {e}")
        send_telegram(f"❌ Errore fetching wantlist: {e}")
        return

    seen = load_seen()
    daily_messages = []

    while True:
        print("👂 Controllo nuovi annunci...")
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
                    continue

                for idx, listing in enumerate(results, start=1):
                    listing_id = f"{rid}_{listing.id}"
                    if listing_id in seen:
                        continue
                    seen.add(listing_id)
                    save_seen(seen)

                    price = getattr(listing, "price", None)
                    uri = getattr(listing, "uri", None)

                    msg = f"🆕 Nuovo annuncio Discogs\n\n📀 {listing.title}"
                    if price:
                        msg += f"\n💰 {price.value} {price.currency}"
                    if hasattr(listing, "condition"):
                        msg += f"\n🏷 {listing.condition}"
                    if uri:
                        msg += f"\n🔗 {uri}"

                    send_telegram(msg)
                    daily_messages.append(msg)
                    print(f"✅ Listing #{idx} inviato per release {rid}")
                    time.sleep(1)

            except Exception as e:
                print(f"❌ Marketplace error release {rid}: {e}")
                time.sleep(2)

        # Riepilogo giornaliero
        now = datetime.now()
        if now.hour == DAILY_REPORT_HOUR and daily_messages:
            report_msg = "📊 Riepilogo giornaliero Discogs:\n\n" + "\n\n".join(daily_messages)
            send_telegram(report_msg)
            daily_messages = []  # reset

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
