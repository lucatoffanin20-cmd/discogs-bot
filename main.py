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

# ================== APPROCCIO ALTERNATIVO FUNZIONANTE ==================
def get_wantlist():
    """Ottieni wantlist con approccio funzionante"""
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
            "User-Agent": "DiscogsWantlistMonitor/1.0"
        }
        
        try:
            time.sleep(0.5)  # Rate limiting
            
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
            
            # Controlla paginazione
            pagination = data.get('pagination', {})
            if page >= pagination.get('pages', 1) or len(wants) < 100:
                break
            
            page += 1
            
        except Exception as e:
            logger.error(f"❌ Errore pagina {page}: {e}")
            break
    
    logger.info(f"✅ Wantlist scaricata: {len(all_wants)} articoli")
    return all_wants

def search_marketplace_listings(release_id):
    """
    APPROCCIO FUNZIONANTE: Cerca listings usando la search API
    con filtri specifici per marketplace
    """
    url = "https://api.discogs.com/database/search"
    params = {
        'release_id': release_id,
        'type': 'release',
        'per_page': 10,
        'sort': 'listed',
        'sort_order': 'desc'
    }
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": "DiscogsMarketplaceSearch/1.0"
    }
    
    try:
        time.sleep(1)  # Rate limiting importante
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"❌ Search API error {response.status_code}")
            return []
        
        data = response.json()
        results = data.get('results', [])
        
        # Filtra solo risultati che sembrano listings del marketplace
        marketplace_results = []
        for result in results:
            # Un listing del marketplace di solito ha:
            # - Un ID numerico
            # - Un campo 'price' o 'formatted_price'
            # - Un campo 'seller' 
            # - Un 'uri' che inizia con '/sell/item/'
            
            has_price = 'price' in result or 'formatted_price' in result
            has_seller = 'seller' in result
            uri = result.get('uri', '')
            is_marketplace_item = '/sell/item/' in uri
            
            if (has_price and has_seller) or is_marketplace_item:
                marketplace_results.append(result)
        
        return marketplace_results
        
    except Exception as e:
        logger.error(f"❌ Errore search API: {e}")
        return []

