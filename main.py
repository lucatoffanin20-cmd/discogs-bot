import os
import time
import threading
import requests
import discogs_client
from flask import Flask

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER = os.getenv("DISCOGS_USER")
DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN")  # personal access token

CHECK_INTERVAL = 600  # 10 minuti
MARKETPLACE_CHECK_LIMIT = 5  # quanti annunci recenti controllare

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
        user_token=DISCOGS_TOKEN
    )

# ================= MARKETPLACE =================
def get_latest_listings(release_id, limit=MARKETPLACE_CHECK_LIMIT):
    url = "https://api.discogs.com/marketplace/search"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": limit,
        "page": 1
    }
    headers = {"Authorization": f"Discogs token={DISCOGS_TOKEN}"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=20)
        if r.status_code != 200:
            print(f"❌ Marketplace error: {r.status_code}")
            return []
        return r.json().get("results", [])
    except Exception as e:
        print(f"❌ Errore fetching marketplace: {e}")
        return []

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🧪 Bot Discogs TEST: controllo 1 release")

    d = init_discogs()
    try:
        user = d.user(DISCOGS_USER)
        wantlist = list(user.wantlist)
        if not wantlist:
            print("❌ Wantlist vuota")
            return
    except Exception as e:
        print(f"❌ Errore fetching wantlist: {e}")
        return

    release_id = wantlist[0].release.id  # TEST: prima release
    print(f"📀 Test sulla release: {release_id}")

    while True:
        print("👂 Controllo annunci...")

        listings = get_latest_listings(release_id)
        if not listings:
            print("⚠️ Nessun annuncio trovato")
        for item in listings:
            msg = (
                f"🧪 TEST Annuncio Discogs\n\n"
                f"📀 {item['title']}\n"
                f"💰 {item['price']['value']} {item['price']['currency']}\n"
                f"🏷 {item['condition']}\n"
                f"🔗 {item['uri']}"
            )
            send_telegram(msg)
            print("✅ Messaggio inviato")
            time.sleep(1)

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
