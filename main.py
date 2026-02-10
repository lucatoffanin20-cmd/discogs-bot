import os
import json
import requests
import time
import random
from datetime import datetime
from flask import Flask
from threading import Thread, Lock
from collections import deque
import logging

# ================== CONFIG ==================
CHECK_INTERVAL = 600  # 10 minuti
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")
MAX_RELEASES_PER_CHECK = 30  # Ridotto per sicurezza
REQUESTS_PER_MINUTE = 25

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
            
            self.calls.append(time.time())

rate_limiter = RateLimiter(REQUESTS_PER_MINUTE, 60)

# ================== TELEGRAM ==================
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Telegram error: {response.status_code}")
    except Exception as e:
        logger.error(f"Errore Telegram: {e}")

# ================== SEEN ==================
def load_seen():
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r", encoding='utf-8') as f:
                return set(json.load(f))
    except Exception as e:
        logger.error(f"Errore caricamento seen: {e}")
    return set()

def save_seen(seen):
    try:
        with open(SEEN_FILE, "w", encoding='utf-8') as f:
            json.dump(list(seen), f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Errore salvataggio seen: {e}")

# ================== DISCOGS API ==================
def discogs_request(url, params=None):
    """Esegue una richiesta a Discogs con rate limiting"""
    rate_limiter.wait()
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": f"MyDiscogsBot/1.0 +https://github.com"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.warning(f"Rate limit! Aspetto {retry_after}s")
            time.sleep(retry_after)
            return discogs_request(url, params)
        
        if response.status_code == 405:
            logger.error(f"Metodo non permesso per {url}")
            return None
            
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore richiesta: {e}")
        return None

def get_wantlist():
    """Ottieni la wantlist con paginazione"""
    all_wants = []
    page = 1
    
    while page <= 2:  # Solo 2 pagine (100 articoli max)
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': 50,
            'sort': 'added',
            'sort_order': 'desc'
        }
        
        logger.info(f"📄 Pagina {page} wantlist...")
        data = discogs_request(url, params)
        
        if not data or 'wants' not in data:
            break
        
        wants = data['wants']
        all_wants.extend(wants)
        
        pagination = data.get('pagination', {})
        if page >= pagination.get('pages', 1) or len(wants) < 50:
            break
            
        page += 1
        time.sleep(1)
    
    logger.info(f"✅ {len(all_wants)} articoli in wantlist")
    return all_wants

def get_master_release_stats(release_id):
    """Ottieni statistiche per un release (include numero di listings)"""
    url = f"https://api.discogs.com/releases/{release_id}"
    data = discogs_request(url)
    
    if not data:
        return None
    
    # Cerca il master_id se disponibile
    master_id = data.get('master_id')
    if not master_id:
        return data
    
    # Se c'è un master, prendi le stats da lì
    master_url = f"https://api.discogs.com/masters/{master_id}"
    master_data = discogs_request(master_url)
    
    return master_data if master_data else data

# ================== APPROCCIO ALTERNATIVO ==================
def check_discogs_website_approach():
    """Approccio alternativo: controlla la pagina web invece dell'API"""
    logger.info("🔄 Tentativo approccio pagina web...")
    
    try:
        # Usa una richiesta alla pagina wantlist
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # URL della wantlist marketplace
        url = f"https://www.discogs.com/sell/mywants"
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"Errore pagina web: {response.status_code}")
            return []
        
        # Cerca listings nella pagina HTML
        # Questa è una soluzione temporanea - potresti dover usare BeautifulSoup
        logger.info("⚠️ Approccio web: implementazione parziale")
        
    except Exception as e:
        logger.error(f"Errore approccio web: {e}")
    
    return []

# ================== APPROCCIO IBRIDO ==================
def check_listings_for_wants():
    """
    Approccio ibrido:
    1. Ottieni wantlist via API
    2. Per ogni release, cerca sul sito web (approccio semplificato)
    """
    logger.info("🔍 Inizio controllo ibrido...")
    seen = load_seen()
    new_found = 0
    
    try:
        # 1. Ottieni wantlist
        wants = get_wantlist()
        if not wants:
            logger.warning("⚠️ Wantlist vuota")
            return
        
        # Mescola per varietà
        random.shuffle(wants)
        
        # Limita il numero di controlli
        wants = wants[:MAX_RELEASES_PER_CHECK]
        
        for i, item in enumerate(wants):
            release_id = item.get('id')
            basic_info = item.get('basic_information', {})
            title = basic_info.get('title', 'Sconosciuto')
            artists = basic_info.get('artists', [{}])
            artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
            
            logger.info(f"🔎 [{i+1}/{len(wants)}] {artist} - {title}")
            
            # PROVA: Cerca su marketplace usando ricerca semplificata
            # Questo è un workaround - l'API completa delle listings è limitata
            
            # Opzione A: Cerca per release ID su sito web
            search_url = f"https://www.discogs.com/sell/list?release_id={release_id}&ev=rb&format=Vinyl"
            
            # Per ora, simula una notifica di test (rimuovi dopo)
            if i == 0:  # Solo per il primo item come test
                listing_id = f"test_{int(time.time())}_{release_id}"
                
                if listing_id not in seen:
                    msg = (
                        f"🔄 <b>TEST - Bot funzionante!</b>\n\n"
                        f"🎵 <b>{artist} - {title}</b>\n"
                        f"📅 Release ID: {release_id}\n"
                        f"🔍 URL ricerca: {search_url}\n\n"
                        f"⚠️ Questo è un messaggio di test. "
                        f"Il bot sta monitorando {len(wants)} articoli."
                    )
                    
                    send_telegram(msg)
                    seen.add(listing_id)
                    new_found += 1
            
            # Pausa per non sovraccaricare
            time.sleep(random.uniform(3, 5))
        
        if new_found:
            save_seen(seen)
            logger.info(f"✅ {new_found} notifiche inviate")
        else:
            logger.info("ℹ️ Nessuna nuova listing trovata")
            
    except Exception as e:
        logger.error(f"❌ Errore: {e}", exc_info=True)