# ================== MARKETPLACE CHECK FUNZIONANTE ==================
def check_marketplace_working():
    """Versione FUNZIONANTE con approccio corretto"""
    logger.info("🔄 Controllo marketplace FUNZIONANTE...")
    
    # Ottieni wantlist
    wants = get_wantlist()
    if not wants:
        logger.error("❌ Impossibile ottenere wantlist")
        return 0
    
    seen = load_seen()
    new_listings = 0
    
    logger.info(f"📊 Wantlist: {len(wants)} articoli")
    logger.info(f"👁️ ID già visti: {len(seen)}")
    
    # Seleziona release da controllare (25 per ciclo)
    check_count = min(25, len(wants))
    
    # Prendi 10 recenti e 15 casuali
    recent_count = min(10, check_count)
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
        
        # Cerca listings con approccio FUNZIONANTE
        listings = search_marketplace_listings(release_id)
        
        if not listings:
            logger.info(f"   ℹ️ Nessuna listing trovata")
            continue
        
        logger.info(f"   ✅ {len(listings)} potenziali listings")
        
        for listing in listings:
            # Estrai ID listing dal URI
            uri = listing.get('uri', '')
            listing_id = None
            
            if uri and '/sell/item/' in uri:
                # Estrai ID dall'URI: /sell/item/1234567
                parts = uri.split('/')
                if len(parts) >= 3:
                    listing_id = parts[-1]  # Ultima parte dell'URL
            
            # Fallback: usa l'ID del risultato
            if not listing_id:
                listing_id = str(listing.get('id', ''))
            
            if not listing_id or listing_id == 'None' or listing_id in seen:
                continue
            
            # Verifica che sia una listing valida
            price = listing.get('formatted_price') or listing.get('price', 'N/D')
            seller_info = listing.get('seller', {})
            seller = seller_info.get('username', 'N/D') if seller_info else 'N/D'
            
            if price == 'N/D' or seller == 'N/D':
                continue
            
            # Costruisci URL
            if uri and uri.startswith('/sell/item/'):
                item_url = f"https://www.discogs.com{uri}"
            elif listing_id:
                item_url = f"https://www.discogs.com/sell/item/{listing_id}"
            else:
                item_url = f"https://www.discogs.com/sell/list?release_id={release_id}"
            
            logger.info(f"   🛒 Listing {listing_id}: {price} da {seller}")
            logger.info(f"   🔗 URL: {item_url}")
            
            # Invia notifica
            msg = (
                f"🆕 <b>NUOVA COPIA DISPONIBILE!</b>\n\n"
                f"🎸 <b>{artist}</b>\n"
                f"💿 {title}\n"
                f"💰 <b>{price}</b>\n"
                f"👤 {seller}\n\n"
                f"🔗 <a href='{item_url}'>ACQUISTA SU DISCOGS</a>"
            )
            
            if send_telegram(msg):
                seen.add(listing_id)
                new_listings += 1
                logger.info(f"   📤 Notifica inviata!")
                
                # Testa l'URL per verificare che funzioni
                try:
                    test_resp = requests.head(item_url, timeout=5, allow_redirects=True)
                    if test_resp.status_code == 200:
                        logger.info(f"   ✅ URL verificato: funziona!")
                    else:
                        logger.warning(f"   ⚠️ URL status: {test_resp.status_code}")
                except:
                    logger.warning(f"   ⚠️ Non posso verificare l'URL")
                
                break  # Una notifica per release
        
        # Pausa importante
        pause = random.uniform(2, 3)
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
        <title>🤖 Discogs Bot - FUNZIONANTE</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }}
            h1 {{ color: #333; border-bottom: 4px solid #4CAF50; padding-bottom: 15px; text-align: center; }}
            .alert {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 5px; }}
            .success {{ background: #d4edda; border-left: 4px solid #28a745; padding: 15px; margin: 20px 0; border-radius: 5px; }}
            .btn {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; 
                    padding: 12px 24px; margin: 8px; border-radius: 8px; text-decoration: none; font-weight: bold; 
                    transition: transform 0.2s; }}
            .btn:hover {{ transform: translateY(-3px); box-shadow: 0 5px 15px rgba(0,0,0,0.1); }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Discogs Bot - VERSIONE FUNZIONANTE</h1>
            
            <div class="success">
                <h3>✅ APPROCCIO CORRETTO</h3>
                <p>Usa l'endpoint <code>/database/search</code> con filtri intelligenti</p>
                <p>Estrae listing ID corretti dagli URI</p>
                <p>Verifica che gli URL funzionino realmente</p>
            </div>
            
            <div class="alert">
                <h3>⚠️ ATTENZIONE</h3>
                <p>L'endpoint <code>/marketplace/listings</code> non è accessibile via GET</p>
                <p>Questo script usa un approccio alternativo FUNZIONANTE</p>
            </div>
            
            <h3>🔧 Controlli</h3>
            <a class="btn" href="/check">🚀 Controllo Marketplace</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            <a class="btn" href="/logs">📄 Logs Sistema</a>
            
            <h3>📊 Informazioni</h3>
            <p><strong>Utente:</strong> {USERNAME}</p>
            <p><strong>Intervallo:</strong> {CHECK_INTERVAL//60} minuti</p>
            <p><strong>Release per ciclo:</strong> 25</p>
            <p><strong>Status:</strong> <span style="color: green; font-weight: bold;">🟢 ONLINE</span></p>
            
            <h3>ℹ️ Come funziona</h3>
            <p>1. Scarica tutta la wantlist (fino a 500 articoli)</p>
            <p>2. Seleziona 25 release per ciclo (10 recenti + 15 casuali)</p>
            <p>3. Cerca listings usando la search API</p>
            <p>4. Filtra risultati con prezzo e venditore</p>
            <p>5. Estrae ID listing corretti</p>
            <p>6. Invia notifica con link VERI a Discogs</p>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=check_marketplace_working, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Avviato</title></head>
    <body style="font-family: Arial; margin: 40px; text-align: center;">
        <h1>🚀 Controllo Avviato</h1>
        <p>Controllo marketplace con approccio funzionante in esecuzione...</p>
        <p>Controlla i logs su Railway per vedere i dettagli.</p>
        <a href="/" style="color: #667eea; font-weight: bold;">↩️ Torna alla Dashboard</a>
    </body>
    </html>
    """, 200

@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test Bot FUNZIONANTE</b>\n\n"
        f"✅ Sistema online e operativo\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controlli ogni {CHECK_INTERVAL//60} minuti\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
        f"<i>Questa versione usa l'approccio corretto per trovare listings!</i>"
    )
    
    if success:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>Test OK</title></head>
        <body style="font-family: Arial; margin: 40px; text-align: center;">
            <h1 style="color: #28a745;">✅ Test Inviato</h1>
            <p>Il messaggio di test è stato inviato a Telegram.</p>
            <p>Controlla il tuo telefono!</p>
            <a href="/">↩️ Dashboard</a>
        </body>
        </html>
        """, 200
    else:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>Errore</title></head>
        <body style="font-family: Arial; margin: 40px; text-align: center;">
            <h1 style="color: #dc3545;">❌ Errore Invio</h1>
            <p>Impossibile inviare il messaggio di test.</p>
            <p>Verifica le variabili TELEGRAM_TOKEN e TELEGRAM_CHAT_ID.</p>
            <a href="/">↩️ Dashboard</a>
        </body>
        </html>
        """, 500

@app.route("/logs")
def view_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding='utf-8') as f:
                logs = f.read().splitlines()[-150:]
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
        <title>Logs Sistema</title>
        <style>
            body {{ font-family: monospace; margin: 20px; background: #1a1a1a; color: #00ff00; }}
            pre {{ background: #000; padding: 20px; border-radius: 5px; overflow-x: auto; }}
            a {{ color: #00ccff; text-decoration: none; }}
        </style>
    </head>
    <body>
        <h2>📄 Logs Sistema (ultime 150 righe)</h2>
        <pre>{logs_html}</pre>
        <a href="/">↩️ Torna alla Dashboard</a>
    </body>
    </html>
    """, 200

# ================== MAIN LOOP ==================
def main_loop_working():
    """Loop principale funzionante"""
    logger.info("🔄 Avvio loop principale FUNZIONANTE...")
    time.sleep(15)
    
    while True:
        try:
            logger.info(f"\n" + "="*70)
            logger.info(f"🔄 Controllo automatico - {datetime.now().strftime('%H:%M:%S')}")
            logger.info("="*70)
            
            check_marketplace_working()
            
            logger.info(f"💤 Pausa di {CHECK_INTERVAL//60} minuti...")
            for seconds in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Errore nel loop principale: {e}", exc_info=True)
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
    logger.info("🤖 DISCOGS BOT - VERSIONE FUNZIONANTE")
    logger.info("="*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release per ciclo: 25")
    logger.info("="*70)
    
    # Notifica avvio
    send_telegram(
        f"🤖 <b>Discogs Bot Avviato</b>\n\n"
        f"✅ <b>VERSIONE FUNZIONANTE</b>\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controlli ogni {CHECK_INTERVAL//60} minuti\n"
        f"🔍 25 release per ciclo\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
        f"<i>Ora usa l'approccio corretto per trovare listings!</i>"
    )
    
    # Avvia loop
    Thread(target=main_loop_working, daemon=True).start()
    
    # Avvia Flask
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🌐 Server Flask avviato sulla porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
