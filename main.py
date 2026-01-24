print("🤖 Bot Discogs avviato! In ascolto...")

# ===== IMPORT =====
from dotenv import load_dotenv
load_dotenv()
import os
import requests
import time
import json
import threading
from requests.exceptions import RequestException, HTTPError

# ===== VARIABILI D'AMBIENTE =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER = os.getenv("DISCOGS_USER") or "tuo_username_discogs"
DISCOGS_USER_TOKEN = os.getenv("DISCOGS_USER_TOKEN")

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCOGS_USER, DISCOGS_USER_TOKEN]):
    print("⚠️ Attenzione: alcune variabili potrebbero non essere impostate correttamente!")

# ===== INTERVALLI =====
CHECK_INTERVAL = 300        # ogni 5 minuti
DELAY_BETWEEN_CALLS = 1.2
STATE_FILE = "seen_items.json"

HEADERS = {
    "User-Agent": "DiscogsWantlistNotifier/1.0",
    "Authorization": f"Discogs token={DISCOGS_USER_TOKEN}"
}

# ===== LOAD STATO =====
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
        seen_items = set(json.load(f))
else:
    seen_items = set()

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen_items), f)

# ===== TELEGRAM =====
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
        r.raise_for_status()
    except RequestException as e:
        print(f"⚠️ Errore Telegram: {e}")

# ===== DISCOGS =====
def get_wantlist(page=1):
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    r = requests.get(url, headers=HEADERS, params={"page": page, "per_page": 100})
    r.raise_for_status()
    return r.json()

def check_marketplace(release_id):
    url = "https://api.discogs.com/marketplace/search"
    try:
        r = requests.get(url, headers=HEADERS, params={"release_id": release_id})
        r.raise_for_status()
        return r.json().get("results", [])
    except HTTPError as e:
        if r.status_code == 404:
            return []  # silenzia 404
        elif r.status_code == 429:
            print("⚠️ Rate limit Discogs, pausa 60s")
            time.sleep(60)
            return []
        else:
            print(f"⚠️ Errore Discogs ({release_id}): {e}")
            return []

# ===== BOT LOOP =====
def discogs_bot():
    send_telegram("🤖 Bot Discogs avviato correttamente!")
    print("Bot Discogs avviato e in ascolto…")

    last_ping = time.time()

    while True:
        try:
            # Log regolare per dimostrare che il bot è attivo
            if time.time() - last_ping > 60:
                print("⏱ Bot attivo, controllo wantlist…")
                last_ping = time.time()

            page = 1
            while True:
                data = get_wantlist(page)
                wants = data.get("wants", [])

                for item in wants:
                    release_id = item["id"]
                    listings = check_marketplace(release_id)

                    new_listings = [l for l in listings if str(l["id"]) not in seen_items]

                    for l in new_listings:
                        uid = str(l["id"])
                        seen_items.add(uid)
                        save_state()
                        msg = (
                            f"🎵 NUOVO ARTICOLO!\n"
                            f"{l['title']}\n"
                            f"Prezzo: {l['price']['value']} {l['price']['currency']}\n"
                            f"https://www.discogs.com/sell/item/{uid}"
                        )
                        send_telegram(msg)
                        print(f"✅ Notifica inviata: {l['title']}")

                    time.sleep(DELAY_BETWEEN_CALLS)

                if page >= data.get("pagination", {}).get("pages", 1):
                    break
                page += 1

            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"⚠️ Errore generale: {e}")
            time.sleep(60)

# ===== START =====
if __name__ == "__main__":
    threading.Thread(target=discogs_bot, daemon=True).start()
    # Flask non serve più per worker puro, quindi rimosso per semplicità
    while True:
        time.sleep(60)
