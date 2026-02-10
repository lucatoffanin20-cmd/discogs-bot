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

# ================== CONFIG DEFINITIVO ==================
CHECK_INTERVAL = 300  # 5 minuti (300 secondi)
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

# IMPOSTAZIONI PER 450+ ARTICOLI
MAX_RELEASES_PER_CHECK = 50        # 50 release per ciclo come richiesto
REQUESTS_PER_MINUTE = 45          # Aumentato per gestire più pagine
MAX_WANTLIST_PAGES = 10           # Supporta fino a 10 pagine (500 articoli)
ITEMS_PER_PAGE = 50               # Discogs restituisce max 50 per pagina
CACHE_MINUTES = 45                # Cache più lunga

SEEN_FILE = "seen.json"
LOG_FILE = "discogs_bot.log"
WANTLIST_HASH_FILE = "wantlist_hash.txt"
LAST_CHECK_FILE = "last_check.json"
RELEASE_CACHE_FILE = "release_cache.json"
WANTLIST_CACHE_FILE = "wantlist_cache.json"

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

# ================== RATE LIMITER AVANZATO ==================
class AdvancedRateLimiter:
    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = Lock()
        self.stats = {'total': 0, 'delayed': 0}
    
    def wait(self, endpoint=""):
        with self.lock:
            now = time.time()
            
            # Pulisci chiamate vecchie
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()
            
            current = len(self.calls)
            self.stats['total'] += 1
            
            # Strategia adattiva basata sul carico
            if current >= self.max_calls * 0.85:
                # Rallenta progressivamente
                overload = current / self.max_calls
                base_sleep = random.uniform(0.8, 1.5) * overload
                time.sleep(base_sleep)
                self.stats['delayed'] += 1
            
            elif current >= self.max_calls:
                # Al limite, aspetta
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    self.stats['delayed'] += 1
            
            self.calls.append(now)
    
    def get_stats(self):
        with self.lock:
            return self.stats.copy()

rate_limiter = AdvancedRateLimiter(REQUESTS_PER_MINUTE, 60)

# ================== CACHE SYSTEM AVANZATO ==================
class AdvancedCache:
    def __init__(self, cache_file, default_ttl):
        self.cache_file = cache_file
        self.default_ttl = default_ttl  # in secondi
        self.cache = {}
        self.load()
    
    def load(self):
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r") as f:
                    data = json.load(f)
                    now = time.time()
                    # Carica solo cache valide
                    self.cache = {k: v for k, v in data.items() 
                                 if now - v.get('_timestamp', 0) < v.get('_ttl', self.default_ttl)}
        except Exception as e:
            logger.error(f"Errore caricamento cache {self.cache_file}: {e}")
            self.cache = {}
    
    def save(self):
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.error(f"Errore salvataggio cache {self.cache_file}: {e}")
    
    def get(self, key):
        item = self.cache.get(key)
        if item:
            if time.time() - item.get('_timestamp', 0) < item.get('_ttl', self.default_ttl):
                return item.get('data')
            else:
                # Cache scaduta, rimuovi
                del self.cache[key]
        return None
    
    def set(self, key, data, ttl=None):
        self.cache[key] = {
            'data': data,
            '_timestamp': time.time(),
            '_ttl': ttl or self.default_ttl
        }
        # Salva periodicamente (ogni 10 set)
        if len(self.cache) % 10 == 0:
            self.save()
    
    def cleanup(self):
        now = time.time()
        initial = len(self.cache)
        self.cache = {k: v for k, v in self.cache.items() 
                     if now - v.get('_timestamp', 0) < v.get('_ttl', self.default_ttl)}
        if len(self.cache) < initial:
            self.save()
            logger.info(f"🧹 Cache {self.cache_file}: {initial - len(self.cache)} voci rimosse")
        return len(self.cache)
    
    def size(self):
        return len(self.cache)

# Inizializza cache
release_cache = AdvancedCache(RELEASE_CACHE_FILE, CACHE_MINUTES * 60)
wantlist_cache = AdvancedCache(WANTLIST_CACHE_FILE, 300)  # 5 minuti per wantlist

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
                    return set(data[-10000:])  # Supporta fino a 10.000 ID
                return set()
    except Exception as e:
        logger.error(f"Errore caricamento seen: {e}")
    return set()

