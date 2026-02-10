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
CHECK_INTERVAL = 600  # 10 minuti (Discogs ha limiti severi!)
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")
MAX_WANTS_PER_CHECK = 50  # Controlla solo 50 articoli per ciclo
REQUESTS_PER_MINUTE = 30  # Limite API Discogs

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
            # Rimuovi chiamate vecchie
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()
            
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    logger.info(f"⏳ Rate limit: aspetto {sleep_time:.1f} secondi")
                    time.sleep(sleep_time)
            
            self.calls.append(time.time())

rate_limiter = RateLimiter(REQUESTS_PER_MINUTE, 60)

# ================== TELEGRAM ==================
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        logger.error("Token Telegram non configurato")
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
                data = json.load(f)
                # Converte in set ma mantiene solo gli ultimi 1000 ID
                return set(data[-1000:]) if isinstance(data, list) else set()
    except Exception as e:
        logger.error(f"Errore caricamento seen: {e}")
    return set()

def save_seen(seen):
    try:
        # Salva solo gli ultimi 1000 ID per non far crescere troppo il file
        seen_list = list(seen)[-1000:]
        with open(SEEN_FILE, "w", encoding='utf-8') as f:
            json.dump(seen_list, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Errore salvataggio seen: {e}")

# ================== DISCOGS API ==================
def discogs_request(url, params=None):
    """Esegue una richiesta a Discogs con rate limiting"""
    rate_limiter.wait()
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": f"MyDiscogsWantlistBot/1.0 (contact: {USERNAME})"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        # Controlla rate limit
        remaining = int(response.headers.get('X-Discogs-Ratelimit-Remaining', 60))
        if remaining < 10:
            logger.warning(f"⚠️ Rate limit basso: {remaining} richieste rimaste")
            time.sleep(5)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.warning(f"Rate limit raggiunto! Aspetto {retry_after} secondi")
            time.sleep(retry_after)
            return discogs_request(url, params)  # Ritenta
        
        if response.status_code == 403:
            logger.error("❌ Token API scaduto o non valido!")
            return None
        
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore richiesta a {url}: {e}")
        return None

def get_paginated_wantlist():
    """Ottieni la wantlist paginata"""
    all_wants = []
    page = 1
    per_page = 50
    
    while page <= 3:  # Massimo 3 pagine (150 articoli per ciclo)
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': per_page,
            'sort': 'added',  # Più recenti prima
            'sort_order': 'desc'
        }
        
        logger.info(f"📄 Recupero pagina {page} della wantlist...")
        data = discogs_request(url, params)
        
        if not data or 'wants' not in data:
            break
        
        wants = data['wants']
        if not wants:
            break
        
        all_wants.extend(wants)
        
        # Controlla se ci sono altre pagine
        pagination = data.get('pagination', {})
        if page >= pagination.get('pages', 1):
            break
        
        page += 1
        time.sleep(1)  # Piccola pausa tra pagine
    
    logger.info(f"✅ Totale {len(all_wants)} articoli nella wantlist")
    return all_wants[:MAX_WANTS_PER_CHECK]  # Limita per ciclo

def get_listings_for_release(release_id, max_listings=3):
    """Ottieni le listings per un release"""
    url = "https://api.discogs.com/marketplace/listings"
    params = {
        'release_id': release_id,
        'status': 'For Sale',
        'per_page': max_listings,
        'sort': 'listed',  # Più recenti prima
        'sort_order': 'desc'
    }
    
    data = discogs_request(url, params)
    if data and 'listings' in data:
        return data['listings']
    return []

