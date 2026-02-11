import os
import json
import requests
import time
import random
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread, Lock
from collections import deque
import logging
import hashlib

# ================== CONFIG DEFINITIVO CON FIX ==================
CHECK_INTERVAL = 300  # 5 minuti
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

# IMPOSTAZIONI CON FIX
MAX_RELEASES_PER_CHECK = 50
REQUESTS_PER_MINUTE = 45
MAX_WANTLIST_PAGES = 10
ITEMS_PER_PAGE = 50
CACHE_MINUTES = 30

SEEN_FILE = "seen.json"
LOG_FILE = "discogs_bot.log"
WANTLIST_HASH_FILE = "wantlist_hash.txt"
LAST_CHECK_FILE = "last_check.json"
RELEASE_CACHE_FILE = "release_cache.json"
WANTLIST_CACHE_FILE = "wantlist_cache.json"

# ================== LOGGING DETTAGLIATO ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================== RATE LIMITER ==================
class RateLimiter:
    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = Lock()
    
    def wait(self):
        with self.lock:
            now = time.time()
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()
            
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
            
            self.calls.append(now)

rate_limiter = RateLimiter(REQUESTS_PER_MINUTE, 60)

# ================== CACHE SYSTEM ==================
class Cache:
    def __init__(self, cache_file, ttl):
        self.cache_file = cache_file
        self.ttl = ttl
        self.cache = {}
        self.load()
    
    def load(self):
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r") as f:
                    self.cache = json.load(f)
        except:
            self.cache = {}
    
    def save(self):
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache, f, indent=2)
        except:
            pass
    
    def get(self, key):
        item = self.cache.get(key)
        if item and time.time() - item.get('_time', 0) < self.ttl:
            return item.get('data')
        return None
    
    def set(self, key, data):
        self.cache[key] = {
            'data': data,
            '_time': time.time()
        }

release_cache = Cache(RELEASE_CACHE_FILE, CACHE_MINUTES * 60)
wantlist_cache = Cache(WANTLIST_CACHE_FILE, 300)

# ================== TELEGRAM ==================
def send_telegram(msg, silent=False):
    if not TG_TOKEN or not TG_CHAT:
        logger.error("Token Telegram mancante")
        return False
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "disable_notification": silent
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"📤 Telegram inviato: {msg[:50]}...")
            return True
        else:
            logger.error(f"Telegram error {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Errore Telegram: {e}")
        return False

# ================== FILE MANAGEMENT ==================
def load_seen():
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r", encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
    except Exception as e:
        logger.error(f"Errore caricamento seen: {e}")
    return set()

def save_seen(seen):
    try:
        with open(SEEN_FILE, "w", encoding='utf-8') as f:
            json.dump(list(seen), f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Salvati {len(seen)} ID in seen.json")
    except Exception as e:
        logger.error(f"Errore salvataggio seen: {e}")

# ================== DISCOGS API FIXED ==================
def discogs_api_call(url, params=None, cache_key=None):
    """API call con fix per risultati marketplace"""
    if cache_key:
        cached = release_cache.get(cache_key) if 'search' in str(cache_key) else wantlist_cache.get(cache_key)
        if cached:
            return cached
    
    rate_limiter.wait()
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": f"DiscogsBot/1.0"
    }
    
    try:
        logger.debug(f"🌐 API call: {url}")
        response = requests.get(url, headers=headers, params=params, timeout=20)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 30))
            logger.warning(f"⏳ Rate limit, aspetto {retry_after}s")
            time.sleep(retry_after)
            return discogs_api_call(url, params, cache_key)
        
        if response.status_code == 200:
            data = response.json()
            if cache_key:
                if 'search' in str(cache_key):
                    release_cache.set(cache_key, data)
                else:
                    wantlist_cache.set(cache_key, data)
            return data
        
        logger.error(f"❌ API error {response.status_code} per {url}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Errore API {url}: {e}")
        return None

