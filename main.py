import os
import json
import requests
import time
import random
from datetime import datetime
from flask import Flask
from threading import Thread
import logging

# ================== CONFIG ==================
CHECK_INTERVAL = 300
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

SEEN_FILE = "seen.json"
LOG_FILE = "discogs_bot.log"

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================== TELEGRAM ==================
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        logger.error("❌ Token Telegram mancante")
        return False
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            logger.error(f"❌ Telegram error {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Errore Telegram: {e}")
        return False

# ================== FILE MANAGEMENT ==================
def load_seen():
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
    except Exception as e:
        logger.error(f"❌ Errore caricamento seen: {e}")
    return set()

def save_seen(seen):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen), f)
        logger.info(f"💾 Salvati {len(seen)} ID")
    except Exception as e:
        logger.error(f"❌ Errore salvataggio seen: {e}")

# ================== DISCOGS API ROBUSTA ==================
def discogs_api_call(url, params=None, retry=2):
    """Chiamata API con error handling"""
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": "DiscogsWantlistBot/1.0"
    }
    
    for attempt in range(retry):
        try:
            # Rate limiting semplice
            time.sleep(1)
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 429:
                wait = int(response.headers.get('Retry-After', 60))
                logger.warning(f"⏳ Rate limit, aspetto {wait}s")
                time.sleep(wait)
                continue
            
            if response.status_code == 200:
                return response.json()
            
            logger.error(f"❌ API error {response.status_code} per {url}")
            
        except Exception as e:
            logger.error(f"❌ Errore API tentativo {attempt+1}: {e}")
            if attempt < retry - 1:
                time.sleep(2)
    
    return None

def get_wantlist_robust():
    """Ottieni wantlist con error handling"""
    all_wants = []
    page = 1
    max_pages = 10
    
    logger.info(f"📥 Scaricamento wantlist per {USERNAME}...")
    
    while page <= max_pages:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': 50
        }
        
        data = discogs_api_call(url, params)
        if not data:
            logger.error(f"❌ Errore pagina {page}")
            break
        
        wants = data.get('wants', [])
        if not wants:
            break
        
        all_wants.extend(wants)
        logger.info(f"📄 Pagina {page}: {len(wants)} articoli")
        
        # Controlla se ci sono altre pagine
        pagination = data.get('pagination', {})
        total_pages = pagination.get('pages', 1)
        
        if page >= total_pages or len(wants) < 50:
            break
        
        page += 1
    
    logger.info(f"✅ Wantlist scaricata: {len(all_wants)} articoli totali")
    return all_wants

def get_marketplace_listings_safe(release_id):
    """Ottieni listings del marketplace con error handling"""
    url = "https://api.discogs.com/marketplace/listings"
    params = {
        'release_id': release_id,
        'status': 'For Sale',
        'per_page': 5,
        'sort': 'listed',
        'sort_order': 'desc'
    }
    
    data = discogs_api_call(url, params)
    if data and 'listings' in data:
        return data['listings']
    
    return []

# ================== MARKETPLACE CHECK ROBUSTO ==================
def check_marketplace_robust():
    """Controllo marketplace robusto"""
    logger.info("🔄 Controllo marketplace ROBUSTO...")
    
    # Carica wantlist
    wants = get_wantlist_robust()
    if not wants:
        logger.error("❌ Impossibile ottenere wantlist")
        return 0
    
    seen = load_seen()
    new_listings = 0
    
    logger.info(f"📊 Wantlist: {len(wants)} articoli")
    logger.info(f"👁️ ID già visti: {len(seen)}")
    
    # Seleziona release da controllare
    check_count = min(30, len(wants))
    if check_count == 0:
        return 0
    
    # Prendi alcuni recenti e alcuni casuali
    recent_count = min(15, check_count)
    recent = wants[:recent_count]
    
    if len(wants) > recent_count:
        random_count = check_count - recent_count
        random_sample = random.sample(wants[recent_count:], min(random_count, len(wants[recent_count:])))
        releases_to_check = recent + random_sample
    else:
        releases_to_check = recent
    
    random.shuffle(releases_to_check)
    
    logger.info(f"🔍 Controllo {len(releases_to_check)} release...")
    
    for i, item in enumerate(releases_to_check):
        release_id = item.get('id')
        if not release_id:
            continue
        
        basic_info = item.get('basic_information', {})
        title = basic_info.get('title', 'Sconosciuto')
        artists = basic_info.get('artists', [{}])
        artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
        
        logger.info(f"[{i+1}/{len(releases_to_check)}] {artist} - {title[:40]}...")
        
        # Ottieni listings
        listings = get_marketplace_listings_safe(release_id)
        
        if not listings:
            logger.info(f"   ℹ️ Nessuna listing attiva")
            continue
        
        logger.info(f"   ✅ {len(listings)} listings trovate")
        
        for listing in listings:
            listing_id = str(listing.get('id'))
            
            if not listing_id or listing_id in seen:
                continue
            
            # Dati della listing
            price_obj = listing.get('price', {})
            price = price_obj.get('formatted', 'N/D')
            seller = listing.get('seller', {}).get('username', 'N/D')
            condition = listing.get('condition', 'N/D')
            
            # URL REALE
            item_url = f"https://www.discogs.com/sell/item/{listing_id}"
            
            logger.info(f"   🛒 Listing {listing_id}: {price} da {seller}")
            
            # Invia notifica
            msg = (
                f"🆕 <b>NUOVA COPIA DISPONIBILE!</b>\n\n"
                f"🎸 <b>{artist}</b>\n"
                f"💿 {title}\n"
                f"💰 <b>{price}</b>\n"
                f"👤 {seller}\n"
                f"⭐ {condition}\n\n"
                f"🔗 <a href='{item_url}'>ACQUISTA SU DISCOGS</a>"
            )
            
            if send_telegram(msg):
                seen.add(listing_id)
                new_listings += 1
                logger.info(f"   📤 Notifica inviata!")
                break  # Una notifica per release
            else:
                logger.error(f"   ❌ Errore invio notifica")
        
        # Pausa importante
        pause = random.uniform(2, 4)
        time.sleep(pause)
    
    # Salva risultati
    if new_listings > 0:
        save_seen(seen)
        logger.info(f"✅ {new_listings} nuove listings notificate")
    else:
        logger.info("ℹ️ Nessuna nuova listing trovata")
    
    return new_listings

