import os
import time
import threading
import requests
import discogs_client

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
    send_telegram("🧪 BOT TEST - controllo robusto listing.data")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    # Inserisci qui l'ID della release da testare
    release_ids = [7334987]  # esempio: Sing For Absolution

    print(f"📀 Test sulla release: {release_ids[0]}")
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
                    print("⚠️ Nessun annuncio trovato.")
                    continue

                for idx, listing in enumerate(results, start=1):
                    data = listing.data
                    price_info = data.get("price")
                    uri = data.get("uri") or data.get("resource_url")
                    
                    if not price_info or not uri:
                        print(f"⚠️ Skipping listing #{idx}, price/uri mancanti.")
                        continue

                    msg = (
                        f"🧪 TEST Annuncio Discogs\n\n"
                        f"📀 {data.get('title')}\n"
                        f"💰 {price_info.get('value')} {price_info.get('currency')}\n"
                        f"🏷 {data.get('condition', 'N/A')}\n"
                        f"🔗 https://www.discogs.com{uri}"  # link completo
                    )
                    send_telegram(msg)
                    print(f"✅ Listing #{idx} inviato")
                    return  # stop dopo il primo listing per il test

            except Exception as e:
                print(f"❌ Marketplace error: {e}")

        time.sleep(CHECK_INTERVAL)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