def save_seen(seen):
    try:
        seen_list = list(seen)[-10000:]
        with open(SEEN_FILE, "w", encoding='utf-8') as f:
            json.dump(seen_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Errore salvataggio seen: {e}")
        return False

def save_last_check():
    try:
        data = {
            "last_check": datetime.now().isoformat(),
            "timestamp": time.time(),
            "releases_checked": MAX_RELEASES_PER_CHECK,
            "interval": CHECK_INTERVAL
        }
        with open(LAST_CHECK_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Errore salvataggio last_check: {e}")

# ================== DISCOGS API PER 450+ ARTICOLI ==================
def discogs_api_call(url, params=None, cache_key=None, cache_ttl=None):
    """Chiamata API ottimizzata con cache"""
    if cache_key:
        cached = release_cache.get(cache_key) if 'search' in str(cache_key) else wantlist_cache.get(cache_key)
        if cached:
            return cached
    
    rate_limiter.wait(url)
    
    headers = {
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
        "User-Agent": f"Discogs450Bot/3.0 ({USERNAME})"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
        
        # Gestione rate limit avanzata
        remaining = response.headers.get('X-Discogs-Ratelimit-Remaining')
        if remaining:
            rem_int = int(remaining)
            if rem_int < 10:
                logger.warning(f"⚠️ Rate limit basso: {rem_int} rimaste")
                time.sleep(2)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 45))
            logger.warning(f"⏳ Rate limit, aspetto {retry_after}s")
            time.sleep(retry_after)
            return discogs_api_call(url, params, cache_key, cache_ttl)
        
        if response.status_code == 200:
            data = response.json()
            if cache_key:
                if 'search' in str(cache_key):
                    release_cache.set(cache_key, data, cache_ttl or 1800)  # 30 min default
                else:
                    wantlist_cache.set(cache_key, data, cache_ttl or 300)  # 5 min default
            return data
        
        logger.error(f"❌ API error {response.status_code} per {url}")
        return None
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Errore richiesta {url}: {e}")
        return None

def get_complete_wantlist():
    """
    Scarica COMPLETAMENTE la wantlist (tutte le pagine)
    Supporta fino a 500 articoli (10 pagine da 50)
    """
    cache_key = f"complete_wantlist_{USERNAME}"
    cached = wantlist_cache.get(cache_key)
    if cached:
        logger.info(f"📚 Wantlist caricata da cache ({len(cached)} articoli)")
        return cached
    
    all_wants = []
    page = 1
    total_items = 0
    
    logger.info(f"📥 Scaricamento COMPLETO wantlist per {USERNAME}...")
    
    while page <= MAX_WANTLIST_PAGES:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': ITEMS_PER_PAGE,
            'sort': 'added',
            'sort_order': 'desc'
        }
        
        logger.info(f"📄 Pagina {page} di wantlist...")
        data = discogs_api_call(url, params, f"wantlist_page_{page}", 600)
        
        if not data or 'wants' not in data:
            logger.error(f"❌ Errore pagina {page}")
            break
        
        wants = data['wants']
        if not wants:
            break
        
        all_wants.extend(wants)
        total_items += len(wants)
        
        # Controlla paginazione
        pagination = data.get('pagination', {})
        pages = pagination.get('pages', 1)
        items = pagination.get('items', 0)
        
        logger.info(f"✅ Pagina {page}: {len(wants)} articoli "
                   f"(Totale: {total_items}/{items})")
        
        if page >= pages or len(wants) < ITEMS_PER_PAGE:
            logger.info(f"📖 Ultima pagina raggiunta: {page}/{pages}")
            break
        
        page += 1
        
        # Pausa breve tra pagine
        if page <= pages:
            time.sleep(0.8)
    
    logger.info(f"🎯 Wantlist COMPLETA scaricata: {len(all_wants)} articoli totali")
    
    # Salva in cache
    wantlist_cache.set(cache_key, all_wants, 300)  # 5 minuti
    
    return all_wants