def get_complete_wantlist():
    """Scarica tutta la wantlist"""
    cache_key = f"wantlist_{USERNAME}"
    cached = wantlist_cache.get(cache_key)
    if cached:
        logger.info(f"📚 Wantlist da cache: {len(cached)} articoli")
        return cached
    
    all_wants = []
    page = 1
    
    logger.info(f"📥 Scaricamento wantlist {USERNAME}...")
    
    while page <= MAX_WANTLIST_PAGES:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': ITEMS_PER_PAGE
        }
        
        data = discogs_api_call(url, params, f"wantlist_page_{page}")
        if not data:
            break
        
        wants = data.get('wants', [])
        if not wants:
            break
        
        all_wants.extend(wants)
        logger.info(f"📄 Pagina {page}: {len(wants)} articoli")
        
        pagination = data.get('pagination', {})
        if page >= pagination.get('pages', 1) or len(wants) < ITEMS_PER_PAGE:
            break
        
        page += 1
        time.sleep(0.5)
    
    logger.info(f"✅ Wantlist completa: {len(all_wants)} articoli")
    wantlist_cache.set(cache_key, all_wants)
    return all_wants

# ================== MARKETPLACE CHECK FIXED ==================
def check_release_for_listings_fixed(release_info, seen):
    """FIXED: Controllo corretto delle listings"""
    release_id = release_info.get('id')
    basic_info = release_info.get('basic_information', {})
    title = basic_info.get('title', 'Sconosciuto')
    artists = basic_info.get('artists', [{}])
    artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
    
    logger.info(f"  🔍 Controllo: {artist} - {title[:40]}...")
    
    # CERCA con parametri CORRETTI per marketplace
    search_url = "https://api.discogs.com/database/search"
    params = {
        'release_id': release_id,
        'type': 'release',
        'per_page': 5,
        'sort': 'listed',
        'sort_order': 'desc'
    }
    
    search_data = discogs_api_call(search_url, params, f"search_{release_id}")
    
    if not search_data:
        logger.info(f"    ❌ Nessun dato API")
        return 0
    
    if 'results' not in search_data:
        logger.info(f"    ❌ Nessun campo 'results'")
        return 0
    
    results = search_data['results']
    logger.info(f"    📊 {len(results)} risultati trovati")
    
    new_listings = 0
    
    for result in results:
        listing_id = str(result.get('id', ''))
        
        if not listing_id or listing_id in seen:
            continue
        
        # DEBUG: Log delle proprietà della listing
        price = result.get('price')
        formatted_price = result.get('formatted_price')
        status = result.get('status', '').lower()
        seller = result.get('seller', {})
        
        logger.info(f"    📝 Listing {listing_id}:")
        logger.info(f"      💰 Price: {price}, Formatted: {formatted_price}")
        logger.info(f"      🛒 Status: {status}")
        logger.info(f"      👤 Seller: {seller.get('username', 'N/A')}")
        
        # CRITERIO FIXED: deve avere price OPPURE formatted_price
        # E status 'for sale' (non sempre presente, ma utile)
        is_for_sale = (price is not None or formatted_price is not None)
        
        if not is_for_sale:
            logger.info(f"      ❌ Non in vendita (manca prezzo)")
            continue
        
        # LISTING VALIDA TROVATA!
        price_display = formatted_price or price or 'N/D'
        seller_name = seller.get('username', 'N/D')
        condition = result.get('condition', result.get('sleeve_condition', 'N/D'))
        
        # Costruisci URL REALE
        uri = result.get('uri', '')
        if uri and '/sell/item/' in uri:
            item_url = f"https://www.discogs.com{uri}"
        elif listing_id:
            item_url = f"https://www.discogs.com/sell/item/{listing_id}"
        else:
            item_url = f"https://www.discogs.com/sell/list?release_id={release_id}"
        
        # Verifica che l'URL sia valido
        logger.info(f"      🔗 URL: {item_url}")
        
        # Invia notifica
        msg = (
            f"🆕 <b>NUOVA COPIA IN VENDITA!</b>\n\n"
            f"🎸 <b>{artist}</b>\n"
            f"💿 {title}\n"
            f"💰 <b>{price_display}</b>\n"
            f"👤 {seller_name}\n"
            f"⭐ {condition}\n\n"
            f"🔗 <a href='{item_url}'>ACQUISTA SU DISCOGS</a>"
        )
        
        if send_telegram(msg):
            seen.add(listing_id)
            new_listings += 1
            logger.info(f"      ✅ NOTIFICA INVIATA!")
            break  # Una notifica per release
        else:
            logger.info(f"      ❌ ERRORE INVIO TELEGRAM")
    
    return new_listings

