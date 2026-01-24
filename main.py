print("🤖 Bot Discogs avviato! In ascolto...")

# ===== IMPORT =====
from dotenv import load_dotenv
load_dotenv()  # carica eventuali variabili dal file .env locale
import os
import requests
import time
import json
import threading
from flask import Flask
from requests.exceptions import RequestException, HTTPError

# ===== VARIABILI D'AMBIENTE CON FALLBACK =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER = os.getenv("DISCOGS_USER") or "tuo_username_discogs"  # fallback sicuro
DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN")

# Controllo rapido (avviso, non blocca più l'esecuzione)
if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCOGS_USER, DISCOGS_TOKEN]):
    print("⚠️ Attenzione: alcune variabili potrebbero non essere impostate correttamente!")

# ===== INTERVALLI =====
CHECK_INTERVAL = 300        # 5 minuti tra controlli
DELAY_BETWEEN_CALLS = 1.2
STATE_FILE = "seen_items.json"

HEADERS = {
    "User-Agent": "DiscogsWantlistNotifier/1.0",
    "Authorization": f"Discogs token={DISCOGS_TOKEN}"
}

# ===== LOAD STATO =====
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
        seen_items = set(json.load(f))
else:
    seen_items = set()

# ===== SALVA STATO =====
def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen_items), f)

# ===== TELEGRAM =====
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram non configurato. Messaggio non inviato:", msg)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
        r.raise_for_status()
    except RequestException as e:
        print("Errore Telegram:", e)

# ===== DISCOGS =====
def get_wantlist(page=1):
    url = f"https://api.discogs.com/users/{DISCOGS_USER}/wants"
    r = requests.get(url, headers=HEADERS, params={"page": page, "per_page": 100})
    r.raise_for_status()
    return r.json()

def check_marketplace(release_id):
    url = "https://api.discogs.com/marketplace/search"
    r = requests.get(url, headers=HEADERS, params={"release_id": release_id})
    try:
        r.raise_for_status()
    except HTTPError as e:
        if r.status_code == 429:
            print("⚠️ Rate limit Discogs, pausa 60s")
            time.sleep(60)
            return []
        raise e
    return r.json().get("results", [])

# ===== BOT LOOP =====
def discogs_bot():
    send_telegram("🤖 Bot Discogs avviato correttamente!")
    print("Bot Discogs avviato e in ascolto…")

    while True:
        try:
            page = 1
            while True:
                data = get_wantlist(page)
                wants = data.get("wants", [])

                for item in wants:
                    release_id = item["id"]
                    listings = check_marketplace(release_id)

                    for l in listings:
                        uid = str(l["id"])
                        if uid not in seen_items:
                            seen_items.add(uid)
                            save_state()
                            msg = (
                                f"🎵 NUOVO ARTICOLO!\n"
                                f"{l['title']}\n"
                                f"Prezzo: {l['price']['value']} {l['price']['currency']}\n"
                                f"https://www.discogs.com/sell/item/{uid}"
                            )
                            send_telegram(msg)

                    time.sleep(DELAY_BETWEEN_CALLS)

                if page >= data.get("pagination", {}).get("pages", 1):
                    break
                page += 1

            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print("Errore generale:", e)
            time.sleep(60)

# ===== FLASK SERVER (UPTIME ROBOT) =====
app = Flask(__name__)

@app.route("/")
def home():
    print("Ping ricevuto ✅")   # log per Uptime Robot
    return "Bot Discogs attivo ✅"

@app.route("/ping")
def ping():
    return "Bot online e attivo ✅", 200

# ===== START =====
if __name__ == "__main__":
    threading.Thread(target=discogs_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=3000)