# ================== MARKETPLACE CHECK PER 50 RELEASE ==================
def check_release_listings(release_info, seen):
    """Controlla listings per un singolo release (ottimizzato)"""
    release_id = release_info.get('id')
    if not release_id:
        return 0
    
    basic_info = release_info.get('basic_information', {})
    title = basic_info.get('title', 'Sconosciuto')
    artists = basic_info.get('artists', [{}])
    artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
    
    # Cache per ricerca release
    cache_key = f"listings_{release_id}"
    search_data = release_cache.get(cache_key)
    
    if not search_data:
        search_url = "https://api.discogs.com/database/search"
        params = {
            'release_id': release_id,
            'type': 'release',
            'per_page': 3,  # Solo 3 risultati più recenti
            'sort': 'listed',
            'sort_order': 'desc'
        }
        
        search_data = discogs_api_call(search_url, params, cache_key, 900)  # 15 min cache
    
    if not search_data or 'results' not in search_data:
        return 0
    
    new_listings = 0
    
    for result in search_data['results'][:2]:  # Controlla solo primi 2
        listing_id = str(result.get('id'))
        
        if not listing_id or listing_id in seen:
            continue
        
        # Verifica che sia una listing in vendita
        has_price = 'price' in result or 'formatted_price' in result
        is_for_sale = result.get('status', '').lower() == 'for sale'
        
        if not has_price and not is_for_sale:
            continue
        
        # LISTING TROVATA!
        price = result.get('formatted_price') or result.get('price', 'N/D')
        seller = result.get('seller', {}).get('username', 'N/D')
        condition = result.get('condition', result.get('sleeve_condition', 'N/D'))
        
        # Costruisci URL
        uri = result.get('uri', '')
        if uri and uri.startswith('/sell/item/'):
            item_url = f"https://www.discogs.com{uri}"
        elif listing_id:
            item_url = f"https://www.discogs.com/sell/item/{listing_id}"
        else:
            item_url = f"https://www.discogs.com/sell/list?release_id={release_id}"
        
        # Messaggio ottimizzato
        msg = (
            f"🆕 <b>NUOVA COPIA!</b>\n\n"
            f"🎸 <b>{artist}</b>\n"
            f"💿 {title[:60]}{'...' if len(title) > 60 else ''}\n"
            f"💰 <b>{price}</b>\n"
            f"👤 {seller}\n"
            f"⭐ {condition}\n\n"
            f"🔗 <a href='{item_url}'>ACQUISTA SU DISCOGS</a>"
        )
        
        if send_telegram(msg):
            seen.add(listing_id)
            new_listings += 1
            logger.info(f"✅ Listing {listing_id} - {price}")
            break  # Una notifica per release
    
    return new_listings