def check_50_releases_with_logging():
    """Controllo 50 release con logging dettagliato"""
    logger.info("🚀 Controllo marketplace con LOGGING DETTAGLIATO...")
    seen = load_seen()
    new_listings_found = 0
    
    # Ottieni wantlist
    wants = get_complete_wantlist()
    if not wants:
        return 0
    
    logger.info(f"📊 Wantlist totale: {len(wants)} articoli")
    logger.info(f"👁️ ID già visti: {len(seen)}")
    
    # Strategia di selezione
    if len(wants) <= MAX_RELEASES_PER_CHECK:
        releases_to_check = wants
    else:
        # 20 recenti + 30 casuali
        recent = wants[:20]
        remaining = wants[20:]
        random_sample = random.sample(remaining, min(30, len(remaining)))
        releases_to_check = recent + random_sample
    
    random.shuffle(releases_to_check)
    
    logger.info(f"🔍 Controllo {len(releases_to_check)} release...")
    
    for i, release_info in enumerate(releases_to_check):
        logger.info(f"\n[{i+1}/{len(releases_to_check)}] " + "="*50)
        
        try:
            new_listings = check_release_for_listings_fixed(release_info, seen)
            new_listings_found += new_listings
            
            if new_listings > 0:
                logger.info(f"✅ Trovate {new_listings} nuove listings!")
            
        except Exception as e:
            logger.error(f"❌ Errore release {i+1}: {e}")
        
        # Pausa
        time.sleep(random.uniform(2, 3))
        
        # Timeout
        if i >= 49:  # Massimo 50
            break
    
    # Salva risultati
    if new_listings_found > 0:
        save_seen(seen)
    
    logger.info(f"\n" + "="*60)
    logger.info(f"🎯 CONTROLLO COMPLETATO")
    logger.info(f"📈 Nuove listings trovate: {new_listings_found}")
    logger.info(f"👁️ ID totali memorizzati: {len(seen)}")
    logger.info("="*60)
    
    return new_listings_found

# ================== FUNZIONI AUSILIARIE ==================
def monitor_wantlist_changes():
    """Monitora cambiamenti wantlist"""
    logger.info("👀 Monitoraggio wantlist...")
    
    wants = get_complete_wantlist()
    if not wants:
        return False
    
    # Carica hash precedente
    old_hash = ""
    try:
        if os.path.exists(WANTLIST_HASH_FILE):
            with open(WANTLIST_HASH_FILE, "r") as f:
                old_hash = f.read().strip()
    except:
        pass
    
    # Calcola nuovo hash
    current_count = len(wants)
    sample_ids = [str(item.get('id', '')) for item in wants[:20]]
    hash_input = "_".join(sample_ids) + f"_{current_count}"
    current_hash = hashlib.md5(hash_input.encode()).hexdigest()
    
    changed = current_hash != old_hash if old_hash else False
    
    if changed and old_hash and current_count > int(old_hash.split('|')[1] if '|' in old_hash else 0):
        send_telegram(f"🎉 Nuovi articoli in wantlist! Totale: {current_count}")
    
    # Salva
    try:
        with open(WANTLIST_HASH_FILE, "w") as f:
            f.write(f"{current_hash}|{current_count}")
    except:
        pass
    
    return changed

# ================== MAIN CHECK ==================
def perform_check_with_fixes():
    """Controllo principale con fixes"""
    logger.info("\n" + "="*70)
    logger.info("🎯 CONTROLLO CON FIX - " + datetime.now().strftime("%H:%M:%S"))
    logger.info("="*70)
    
    try:
        # 1. Wantlist
        monitor_wantlist_changes()
        
        # 2. Marketplace (CON FIX)
        new_listings = check_50_releases_with_logging()
        
        # 3. Salva timestamp
        try:
            with open(LAST_CHECK_FILE, "w") as f:
                json.dump({
                    "last_check": datetime.now().isoformat(),
                    "listings_found": new_listings
                }, f)
        except:
            pass
        
        # 4. Notifica riepilogo
        if new_listings > 0:
            send_telegram(f"✅ Controllo completato: {new_listings} nuove listings trovate!", silent=True)
        
        logger.info(f"✅ Check completato - {new_listings} nuove listings")
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore check: {e}", exc_info=True)
        send_telegram(f"❌ Errore nel controllo: {str(e)[:100]}")
        return False

