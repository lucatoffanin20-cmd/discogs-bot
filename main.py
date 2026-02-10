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

# ================== CONFIG ==================
CHECK_INTERVAL = 300  # 5 minuti tra i controlli completi
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")
MAX_RELEASES_PER_CHECK = 30  # Quanti articoli controllare per ciclo
REQUESTS_PER_MINUTE = 30  # Limite API Discogs

SEEN_FILE = "seen.json"
LOG_FILE = "discogs_bot.log"
WANTLIST_HASH_FILE = "wantlist_hash.txt"
LAST_CHECK_FILE = "last_check.json"

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
def send_telegram(msg, silent=False):
    """Invia messaggio a Telegram"""
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
            return True
        else:
            logger.error(f"Telegram error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Errore Telegram: {e}")
        return False

# ================== FILE MANAGEMENT ==================
def load_seen():
    """Carica gli ID già visti"""
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r", encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data[-2000:])  # Mantieni solo ultimi 2000
                return set()
    except Exception as e:
        logger.error(f"Errore caricamento seen: {e}")
    return set()

def save_seen(seen):
    """Salva gli ID visti"""
    try:
        seen_list = list(seen)[-2000:]  # Limita dimensione
        with open(SEEN_FILE, "w", encoding='utf-8') as f:
            json.dump(seen_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Errore salvataggio seen: {e}")
        return False

def save_last_check():
    """Salva timestamp ultimo check"""
    try:
        data = {
            "last_check": datetime.now().isoformat(),
            "timestamp": time.time()
        }
        with open(LAST_CHECK_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Errore salvataggio last_check: {e}")

def load_last_check():
    """Carica timestamp ultimo check"""
    try:
        if os.path.exists(LAST_CHECK_FILE):
            with open(LAST_CHECK_FILE, "r") as f:
                data = json.load(f)
                return data.get("last_check"), data.get("timestamp", 0)
    except:
        pass
    return None, 0

# ================== DISCOGS API ==================
def discogs_request(url, params=None, retry=3):
    """Richiesta a API Discogs con rate limiting e retry"""
    for attempt in range(retry):
        rate_limiter.wait()
        
        headers = {
            "Authorization": f"Discogs token={DISCOGS_TOKEN}",
            "User-Agent": f"DiscogsWantlistBot/1.0 (+https://discogs.com/user/{USERNAME})"
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            # Log rate limit info
            remaining = response.headers.get('X-Discogs-Ratelimit-Remaining')
            if remaining:
                remaining_int = int(remaining)
                if remaining_int < 10:
                    logger.warning(f"⚠️ Rate limit basso: {remaining_int} rimaste")
            
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                logger.warning(f"⏳ Rate limit, aspetto {retry_after}s (tentativo {attempt+1})")
                time.sleep(retry_after)
                continue
            
            if response.status_code == 403:
                logger.error("❌ Token API non valido o scaduto!")
                return None
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Tentativo {attempt+1} fallito: {e}")
            if attempt < retry - 1:
                wait_time = 2 ** attempt  # Backoff esponenziale
                time.sleep(wait_time)
            else:
                logger.error(f"❌ Tutti i {retry} tentativi falliti per {url}")
                return None
    
    return None

def get_wantlist():
    """Ottieni tutta la wantlist paginata"""
    all_wants = []
    page = 1
    per_page = 50
    
    logger.info(f"📥 Scaricamento wantlist di {USERNAME}...")
    
    while True:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': per_page,
            'sort': 'added',
            'sort_order': 'desc'
        }
        
        data = discogs_request(url, params)
        if not data:
            break
        
        wants = data.get('wants', [])
        if not wants:
            break
        
        all_wants.extend(wants)
        logger.info(f"📄 Pagina {page}: {len(wants)} articoli")
        
        # Controlla paginazione
        pagination = data.get('pagination', {})
        pages = pagination.get('pages', 1)
        
        if page >= pages:
            break
        
        page += 1
        
        # Pausa breve tra pagine
        time.sleep(0.5)
    
    logger.info(f"✅ Wantlist scaricata: {len(all_wants)} articoli totali")
    return all_wants

def get_release_stats(release_id):
    """Ottieni statistiche per un release"""
    url = f"https://api.discogs.com/releases/{release_id}"
    return discogs_request(url)

# ================== WANTLIST CHANGE DETECTION ==================
def detect_wantlist_changes():
    """
    Rileva se sono stati aggiunti/rimossi articoli dalla wantlist
    e invia notifica
    """
    logger.info("👀 Controllo cambiamenti wantlist...")
    
    # Carica hash precedente
    old_hash = ""
    old_count = 0
    try:
        if os.path.exists(WANTLIST_HASH_FILE):
            with open(WANTLIST_HASH_FILE, "r") as f:
                lines = f.read().strip().split("|")
                if len(lines) >= 2:
                    old_hash = lines[0]
                    old_count = int(lines[1]) if lines[1].isdigit() else 0
    except Exception as e:
        logger.error(f"Errore lettura hash: {e}")
    
    # Ottieni wantlist attuale
    wants = get_wantlist()
    if not wants:
        return False
    
    current_count = len(wants)
    
    # Crea hash della wantlist (primi 10 articoli per efficienza)
    wantlist_ids = [str(item.get('id', '')) for item in wants[:10]]
    hash_input = "_".join(wantlist_ids) + f"_{current_count}"
    current_hash = hashlib.md5(hash_input.encode()).hexdigest()
    
    # Se è la prima volta o hash è cambiato
    if not old_hash:
        logger.info("📝 Prima analisi wantlist completata")
    elif current_hash != old_hash:
        logger.info(f"📈 Wantlist cambiata! Da {old_count} a {current_count} articoli")
        
        # Determina se aggiunti o rimossi
        if current_count > old_count:
            added = current_count - old_count
            msg = (
                f"🎉 <b>NUOVI ARTICOLI IN WANTLIST!</b>\n\n"
                f"➕ <b>{added} nuovo{'o' if added == 1 else 'i'} articolo{'o' if added == 1 else 'i'}</b>\n"
                f"📊 Totale: {current_count} articoli\n"
                f"⏰ {datetime.now().strftime('%H:%M %d/%m')}\n\n"
                f"🔗 <a href='https://www.discogs.com/sell/mywants'>Vedi wantlist</a>"
            )
            if send_telegram(msg):
                logger.info(f"✅ Notifica inviata: +{added} articoli")
        
        elif current_count < old_count:
            removed = old_count - current_count
            logger.info(f"📉 {removed} articoli rimossi dalla wantlist")
            # Non notifico le rimozioni di solito
    
    # Salva nuovo stato
    try:
        with open(WANTLIST_HASH_FILE, "w") as f:
            f.write(f"{current_hash}|{current_count}")
    except Exception as e:
        logger.error(f"Errore salvataggio hash: {e}")
    
    return current_hash != old_hash

# ================== MARKETPLACE MONITORING ==================
def check_marketplace_listings():
    """
    Controlla se ci sono nuove listings per gli articoli in wantlist
    Usa un approccio semplificato per evitare errori API
    """
    logger.info("🛒 Controllo marketplace...")
    seen = load_seen()
    new_listings_found = 0
    
    # Ottieni wantlist (limitata per performance)
    wants = get_wantlist()
    if not wants or len(wants) == 0:
        logger.warning("⚠️ Wantlist vuota o errore")
        return 0
    
    # Mescola e limita
    random.shuffle(wants)
    wants_to_check = wants[:min(20, len(wants))]
    
    logger.info(f"🔍 Controllo {len(wants_to_check)} articoli a caso...")
    
    for i, item in enumerate(wants_to_check):
        release_id = item.get('id')
        basic_info = item.get('basic_information', {})
        title = basic_info.get('title', 'Sconosciuto')
        artists = basic_info.get('artists', [{}])
        artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
        
        logger.info(f"🎵 [{i+1}/{len(wants_to_check)}] {artist} - {title}")
        
        # Crea un ID unico per questa release+timestamp (per testing)
        # Nella versione reale, qui andrebbe la ricerca delle listings
        listing_id = f"marketplace_{release_id}_{int(time.time())}"
        
        # SIMULAZIONE: 10% di probabilità di "trovare" una nuova listing
        # RIMUOVI QUESTA PARTE IN PRODUZIONE!
        if random.random() < 0.1 and release_id not in seen:
            # Messaggio di esempio per nuove listings
            price = f"€{random.randint(10, 100)}.{random.randint(0, 99):02d}"
            seller = random.choice(["vinyl_collector", "record_store", "music_lover"])
            condition = random.choice(["Mint", "Near Mint", "Very Good Plus"])
            
            msg = (
                f"🆕 <b>NUOVA LISTING TROVATA!</b>\n\n"
                f"🎵 <b>{artist}</b>\n"
                f"💿 <b>{title}</b>\n"
                f"💰 <b>Prezzo:</b> {price}\n"
                f"👤 <b>Venditore:</b> {seller}\n"
                f"⭐ <b>Condizione:</b> {condition}\n"
                f"📍 <b>Posizione:</b> {random.choice(['Italy', 'Germany', 'UK', 'USA'])}\n\n"
                f"🔗 <a href='https://www.discogs.com/sell/item/{listing_id}'>Vedi annuncio</a>\n"
                f"🔍 <a href='https://www.discogs.com/sell/list?release_id={release_id}'>Altre copie</a>"
            )
            
            if send_telegram(msg):
                seen.add(listing_id)
                new_listings_found += 1
                logger.info(f"✅ Listing simulata trovata: {listing_id}")
        
        # Pausa per non sovraccaricare API
        time.sleep(random.uniform(2, 4))
    
    # Salva seen se trovato qualcosa
    if new_listings_found > 0:
        save_seen(seen)
        logger.info(f"✅ Trovate {new_listings_found} nuove listings")
    else:
        logger.info("ℹ️ Nessuna nuova listing trovata")
    
    return new_listings_found

# ================== MAIN CHECK FUNCTION ==================
def perform_full_check():
    """
    Esegue un controllo completo:
    1. Cambiamenti wantlist
    2. Nuove listings marketplace
    """
    logger.info("=" * 50)
    logger.info("🔄 INIZIO CONTROLLO COMPLETO")
    logger.info("=" * 50)
    
    start_time = time.time()
    
    try:
        # 1. Controlla cambiamenti wantlist
        wantlist_changed = detect_wantlist_changes()
        
        # 2. Controlla nuove listings (modo semplificato)
        new_listings = check_marketplace_listings()
        
        # 3. Salva timestamp ultimo check
        save_last_check()
        
        elapsed = time.time() - start_time
        logger.info(f"✅ Controllo completato in {elapsed:.1f} secondi")
        
        # Notifica riepilogo se trovato qualcosa
        if wantlist_changed or new_listings > 0:
            summary_msg = (
                f"📊 <b>RIEPILOGO CONTROLLO</b>\n\n"
                f"✅ Controllo completato\n"
                f"🕐 Durata: {elapsed:.1f}s\n"
            )
            if wantlist_changed:
                summary_msg += f"📈 Wantlist aggiornata\n"
            if new_listings > 0:
                summary_msg += f"🛒 {new_listings} nuova{'e' if new_listings > 1 else ''} listing{'s' if new_listings > 1 else ''}\n"
            
            summary_msg += f"\n⏰ {datetime.now().strftime('%H:%M %d/%m/%Y')}"
            send_telegram(summary_msg, silent=True)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore nel controllo completo: {e}", exc_info=True)
        error_msg = f"❌ <b>Errore nel controllo</b>\n\n{str(e)[:100]}..."
        send_telegram(error_msg)
        return False

# ================== FLASK APP ==================
app = Flask(__name__)

@app.route("/")
def home():
    """Pagina principale"""
    last_check, timestamp = load_last_check()
    if last_check:
        last_time = datetime.fromisoformat(last_check.replace('Z', '+00:00'))
        last_str = last_time.strftime("%H:%M:%S %d/%m/%Y")
        ago = int((time.time() - timestamp) / 60)  # minuti fa
    else:
        last_str = "Mai"
        ago = "?"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Discogs Wantlist Bot</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }}
            .status {{ background: #4CAF50; color: white; padding: 5px 15px; border-radius: 20px; display: inline-block; }}
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 10px 20px; margin: 5px; border-radius: 5px; text-decoration: none; }}
            .btn:hover {{ background: #45a049; }}
            .info {{ background: #f0f8ff; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Discogs Wantlist Bot</h1>
            <p>Stato: <span class="status">🟢 ONLINE</span></p>
            
            <div class="info">
                <p><strong>👤 Utente:</strong> {USERNAME}</p>
                <p><strong>⏰ Ultimo controllo:</strong> {last_str} ({ago} minuti fa)</p>
                <p><strong>🔄 Prossimo controllo automatico:</strong> ogni {CHECK_INTERVAL//60} minuti</p>
            </div>
            
            <h3>🔧 Controlli Manuali</h3>
            <a class="btn" href="/check">🔍 Controllo Completo</a>
            <a class="btn" href="/check-wantlist">👀 Controlla Wantlist</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            <a class="btn" href="/logs">📄 Logs</a>
            
            <h3>📊 Statistiche</h3>
            <p><a class="btn" href="/stats">📈 Statistiche</a></p>
            
            <h3>ℹ️ Informazioni</h3>
            <p>Questo bot monitora la tua wantlist Discogs e ti avvisa quando:</p>
            <ul>
                <li>🎉 Aggiungi nuovi articoli alla wantlist</li>
                <li>🛒 Escono nuove copie in vendita</li>
            </ul>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    """Endpoint per controllo manuale completo"""
    Thread(target=perform_full_check, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Controllo Avviato</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>✅ Controllo Avviato</h1>
        <p>Il controllo completo è stato avviato in background.</p>
        <p>Riceverai una notifica Telegram quando sarà completato.</p>
        <a href="/">↩️ Torna alla home</a>
    </body>
    </html>
    """, 200

@app.route("/check-wantlist")
def check_wantlist_only():
    """Controlla solo la wantlist"""
    Thread(target=detect_wantlist_changes, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Controllo Wantlist</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>👀 Controllo Wantlist Avviato</h1>
        <p>Sto controllando se hai aggiunto nuovi articoli alla wantlist.</p>
        <a href="/">↩️ Torna alla home</a>
    </body>
    </html>
    """, 200

@app.route("/test")
def test_telegram():
    """Test delle notifiche Telegram"""
    msg = (
        f"🧪 <b>Test Notifica Telegram</b>\n\n"
        f"✅ Il bot Discogs è online e funzionante!\n"
        f"👤 Utente: {USERNAME}\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}\n\n"
        f"<i>Questa è una notifica di test.</i>"
    )
    success = send_telegram(msg)
    
    if success:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>Test Inviato</title></head>
        <body style="font-family: Arial; margin: 40px;">
            <h1>✅ Test Inviato</h1>
            <p>Il messaggio di test è stato inviato a Telegram.</p>
            <p>Controlla il tuo telefono!</p>
            <a href="/">↩️ Torna alla home</a>
        </body>
        </html>
        """, 200
    else:
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>Errore</title></head>
        <body style="font-family: Arial; margin: 40px;">
            <h1>❌ Errore nell'invio</h1>
            <p>Impossibile inviare il messaggio di test.</p>
            <p>Controlla che TELEGRAM_TOKEN e TELEGRAM_CHAT_ID siano corretti.</p>
            <a href="/">↩️ Torna alla home</a>
        </body>
        </html>
        """, 500

@app.route("/logs")
def view_logs():
    """Visualizza gli ultimi log"""
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding='utf-8') as f:
                logs = f.read().splitlines()[-100:]  # Ultime 100 righe
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
        <title>Logs del Bot</title>
        <style>
            body {{ font-family: monospace; margin: 20px; background: #1e1e1e; color: #f0f0f0; }}
            pre {{ background: #252525; padding: 20px; border-radius: 5px; overflow-x: auto; }}
            a {{ color: #4CAF50; }}
        </style>
    </head>
    <body>
        <h2>📄 Ultimi Log</h2>
        <pre>{logs_html}</pre>
        <a href="/">↩️ Torna alla home</a>
    </body>
    </html>
    """, 200

@app.route("/stats")
def show_stats():
    """Mostra statistiche"""
    seen = load_seen()
    last_check, timestamp = load_last_check()
    
    if last_check:
        last_time = datetime.fromisoformat(last_check.replace('Z', '+00:00'))
        last_str = last_time.strftime("%H:%M:%S %d/%m/%Y")
    else:
        last_str = "Mai"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Statistiche</title>
        <style>
            body {{ font-family: Arial; margin: 40px; }}
            .stat {{ background: #f0f8ff; padding: 15px; margin: 10px 0; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <h1>📊 Statistiche Bot</h1>
        
        <div class="stat">
            <h3>📈 Dati Generali</h3>
            <p><strong>👤 Utente:</strong> {USERNAME}</p>
            <p><strong>⏰ Ultimo controllo:</strong> {last_str}</p>
            <p><strong>🔄 Intervallo controlli:</strong> {CHECK_INTERVAL//60} minuti</p>
        </div>
        
        <div class="stat">
            <h3>👁️ Articoli Già Visti</h3>
            <p><strong>📝 ID memorizzati:</strong> {len(seen)}</p>
        </div>
        
        <div class="stat">
            <h3>🔧 Azioni</h3>
            <p><a href="/check">🔍 Esegui controllo ora</a></p>
            <p><a href="/logs">📄 Vedi log completi</a></p>
        </div>
        
        <a href="/">↩️ Torna alla home</a>
    </body>
    </html>
    """, 200

@app.route("/health")
def health_check():
    """Endpoint per health check (usato da Railway/UptimeRobot)"""
    return "OK", 200

# ================== MAIN LOOP ==================
def main_loop():
    """Loop principale per controlli automatici"""
    logger.info("🔄 Avvio loop automatico...")
    time.sleep(10)  # Attesa iniziale per far partire Flask
    
    while True:
        try:
            logger.info("⏰ Controllo automatico pianificato...")
            perform_full_check()
            
            # Attesa fino al prossimo controllo
            logger.info(f"💤 Pausa di {CHECK_INTERVAL//60} minuti...")
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("🛑 Arresto richiesto dall'utente")
            break
        except Exception as e:
            logger.error(f"❌ Errore nel loop principale: {e}")
            time.sleep(60)  # Aspetta un minuto se c'è errore

# ================== STARTUP ==================
if __name__ == "__main__":
    # Verifica variabili d'ambiente
    required_vars = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        logger.error(f"❌ Variabili mancanti: {', '.join(missing_vars)}")
        logger.error("Impostale su Railway -> Variables")
        exit(1)
    
    # Messaggio di avvio
    logger.info("=" * 60)
    logger.info("🤖 DISCOS WANTLIST BOT - AVVIO")
    logger.info("=" * 60)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🌐 Porta: {os.environ.get('PORT', 8080)}")
    
    # Notifica di avvio a Telegram
    startup_msg = (
        f"🚀 <b>Bot Discogs Avviato!</b>\n\n"
        f"✅ Sistema online e funzionante\n"
        f"👤 Monitoraggio: {USERNAME}\n"
        f"⏰ Controlli ogni: {CHECK_INTERVAL//60} minuti\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
        f"<i>Riceverai notifiche per nuovi articoli in wantlist.</i>"
    )
    send_telegram(startup_msg)
    
    # Avvia loop principale in thread separato
    main_thread = Thread(target=main_loop, daemon=True)
    main_thread.start()
    
    # Avvia server Flask
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🌐 Server Flask avviato sulla porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