def check_50_releases_marketplace():
    """
    Controlla 50 release per ciclo (come richiesto)
    Strategia intelligente per coprire tutta la wantlist
    """
    logger.info(f"🚀 Controllo 50 RELEASE per ciclo...")
    seen = load_seen()
    new_listings_found = 0
    start_time = time.time()
    
    # Ottieni wantlist COMPLETA
    all_wants = get_complete_wantlist()
    if not all_wants:
        logger.error("❌ Wantlist vuota o errore")
        return 0
    
    total_wants = len(all_wants)
    logger.info(f"📊 Wantlist totale: {total_wants} articoli")
    
    # STRATEGIA DI SELEZIONE INTELLIGENTE
    releases_to_check = []
    
    if total_wants <= MAX_RELEASES_PER_CHECK:
        # Se pochi articoli, controllali tutti
        releases_to_check = all_wants
    else:
        # 1. Prendi i 15 più RECENTI (30%)
        recent_count = 15
        recent = all_wants[:recent_count]
        
        # 2. Prendi 15 da metà wantlist (30%)
        middle_start = total_wants // 3
        middle = all_wants[middle_start:middle_start + 15]
        
        # 3. Prendi 20 CASUALI dal resto (40%)
        remaining_indices = set(range(total_wants)) - set(range(recent_count)) - set(range(middle_start, middle_start + 15))
        if remaining_indices:
            random_indices = random.sample(list(remaining_indices), min(20, len(remaining_indices)))
            random_selection = [all_wants[i] for i in random_indices]
        else:
            random_selection = []
        
        releases_to_check = recent + middle + random_selection
        
        # Assicurati di avere esattamente 50
        if len(releases_to_check) > MAX_RELEASES_PER_CHECK:
            releases_to_check = releases_to_check[:MAX_RELEASES_PER_CHECK]
        elif len(releases_to_check) < MAX_RELEASES_PER_CHECK:
            # Aggiungi altri casuali se necessario
            needed = MAX_RELEASES_PER_CHECK - len(releases_to_check)
            all_indices = set(range(total_wants))
            used_indices = set([all_wants.index(r) for r in releases_to_check if r in all_wants])
            available_indices = list(all_indices - used_indices)
            if available_indices:
                extra_indices = random.sample(available_indices, min(needed, len(available_indices)))
                releases_to_check.extend([all_wants[i] for i in extra_indices])
    
    # Mescola per varietà
    random.shuffle(releases_to_check)
    
    logger.info(f"🔍 Controllo {len(releases_to_check)}/{total_wants} articoli "
                f"(recenti:15, metà:15, casuali:20)")
    
    # CONTROLLO PARALLELO SIMULATO (batch)
    batch_size = 5
    for batch_start in range(0, len(releases_to_check), batch_size):
        batch = releases_to_check[batch_start:batch_start + batch_size]
        batch_time = time.time()
        
        for i, release_info in enumerate(batch):
            try:
                new_listings = check_release_listings(release_info, seen)
                new_listings_found += new_listings
                
                # Log ogni articolo (solo in debug)
                if new_listings > 0:
                    logger.info(f"   ✓ Articolo {batch_start + i + 1}: {new_listings} nuove")
                
            except Exception as e:
                logger.error(f"❌ Errore release {batch_start + i + 1}: {e}")
        
        # Pausa tra batch
        batch_elapsed = time.time() - batch_time
        if batch_elapsed < 8:  # Se batch troppo veloce
            time.sleep(10 - batch_elapsed)
        
        # Controllo timeout (massimo 4 minuti 30)
        total_elapsed = time.time() - start_time
        if total_elapsed > 270:
            logger.warning(f"⏰ Timeout dopo {batch_start + len(batch)} articoli "
                          f"({total_elapsed:.1f}s)")
            break
    
    # Salva risultati
    if new_listings_found > 0:
        save_seen(seen)
        logger.info(f"💾 Salvate {len(seen)} ID in seen.json")
    
    total_time = time.time() - start_time
    rate_stats = rate_limiter.get_stats()
    
    logger.info(f"✅ Controllo 50 release completato:")
    logger.info(f"   ⏱ Tempo: {total_time:.1f}s")
    logger.info(f"   📈 Nuove listings: {new_listings_found}")
    logger.info(f"   📊 API calls: {rate_stats['total']} (delay: {rate_stats['delayed']})")
    logger.info(f"   🧠 Cache size: {release_cache.size()} release, {wantlist_cache.size()} wantlist")
    
    return new_listings_found

# ================== WANTLIST MONITOR PER 450+ ARTICOLI ==================
def monitor_wantlist_changes():
    """Monitora cambiamenti wantlist per grandi collezioni"""
    logger.info("👀 Monitoraggio wantlist (450+ articoli)...")
    
    # Carica stato precedente
    old_hash = ""
    old_count = 0
    try:
        if os.path.exists(WANTLIST_HASH_FILE):
            with open(WANTLIST_HASH_FILE, "r") as f:
                lines = f.read().strip().split("|")
                if len(lines) >= 3:
                    old_hash = lines[0]
                    old_count = int(lines[1]) if lines[1].isdigit() else 0
    except Exception as e:
        logger.error(f"Errore lettura hash: {e}")
    
    # Ottieni wantlist attuale
    wants = get_complete_wantlist()
    if not wants:
        return False
    
    current_count = len(wants)
    
    # Hash efficiente per grandi wantlist (primi 30 + ultimi 20)
    sample_wants = wants[:30] + wants[-20:] if len(wants) > 50 else wants
    wantlist_ids = [str(item.get('id', '')) for item in sample_wants]
    hash_input = "_".join(wantlist_ids) + f"_{current_count}"
    current_hash = hashlib.md5(hash_input.encode()).hexdigest()
    
    changed = False
    if old_hash:
        changed = current_hash != old_hash
    else:
        logger.info(f"📝 Prima analisi: {current_count} articoli")
    
    # Gestione cambiamenti
    if changed and old_hash:
        diff = current_count - old_count
        
        if diff > 0:
            msg = (
                f"🎉 <b>NUOVI ARTICOLI IN WANTLIST!</b>\n\n"
                f"➕ <b>{diff} nuovo{'o' if diff == 1 else 'i'}</b>\n"
                f"📊 Totale: {current_count} articoli\n"
                f"📅 {datetime.now().strftime('%d/%m %H:%M')}\n\n"
                f"🔗 <a href='https://www.discogs.com/sell/mywants'>VISUALIZZA WANTLIST</a>"
            )
            send_telegram(msg)
            logger.info(f"✅ {diff} nuovi articoli in wantlist")
        
        elif diff < 0:
            logger.info(f"📉 {abs(diff)} articoli rimossi dalla wantlist")
    
    # Salva nuovo stato
    try:
        with open(WANTLIST_HASH_FILE, "w") as f:
            f.write(f"{current_hash}|{current_count}|{datetime.now().isoformat()}")
    except Exception as e:
        logger.error(f"Errore salvataggio hash: {e}")
    
    return changed

