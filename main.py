import os
import json
import requests
import time
import random
from datetime import datetime
from flask import Flask, request
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
        return response.status_code == 200
    except:
        return False

# ================== FILE MANAGEMENT ==================
def load_seen():
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
    except:
        return set()
    return set()

def save_seen(seen):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen)[-5000:], f)
    except:
        pass

# ================== DISCOGS API - ENDPOINT FUNZIONANTE ==================
def get_wantlist():
    """Ottieni wantlist completa"""
    all_wants = []
    page = 1
    
    logger.info(f"📥 Scaricamento wantlist...")
    
    while True:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {'page': page, 'per_page': 100, 'sort': 'added', 'sort_order': 'desc'}
        headers = {"Authorization": f"Discogs token={DISCOGS_TOKEN}", "User-Agent": "DiscogsBot/3.0"}
        
        try:
            time.sleep(0.5)
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
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
        except:
            break
    
    logger.info(f"✅ Wantlist: {len(all_wants)} articoli")
    return all_wants

def get_release_details(release_id):
    """Ottieni dettagli del release"""
    url = f"https://api.discogs.com/releases/{release_id}"
    headers = {"Authorization": f"Discogs token={DISCOGS_TOKEN}", "User-Agent": "DiscogsBot/3.0"}
    
    try:
        time.sleep(0.5)
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def search_marketplace_listings(query, release_id=None):
    """
    ENDPOINT CHE FUNZIONA: /database/search con filtri
    """
    url = "https://api.discogs.com/database/search"
    params = {
        'type': 'release',
        'sort': 'listed',
        'sort_order': 'desc',
        'per_page': 5
    }
    
    if release_id:
        params['release_id'] = release_id
    else:
        params['query'] = query
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": "DiscogsMarketplaceBot/3.0"
    }
    
    try:
        time.sleep(1.2)  # Rate limiting importante
        logger.info(f"   🔍 Search API: {release_id or query[:30]}...")
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            
            # Filtra SOLO listings del marketplace
            marketplace_items = []
            for r in results:
                # Controlla se è una listing valida
                uri = r.get('uri', '')
                has_price = r.get('formatted_price') or r.get('price')
                has_seller = r.get('seller') is not None
                
                # Una listing valida ha URI con /sell/item/ E prezzo
                if '/sell/item/' in uri and (has_price or has_seller):
                    marketplace_items.append(r)
            
            logger.info(f"   ✅ {len(marketplace_items)} listings trovate")
            return marketplace_items
        else:
            logger.error(f"   ❌ API error {response.status_code}")
            
    except Exception as e:
        logger.error(f"   ❌ Errore: {e}")
    
    return []