# ================== CORE LOGIC ==================
def check_new_listings():
    logger.info("🔍 Inizio controllo nuove listings...")
    seen = load_seen()
    new_found = 0
    total_checked = 0
    
    try:
        # Ottieni la wantlist (solo primi N articoli)
        wants = get_paginated_wantlist()
        
        if not wants:
            logger.warning("⚠️ Wantlist vuota o errore di recupero")
            return
        
        # Mescola gli articoli per non controllare sempre gli stessi
        random.shuffle(wants)
        
        for item in wants:
            release_id = item.get('id')
            if not release_id:
                continue
            
            basic_info = item.get('basic_information', {})
            title = basic_info.get('title', 'Sconosciuto')
            artists = basic_info.get('artists', [{}])
            artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
            
            logger.info(f"🎵 Controllo: {artist} - {title}")
            
            # Cerca listings per questo release
            listings = get_listings_for_release(release_id, max_listings=3)
            total_checked += 1
            
            for listing in listings:
                listing_id = str(listing.get('id'))
                
                if not listing_id or listing_id in seen:
                    continue
                
                # Nuova listing trovata!
                price = listing.get('price', {})
                price_formatted = price.get('formatted', 'N/D')
                seller = listing.get('seller', {}).get('username', 'N/D')
                condition = listing.get('condition', 'N/D')
                sleeve_condition = listing.get('sleeve_condition', 'N/D')
                location = listing.get('location', 'N/D')
                
                msg = (
                    f"🆕 <b>NUOVA LISTING DISPONIBILE!</b>\n\n"
                    f"🎵 <b>{artist}</b>\n"
                    f"📀 <b>{title}</b>\n"
                    f"💰 <b>Prezzo:</b> {price_formatted}\n"
                    f"👤 <b>Venditore:</b> {seller}\n"
                    f"📍 <b>Posizione:</b> {location}\n"
                    f"📦 <b>Condizione:</b> {condition}\n"
                    f"📁 <b>Sleeve:</b> {sleeve_condition}\n\n"
                    f"🔗 https://www.discogs.com/sell/item/{listing_id}"
                )
                
                send_telegram(msg)
                seen.add(listing_id)
                new_found += 1
                
                logger.info(f"✅ Nuova listing trovata: {listing_id}")
                time.sleep(0.5)  # Pausa tra notifiche
            
            # Pausa più lunga tra un release e l'altro
            if total_checked < len(wants):
                sleep_time = random.uniform(2, 4)
                time.sleep(sleep_time)
            
            # Se abbiamo trovato abbastanza nuove listings, fermati
            if new_found >= 10:
                logger.info("⚠️ Trovate 10 nuove listings, fermo il ciclo")
                break
        
        if new_found:
            save_seen(seen)
            logger.info(f"✅ {new_found} nuove listings notificate")
        else:
            logger.info("ℹ️ Nessuna nuova listing trovata")
            
    except Exception as e:
        logger.error(f"❌ Errore nel controllo: {e}", exc_info=True)

# ================== FLASK + SCHEDULER ==================
app = Flask(__name__)

@app.route("/")
def home():
    return """
    <h1>🤖 Bot Discogs Wantlist Monitor</h1>
    <p>Stato: <span style="color: green;">🟢 Online</span></p>
    <p>Ultimo controllo: {}</p>
    <p><a href="/check">🔍 Controllo manuale</a></p>
    <p><a href="/logs">📄 Logs</a></p>
    """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

@app.route("/check")
def manual_check():
    """Endpoint per controllo manuale"""
    Thread(target=check_new_listings, daemon=True).start()
    return "✅ Controllo avviato in background", 200

@app.route("/logs")
def view_logs():
    """Visualizza gli ultimi log"""
    try:
        with open(LOG_FILE, "r", encoding='utf-8') as f:
            logs = f.read().splitlines()[-50:]  # Ultime 50 righe
        return "<br>".join(logs)
    except:
        return "Nessun log disponibile"

# ================== MAIN ==================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🤖 AVVIO BOT DISCOGS WANTLIST MONITOR")
    logger.info("=" * 50)
    
    # Invia notifica di avvio
    start_msg = f"🤖 <b>Bot Discogs avviato</b>\n\n"
    start_msg += f"👤 Utente: {USERNAME}\n"
    start_msg += f"⏰ Controllo ogni: {CHECK_INTERVAL//60} minuti\n"
    start_msg += f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    send_telegram(start_msg)
    
    # Funzione di controllo periodico
    def run_periodic_checks():
        while True:
            try:
                check_new_listings()
                logger.info(f"⏳ Prossimo controllo tra {CHECK_INTERVAL//60} minuti...")
            except Exception as e:
                logger.error(f"Errore nel ciclo principale: {e}")
            
            time.sleep(CHECK_INTERVAL)
    
    # Avvia il checker in un thread separato
    checker_thread = Thread(target=run_periodic_checks, daemon=True)
    checker_thread.start()
    
    # Avvia Flask
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🌐 Server Flask in ascolto sulla porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
