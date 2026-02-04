import os
import time
import json
import requests
from flask import Flask
from threading import Thread

# ================== CONFIG ==================

CHECK_INTERVAL = 180  # 3 minuti
SEEN_FILE = "seen.json"

DISCOGS_URL = "https://www.discogs.com/sell/mywants/data"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.discogs.com/sell/mywants",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive"
}

COOKIES = {
    "Cookie": os.environ.get("DISCOGS_COOKIE", "")
}

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

# ================== TELEGRAM ==================

def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT,
        "text": msg,
        "disable_web_page_preview": False
    }
    requests.post(url, json=payload, timeout=10)

# ================== SEEN ==================

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ================== CORE ==================

def check_mywants():
    print("👂 Controllo annunci...")

    seen = load_seen()

    try:
        r = requests.get(
            DISCOGS_URL,
            headers=HEADERS,
            cookies=COOKIES,
            timeout=20
        )

        if r.status_code == 403:
            send_telegram("❌ Cookie Discogs scaduto (403). Rigenera il cookie.")
            print("❌ 403 Forbidden")
            return

        r.raise_for_status()
        data = r.json()

    except Exception as e:
        print(f"❌ Errore richiesta: {e}")
        return

    new_found = 0

    for release in data.get("releases", []):
        for listing in release.get("listings", []):

            listing_id = listing.get("id")
            if not listing_id or listing_id in seen:
                continue

            price = listing.get("price", {}).get("formatted", "N/D")
            uri = listing.get("uri")
            seller = listing.get("seller", {}).get("username", "N/D")
            title = release.get("title", "Release")

            if not uri:
                continue

            msg = (
                f"🆕 NUOVO ANNUNCIO\n\n"
                f"🎵 {title}\n"
                f"💰 {price}\n"
                f"👤 {seller}\n\n"
                f"🔗 https://www.discogs.com{uri}"
            )

            send_telegram(msg)
            seen.add(listing_id)
            new_found += 1

    if new_found:
        save_seen(seen)
        print(f"✅ {new_found} nuovi annunci notificati")
    else:
        print("ℹ️ Nessun nuovo annuncio")

# ================== LOOP ==================

def loop():
    while True:
        check_mywants()
        print(f"⏱ Pausa {CHECK_INTERVAL} secondi\n")
        time.sleep(CHECK_INTERVAL)

# ================== FLASK KEEP ALIVE ==================

app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200

if __name__ == "__main__":
    Thread(target=loop, daemon=True).start()
    send_telegram("🤖 Bot Discogs avviato correttamente")
    app.run(host="0.0.0.0", port=8080)
