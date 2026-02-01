import os
import time
import threading
import requests
from flask import Flask

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER = os.getenv("DISCOGS_USER")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")  # token Discogs per OAuth marketplace

CHECK_INTERVAL = 600  # 10 minuti
TEST_RELEASE_INDEX = 0  # ← indice della release da testare (0 = prima release)

# ================= FLASK (Railway healthcheck) =================
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

# ================= GET WANTLIST =================
def get_wantlist():
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    headers = {"Authorization": f"Discogs token={OAUTH_TOKEN}"}
    params = {"per_page": 50, "page": 1}

    response = requests.get(url, headers=headers, params=params, timeout=10)
    if response.status_code != 200:
        print(f"❌ Errore fetching wantlist: {response.status_code}")
        return []

    data = response.json()
    return [w["basic_information"]["id"] for w in data.get("wants", [])]

# ================= GET MARKETPLACE =================
def get_marketplace_listings(release_id, limit=5):
    url = "https://api.discogs.com/marketplace/search"
    headers = {"Authorization": f"Discogs token={OAUTH_TOKEN}"}
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": limit,
        "page": 1
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            print(f"❌ Marketplace error: {r.status_code}")
            return []
        return r.json().get("results", [])
    except Exception as e:
        print(f"❌ Marketplace request failed: {e}")
        return []

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🧪 Bot Discogs TEST avviato (1 release)")

    wantlist = get_wantlist()
    if not wantlist:
        print("❌ Wantlist vuota")
        return

    release_id = wantlist[TEST_RELEASE_INDEX]
    print(f"📀 Release selezionata: {release_id}")

    while True:
        print("👂 Controllo annunci release di test...")

        listings = get_marketplace_listings(release_id)
        if not listings:
            print("⚠️ Nessun annuncio trovato")
        else:
            for item in listings:
                title = item.get("title", "Titolo sconosciuto")
                price = item.get("price", {})
                price_value = price.get("value", "?")
                price_currency = price.get("currency", "?")
                condition = item.get("condition", "?")
                uri = item.get("uri", "?")

                msg = (
                    f"🧪 TEST Annuncio Discogs\n\n"
                    f"📀 {title}\n"
                    f"💰 {price_value} {price_currency}\n"
                    f"🏷 {condition}\n"
                    f"🔗 {uri}"
                )
                send_telegram(msg)
                print("✅ Messaggio inviato")
                time.sleep(2)

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
