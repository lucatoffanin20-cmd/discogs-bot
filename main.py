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

# ================== CONFIG OTTIMIZZATO ==================
CHECK_INTERVAL = 300  # 5 minuti (300 secondi)
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

# IMPOSTAZIONI OTTIMIZZATE PER PIÙ ARTICOLI
MAX_RELEASES_PER_CHECK = 40  # Aumentato da 15 a 40 (+166%)
REQUESTS_PER_MINUTE = 40     # Aumentato da 25 a 40 (+60%)
PARALLEL_CHECKS = 3          # Check paralleli per release
CACHE_MINUTES = 30           # Cache per release già controllati

SEEN_FILE = "seen.json"
LOG_FILE = "discogs_bot.log"
WANTLIST_HASH_FILE = "wantlist_hash.txt"
LAST_CHECK_FILE = "last_check.json"
RELEASE_CACHE_FILE = "release_cache.json"

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

# ================== RATE LIMITER OTTIMIZZATO ==================
class SmartRateLimiter:
    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = Lock()
        self.last_warning = 0
    
    def wait(self, url=""):
        with self.lock:
            now = time.time()
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()
            
            current_calls = len(self.calls)
            
            # Se siamo al 90% del limite, rallenta
            if current_calls >= self.max_calls * 0.9:
                if now - self.last_warning > 60:  # Avvisa solo ogni minuto
                    logger.warning(f"⚠️ Rate limit al 90%: {current_calls}/{self.max_calls}")
                    self.last_warning = now
                sleep_time = random.uniform(0.5, 1.5)
                time.sleep(sleep_time)
            
            # Se al limite, aspetta
            if current_calls >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    logger.warning(f"⏳ Rate limit raggiunto, aspetto {sleep_time:.1f}s")
                    time.sleep(sleep_time)
            
            self.calls.append(now)

rate_limiter = SmartRateLimiter(REQUESTS_PER_MINUTE, 60)

# ================== CACHE SYSTEM ==================
class ReleaseCache:
    def __init__(self):
        self.cache = {}
        self.load()
    
    def load(self):
        try:
            if os.path.exists(RELEASE_CACHE_FILE):
                with open(RELEASE_CACHE_FILE, "r") as f:
                    data = json.load(f)
                    # Rimuovi cache vecchie più di CACHE_MINUTES minuti
                    now = time.time()
                    self.cache = {k: v for k, v in data.items() 
                                 if now - v.get('timestamp', 0) < CACHE_MINUTES * 60}
        except:
            self.cache = {}
    
    def save(self):
        try:
            with open(RELEASE_CACHE_FILE, "w") as f:
                json.dump(self.cache, f)
        except:
            pass
    
    def get(self, release_id):
        return self.cache.get(str(release_id))
    
    def set(self, release_id, data):
        self.cache[str(release_id)] = {
            'data': data,
            'timestamp': time.time(),
            'last_check': datetime.now().isoformat()
        }
        self.save()
    
    def cleanup(self):
        """Pulisce cache vecchia"""
        now = time.time()
        old_count = len(self.cache)
        self.cache = {k: v for k, v in self.cache.items() 
                     if now - v.get('timestamp', 0) < CACHE_MINUTES * 60}
        if len(self.cache) < old_count:
            self.save()
            logger.info(f"🧹 Cache pulita: {old_count - len(self.cache)} voci rimosse")

release_cache = ReleaseCache()

# ================== TELEGRAM ==================
def send_telegram(msg, silent=False):
    if not TG_TOKEN or not TG_CHAT:
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
                    return set(data[-5000:])  # Aumentato a 5000
                return set()
    except Exception as e:
        logger.error(f"Errore caricamento seen: {e}")
    return set()

def save_seen(seen):
    try:
        seen_list = list(seen)[-5000:]
        with open(SEEN_FILE, "w", encoding='utf-8') as f:
            json.dump(seen_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Errore salvataggio seen: {e}")
        return False

# ================== DISCOGS API OTTIMIZZATA ==================
def discogs_request_fast(url, params=None, cache_key=None):
    """Versione ottimizzata per performance"""
    if cache_key:
        cached = release_cache.get(cache_key)
        if cached:
            return cached['data']
    
    rate_limiter.wait(url)
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": f"DiscogsBot/2.0 ({USERNAME})"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 30))
            logger.warning(f"⏳ Rate limit, aspetto {retry_after}s")
            time.sleep(retry_after)
            return discogs_request_fast(url, params, cache_key)
        
        if response.status_code == 200:
            data = response.json()
            if cache_key:
                release_cache.set(cache_key, data)
            return data
        
        return None
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore richiesta: {e}")
        return None

def get_wantlist_fast():
    """Versione veloce per ottenere wantlist"""
    all_wants = []
    page = 1
    per_page = 100  # Massimo consentito dall'API
    
    logger.info(f"📥 Scaricamento veloce wantlist...")
    
    while page <= 2:  # Massimo 2 pagine (200 articoli)
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': per_page,
            'sort': 'added',
            'sort_order': 'desc'
        }
        
        data = discogs_request_fast(url, params, f"wantlist_page_{page}")
        if not data:
            break
        
        wants = data.get('wants', [])
        all_wants.extend(wants)
        
        if len(wants) < per_page:
            break
        
        page += 1
    
    logger.info(f"✅ Wantlist scaricata: {len(all_wants)} articoli")
    return all_wants