# ================== FLASK APP ==================
app = Flask(__name__)

@app.route("/")
def home():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Discogs Bot - ROBUSTO</title>
        <style>
            body {{ font-family: Arial; margin: 40px; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            .card {{ background: #f5f5f5; padding: 20px; border-radius: 10px; margin: 20px 0; }}
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 10px 20px; 
                    text-decoration: none; border-radius: 5px; margin: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Discogs Bot - VERSIONE ROBUSTA</h1>
            
            <div class="card">
                <h3>✅ SISTEMA ROBUSTO</h3>
                <p><strong>Utente:</strong> {USERNAME}</p>
                <p><strong>Intervallo:</strong> {CHECK_INTERVAL//60} minuti</p>
                <p><strong>Status:</strong> <span style="color: green;">🟢 ONLINE</span></p>
            </div>
            
            <div class="card">
                <h3>🔧 Controlli</h3>
                <a class="btn" href="/check">🚀 Controllo Marketplace</a>
                <a class="btn" href="/test">🧪 Test Telegram</a>
                <a class="btn" href="/logs">📄 Logs Sistema</a>
            </div>
            
            <div class="card">
                <h3>ℹ️ Informazioni</h3>
                <p>Versione robusta con error handling migliorato.</p>
                <p>Usa l'endpoint corretto <code>/marketplace/listings</code>.</p>
                <p>Link REALI e funzionanti al 100%.</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=check_marketplace_robust, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Avviato</title></head>
    <body style="font-family: Arial; margin: 40px; text-align: center;">
        <h1>🚀 Controllo Avviato</h1>
        <p>Controllo marketplace in esecuzione...</p>
        <p>Controlla i logs su Railway per i dettagli.</p>
        <a href="/" style="color: #4CAF50;">↩️ Torna alla Home</a>
    </body>
    </html>
    """, 200

@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 Test Bot ROBUSTO\n\n"
        f"✅ Sistema online\n"
        f"👤 {USERNAME}\n"
        f"⏰ {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    if success:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>Test OK</title></head>
        <body style="font-family: Arial; margin: 40px; text-align: center;">
            <h1 style="color: green;">✅ Test Inviato</h1>
            <p>Controlla il tuo Telegram per il messaggio di test.</p>
            <a href="/">↩️ Home</a>
        </body>
        </html>
        """, 200
    else:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>Errore</title></head>
        <body style="font-family: Arial; margin: 40px; text-align: center;">
            <h1 style="color: red;">❌ Errore Invio</h1>
            <p>Controlla le variabili TELEGRAM_TOKEN e TELEGRAM_CHAT_ID</p>
            <a href="/">↩️ Home</a>
        </body>
        </html>
        """, 500

@app.route("/logs")
def view_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding='utf-8') as f:
                logs = f.read().splitlines()[-100:]
            logs_html = "<br>".join(logs)
        else:
            logs_html = "Nessun log disponibile"
    except:
        logs_html = "Errore nella lettura dei log"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: monospace; margin: 20px; background: #1a1a1a; color: #00ff00; }}
            pre {{ background: #000; padding: 20px; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <h2>📄 Logs Sistema (ultime 100 righe)</h2>
        <pre>{logs_html}</pre>
        <a href="/" style="color: #00ccff;">↩️ Home</a>
    </body>
    </html>
    """, 200

# ================== MAIN LOOP ==================
def main_loop_robust():
    """Loop principale robusto"""
    logger.info("🔄 Avvio loop principale...")
    time.sleep(10)
    
    while True:
        try:
            logger.info(f"🔄 Controllo automatico ({datetime.now().strftime('%H:%M')})")
            check_marketplace_robust()
            
            logger.info(f"💤 Pausa di {CHECK_INTERVAL//60} minuti...")
            for seconds in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Errore nel loop principale: {e}")
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    # Verifica variabili
    required_vars = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [var for var in required_vars if not os.environ.get(var)]
    
    if missing:
        logger.error(f"❌ Variabili mancanti: {', '.join(missing)}")
        exit(1)
    
    logger.info("="*70)
    logger.info("🤖 DISCOGS BOT - VERSIONE ROBUSTA")
    logger.info("="*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info("="*70)
    
    # Notifica avvio
    send_telegram(
        f"🤖 <b>Discogs Bot Avviato</b>\n\n"
        f"✅ Versione robusta online\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controlli ogni {CHECK_INTERVAL//60} minuti\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    # Avvia loop
    Thread(target=main_loop_robust, daemon=True).start()
    
    # Avvia Flask
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🌐 Server Flask sulla porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