# ================== FLASK APP ==================
app = Flask(__name__)

@app.route("/")
def home():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Discogs Bot - FIXED</title>
        <style>
            body {{ font-family: Arial; margin: 40px; }}
            .alert {{ background: #ffebee; padding: 20px; border-left: 4px solid #f44336; }}
            .success {{ background: #e8f5e9; padding: 20px; border-left: 4px solid #4CAF50; }}
        </style>
    </head>
    <body>
        <h1>🤖 Discogs Bot - VERSIONE FIXED</h1>
        
        <div class="alert">
            <h3>⚠️ FIX APPLICATO</h3>
            <p>Corretto il controllo delle listings marketplace</p>
            <p>Ora il bot dovrebbe inviare notifiche correttamente</p>
        </div>
        
        <div class="success">
            <h3>✅ Configurazione</h3>
            <p><strong>Release per ciclo:</strong> {MAX_RELEASES_PER_CHECK}</p>
            <p><strong>Intervallo:</strong> {CHECK_INTERVAL//60} minuti</p>
            <p><strong>Utente:</strong> {USERNAME}</p>
        </div>
        
        <h3>🔧 Controlli</h3>
        <a href="/check" style="background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none;">🚀 Controllo FIXED</a>
        <a href="/check-marketplace" style="background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; margin-left: 10px;">🛒 Solo Marketplace</a>
        <a href="/test" style="background: #FF9800; color: white; padding: 10px 20px; text-decoration: none; margin-left: 10px;">🧪 Test Telegram</a>
        <a href="/logs" style="background: #9C27B0; color: white; padding: 10px 20px; text-decoration: none; margin-left: 10px;">📄 Logs</a>
        
        <h3>ℹ️ Informazioni</h3>
        <p>Questa versione include fix per il rilevamento corretto delle listings.</p>
        <p>Controlla i logs per vedere esattamente cosa viene trovato.</p>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=perform_check_with_fixes, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Avviato</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>🚀 Controllo FIXED Avviato</h1>
        <p>Controllo marketplace con fix applicati.</p>
        <p>Controlla i logs su Railway per vedere il debug dettagliato.</p>
        <a href="/">↩️ Home</a>
    </body></html>
    """, 200

@app.route("/check-marketplace")
def marketplace_check():
    Thread(target=check_50_releases_with_logging, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Marketplace</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>🛒 Marketplace Check Avviato</h1>
        <p>Controllo dettagliato con logging avanzato.</p>
        <a href="/">↩️ Home</a>
    </body></html>
    """, 200

@app.route("/test")
def test_telegram():
    msg = f"🧪 Test bot FIXED\n\nUtente: {USERNAME}\n{datetime.now().strftime('%H:%M %d/%m/%Y')}"
    send_telegram(msg)
    return "✅ Test inviato", 200

@app.route("/logs")
def view_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding='utf-8') as f:
                logs = f.read().splitlines()[-200:]
            logs_html = "<br>".join(logs)
        else:
            logs_html = "Nessun log"
    except:
        logs_html = "Errore logs"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: monospace; background: #000; color: #0f0; }}
            pre {{ white-space: pre-wrap; }}
        </style>
    </head>
    <body>
        <pre>{logs_html}</pre>
    </body>
    </html>
    """, 200

# ================== MAIN LOOP ==================
def main_loop_fixed():
    """Loop principale fixed"""
    time.sleep(10)
    
    while True:
        try:
            perform_check_with_fixes()
            
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    logger.info("="*70)
    logger.info("🤖 DISCOGS BOT - VERSIONE FIXED")
    logger.info("="*70)
    logger.info(f"👤 {USERNAME}")
    logger.info(f"🎯 {MAX_RELEASES_PER_CHECK} release/controllo")
    logger.info(f"⏰ Ogni {CHECK_INTERVAL//60} minuti")
    
    send_telegram(f"🤖 Bot FIXED avviato\nUtente: {USERNAME}\n{datetime.now().strftime('%H:%M %d/%m')}")
    
    Thread(target=main_loop_fixed, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
