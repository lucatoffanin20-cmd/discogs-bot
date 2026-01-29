import os
import time
import json
import requests
from flask import Flask
from requests_oauthlib import OAuth1
from threading import Thread
from dotenv import load_dotenv

load_dotenv()

# ===== VARIABILI =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

CHECK_INTERVAL = 300
DATA_FILE = "seen.json"

auth = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, OAUTH_TOKEN, OAUTH_TOKEN_SECRET)

# ===== STORAGE =====
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        seen = json.load(f)
else:
    seen = {}

# ===== TELEGRAM =====
def send_telegram(msg):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
        timeout=10
    )

# ===== WANTLIST =====
def get_wantlist():
    r = requests.get(
        f"https://api.discogs.com/users/{DISCOGS_USER}/wants",
        auth=auth,
        timeout=15
    )
    return r.json().get("wants", [])

# ===== LISTINGS =====
def get_listings(release_id):
    url = f"https://api.discogs.com/marketplace/listings"
    params = {
        "release_id": release_id,
        "sort": "listed",
        "sort_order": "desc",
        "per_page": 10,
        "page": 1
    }
    r = requests.get(url, params=params, auth=auth, timeout=15)
    return r.json().get("listings", [])

# ===== BOT LOOP =====
def bot_loop():
    send_telegram("🤖 Bot Discogs avviato e operativo")
    while True:
        wants = get_wantlist()

        for w in wants:
            rid = str(w["id"])
            if rid not in seen:
                seen[rid] = []

            listings = get_listings(rid)

            for l in listings:
                lid = str(l["id"])
                if lid not in seen[rid]:
                    seen[rid].append(lid)

                    price = l["price"]["value"]
                    title = l.get("title", "N/D")
                    url = f"https://www.discogs.com/sell/item/{lid}"

                    send_telegram(
                        f"🎵 NUOVO ARTICOLO\n{title}\n💰 {price}\n{url}"
                    )

                    with open(DATA_FILE, "w") as f:
                        json.dump(seen, f)

            time.sleep(1)

        time.sleep(CHECK_INTERVAL)

# ===== FLASK =====
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot attivo ✅"

if __name__ == "__main__":
    Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