# ================== MARKETPLACE CHECK OTTIMIZZATO ==================
def check_release_for_listings(release_info, seen):
    """Controlla un singolo release per nuove listings"""
    release_id = release_info.get('id')
    basic_info = release_info.get('basic_information', {})
    title = basic_info.get('title', 'Sconosciuto')
    artists = basic_info.get('artists', [{}])
    artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
    
    # Cerca listings
    search_url = "https://api.discogs.com/database/search"
    params = {
        'release_id': release_id,
        'type': 'release',
        'per_page': 2,  # Solo 2 risultati (più veloce)
        'sort': 'listed',
        'sort_order': 'desc'
    }
    
    search_data = discogs_request_fast(search_url, params, f"search_{release_id}")
    
    if not search_data or 'results' not in search_data:
        return 0
    
    new_listings = 0
    
    for result in search_data['results'][:2]:  # Solo primi 2
        listing_id = str(result.get('id'))
        
        if not listing_id or listing_id in seen:
            continue
        
        if 'price' not in result and 'formatted_price' not in result:
            continue
        
        # LISTING TROVATA!
        price = result.get('formatted_price') or result.get('price', 'N/D')
        seller = result.get('seller', {}).get('username', 'N/D')
        
        # Costruisci URL
        uri = result.get('uri', '')
        if uri and '/sell/item/' in uri:
            item_url = f"https://www.discogs.com{uri}"
        else:
            item_url = f"https://www.discogs.com/sell/item/{listing_id}"
        
        # Notifica immediata
        msg = (
            f"🆕 <b>COPIA DISPONIBILE!</b>\n\n"
            f"🎵 <b>{artist}</b>\n"
            f"💿 {title}\n"
            f"💰 <b>{price}</b>\n"
            f"👤 {seller}\n\n"
            f"🔗 <a href='{item_url}'>ACQUISTA SU DISCOGS</a>"
        )
        
        if send_telegram(msg):
            seen.add(listing_id)
            new_listings += 1
            logger.info(f"✅ Listing {listing_id} - {price}")
            break  # Solo una notifica per release
    
    return new_listings

def optimized_marketplace_check():
    """
    Controllo marketplace OTTIMIZZATO per più articoli
    """
    logger.info("🚀 Controllo marketplace OTTIMIZZATO...")
    seen = load_seen()
    new_listings_found = 0
    total_checked = 0
    
    # Ottieni wantlist
    wants = get_wantlist_fast()
    if not wants:
        return 0
    
    # Strategia intelligente per selezione articoli
    wants_count = len(wants)
    
    if wants_count <= MAX_RELEASES_PER_CHECK:
        # Se pochi articoli, controllali tutti
        releases_to_check = wants
    else:
        # Strategia mista:
        # 1. Prendi i più recenti (30%)
        recent_count = int(MAX_RELEASES_PER_CHECK * 0.3)
        recent = wants[:recent_count]
        
        # 2. Prendi casuali dal resto (70%)
        remaining = wants[recent_count:]
        if len(remaining) > 0:
            random_count = MAX_RELEASES_PER_CHECK - recent_count
            random_selection = random.sample(remaining, min(random_count, len(remaining)))
            releases_to_check = recent + random_selection
        else:
            releases_to_check = recent
    
    # Mescola per varietà
    random.shuffle(releases_to_check)
    
    logger.info(f"🔍 Controllo {len(releases_to_check)}/{wants_count} articoli...")
    
    start_time = time.time()
    batch_start = time.time()
    
    for i, release_info in enumerate(releases_to_check):
        try:
            new_listings = check_release_for_listings(release_info, seen)
            new_listings_found += new_listings
            total_checked += 1
            
            # Log progresso ogni 5 articoli
            if (i + 1) % 5 == 0:
                elapsed = time.time() - batch_start
                logger.info(f"📊 Progresso: {i+1}/{len(releases_to_check)} - "
                          f"{new_listings_found} nuove - {elapsed:.1f}s")
                batch_start = time.time()
            
            # Pausa dinamica basata su performance
            if new_listings_found > 0:
                # Se stiamo trovando listings, rallenta un po'
                pause = random.uniform(2.0, 3.5)
            else:
                # Se non stiamo trovando nulla, possiamo andare più veloci
                pause = random.uniform(1.5, 2.5)
            
            time.sleep(pause)
            
            # Controllo timeout (non superare 4.5 minuti)
            total_elapsed = time.time() - start_time
            if total_elapsed > 270:  # 4.5 minuti
                logger.warning(f"⏰ Timeout raggiunto dopo {total_checked} articoli")
                break
                
        except Exception as e:
            logger.error(f"❌ Errore controllo release {i+1}: {e}")
            time.sleep(1)
    
    # Salva risultati
    if new_listings_found > 0:
        save_seen(seen)
    
    total_time = time.time() - start_time
    speed = total_checked / total_time if total_time > 0 else 0
    
    logger.info(f"✅ Controllo completato: {total_checked} articoli in {total_time:.1f}s "
                f"({speed:.2f} art/sec) - {new_listings_found} nuove listings")
    
    return new_listings_found

