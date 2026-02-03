import os
import time
import threading
import json
import requests
import discogs_client
from flask import Flask

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

CHECK_INTERVAL = 540        # 9 minuti tra i cicli
SLEEP_PER_RELEASE = 1.2     # pausa tra ogni release (CRUCIALE)
PER_PAGE = 5               # 5 listing (i più recenti)

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
    except:
        pass

# ================= SEEN =================
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
        "WantlistNotifier/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT =================
def bot_loop():
    send_telegram("🤖 Bot Discogs avviato")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    try:
        wantlist = list(user.wantlist)
        release_ids = [w.release.id for w in wantlist]
        print(f"📀 Wantlist caricata: {len(release_ids)} release")
    except Exception as e:
        send_telegram(f"❌ Errore wantlist: {e}")
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
                    per_page=PER_PAGE,
                )

                for listing in results:
                    listing_id = str(listing.id)

                    if listing_id in seen:
                        continue

                    # usa SEMPRE uri (non resource_url)
                    uri = getattr(listing, "uri", None)
                    if not uri:
                        continue

                    seen.add(listing_id)
                    save_seen(seen)

                    msg = (
                        "🆕 Nuovo annuncio Discogs\n\n"
                        f"📀 {listing.title}\n"
                        f"🏷 {listing.condition}\n"
                        f"🔗 {uri}"
                    )
                    send_telegram(msg)

                time.sleep(SLEEP_PER_RELEASE)

            except discogs_client.exceptions.HTTPError as e:
                if "429" in str(e):
                    print("⚠️ 429 ricevuto → pausa 120s")
                    time.sleep(120)
                else:
                    print(f"❌ Errore release {rid}: {e}")

            except Exception as e:
                print(f"❌ Errore release {rid}: {e}")

        print(f"⏱ Pausa {CHECK_INTERVAL}s prima del prossimo ciclo")
        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
