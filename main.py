import os
import time
import threading
import requests
import discogs_client
from flask import Flask

# ================= VARIABILI =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
OAUTH_TOKEN_SECRET = os.getenv("OAUTH_TOKEN_SECRET")
DISCOGS_USER = os.getenv("DISCOGS_USER")

CHECK_INTERVAL = 600  # 10 minuti
MARKETPLACE_CHECK_LIMIT = 5

# ================= FLASK =================
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "", 200

# ================= TELEGRAM =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    requests.post(url, data=data, timeout=10)

# ================= DISCOGS =================
def init_discogs():
    return discogs_client.Client(
        "WantlistWatcherTest/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🧪 Bot Discogs TEST avviato")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    # Stampiamo tutte le release per scegliere quale testare
    wantlist = list(user.wantlist)
    print("📀 Wantlist caricata:")
    for idx, w in enumerate(wantlist):
        print(f"{idx+1}: {w.release.id} – {w.release.title}")

    # Inserisci qui manualmente l'ID della release da testare
    release_id = 7334987  # ← METTI L'ID CHE VUOI TESTARE
    print(f"\n📌 Test sulla release: {release_id}")

    while True:
        print("👂 Controllo annunci...")
        try:
            listings = d.search(
                type="marketplace",
                release_id=release_id,
                sort="listed",
                sort_order="desc",
                per_page=MARKETPLACE_CHECK_LIMIT
            )

            if not listings:
                print("⚠️ Nessun annuncio trovato.")
            else:
                for item in listings:
                    # Evita errori se non c'è price
                    if not hasattr(item, "price"):
                        continue
                    msg = (
                        f"🧪 TEST Annuncio Discogs\n\n"
                        f"📀 {item.title}\n"
                        f"💰 {item.price.value} {item.price.currency}\n"
                        f"🏷 {item.condition}\n"
                        f"🔗 {item.uri}"
                    )
                    send_telegram(msg)
                    print("✅ Annuncio inviato")

        except Exception as e:
            print(f"❌ Marketplace error: {e}")

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
