import os
import time
import json
import threading
import requests
import discogs_client
from datetime import datetime
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

# 🔴 MODALITÀ TEST
TEST_MODE = True        # ← True per test
TEST_RELEASE_COUNT = 1   # ← quante release controllare in test

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

    wantlist = list(user.wantlist)
    release_ids = [w.release.id for w in wantlist]

    if TEST_MODE:
        release_ids = release_ids[:TEST_RELEASE_COUNT]
        send_telegram(f"🧪 MODALITÀ TEST: {len(release_ids)} release")

    print(f"📀 Wantlist caricata: {len(release_ids)} release")

    seen = load_seen()
    today_notified = set()
    last_summary_day = None

    while True:
        print("👂 Controllo nuovi annunci...")

        for rid in release_ids:
            try:
                results = d.search(
                    type="marketplace",
                    release_id=rid,
                    sort="listed",
                    sort_order="desc",
                    per_page=5,
                )

                for item in results:
                    if not hasattr(item, "price"):
                        continue

                    listing_id = str(item.id)
                    if listing_id in seen:
                        continue

                    seen.add(listing_id)
                    today_notified.add(listing_id)
                    save_seen(seen)

                    msg = (
                        f"🆕 Nuovo annuncio Discogs\n\n"
                        f"📀 {item.release.title}\n"
                        f"💰 {item.price.value} {item.price.currency}\n"
                        f"🏷 {item.condition}\n"
                        f"🔗 {item.uri}"
                    )

                    send_telegram(msg)
                    time.sleep(2)

                time.sleep(1.2)  # rate limit Discogs

            except Exception as e:
                print(f"⚠️ Errore release {rid}: {e}")
                time.sleep(2)

        # ===== RIEPILOGO ORE 22 =====
        now = datetime.now()
        if now.hour == 22 and last_summary_day != now.date():
            last_summary_day = now.date()

            if today_notified:
                send_telegram(
                    f"📊 Riepilogo giornaliero\n"
                    f"🆕 Annunci trovati oggi: {len(today_notified)}"
                )
                today_notified.clear()
            else:
                send_telegram("📊 Riepilogo giornaliero\nNessun nuovo annuncio oggi")

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
