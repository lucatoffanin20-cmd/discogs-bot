import requests
import time
import json
import os
from flask import Flask
from threading import Thread

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_COOKIE = os.getenv("DISCOGS_COOKIE")

CHECK_INTERVAL = 180  # 3 minuti
SEEN_FILE = "seen.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.discogs.com/sell/mywants",
    "Cookie": DISCOGS_COOKIE
}

URL = "https://www.discogs.com/sell/mywants/data"

# ================= STORAGE =================
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return json.load(f)
    return {}

def save_seen(data):
    with open(SEEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    requests.post(url, data=payload, timeout=10)

# ================= FETCH =================
def fetch_listings():
    r = requests.get(URL, headers=HEADERS, timeout=15)

    if r.status_code == 429:
        print("⚠️ Rate limit – pausa 120s")
        time.sleep(120)
        return []

    r.raise_for_status()

    try:
        return r.json().get("listings", [])
    except Exception:
        send_telegram(
            "⚠️ Cookie Discogs non valido o scaduto.\n"
            "Rigeneralo aprendo Shop → Wantlist e copia il nuovo cookie."
        )
        time.sleep(600)  # pausa lunga anti-spam
        return []

# ================= LOGIC =================
def check_announcements():
    seen = load_seen()
    listings = fetch_listings()

    print(f"📦 Listing ricevuti: {len(listings)}")

    for l in listings:
        try:
            listing_id = str(l["listing_id"])
            price = f'{l["price"]["value"]} {l["price"]["currency"]}'
            url = "https://www.discogs.com" + l["uri"]
            title = l["release"]["title"]
        except Exception:
            continue

        if listing_id not in seen:
            send_telegram(
                f"🆕 Nuovo annuncio\n"
                f"{title}\n"
                f"💸 {price}\n"
                f"{url}"
            )
            seen[listing_id] = price

        elif seen[listing_id] != price:
            send_telegram(
                f"💸 Prezzo modificato\n"
                f"{title}\n"
                f"{seen[listing_id]} ➜ {price}\n"
                f"{url}"
            )
            seen[listing_id] = price

    save_seen(seen)

# ================= LOOP =================
def loop():
    send_telegram("🤖 Bot Discogs avviato")
    while True:
        try:
            print("👂 Controllo annunci...")
            check_announcements()
        except Exception as e:
            print("❌ Errore:", e)
        time.sleep(CHECK_INTERVAL)

# ================= FLASK =================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot Discogs attivo"

if __name__ == "__main__":
    Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
