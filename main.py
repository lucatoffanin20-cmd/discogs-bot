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
MARKETPLACE_CHECK_LIMIT = 5  # quanti annunci recenti controllare

# 🔴 MODALITÀ TEST
TEST_MODE = True   # True per test, False per script finale
TEST_RELEASES = [368616]  # ID delle release da testare

# ================= FLASK (healthcheck Railway) =================
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
        "WantlistWatcher/1.0",
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        token=OAUTH_TOKEN,
        secret=OAUTH_TOKEN_SECRET,
    )

# ================= BOT LOOP =================
def bot_loop():
    send_telegram("🧪 BOT TEST – controllo listing.data")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    if TEST_MODE:
        release_ids = TEST_RELEASES
        print(f"📀 Modalità TEST attiva. Controllo release: {release_ids}")
    else:
        try:
            wantlist = list(user.wantlist)
            release_ids = [w.release.id for w in wantlist]
            print(f"📀 Wantlist caricata: {len(release_ids)} release")
        except Exception as e:
            print(f"❌ Errore fetching wantlist: {e}")
            send_telegram(f"❌ Errore fetching wantlist: {e}")
            return

    while True:
        print("👂 Controllo annunci...")

        for rid in release_ids:
            try:
                results = d.search(
                    type="marketplace",
                    release_id=rid,
                    sort="listed",
                    sort_order="desc",
                    per_page=MARKETPLACE_CHECK_LIMIT,
                )

                if not results:
                    print(f"⚠️ Nessun annuncio trovato per release {rid}.")
                    continue

                for idx, listing in enumerate(results, start=1):
                    # 🔑 Preleviamo dati direttamente da listing.data
                    data = getattr(listing, 'data', {})
                    price_data = data.get('price')
                    uri = data.get('uri')
                    title = data.get('title')
                    condition = getattr(listing, 'condition', 'N/A')

                    if not price_data or not uri:
                        print(f"⚠️ Skipping listing #{idx} release {rid}, price/uri mancanti.")
                        continue

                    msg = (
                        f"🧪 TEST Annuncio Discogs\n\n"
                        f"📀 {title}\n"
                        f"💰 {price_data['value']} {price_data['currency']}\n"
                        f"🏷 {condition}\n"
                        f"🔗 {uri}"
                    )

                    send_telegram(msg)
                    print(f"✅ Listing #{idx} inviato per release {rid}")
                    return  # 🔴 STOP dopo il primo listing trovato per test

            except Exception as e:
                print(f"❌ Marketplace error release {rid}: {e}")

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