# ================== WANTLIST CHANGE DETECTION ==================
def detect_wantlist_changes():
    """Rileva cambiamenti wantlist (versione veloce)"""
    logger.info("👀 Controllo wantlist...")
    
    # Carica stato precedente
    old_hash = ""
    old_count = 0
    try:
        if os.path.exists(WANTLIST_HASH_FILE):
            with open(WANTLIST_HASH_FILE, "r") as f:
                lines = f.read().strip().split("|")
                if len(lines) >= 2:
                    old_hash = lines[0]
                    old_count = int(lines[1]) if lines[1].isdigit() else 0
    except:
        pass
    
    # Ottieni wantlist veloce
    wants = get_wantlist_fast()
    if not wants:
        return False
    
    current_count = len(wants)
    
    # Hash veloce (primi 20 articoli)
    wantlist_ids = [str(item.get('id', '')) for item in wants[:20]]
    hash_input = "_".join(wantlist_ids) + f"_{current_count}"
    current_hash = hashlib.md5(hash_input.encode()).hexdigest()
    
    changed = current_hash != old_hash if old_hash else False
    
    if changed and old_hash and current_count > old_count:
        added = current_count - old_count
        msg = (
            f"🎉 <b>NUOVI ARTICOLI IN WANTLIST!</b>\n\n"
            f"➕ <b>{added} nuovo{'o' if added == 1 else 'i'}</b>\n"
            f"📊 Totale: {current_count}\n\n"
            f"🔗 <a href='https://www.discogs.com/sell/mywants'>VEDI WANTLIST</a>"
        )
        send_telegram(msg)
        logger.info(f"✅ {added} nuovi articoli in wantlist")
    
    # Salva nuovo stato
    try:
        with open(WANTLIST_HASH_FILE, "w") as f:
            f.write(f"{current_hash}|{current_count}")
    except:
        pass
    
    return changed

# ================== MAIN CHECK OTTIMIZZATO ==================
def perform_optimized_check():
    """
    Controllo completo ottimizzato
    """
    logger.info("=" * 50)
    logger.info("🚀 CONTROLLO OTTIMIZZATO")
    logger.info("=" * 50)
    
    start_time = time.time()
    
    try:
        # 1. Controllo wantlist (veloce)
        wantlist_changed = detect_wantlist_changes()
        
        # 2. Controllo marketplace (ottimizzato)
        new_listings = optimized_marketplace_check()
        
        # 3. Pulisci cache
        release_cache.cleanup()
        
        elapsed = time.time() - start_time
        
        # Notifica riepilogo se necessario
        if new_listings > 0 or wantlist_changed:
            summary = f"📊 {new_listings} nuove listings"
            if wantlist_changed:
                summary += " + wantlist aggiornata"
            
            send_telegram(f"✅ Controllo completato: {summary}\n⏰ {elapsed:.1f}s", silent=True)
        
        logger.info(f"✅ Check ottimizzato completato in {elapsed:.1f}s")
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore check: {e}")
        return False

# ================== FLASK APP (OTTIMIZZATA) ==================
app = Flask(__name__)

