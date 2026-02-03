import os
import time
import threading
import json
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

CHECK_INTERVAL = 120  # controlla ogni 2 minuti
MARKETPLACE_CHECK_LIMIT = 10  # controlla i listing più recenti

SEEN_FILE = "seen.json"

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
        print(f"❌ Errore Telegram: {e}")

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
        "WantlistNotifier/2.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🤖 Bot Discogs avviato")
    d = init_discogs()
    seen = load_seen()

    try:
        user = d.user(DISCOGS_USER)
        wantlist = list(user.wantlist)
        release_ids = [w.release.id for w in wantlist]
        print(f"📀 Wantlist caricata: {len(release_ids)} release")
    except Exception as e:
        print(f"❌ Errore fetching wantlist: {e}")
        send_telegram(f"❌ Errore fetching wantlist: {e}")
        return

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

                active_listings = []
                for listing in results:
                    # ✅ solo listing attivi
                    if getattr(listing, "status", "") != "For Sale":
                        continue

                    listing_id = str(listing.id)
                    if listing_id in seen:
                        continue

                    seen.add(listing_id)
                    # raccoglie listing attivi per messaggio unico
                    active_listings.append(listing)

                if active_listings:
                    msg_lines = [f"🆕 Nuovi annunci per release: {active_listings[0].title}\n"]
                    for l in active_listings:
                        # link diretto alla pagina del listing
                        msg_lines.append(f"🔗 https://www.discogs.com/sell/release/{l.id}")
                    send_telegram("\n".join(msg_lines))
                    print(f"✅ Notifica inviata release {rid} con {len(active_listings)} listing")
                    save_seen(seen)

                time.sleep(1)  # pausa tra release per rispettare API

            except discogs_client.exceptions.HTTPError as e:
                if "429" in str(e):
                    print(f"⚠️ Troppe richieste, aspetto 60 secondi...")
                    time.sleep(60)
                else:
                    print(f"❌ Marketplace error release {rid}: {e}")
            except Exception as e:
                print(f"❌ Errore release {rid}: {e}")

        print(f"⏱ Pausa {CHECK_INTERVAL} secondi prima del prossimo controllo")
        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