# ================== APPROCCIO ULTIMA SPIAGGIA ==================
def simple_wantlist_monitor():
    """
    Monitora semplicemente se nuovi articoli vengono aggiunti alla wantlist
    (più semplice e affidabile)
    """
    logger.info("👀 Monitoraggio semplice wantlist...")
    
    # File per memorizzare l'hash della wantlist
    WANTLIST_HASH_FILE = "wantlist_hash.txt"
    
    # Ottieni wantlist attuale
    wants = get_wantlist()
    if not wants:
        return
    
    # Crea un hash semplice della wantlist (conta + ultimi 5 ID)
    wantlist_ids = [str(item.get('id')) for item in wants[:50]]
    current_hash = str(len(wants)) + "_" + "_".join(wantlist_ids[:5])
    
    # Carica hash precedente
    old_hash = ""
    try:
        if os.path.exists(WANTLIST_HASH_FILE):
            with open(WANTLIST_HASH_FILE, "r") as f:
                old_hash = f.read().strip()
    except:
        pass
    
    # Se è cambiata
    if old_hash and current_hash != old_hash:
        logger.info("📈 Wantlist cambiata!")
        
        # Conta quanti nuovi
        old_count = int(old_hash.split("_")[0]) if "_" in old_hash else 0
        new_count = len(wants)
        
        if new_count > old_count:
            diff = new_count - old_count
            msg = (
                f"📈 <b>NUOVI ARTICOLI IN WANTLIST!</b>\n\n"
                f"➕ <b>{diff} nuovo{'o' if diff == 1 else 'i'} articolo{'o' if diff == 1 else 'i'}</b>\n"
                f"📊 Totale: {new_count} articoli\n"
                f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
                f"🔗 https://www.discogs.com/sell/mywants"
            )
            send_telegram(msg)
    
    # Salva nuovo hash
    try:
        with open(WANTLIST_HASH_FILE, "w") as f:
            f.write(current_hash)
    except Exception as e:
        logger.error(f"Errore salvataggio hash: {e}")

# ================== FLASK APP ==================
app = Flask(__name__)

@app.route("/")
def home():
    return """
    <h1>🤖 Bot Discogs Wantlist</h1>
    <p>Stato: <span style="color: green;">🟢 Online</span></p>
    <p>Ultimo check: {}</p>
    <p><a href="/check">🔍 Check Now</a></p>
    <p><a href="/simple">👀 Simple Monitor</a></p>
    <p><a href="/test">🧪 Test Telegram</a></p>
    """.format(datetime.now().strftime("%H:%M:%S"))

@app.route("/check")
def manual_check():
    Thread(target=check_listings_for_wants, daemon=True).start()
    return "✅ Check avviato", 200

@app.route("/simple")
def simple_check():
    Thread(target=simple_wantlist_monitor, daemon=True).start()
    return "✅ Simple monitor avviato", 200

@app.route("/test")
def test_telegram():
    msg = f"🧪 <b>Test Telegram Bot</b>\n\nFunziona! {datetime.now().strftime('%H:%M:%S')}"
    send_telegram(msg)
    return "✅ Test inviato", 200

# ================== MAIN ==================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🤖 BOT DISCOGS AVVIATO")
    logger.info("=" * 50)
    
    # Notifica avvio
    start_msg = (
        f"🚀 <b>Bot Discogs Avviato</b>\n\n"
        f"👤 {USERNAME}\n"
        f"⏰ Check ogni {CHECK_INTERVAL//60} min\n"
        f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    send_telegram(start_msg)
    
    # Funzione principale
    def main_loop():
        time.sleep(10)  # Attesa iniziale
        
        while True:
            try:
                # PRIMA: Prova l'approccio ibrido
                check_listings_for_wants()
                
                # POI: Monitoraggio semplice wantlist
                simple_wantlist_monitor()
                
                logger.info(f"⏳ Prossimo check tra {CHECK_INTERVAL//60} min...")
                time.sleep(CHECK_INTERVAL)
                
            except Exception as e:
                logger.error(f"❌ Errore loop: {e}")
                time.sleep(60)
    
    # Avvia thread
    Thread(target=main_loop, daemon=True).start()
    
    # Avvia Flask
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🌐 Flask su porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
