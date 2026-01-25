print("🤖 Discogs Wantlist Notifier avviato")

import os
import time
import requests
from dotenv import load_dotenv
from requests.exceptions import RequestException

# ================= CONFIG =================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER_TOKEN = os.getenv("DISCOGS_USER_TOKEN")

CHECK_INTERVAL = 180  # 3 minuti (sicuro per Discogs)

HEADERS = {
    "User-Agent": "DiscogsWantlistNotifier/1.0",
    "Authorization": f"Discogs token={DISCOGS_USER_TOKEN}"
}

# ================= STATE =================

last_seen_timestamp = 0

# ================= TELEGRAM =================

def send_telegram(message):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10
        )
    except RequestException as e:
        print(f"⚠️ Errore Telegram: {e}")

# ================= DISCOGS =================

def get_latest_wantlist_listings():
    url = "https://api.discogs.com/marketplace/listings"
    params = {
        "want": "true",
        "sort": "listed",
        "sort_order": "desc",
        "per_page": 50,
        "page": 1
    }

    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("listings", [])

# ================= MAIN LOOP =================

def bot_loop():
    global last_seen_timestamp
    print("👂 In ascolto dei nuovi annunci Discogs…")

    while True:
        try:
            listings = get_latest_wantlist_listings()
            new_items = []

            for item in listings:
                posted = int(item["posted"])

                if posted > last_seen_timestamp:
                    new_items.append(item)

            if new_items:
                # aggiorna timestamp al più recente
                last_seen_timestamp = max(int(i["posted"]) for i in new_items)

                for l in reversed(new_items):
                    msg = (
                        f"🎵 NUOVO ARTICOLO IN WANTLIST!\n\n"
                        f"{l['release']['description']}\n"
                        f"💰 Prezzo: {l['price']['value']} {l['price']['currency']}\n"
                        f"📦 Condizione: {l['condition']}\n"
                        f"🔗 https://www.discogs.com/sell/item/{l['id']}"
                    )
                    send_telegram(msg)
                    print(f"✅ Notifica inviata: {l['id']}")

            else:
                print("⏱ Nessun nuovo articolo")

        except Exception as e:
            print(f"⚠️ Errore: {e}")

        time.sleep(CHECK_INTERVAL)

# ================= START =================

if __name__ == "__main__":
    bot_loop()