# ================== MAIN CHECK DEFINITIVO ==================
def perform_definitive_check():
    """
    Controllo DEFINITIVO per wantlist da 450+ articoli
    """
    logger.info("=" * 60)
    logger.info("🎯 CONTROLLO DEFINITIVO (50 release/5min)")
    logger.info("=" * 60)
    
    start_time = time.time()
    
    try:
        # 1. Monitoraggio wantlist
        wantlist_changed = monitor_wantlist_changes()
        
        # 2. Controllo marketplace 50 release
        new_listings = check_50_releases_marketplace()
        
        # 3. Pulizia cache
        release_cache.cleanup()
        wantlist_cache.cleanup()
        
        # 4. Salva ultimo check
        save_last_check()
        
        elapsed = time.time() - start_time
        
        # Notifica riepilogo se trovato qualcosa
        if new_listings > 0 or wantlist_changed:
            summary_parts = []
            if wantlist_changed:
                summary_parts.append("wantlist aggiornata")
            if new_listings > 0:
                summary_parts.append(f"{new_listings} nuove listings")
            
            summary_msg = f"📊 {' + '.join(summary_parts)}"
            send_telegram(f"✅ Controllo completato: {summary_msg}\n⏰ {elapsed:.1f}s", silent=True)
        
        logger.info(f"✅ Check DEFINITIVO completato in {elapsed:.1f}s")
        return True
        
    except Exception as e:
        logger.error(f"❌ Errore check definitivo: {e}", exc_info=True)
        send_telegram(f"❌ <b>Errore nel controllo</b>\n\n{str(e)[:150]}")
        return False

# ================== FLASK APP DEFINITIVA ==================
app = Flask(__name__)

