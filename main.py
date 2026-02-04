import os
import asyncio
import json
import requests
from threading import Thread
from flask import Flask
from pyppeteer import launch

# ================== CONFIG ==================
CHECK_INTERVAL = 180  # secondi tra un controllo e l'altro
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_ID")

DISCOGS_URL = "https://www.discogs.com/sell/mywants"
SEEN_FILE = "seen.json"
USER_DATA_DIR = "./user_data"  # sessione persistente Chromium

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
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ Errore Telegram: {e}")

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
async def fetch_mywants(first_time=False):
    # Lancia Chromium con sessione persistente
    browser = await launch(
        headless=not first_time,  # headless=False la prima volta per login
        args=["--no-sandbox"],
        userDataDir=USER_DATA_DIR
    )
    page = await browser.newPage()
    await page.goto(DISCOGS_URL)
    
    if first_time:
        print("❗ Fai login manualmente nella finestra del browser. Dopo il login chiudi il browser.")
        await asyncio.sleep(120)  # 2 minuti per login
        await browser.close()
        return []

    # Estrai la Wantlist dal JS-rendered
    try:
        content = await page.evaluate('''() => {
            return JSON.stringify(window.APP_STATE?.wantlist || [])
        }''')
        data = json.loads(content)
    except Exception as e:
        print(f"❌ Errore lettura pagina: {e}")
        data = []

    await browser.close()
    return data

async def check_mywants(first_time=False):
    print("👂 Controllo annunci...")
    seen = load_seen()
    data = await fetch_mywants(first_time)
    new_found = 0

    for release in data:
        listing_id = str(release.get("id"))
        if not listing_id or listing_id in seen:
            continue

        title = release.get("title", "Release")
        price = release.get("price", {}).get("formatted", "N/D")
        seller = release.get("seller", {}).get("username", "N/D")
        uri = release.get("uri", "")

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
    import time
    first_time = True  # prima volta serve login manuale
    while True:
        asyncio.run(check_mywants(first_time))
        first_time = False
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