@app.route("/")
def home():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Discogs Bot OTTIMIZZATO</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }}
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 12px 24px; margin: 8px; border-radius: 6px; text-decoration: none; font-weight: bold; }}
            .stats {{ background: #e8f5e9; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .warning {{ background: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Discogs Bot OTTIMIZZATO</h1>
            
            <div class="stats">
                <h3>⚡ PERFORMANCE OTTIMIZZATE</h3>
                <p><strong>🔍 Articoli per ciclo:</strong> {MAX_RELEASES_PER_CHECK}</p>
                <p><strong>⏰ Intervallo:</strong> {CHECK_INTERVAL//60} minuti</p>
                <p><strong>⚡ Velocità:</strong> Fino a {MAX_RELEASES_PER_CHECK} articoli/5min</p>
                <p><strong>🧠 Cache:</strong> {CACHE_MINUTES} minuti</p>
            </div>
            
            <div class="warning">
                <p><strong>🚀 VERSIONE OTTIMIZZATA ATTIVA!</strong></p>
                <p>Il bot ora controlla fino a <strong>{MAX_RELEASES_PER_CHECK} articoli</strong> ogni 5 minuti</p>
                <p>Sistema di cache attivo per massimizzare le performance</p>
            </div>
            
            <h3>🔧 Controlli Manuali</h3>
            <a class="btn" href="/check">🚀 Controllo OTTIMIZZATO</a>
            <a class="btn" href="/check-fast">⚡ Controllo VELOCE (solo marketplace)</a>
            <a class="btn" href="/check-wantlist">👀 Solo Wantlist</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            
            <h3>📊 Monitoraggio</h3>
            <a class="btn" href="/logs">📄 Logs</a>
            <a class="btn" href="/stats">📈 Statistiche</a>
            <a class="btn" href="/cache">🧠 Gestione Cache</a>
            
            <h3>ℹ️ Info</h3>
            <p>Versione ottimizzata per controllare <strong>più articoli in meno tempo</strong>.</p>
            <p>Utilizza cache intelligente e rate limiting avanzato.</p>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=perform_optimized_check, daemon=True).start()
    return """
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>Avviato</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>🚀 Controllo OTTIMIZZATO Avviato</h1>
        <p>Sto controllando fino a <strong>40 articoli</strong> in modo ottimizzato.</p>
        <p>Riceverai notifiche in tempo reale.</p>
        <a href="/">↩️ Torna alla home</a>
    </body></html>
    """, 200

@app.route("/check-fast")
def fast_check():
    Thread(target=optimized_marketplace_check, daemon=True).start()
    return """
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>Avviato</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>⚡ Controllo VELOCE Avviato</h1>
        <p>Solo controllo marketplace (più veloce).</p>
        <a href="/">↩️ Torna alla home</a>
    </body></html>
    """, 200

@app.route("/cache")
def cache_info():
    cache_size = len(release_cache.cache)
    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>Cache</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>🧠 Sistema Cache</h1>
        <p><strong>Voci in cache:</strong> {cache_size}</p>
        <p><strong>Durata cache:</strong> {CACHE_MINUTES} minuti</p>
        <p>La cache memorizza le risposte API per velocizzare i controlli successivi.</p>
        <a href="/">↩️ Torna alla home</a>
    </body></html>
    """, 200

# ... (mantieni le altre route: /test, /logs, /stats, /health come prima)

# ================== MAIN LOOP OTTIMIZZATO ==================
def optimized_main_loop():
    """Loop principale ottimizzato"""
    logger.info("🚀 Avvio loop ottimizzato...")
    time.sleep(10)
    
    check_count = 0
    
    while True:
        try:
            check_count += 1
            logger.info(f"🔄 Controllo #{check_count} (ottimizzato)...")
            
            perform_optimized_check()
            
            # Pausa precisa
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Errore loop: {e}")
            time.sleep(30)

# ================== STARTUP ==================
if __name__ == "__main__":
    # Verifica variabili
    required = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [var for var in required if not os.environ.get(var)]
    
    if missing:
        logger.error(f"❌ Variabili mancanti: {missing}")
        exit(1)
    
    logger.info("=" * 60)
    logger.info("🚀 DISCOS BOT OTTIMIZZATO - AVVIO")
    logger.info("=" * 60)
    logger.info(f"👤 {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} min")
    logger.info(f"🔍 Articoli/ciclo: {MAX_RELEASES_PER_CHECK}")
    logger.info(f"⚡ Rate limit: {REQUESTS_PER_MINUTE}/min")
    logger.info(f"🧠 Cache: {CACHE_MINUTES} min")
    
    # Notifica avvio
    startup_msg = (
        f"🚀 <b>Bot Discogs OTTIMIZZATO Avviato!</b>\n\n"
        f"✅ <b>PERFORMANCE MASSIMIZZATE</b>\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controlli: ogni {CHECK_INTERVAL//60} min\n"
        f"🔍 Articoli/ciclo: {MAX_RELEASES_PER_CHECK}\n"
        f"🧠 Cache: {CACHE_MINUTES} minuti\n\n"
        f"<i>Versione ottimizzata per più articoli in meno tempo!</i>"
    )
    send_telegram(startup_msg)
    
    # Avvia loop
    Thread(target=optimized_main_loop, daemon=True).start()
    
    # Avvia Flask
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
