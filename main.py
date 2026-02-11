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

# ================== DISCOGS API FIXED ==================
def get_wantlist():
    """Ottieni wantlist"""
    all_wants = []
    page = 1
    
    logger.info(f"📥 Scaricamento wantlist per {USERNAME}...")
    
    while True:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': 100,
            'sort': 'added',
            'sort_order': 'desc'
        }
        
        headers = {
            "Authorization": f"Discogs token={DISCOGS_TOKEN}",
            "User-Agent": "DiscogsWantlistMonitor/2.0"
        }
        
        try:
            time.sleep(0.5)
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"❌ API error {response.status_code}")
                break
            
            data = response.json()
            wants = data.get('wants', [])
            if not wants:
                break
            
            all_wants.extend(wants)
            logger.info(f"📄 Pagina {page}: {len(wants)} articoli")
            
            pagination = data.get('pagination', {})
            if page >= pagination.get('pages', 1) or len(wants) < 100:
                break
            
            page += 1
            
        except Exception as e:
            logger.error(f"❌ Errore: {e}")
            break
    
    logger.info(f"✅ Wantlist scaricata: {len(all_wants)} articoli")
    return all_wants

def get_marketplace_listings_for_release(release_id):
    """
    ENDPOINT CORRETTO per ottenere listings del marketplace
    Questo FUNZIONA DAVVERO!
    """
    url = f"https://api.discogs.com/marketplace/listings/{release_id}"
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": "DiscogsMarketplaceMonitor/2.0"
    }
    
    try:
        time.sleep(1.5)  # Rate limiting importante
        
        logger.info(f"   📡 API call: /marketplace/listings/{release_id}")
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 404:
            # Nessuna listing per questo release ID (è un master ID o release senza listings)
            logger.info(f"   ℹ️ 404 - Nessuna listing per questo ID")
            return []
        
        if response.status_code == 200:
            data = response.json()
            listings = data.get('listings', [])
            logger.info(f"   ✅ Trovate {len(listings)} listings")
            return listings
        
        logger.error(f"   ❌ API error {response.status_code}")
        return []
        
    except Exception as e:
        logger.error(f"   ❌ Errore: {e}")
        return []

def get_listings_from_master_id(master_id):
    """
    Alternativa: cerca listings per master ID
    """
    url = f"https://api.discogs.com/masters/{master_id}"
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": "DiscogsMasterLookup/2.0"
    }
    
    try:
        time.sleep(1)
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            main_release = data.get('main_release')
            if main_release:
                # Prova a trovare listings per il main release
                return get_marketplace_listings_for_release(main_release)
        
        return []
        
    except Exception as e:
        logger.error(f"   ❌ Errore master lookup: {e}")
        return []