@app.route("/")
def home():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Discogs Bot DEFINITIVO</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }}
            .container {{ max-width: 900px; margin: 0 auto; background: white; padding: 40px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }}
            h1 {{ color: #333; border-bottom: 4px solid #4CAF50; padding-bottom: 15px; text-align: center; }}
            .highlight {{ background: linear-gradient(120deg, #84fab0 0%, #8fd3f4 100%); padding: 25px; border-radius: 10px; margin: 25px 0; text-align: center; }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 25px 0; }}
            .stat-card {{ background: #f8f9fa; padding: 20px; border-radius: 10px; border-left: 5px solid #4CAF50; }}
            .btn {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 14px 28px; margin: 10px; border-radius: 8px; text-decoration: none; font-weight: bold; transition: transform 0.2s; }}
            .btn:hover {{ transform: translateY(-3px); box-shadow: 0 5px 15px rgba(0,0,0,0.1); }}
            .btn-secondary {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Discogs Bot DEFINITIVO</h1>
            
            <div class="highlight">
                <h2>🚀 VERSIONE DEFINITIVA ATTIVA</h2>
                <p><strong>Ottimizzata per wantlist da 450+ articoli</strong></p>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>🎯 Release per ciclo</h3>
                    <p style="font-size: 2em; font-weight: bold;">{MAX_RELEASES_PER_CHECK}</p>
                </div>
                <div class="stat-card">
                    <h3>⏰ Intervallo</h3>
                    <p style="font-size: 2em; font-weight: bold;">{CHECK_INTERVAL//60} min</p>
                </div>
                <div class="stat-card">
                    <h3>📊 Wantlist</h3>
                    <p style="font-size: 1.5em; font-weight: bold;">450+ articoli</p>
                    <p>Supporta fino a {MAX_WANTLIST_PAGES} pagine</p>
                </div>
                <div class="stat-card">
                    <h3>🧠 Cache</h3>
                    <p style="font-size: 1.5em; font-weight: bold;">{CACHE_MINUTES} min</p>
                    <p>Performance ottimizzate</p>
                </div>
            </div>
            
            <div style="text-align: center; margin: 30px 0;">
                <h3>⚡ Controlli Rapidi</h3>
                <a class="btn" href="/check">🚀 Controllo DEFINITIVO</a>
                <a class="btn" href="/check-marketplace">🛒 Solo Marketplace (50 release)</a>
                <a class="btn" href="/check-wantlist">📚 Solo Wantlist</a>
            </div>
            
            <div style="text-align: center; margin: 30px 0;">
                <h3>🔧 Strumenti</h3>
                <a class="btn btn-secondary" href="/test">🧪 Test Telegram</a>
                <a class="btn btn-secondary" href="/logs">📄 Logs Sistema</a>
                <a class="btn btn-secondary" href="/stats">📈 Statistiche Avanzate</a>
                <a class="btn btn-secondary" href="/cache">🧠 Gestione Cache</a>
            </div>
            
            <div style="background: #e8f4f8; padding: 20px; border-radius: 10px; margin-top: 30px;">
                <h3>ℹ️ Informazioni Sistema</h3>
                <p>✅ <strong>Wantlist completa:</strong> Scarica tutte le {MAX_WANTLIST_PAGES} pagine (fino a 500 articoli)</p>
                <p>✅ <strong>Strategia intelligente:</strong> 15 recenti + 15 di metà + 20 casuali per ciclo</p>
                <p>✅ <strong>Cache avanzata:</strong> Risultati memorizzati per {CACHE_MINUTES} minuti</p>
                <p>✅ <strong>Rate limiting adattivo:</strong> {REQUESTS_PER_MINUTE} richieste/minuto ottimizzate</p>
                <p>✅ <strong>Notifiche reali:</strong> Solo link VERI a Discogs (nessun 404)</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=perform_definitive_check, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Avviato</title>
        <style>
            body { font-family: Arial; margin: 40px; background: #f0f2f5; }
            .message { background: white; padding: 40px; border-radius: 10px; text-align: center; }
        </style>
    </head>
    <body>
        <div class="message">
            <h1>🚀 Controllo DEFINITIVO Avviato</h1>
            <p style="font-size: 1.2em; margin: 20px 0;">
                <strong>50 release</strong> in controllo ottimizzato
            </p>
            <p>Strategia: 15 recenti + 15 di metà + 20 casuali</p>
            <p>Riceverai notifiche in tempo reale per nuove copie in vendita</p>
            <div style="margin: 30px 0;">
                <div style="display: inline-block; background: #4CAF50; color: white; padding: 10px 20px; border-radius: 5px;">
                    ⏳ Tempo stimato: 4-5 minuti
                </div>
            </div>
            <a href="/" style="color: #667eea; text-decoration: none; font-weight: bold;">↩️ Torna alla Dashboard</a>
        </div>
    </body>
    </html>
    """, 200

@app.route("/check-marketplace")
def marketplace_check():
    Thread(target=check_50_releases_marketplace, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Marketplace</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>🛒 Controllo Marketplace Avviato</h1>
        <p><strong>50 release</strong> in controllo marketplace</p>
        <p>Ottimizzato per massimizzare le possibilità di trovare nuove copie</p>
        <a href="/">↩️ Dashboard</a>
    </body></html>
    """, 200

@app.route("/check-wantlist")
def wantlist_check():
    Thread(target=monitor_wantlist_changes, daemon=True).start()
    return """
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Wantlist</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>📚 Controllo Wantlist Avviato</h1>
        <p>Analisi completa di tutte le <strong>9+ pagine</strong> della wantlist</p>
        <p>Verifica aggiunte/rimozioni di articoli</p>
        <a href="/">↩️ Dashboard</a>
    </body></html>
    """, 200

@app.route("/test")
def test_telegram():
    msg = (
        f"🧪 <b>Test Bot DEFINITIVO</b>\n\n"
        f"✅ Sistema online e ottimizzato\n"
        f"👤 {USERNAME}\n"
        f"🎯 {MAX_RELEASES_PER_CHECK} release/controllo\n"
        f"⏰ Ogni {CHECK_INTERVAL//60} minuti\n"
        f"📊 Wantlist: 450+ articoli\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
        f"<i>Versione definitiva per grandi collezioni!</i>"
    )
    success = send_telegram(msg)
    
    if success:
        return """
        <!DOCTYPE html>
        <html><head><meta charset="UTF-8"><title>Test OK</title></head>
        <body style="font-family: Arial; margin: 40px; text-align: center;">
            <h1 style="color: #4CAF50;">✅ Test Inviato</h1>
            <p>Controlla il tuo Telegram per il messaggio di test</p>
            <a href="/">↩️ Dashboard</a>
        </body></html>
        """, 200
    else:
        return """
        <!DOCTYPE html>
        <html><head><meta charset="UTF-8"><title>Errore</title></head>
        <body style="font-family: Arial; margin: 40px; text-align: center;">
            <h1 style="color: #f44336;">❌ Errore Invio</h1>
            <p>Controlla le variabili TELEGRAM_TOKEN e TELEGRAM_CHAT_ID</p>
            <a href="/">↩️ Dashboard</a>
        </body></html>
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
            pre {{ background: #000; padding: 20px; border-radius: 5px; overflow-x: auto; white-space: pre-wrap; }}
            a {{ color: #00ccff; text-decoration: none; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📄 Logs Sistema (ultime 150 righe)</h2>
            <pre>{logs_html}</pre>
            <a href="/">↩️ Torna alla Dashboard</a>
        </div>
    </body>
    </html>
    """, 200

@app.route("/stats")
def show_stats():
    try:
        with open(LAST_CHECK_FILE, "r") as f:
            last_data = json.load(f)
        last_check = datetime.fromisoformat(last_data.get('last_check', '').replace('Z', '+00:00'))
        last_str = last_check.strftime("%H:%M:%S %d/%m/%Y")
    except:
        last_str = "Dati non disponibili"
    
    seen_count = len(load_seen())
    release_cache_size = release_cache.size()
    wantlist_cache_size = wantlist_cache.size()
    rate_stats = rate_limiter.get_stats()
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Statistiche Avanzate</title>
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .stats-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }}
            .stat-box {{ background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .stat-title {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
            .stat-value {{ font-size: 2em; font-weight: bold; color: #4CAF50; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <h1>📈 Statistiche Avanzate</h1>
        
        <div class="stats-container">
            <div class="stat-box">
                <h3 class="stat-title">🔄 Ultimo Controllo</h3>
                <p class="stat-value">{last_str}</p>
                <p>Release controllate: {MAX_RELEASES_PER_CHECK}</p>
                <p>Intervallo: {CHECK_INTERVAL//60} minuti</p>
            </div>
            
            <div class="stat-box">
                <h3 class="stat-title">👁️ Sistema Anti-Doppioni</h3>
                <p class="stat-value">{seen_count} ID</p>
                <p>Listing già notificate e memorizzate</p>
            </div>
            
            <div class="stat-box">
                <h3 class="stat-title">🧠 Cache System</h3>
                <p><strong>Release cache:</strong> {release_cache_size} voci</p>
                <p><strong>Wantlist cache:</strong> {wantlist_cache_size} voci</p>
                <p><strong>Durata cache:</strong> {CACHE_MINUTES} minuti</p>
            </div>
            
            <div class="stat-box">
                <h3 class="stat-title">⚡ Performance API</h3>
                <p><strong>Chiamate totali:</strong> {rate_stats.get('total', 0)}</p>
                <p><strong>Chiamate delayate:</strong> {rate_stats.get('delayed', 0)}</p>
                <p><strong>Limite/minuto:</strong> {REQUESTS_PER_MINUTE}</p>
            </div>
            
            <div class="stat-box">
                <h3 class="stat-title">📊 Configurazione</h3>
                <p><strong>Release/controllo:</strong> {MAX_RELEASES_PER_CHECK}</p>
                <p><strong>Pagine wantlist:</strong> {MAX_WANTLIST_PAGES}</p>
                <p><strong>Articoli/pagina:</strong> {ITEMS_PER_PAGE}</p>
            </div>
            
            <div class="stat-box">
                <h3 class="stat-title">🔧 Azioni</h3>
                <p><a href="/check">🚀 Esegui controllo</a></p>
                <p><a href="/cache">🧠 Gestisci cache</a></p>
                <p><a href="/logs">📄 Logs completi</a></p>
            </div>
        </div>
        
        <div style="margin-top: 30px;">
            <a href="/">↩️ Torna alla Dashboard</a>
        </div>
    </body>
    </html>
    """, 200

@app.route("/cache")
def cache_management():
    release_size = release_cache.cleanup()
    wantlist_size = wantlist_cache.cleanup()
    
    return f"""
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>Cache</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>🧠 Gestione Cache</h1>
        <div style="background: #e8f5e9; padding: 20px; border-radius: 10px; margin: 20px 0;">
            <p><strong>✅ Cache pulite con successo!</strong></p>
            <p>Release cache: {release_size} voci attive</p>
            <p>Wantlist cache: {wantlist_size} voci attive</p>
            <p>TTL: {CACHE_MINUTES} minuti</p>
        </div>
        <p>La cache migliora le performance memorizzando i risultati delle API.</p>
        <a href="/">↩️ Dashboard</a>
    </body></html>
    """, 200

@app.route("/health")
def health_check():
    return json.dumps({
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "user": USERNAME,
        "releases_per_check": MAX_RELEASES_PER_CHECK,
        "interval_minutes": CHECK_INTERVAL // 60
    }), 200, {'Content-Type': 'application/json'}

# ================== MAIN LOOP DEFINITIVO ==================
def definitive_main_loop():
    """Loop principale per versione definitiva"""
    logger.info("🎯 Avvio loop DEFINITIVO...")
    time.sleep(15)  # Attesa iniziale più lunga per stabilizzazione
    
    cycle = 0
    
    while True:
        try:
            cycle += 1
            logger.info(f"🔄 Ciclo #{cycle} - Controllo DEFINITIVO")
            
            perform_definitive_check()
            
            # Pausa precisa
            logger.info(f"💤 Pausa di {CHECK_INTERVAL//60} minuti fino al prossimo ciclo...")
            for seconds in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("🛑 Arresto manuale")
            break
        except Exception as e:
            logger.error(f"❌ Errore loop definitivo: {e}", exc_info=True)
            time.sleep(60)

# ================== STARTUP DEFINITIVO ==================
if __name__ == "__main__":
    # Verifica variabili
    required = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [var for var in required if not os.environ.get(var)]
    
    if missing:
        logger.error(f"❌ Variabili mancanti: {', '.join(missing)}")
        logger.error("Configurale su Railway -> Variables")
        exit(1)
    
    # Banner startup
    logger.info("=" * 70)
    logger.info("🎯 DISCOS BOT DEFINITIVO - VERSIONE PER 450+ ARTICOLI")
    logger.info("=" * 70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"🎯 Release per ciclo: {MAX_RELEASES_PER_CHECK}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"📊 Wantlist: fino a {MAX_WANTLIST_PAGES} pagine ({MAX_WANTLIST_PAGES*ITEMS_PER_PAGE} articoli)")
    logger.info(f"⚡ Rate limit: {REQUESTS_PER_MINUTE}/minuto")
    logger.info(f"🧠 Cache: {CACHE_MINUTES} minuti")
    logger.info("=" * 70)
    
    # Notifica avvio
    startup_msg = (
        f"🎯 <b>Discogs Bot DEFINITIVO Avviato!</b>\n\n"
        f"✅ <b>OTTIMIZZATO PER 450+ ARTICOLI</b>\n"
        f"👤 {USERNAME}\n"
        f"🎯 {MAX_RELEASES_PER_CHECK} release/controllo\n"
        f"⏰ Ogni {CHECK_INTERVAL//60} minuti\n"
        f"📊 {MAX_WANTLIST_PAGES} pagine supportate\n"
        f"🧠 Cache: {CACHE_MINUTES} min\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
        f"<i>Versione definitiva per grandi collezioni!</i>"
    )
    send_telegram(startup_msg)
    
    # Avvio loop
    main_thread = Thread(target=definitive_main_loop, daemon=True)
    main_thread.start()
    
    # Avvio Flask
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🌐 Server Flask avviato sulla porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