# ================== MARKETPLACE CHECK FINALE ==================
def check_marketplace_finale():
    """Versione FINALE che FUNZIONA"""
    logger.info("🔄 Controllo marketplace FINALE...")
    
    wants = get_wantlist()
    if not wants:
        return 0
    
    seen = load_seen()
    new_listings = 0
    
    logger.info(f"📊 Wantlist: {len(wants)} articoli")
    logger.info(f"👁️ ID già visti: {len(seen)}")
    
    # 40 release per ciclo (massimo per performance)
    check_count = min(40, len(wants))
    
    # Strategia: 20 recenti + 20 casuali
    recent = wants[:20]
    if len(wants) > 20:
        random_sample = random.sample(wants[20:], min(20, len(wants[20:])))
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
        
        # CERCA LISTINGS PER RELEASE ID
        listings = search_marketplace_listings(None, release_id)
        
        # Se non trova, cerca per titolo e artista
        if not listings:
            search_query = f"{artist} {title}"
            listings = search_marketplace_listings(search_query)
        
        if not listings:
            logger.info(f"   ℹ️ Nessuna listing trovata")
            continue
        
        for listing in listings:
            # Estrai listing ID dall'URI
            uri = listing.get('uri', '')
            listing_id = None
            
            if '/sell/item/' in uri:
                listing_id = uri.split('/')[-1]
            
            if not listing_id:
                continue
            
            if listing_id in seen:
                continue
            
            # Dati della listing
            price = listing.get('formatted_price') or listing.get('price', 'N/D')
            seller_info = listing.get('seller', {})
            seller = seller_info.get('username', 'N/D') if seller_info else 'N/D'
            condition = listing.get('condition', 'N/D')
            
            # URL REALE
            item_url = f"https://www.discogs.com/sell/item/{listing_id}"
            
            logger.info(f"   🛒 TROVATA! {listing_id}: {price} da {seller}")
            logger.info(f"   🔗 {item_url}")
            
            # Invia notifica
            msg = (
                f"🆕 <b>COPIA DISPONIBILE!</b>\n\n"
                f"🎸 <b>{artist}</b>\n"
                f"💿 {title}\n"
                f"💰 <b>{price}</b>\n"
                f"👤 {seller}\n"
                f"⭐ {condition}\n\n"
                f"🔗 <a href='{item_url}'>VEDI SU DISCOGS</a>"
            )
            
            if send_telegram(msg):
                seen.add(listing_id)
                new_listings += 1
                logger.info(f"   📤 NOTIFICA INVIATA!")
                break  # Una notifica per release
        
        # Pausa per rate limiting
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
        <title>🤖 Discogs Bot - FINALE</title>
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; }}
            .success {{ background: #d4edda; padding: 20px; border-radius: 5px; }}
            .btn {{ display: inline-block; background: #28a745; color: white; padding: 12px 24px; 
                    text-decoration: none; border-radius: 5px; margin: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Discogs Bot - VERSIONE FINALE</h1>
            
            <div class="success">
                <h3>✅ ENDPOINT FUNZIONANTE</h3>
                <p><strong>Utente:</strong> {USERNAME}</p>
                <p><strong>Release/ciclo:</strong> 40</p>
                <p><strong>Intervallo:</strong> {CHECK_INTERVAL//60} minuti</p>
                <p><strong>Status:</strong> 🟢 ONLINE</p>
            </div>
            
            <h3>🔧 Controlli</h3>
            <a class="btn" href="/check">🚀 Controllo Marketplace</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            <a class="btn" href="/logs">📄 Logs</a>
            <a class="btn" href="/force-check">⚡ Test Rapido</a>
            
            <h3>📊 Istruzioni</h3>
            <p>1. Vai su <strong>/force-check?release_id=14809291</strong> per test</p>
            <p>2. Controlla i logs per vedere se trova listings</p>
            <p>3. Se trova, le notifiche arriveranno automaticamente</p>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=check_marketplace_finale, daemon=True).start()
    return "<h1>🚀 Controllo avviato!</h1><a href='/'>↩️ Home</a>", 200

@app.route("/force-check")
def force_check():
    """Test forzato con release specifica"""
    release_id = request.args.get('release_id', '14809291')
    
    logger.info(f"⚡ TEST FORZATO per release {release_id}")
    
    listings = search_marketplace_listings(None, release_id)
    
    html = f"<h2>Test Release {release_id}</h2>"
    html += f"<p>Trovate {len(listings)} listings</p><ul>"
    
    for l in listings[:5]:
        uri = l.get('uri', '')
        lid = uri.split('/')[-1] if '/sell/item/' in uri else 'N/A'
        price = l.get('formatted_price') or l.get('price', 'N/D')
        html += f"<li>🛒 {lid}: {price}</li>"
    
    html += "</ul><a href='/'>↩️ Home</a>"
    return html, 200

@app.route("/test")
def test_telegram():
    success = send_telegram(f"🧪 Test Bot FINALE\n{datetime.now().strftime('%H:%M %d/%m/%Y')}")
    return "✅ Test inviato" if success else "❌ Errore", 200

@app.route("/logs")
def view_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = f.read().splitlines()[-100:]
        return "<pre>" + "<br>".join(logs) + "</pre><br><a href='/'>↩️ Home</a>", 200
    except:
        return "<pre>Nessun log</pre><a href='/'>↩️ Home</a>", 200

# ================== MAIN LOOP ==================
def main_loop():
    time.sleep(10)
    while True:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔄 Controllo - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            check_marketplace_finale()
            
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Loop error: {e}")
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    logger.info('='*70)
    logger.info("🤖 DISCOGS BOT - VERSIONE FINALE")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info('='*70)
    
    send_telegram(f"🤖 Bot FINALE avviato!\n👤 {USERNAME}\n✅ 40 release/ciclo")
    
    Thread(target=main_loop, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
