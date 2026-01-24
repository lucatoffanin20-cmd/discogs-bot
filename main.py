from dotenv import load_dotenv
load_dotenv()

import os
import requests
import time
import json

print("🤖 Bot Discogs avviato!")

# ===== ENV =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER = os.getenv("DISCOGS_USER")
DISCOGS_USER_TOKEN = os.getenv("DISCOGS_USER_TOKEN")

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DISCOGS_USER, DISCOGS_USER_TOKEN]):
    raise RuntimeError("❌ Variabili d'ambiente mancanti su Railway")

# ===== CONFIG =====
CHECK_INTERVAL = 300  # 5 minuti
DELAY_BETWEEN_CALLS = 1.5
STATE_FILE = "seen_items.json"

HEADERS = {
    "User-Agent": "DiscogsWantlistNotifier/1.0",
    "Authorization": f"Discogs token={DISCOGS_USER_TOKEN}"
}

# ===== STATE =====
seen_items = set()
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r") as f:
            seen_items = set(json.load(f))
    except Exception:
        seen_items = set()

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen_items), f)

# ===== TELEGRAM =====
def send_telegram(msg):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
        timeout=10
    )

# ===== DISCOGS =====
def get_wantlist():
    r = requests.get(
        f"https://api.discogs.com/users/{DISCOGS_USER}/wants",
        headers=HEADERS,
        timeout=15
    )
    r.raise_for_status()
    return r.json()["wants"]

def check_marketplace(release_id):
    r = requests.get(
        "https://api.discogs.com/marketplace/search",
        headers=HEADERS,
        params={"release_id": release_id, "per_page": 5},
        timeout=15
    )

    if r.status_code == 429:
        print("⏳ Rate limit Discogs, attendo 60s")
        time.sleep(60)
        return []

    r.raise_for_status()
    return r.json().get("results", [])

# ===== START =====
send_telegram("🤖 Bot Discogs ONLINE")
print("✅ Bot avviato correttamente")

while True:
    try:
        print("🔍 Controllo wantlist...")
        wants = get_wantlist()

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

    except Exception as e:
        print("❌ Errore:", e)
        time.sleep(60)

    time.sleep(CHECK_INTERVAL)