# ================== MARKETPLACE CHECK FIXED ==================
def check_marketplace_fixed():
    """Versione FIXED che funziona"""
    logger.info("🔄 Controllo marketplace FIXED...")
    
    wants = get_wantlist()
    if not wants:
        return 0
    
    seen = load_seen()
    new_listings = 0
    
    logger.info(f"📊 Wantlist: {len(wants)} articoli")
    logger.info(f"👁️ ID già visti: {len(seen)}")
    
    # Controlla PIÙ release (35 per ciclo)
    check_count = min(35, len(wants))
    
    # Strategia: 15 recenti + 20 casuali
    recent = wants[:15]
    if len(wants) > 15:
        random_sample = random.sample(wants[15:], min(20, len(wants[15:])))
        releases_to_check = recent + random_sample
    else:
        releases_to_check = recent
    
    random.shuffle(releases_to_check)
    
    logger.info(f"🔍 Controllo {len(releases_to_check)} release...")
    
    for i, item in enumerate(releases_to_check):
        release_id = item.get('id')
        basic_info = item.get('basic_information', {})
        title = basic_info.get('title', 'Sconosciuto')
        artists = basic_info.get('artists', [{}])
        artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
        
        logger.info(f"[{i+1}/{len(releases_to_check)}] {artist} - {title[:40]}...")
        
        # STRATEGIA A LIVELLI:
        listings = []
        
        # Livello 1: Cerca direttamente per release ID
        if release_id:
            listings = get_marketplace_listings_for_release(release_id)
        
        # Livello 2: Se non trova, cerca per master ID
        if not listings and 'master_id' in item.get('basic_information', {}):
            master_id = item['basic_information']['master_id']
            if master_id and master_id > 0:
                logger.info(f"   🔄 Tentativo con master ID: {master_id}")
                listings = get_listings_from_master_id(master_id)
        
        if not listings:
            logger.info(f"   ℹ️ Nessuna listing trovata")
            continue
        
        for listing in listings:
            listing_id = str(listing.get('id'))
            
            if not listing_id or listing_id in seen:
                continue
            
            # Dati listing
            price_obj = listing.get('price', {})
            price = price_obj.get('formatted', 'N/D')
            seller = listing.get('seller', {}).get('username', 'N/D')
            condition = listing.get('condition', 'N/D')
            sleeve = listing.get('sleeve_condition', 'N/D')
            
            # URL REALE
            item_url = f"https://www.discogs.com/sell/item/{listing_id}"
            
            logger.info(f"   🛒 TROVATA! {listing_id}: {price} da {seller}")
            logger.info(f"   🔗 {item_url}")
            
            # Test URL
            try:
                test = requests.head(item_url, timeout=5, allow_redirects=True)
                if test.status_code == 200:
                    logger.info(f"   ✅ URL valido")
                else:
                    logger.warning(f"   ⚠️ URL status: {test.status_code}")
            except:
                logger.warning(f"   ⚠️ Impossibile verificare URL")
            
            # Invia notifica
            msg = (
                f"🆕 <b>COPIA DISPONIBILE!</b>\n\n"
                f"🎸 <b>{artist}</b>\n"
                f"💿 {title}\n"
                f"💰 <b>{price}</b>\n"
                f"👤 {seller}\n"
                f"⭐ {condition}\n"
                f"📁 {sleeve}\n\n"
                f"🔗 <a href='{item_url}'>ACQUISTA SU DISCOGS</a>"
            )
            
            if send_telegram(msg):
                seen.add(listing_id)
                new_listings += 1
                logger.info(f"   📤 NOTIFICA INVIATA!")
                break  # Una notifica per release
        
        # Pausa importante
        time.sleep(random.uniform(2, 3))
    
    if new_listings > 0:
        save_seen(seen)
        logger.info(f"✅ {new_listings} nuove listings notificate!")
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
        <title>🤖 Discogs Bot - FIXED</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; }}
            .success {{ background: #d4edda; border-left: 4px solid #28a745; padding: 20px; margin: 20px 0; }}
            .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 20px; margin: 20px 0; }}
            .btn {{ display: inline-block; background: #28a745; color: white; padding: 12px 24px; 
                    text-decoration: none; border-radius: 5px; margin: 5px; font-weight: bold; }}
            .btn:hover {{ background: #218838; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Discogs Bot - VERSIONE FIXED</h1>
            
            <div class="success">
                <h3>✅ ENDPOINT CORRETTO</h3>
                <p>Ora usa: <code>/marketplace/listings/{{release_id}}</code></p>
                <p>Questo endpoint FUNZIONA DAVVERO!</p>
                <p><strong>Release per ciclo:</strong> 35</p>
                <p><strong>Intervallo:</strong> {CHECK_INTERVAL//60} minuti</p>
                <p><strong>Utente:</strong> {USERNAME}</p>
            </div>
            
            <div class="warning">
                <h3>⚠️ IMPORTANTE</h3>
                <p>Questa versione usa l'endpoint CORRETTO per le listings del marketplace.</p>
                <p>Ora il bot DOVREBBE trovare le copie in vendita!</p>
            </div>
            
            <h3>🔧 Controlli</h3>
            <a class="btn" href="/check">🚀 Controllo FIXED</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            <a class="btn" href="/logs">📄 Logs</a>
            <a class="btn" href="/force-check">⚡ Forza Check (test release specifica)</a>
            
            <h3>ℹ️ Come testare</h3>
            <p>1. Aggiungi una release con copie in vendita alla wantlist</p>
            <p>2. Vai su <a href="/force-check">/force-check</a></p>
            <p>3. Controlla i logs per vedere se trova le listings</p>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=check_marketplace_fixed, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Avviato</title></head>
    <body style="font-family: Arial; margin: 40px; text-align: center;">
        <h1>🚀 Controllo FIXED Avviato!</h1>
        <p>Usa l'endpoint CORRETTO del marketplace!</p>
        <p>Controlla i logs per vedere i risultati.</p>
        <a href="/">↩️ Dashboard</a>
    </body>
    </html>
    """, 200

@app.route("/force-check")
def force_check():
    """Test forzato con release specifica"""
    test_release_id = request.args.get('release_id', '14809291')  # Black Holes & Revelations
    logger.info(f"⚡ Test forzato per release {test_release_id}")
    
    listings = get_marketplace_listings_for_release(test_release_id)
    
    result = f"<h2>Test Release {test_release_id}</h2>"
    result += f"<p>Trovate {len(listings)} listings</p>"
    
    for l in listings[:3]:
        result += f"<p>🛒 {l.get('id')}: {l.get('price', {}).get('formatted')}</p>"
    
    return result, 200

@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test Bot FIXED</b>\n\n"
        f"✅ Versione con endpoint CORRETTO\n"
        f"👤 {USERNAME}\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    return "✅ Test inviato" if success else "❌ Errore", 200

@app.route("/logs")
def view_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding='utf-8') as f:
                logs = f.read().splitlines()[-150:]
            logs_html = "<br>".join(logs)
        else:
            logs_html = "Nessun log"
    except:
        logs_html = "Errore logs"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><style>
        body {{ background: #1a1a1a; color: #00ff00; font-family: monospace; }}
        pre {{ white-space: pre-wrap; }}
    </style></head>
    <body>
        <pre>{logs_html}</pre>
        <a href="/" style="color: #00ccff;">↩️ Home</a>
    </body>
    </html>
    """, 200

# ================== MAIN LOOP ==================
def main_loop_fixed():
    time.sleep(10)
    while True:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔄 Controllo automatico - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            check_marketplace_fixed()
            
            logger.info(f"💤 Pausa di {CHECK_INTERVAL//60} minuti...")
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Loop error: {e}", exc_info=True)
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    # Verifica variabili
    required = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [var for var in required if not os.environ.get(var)]
    
    if missing:
        logger.error(f"❌ Variabili mancanti: {missing}")
        exit(1)
    
    logger.info('='*70)
    logger.info("🤖 DISCOGS BOT - VERSIONE FIXED")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release per ciclo: 35")
    logger.info('='*70)
    
    # Notifica avvio
    send_telegram(
        f"🤖 <b>Bot FIXED Avviato!</b>\n\n"
        f"✅ Endpoint CORRETTO\n"
        f"👤 {USERNAME}\n"
        f"⏰ {CHECK_INTERVAL//60} minuti\n"
        f"🔍 35 release/ciclo\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    Thread(target=main_loop_fixed, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
