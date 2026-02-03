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

CHECK_INTERVAL = 60  # intervallo tra i controlli in secondi
MARKETPLACE_CHECK_LIMIT = 5
SEEN_FILE = "seen.json"

# ================= FLASK =================
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "", 200

# ================= TELEGRAM =================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"❌ Errore invio Telegram: {e}")

# ================= SEEN STORAGE =================
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ================= DISCOGS =================
def init_discogs():
    return discogs_client.Client(
        "WantlistNotifier/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🤖 Bot Discogs avviato (massima visibilità)")

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

                for listing in results:
                    listing_id = str(listing.id)
                    if listing_id in seen:
                        continue

                    seen.add(listing_id)

                    uri = getattr(listing, "uri", None)
                    if not uri:
                        uri_msg = "🔗 Link non disponibile"
                        print(f"⚠️ Listing senza uri, notificato comunque")
                    else:
                        uri_msg = f"🔗 {uri}"

                    msg = (
                        f"🆕 Nuovo annuncio Discogs\n\n"
                        f"📀 {listing.title}\n"
                        f"ID release: {rid}\n"
                        f"{uri_msg}"
                    )

                    send_telegram(msg)
                    time.sleep(2)  # pausa tra notifiche Telegram

                time.sleep(1)  # pausa tra le release per rate limit

            except discogs_client.exceptions.HTTPError as e:
                if "429" in str(e):
                    print("⚠️ Troppe richieste, aspetto 60 secondi...")
                    time.sleep(60)
                else:
                    print(f"❌ Marketplace error release {rid}: {e}")
            except Exception as e:
                print(f"❌ Errore release {rid}: {e}")

        save_seen(seen)
        print(f"⏱ Pausa {CHECK_INTERVAL} secondi prima del prossimo controllo")
        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
