from dotenv import load_dotenv
load_dotenv()

import os
import requests
import time
import json
from flask import Flask
from requests.exceptions import RequestException

print("🤖 Bot Discogs avviato!")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER = os.getenv("DISCOGS_USER")
DISCOGS_USER_TOKEN = os.getenv("DISCOGS_USER_TOKEN")

CHECK_INTERVAL = 300
DELAY_BETWEEN_CALLS = 1.5
STATE_FILE = "seen_items.json"

HEADERS = {
    "User-Agent": "DiscogsWantlistNotifier/1.0",
    "Authorization": f"Discogs token={DISCOGS_USER_TOKEN}"
}

# ===== LOAD STATE =====
seen_items = set()
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r") as f:
            seen_items = set(json.load(f))
    except:
        seen_items = set()

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen_items), f)

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print("❌ Telegram error:", e)

def get_wantlist(page):
    r = requests.get(
        f"https://api.discogs.com/users/{DISCOGS_USER}/wants",
        headers=HEADERS,
        params={"page": page, "per_page": 50},
        timeout=15
    )
    r.raise_for_status()
    return r.json()

def check_marketplace(release_id):
    r = requests.get(
        "https://api.discogs.com/marketplace/search",
        headers=HEADERS,
        params={"release_id": release_id, "per_page": 10},
        timeout=15
    )
    if r.status_code == 429:
        print("⚠️ Rate limit Discogs, sleep 60s")
        time.sleep(60)
        return []
    r.raise_for_status()
    return r.json().get("results", [])

# ===== BOT LOOP =====
def run_bot():
    send_telegram("🤖 Bot Discogs ONLINE")
    print("✅ Bot Discogs in esecuzione")

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

                if page >= data["pagination"]["pages"]:
                    break
                page += 1

            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print("❌ Errore bot:", e)
            time.sleep(60)

# ===== FLASK =====
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot Discogs attivo ✅"

# ===== START =====
if __name__ == "__main__":
    import threading
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=3000)

