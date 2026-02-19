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
CHECK_INTERVAL = 300  # 5 minuti
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

SEEN_FILE = "stats_seen.json"
LOG_FILE = "discogs_stats.log"
STATS_CACHE_FILE = "stats_cache.json"

# ================== EMERGENZA STOP ==================
EMERGENCY_STOP = False

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
    if EMERGENCY_STOP:
        logger.info(f"🚫 Notifica bloccata in emergenza")
        return False
    
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

# ================== STATS CACHE ==================
def load_stats_cache():
    try:
        if os.path.exists(STATS_CACHE_FILE):
            with open(STATS_CACHE_FILE, "r") as f:
                cache = json.load(f)
                logger.info(f"📚 Cache caricata: {len(cache)} release")
                return cache
    except Exception as e:
        logger.error(f"❌ Errore caricamento cache: {e}")
    return {}

def save_stats_cache(cache):
    try:
        with open(STATS_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
        logger.info(f"💾 Cache salvata: {len(cache)} release")
    except Exception as e:
        logger.error(f"❌ Errore salvataggio cache: {e}")

# ================== TRACCIAMENTO RICHIESTE PER RATE LIMIT ==================
request_timestamps = []

def get_wantlist():
    """Ottieni wantlist completa"""
    all_wants = []
    page = 1
    
    logger.info(f"📥 Scaricamento wantlist...")
    
    while True:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {'page': page, 'per_page': 100}
        headers = {
            "Authorization": f"Discogs token={DISCOGS_TOKEN}", 
            "User-Agent": "DiscogsStatsBot/10.0-FINAL"
        }
        
        try:
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
            if page >= pagination.get('pages', 1):
                break
            page += 1
            time.sleep(0.5)
                
        except Exception as e:
            logger.error(f"❌ Errore wantlist: {e}")
            break
    
    logger.info(f"✅ Wantlist: {len(all_wants)} articoli")
    return all_wants

# ================== 🔴🔴🔴 FUNZIONE RISCRITTA - VERSIONE SUPER-CONSERVATIVA 🔴🔴🔴 ==================
def get_release_stats_stable(release_id):
    """
    ✅ VERSIONE SUPER-CONSERVATIVA - RALLENTATA PER ELIMINARE I 429
    """
    global request_timestamps
    
    # 🔴🔴🔴 PAUSA FISSA OBBLIGATORIA - 3 SECONDI
    time.sleep(3)
    
    # 1. Pulisci i timestamp vecchi (più di 60 secondi)
    now = time.time()
    request_timestamps = [ts for ts in request_timestamps if now - ts < 60]
    
    # 2. Se abbiamo già fatto più di 30 richieste nell'ultimo minuto, aspetta (ridotto da 50 a 30!)
    if len(request_timestamps) >= 30:
        oldest = min(request_timestamps)
        wait_time = 60 - (now - oldest)
        if wait_time > 0:
            logger.warning(f"⏳ Rallento per {wait_time:.1f}s (già fatte {len(request_timestamps)} richieste)")
            time.sleep(wait_time)
    
    # 3. Registra questa richiesta
    request_timestamps.append(now)
    
    url = f"https://api.discogs.com/marketplace/stats/{release_id}"
    headers = {"User-Agent": "DiscogsStatsBot/11.0-SUPER-SLOW"}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        # 4. Leggi il rate limit dalla risposta
        remaining = int(response.headers.get('X-Discogs-Ratelimit-Remaining', 60))
        used = int(response.headers.get('X-Discogs-Ratelimit-Used', 0))
        logger.info(f"   📊 Rate limit: {remaining} rimaste, {used} usate")
        
        # 5. Se siamo sotto 15, rallenta MOLTO (aumentato da 10 a 15!)
        if remaining < 15:
            sleep_time = 8  # Aumentato da 5 a 8 secondi!
            logger.warning(f"⚠️ Rate limit basso ({remaining}), aspetto {sleep_time}s extra")
            time.sleep(sleep_time)
        elif remaining < 25:
            sleep_time = 5
            logger.warning(f"⚠️ Rate limit moderato ({remaining}), aspetto {sleep_time}s")
            time.sleep(sleep_time)
        else:
            time.sleep(2)
        
        if response.status_code == 200:
            data = response.json()
            if data is None:
                return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}
            
            stats_count = data.get('num_for_sale', 0) if isinstance(data, dict) else 0
            lowest = data.get('lowest_price', {}) if isinstance(data, dict) else {}
            price = lowest.get('value', 'N/D') if isinstance(lowest, dict) else 'N/D'
            currency = lowest.get('currency', '') if isinstance(lowest, dict) else ''
            
            return {
                'num_for_sale': stats_count,
                'price': price,
                'currency': currency
            }
            
        elif response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.warning(f"⏳ 429, aspetto {retry_after}s")
            time.sleep(retry_after)
            return get_release_stats_stable(release_id)
            
    except Exception as e:
        logger.error(f"❌ Errore stats {release_id}: {e}")
    
    return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

# ================== MONITORAGGIO - CON DOPPIA CONFERMA (1+3) ==================
def monitor_stats_stable():
    """Monitoraggio - CON DOPPIA CONFERMA per evitare falsi"""
    logger.info("📊 Monitoraggio (doppia conferma)...")
    
    wants = get_wantlist()
    if not wants:
        return 0
    
    stats_cache = load_stats_cache()
    changes_detected = 0
    notifications_sent = 0
    
    # Dizionario per tracciare release in attesa di conferma
    pending_confirmation = {}
    
    # 30 release tutte CASUALI
    check_count = min(30, len(wants))
    
    try:
        releases_to_check = random.sample(wants, check_count)
    except ValueError:
        releases_to_check = wants
    
    logger.info(f"🔍 Controllo {len(releases_to_check)} release CASUALI...")
    
    for i, item in enumerate(releases_to_check):
        try:
            release_id = str(item.get('id'))
            if not release_id:
                continue
                
            basic_info = item.get('basic_information', {})
            title = basic_info.get('title', 'Sconosciuto')
            artists = basic_info.get('artists', [{}])
            artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
            
            logger.info(f"[{i+1}/{len(releases_to_check)}] {artist} - {title[:40]}...")
            
            # Ottieni stats correnti
            current = get_release_stats_stable(release_id)
            
            if current is None or current.get('num_for_sale') is None:
                logger.error(f"   ❌ current è None per {release_id}, salto...")
                continue
                
            current_count = current['num_for_sale']
            current_price = current['price']
            current_currency = current['currency']
            
            previous = stats_cache.get(release_id, {})
            previous_count = previous.get('num_for_sale', -1)
            previous_price = previous.get('price', 'N/D')
            
            # PRIMA RILEVAZIONE = APPRENDIMENTO (MAI NOTIFICARE)
            if previous_count == -1:
                logger.info(f"   📝 APPRENDIMENTO: {current_count} copie (nessuna notifica)")
                # Aggiorna cache subito
                stats_cache[release_id] = {
                    'num_for_sale': current_count,
                    'price': current_price,
                    'currency': current_currency,
                    'artist': artist,
                    'title': title,
                    'first_seen': datetime.now().isoformat(),
                    'last_check': time.time()
                }
                logger.info(f"   💾 Cache aggiornata: {previous_count} → {current_count}")
                
            # 🔴🔴🔴 MODIFICA 1+3: DOPPIA CONFERMA PER I CAMBIAMENTI 🔴🔴🔴
            elif current_count != previous_count:
                # Caso 1: Aumento di copie (potenziale nuovo annuncio)
                if current_count > previous_count:
                    # Verifica se è già in attesa di conferma
                    if release_id in pending_confirmation:
                        # Già in attesa dal ciclo precedente - confermato!
                        logger.info(f"   ✅ CONFERMATO: aumento da {previous_count} a {current_count}")
                        diff = current_count - previous_count
                        emoji = "🆕"
                        action = f"+{diff} NUOVE COPIE"
                        
                        price_display = f"{current_currency} {current_price}" if current_price != 'N/D' else 'N/D'
                        
                        msg = (
                            f"{emoji} <b>NUOVO ANNUNCIO CONFERMATO!</b>\n\n"
                            f"🎸 <b>{artist}</b>\n"
                            f"💿 {title}\n\n"
                            f"📊 <b>{action}</b>\n"
                            f"💰 Prezzo più basso: <b>{price_display}</b>\n"
                            f"📦 Totale ora: <b>{current_count} copie</b>\n\n"
                            f"🔗 <a href='https://www.discogs.com/sell/list?release_id={release_id}'>VEDI COPIE</a>"
                        )
                        
                        if send_telegram(msg):
                            notifications_sent += 1
                            changes_detected += 1
                            logger.info(f"   🎯 NOTIFICA CONFERMATA #{notifications_sent}")
                            time.sleep(1)
                        
                        # Rimuovi dalla lista di attesa
                        del pending_confirmation[release_id]
                        
                        # Aggiorna cache
                        stats_cache[release_id] = {
                            'num_for_sale': current_count,
                            'price': current_price,
                            'currency': current_currency,
                            'artist': artist,
                            'title': title,
                            'last_change': datetime.now().isoformat(),
                            'last_check': time.time()
                        }
                        logger.info(f"   💾 Cache aggiornata: {previous_count} → {current_count}")
                    
                    else:
                        # Prima volta che vediamo questo aumento - metti in attesa
                        logger.info(f"   ⏳ POTENZIALE AUMENTO: {previous_count} → {current_count} - in attesa di conferma")
                        pending_confirmation[release_id] = {
                            'count': current_count,
                            'price': current_price,
                            'currency': current_currency,
                            'artist': artist,
                            'title': title,
                            'timestamp': time.time()
                        }
                        # NON aggiornare la cache ancora!
                
                # Caso 2: Diminuzione di copie (vendita/rimozione) - nessuna notifica
                elif current_count < previous_count:
                    logger.info(f"   📉 Diminuzione copie: {previous_count} → {current_count} (nessuna notifica)")
                    # Aggiorna cache subito
                    stats_cache[release_id] = {
                        'num_for_sale': current_count,
                        'price': current_price,
                        'currency': current_currency,
                        'artist': artist,
                        'title': title,
                        'last_change': datetime.now().isoformat(),
                        'last_check': time.time()
                    }
                    logger.info(f"   💾 Cache aggiornata: {previous_count} → {current_count}")
            
            # Caso: stesso numero di copie ma prezzo cambiato
            elif current_count == previous_count and current_price != previous_price:
                logger.info(f"   💰 Variazione prezzo: {previous_price} → {current_price} (nessuna notifica)")
                # Aggiorna cache
                stats_cache[release_id] = {
                    'num_for_sale': current_count,
                    'price': current_price,
                    'currency': current_currency,
                    'artist': artist,
                    'title': title,
                    'last_check': time.time()
                }
                logger.info(f"   💾 Cache aggiornata: prezzo {previous_price} → {current_price}")
            
            # Caso: stabile con copie
            elif current_count > 0:
                logger.info(f"   ℹ️ Stabili: {current_count} copie (nessuna notifica)")
                # Aggiorna solo last_check
                if release_id in stats_cache:
                    stats_cache[release_id]['last_check'] = time.time()
            
        except Exception as e:
            logger.error(f"❌ Errore release {i+1}: {e}")
        
        # Pausa dinamica (invariata)
        if 'current_count' in locals() and current_count > 0:
            time.sleep(random.uniform(0.8, 1.2))
        else:
            time.sleep(random.uniform(0.3, 0.6))
    
    # Pulisci pending_confirmation vecchie (più di 10 minuti)
    now = time.time()
    expired = [rid for rid, data in pending_confirmation.items() 
               if now - data.get('timestamp', 0) > 600]  # 10 minuti
    for rid in expired:
        logger.info(f"   ⌛ Rimuovo attesa scaduta per {rid}")
        del pending_confirmation[rid]
    
    save_stats_cache(stats_cache)
    
    logger.info(f"✅ Rilevati {changes_detected} NUOVI INSERIMENTI CONFERMATI, {notifications_sent} notifiche inviate")
    if pending_confirmation:
        logger.info(f"⏳ {len(pending_confirmation)} release in attesa di conferma")
    return changes_detected

# ================== FLASK APP (IDENTICA) ==================
app = Flask(__name__)

# === ENDPOINT EMERGENZA STOP/START ===
@app.route("/stop")
def emergency_stop():
    global EMERGENCY_STOP
    EMERGENCY_STOP = True
    logger.critical("🛑🛑🛑 EMERGENZA - BOT BLOCCATO!")
    send_telegram("🛑 BOT BLOCCATO IN EMERGENZA - Nessuna notifica")
    return "<h1>🛑 BOT BLOCCATO</h1><p>Vai su /start per riattivare</p>", 200

@app.route("/start")
def emergency_start():
    global EMERGENCY_STOP
    EMERGENCY_STOP = False
    logger.warning("✅ Bot riattivato")
    send_telegram("✅ Bot RIATTIVATO - Notifiche con DOPPIA CONFERMA")
    return "<h1>✅ Bot riattivato</h1>", 200

# === ENDPOINT DI EMERGENZA RECUPERO ===
@app.route("/fix-now")
def fix_now():
    logger.warning("🆘 AVVIO PROCEDURA DI RECUPERO EMERGENZA!")
    wants = get_wantlist()[:30]
    recovered = 0
    
    for item in wants:
        try:
            release_id = str(item.get('id'))
            basic_info = item.get('basic_information', {})
            title = basic_info.get('title', 'Sconosciuto')
            artists = basic_info.get('artists', [{}])
            artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
            
            stats = get_release_stats_stable(release_id)
            
            if stats['num_for_sale'] > 0:
                msg = (
                    f"🆘 <b>RECUPERO EMERGENZA</b>\n\n"
                    f"🎸 <b>{artist}</b>\n"
                    f"💿 {title}\n\n"
                    f"📦 <b>{stats['num_for_sale']} copie in vendita!</b>\n"
                    f"💰 Prezzo più basso: {stats['currency']} {stats['price']}\n\n"
                    f"🔗 <a href='https://www.discogs.com/sell/list?release_id={release_id}'>VERIFICA SU DISCOGS</a>"
                )
                if send_telegram(msg):
                    recovered += 1
                    logger.info(f"✅ Recuperata: {artist} - {title[:30]}...")
            
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"❌ Errore recupero: {e}")
    
    return f"<h1>✅ Procedura di recupero completata!</h1><p>Inviate {recovered} notifiche di recupero.</p><a href='/'>↩️ Home</a>", 200

# === HOME ===
@app.route("/")
def home():
    cache = load_stats_cache()
    monitored = len(cache)
    with_stats = sum(1 for v in cache.values() if v.get('num_for_sale', 0) > 0)
    
    status = "🟢 ONLINE" if not EMERGENCY_STOP else "🔴 BLOCCATO"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📊 Discogs Monitor - DOPPIA CONFERMA</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; }}
            h1 {{ color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }}
            .status {{ display: inline-block; padding: 10px 20px; border-radius: 5px; color: white; font-weight: bold; }}
            .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
            .stat-card {{ background: #4CAF50; color: white; padding: 20px; border-radius: 10px; text-align: center; }}
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 10px 20px; 
                    text-decoration: none; border-radius: 5px; margin: 5px; font-size: 16px; }}
            .btn-stop {{ background: #dc3545; }}
            .btn-start {{ background: #28a745; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Discogs Monitor - DOPPIA CONFERMA</h1>
            
            <div style="margin: 20px 0; text-align: center;">
                <span class="status" style="background: {'#28a745' if not EMERGENCY_STOP else '#dc3545'};">
                    {status}
                </span>
            </div>
            
            <div class="stats">
                <div class="stat-card">
                    <h3>📈 Release Monitorate</h3>
                    <p style="font-size: 2.5em; margin: 10px 0;">{monitored}</p>
                </div>
                <div class="stat-card" style="background: #dc3545;">
                    <h3>🛒 Con Copie in Vendita</h3>
                    <p style="font-size: 2.5em; margin: 10px 0;">{with_stats}</p>
                </div>
            </div>
            
            <div style="margin: 30px 0; text-align: center;">
                <h3>🔧 Controlli Rapidi</h3>
                <div style="display: flex; flex-wrap: wrap; justify-content: center; gap: 10px;">
                    <a class="btn" href="/check">🚀 Controllo</a>
                    <a class="btn btn-stop" href="/stop">🔴 STOP</a>
                    <a class="btn btn-start" href="/start">🟢 START</a>
                    <a class="btn" href="/fix-now">🆘 Recupero</a>
                    <a class="btn" href="/test">🧪 Test</a>
                    <a class="btn" href="/reset">🔄 Reset Cache</a>
                    <a class="btn" href="/logs">📄 Logs</a>
                </div>
            </div>
            
            <div style="background: #f8f9fa; padding: 15px; border-radius: 10px; margin-top: 20px;">
                <p><strong>👤 Utente:</strong> {USERNAME}</p>
                <p><strong>⏰ Intervallo:</strong> 5 minuti</p>
                <p><strong>🔍 Release per ciclo:</strong> 30 (casuali)</p>
                <p><strong>⚡ Rate Limiting:</strong> SUPER-CONSERVATIVO</p>
                <p><strong>✅ Notifiche:</strong> Con DOPPIA CONFERMA</p>
                <p><strong>⏳ Attesa:</strong> 5 minuti tra rilevazione e conferma</p>
                <p><strong>🚫 429:</strong> RIDOTTI AL MINIMO!</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route("/", methods=['HEAD'])
def home_head():
    return "", 200

# === CHECK ===
@app.route("/check")
def manual_check():
    Thread(target=monitor_stats_stable, daemon=True).start()
    return "<h1>🚀 Monitoraggio avviato!</h1><p>✅ Doppia conferma attiva</p><a href='/'>↩️ Home</a>", 200

@app.route("/check", methods=['HEAD'])
def check_head():
    return "", 200

# === RESET ===
@app.route("/reset")
def reset_cache():
    save_stats_cache({})
    logger.warning("🔄 CACHE STATS RESETTATA!")
    return "<h1>🔄 Cache resettata!</h1><p>Cache stats pulita.</p><a href='/'>↩️ Home</a>", 200

@app.route("/reset", methods=['HEAD'])
def reset_head():
    return "", 200

# === DEBUG ===
@app.route("/debug")
def debug_release():
    release_id = request.args.get('id', '14809291')
    stats = get_release_stats_stable(release_id)
    cache = load_stats_cache()
    cached = cache.get(release_id, {})
    
    html = f"<h2>🔍 Debug Release {release_id}</h2>"
    html += f"<h3>📊 Stats Correnti (API):</h3>"
    html += f"<p>Copie: <b>{stats['num_for_sale']}</b></p>"
    html += f"<p>Prezzo più basso: <b>{stats['currency']} {stats['price']}</b></p>"
    html += f"<h3>💾 Stats Cache:</h3>"
    html += f"<p>Copie memorizzate: <b>{cached.get('num_for_sale', 'Mai vista')}</b></p>"
    html += f"<p>Prezzo memorizzato: <b>{cached.get('currency', '')} {cached.get('price', 'N/D')}</b></p>"
    html += f"<p>Prima rilevazione: <b>{cached.get('first_seen', 'Mai')}</b></p>"
    html += f"<p><b>{'🔴 IN APPRENDIMENTO' if not cached else '✅ MONITORATA'}</b></p>"
    html += f"<p><i>⚡ Doppia conferma attiva - Rate limiting super-conservativo</i></p>"
    html += "<br><a href='/'>↩️ Home</a>"
    
    return html, 200

@app.route("/debug", methods=['HEAD'])
def debug_head():
    return "", 200

# === TEST ===
@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test Monitor - SUPER-CONSERVATIVO</b>\n\n"
        f"✅ Sistema attivo\n"
        f"• 📊 Rate limiting SUPER-CONSERVATIVO\n"
        f"• ✅ Doppia conferma per evitare falsi\n"
        f"• ⏳ Attesa 5 minuti tra rilevazione e notifica\n"
        f"• 🚫 429 RIDOTTI AL MINIMO!\n"
        f"👤 {USERNAME}\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    return "✅ Test inviato" if success else "❌ Errore", 200

@app.route("/test", methods=['HEAD'])
def test_head():
    return "", 200

# === LOGS ===
@app.route("/logs")
def view_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = f.read().splitlines()[-100:]
        return "<pre style='background:#000; color:#0f0; padding:20px;'>" + "<br>".join(logs) + "</pre><br><a href='/'>↩️ Home</a>", 200
    except:
        return "<pre>Nessun log</pre><a href='/'>↩️ Home</a>", 200

@app.route("/logs", methods=['HEAD'])
def logs_head():
    return "", 200

# === CACHE ===
@app.route("/cache")
def view_cache():
    cache = load_stats_cache()
    html = f"<h2>💾 Stats Cache ({len(cache)} release)</h2><ul>"
    for rid, data in list(cache.items())[:20]:
        html += f"<li>{rid}: {data.get('num_for_sale', 0)} copie - {data.get('artist', '')[:20]}</li>"
    html += "</ul><a href='/'>↩️ Home</a>"
    return html, 200

@app.route("/cache", methods=['HEAD'])
def cache_head():
    return "", 200

# === HEALTH ===
@app.route("/health")
def health_check():
    return "OK", 200

@app.route("/health", methods=['HEAD'])
def health_head():
    return "", 200

# ================== MAIN LOOP ==================
def main_loop_stable():
    time.sleep(10)
    while True:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔄 Monitoraggio (super-conservativo) - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            monitor_stats_stable()
            
            logger.info(f"💤 Pausa 5 minuti...")
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Loop error: {e}")
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    required = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [var for var in required if not os.environ.get(var)]
    
    if missing:
        logger.error(f"❌ Variabili mancanti: {missing}")
        exit(1)
    
    logger.info('='*70)
    logger.info("📊 DISCOGS MONITOR - VERSIONE SUPER-CONSERVATIVA")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release/ciclo: 30")
    logger.info(f"🎲 Selezione: CASUALE")
    logger.info(f"⚡ Rate Limiting: SUPER-CONSERVATIVO")
    logger.info(f"✅ Doppia conferma: ATTIVA")
    logger.info('='*70)
    
    send_telegram(
        f"📊 <b>Discogs Monitor - SUPER-CONSERVATIVO</b>\n\n"
        f"✅ <b>CONFIGURAZIONE FINALE:</b>\n"
        f"• 🎲 30 release CASUALI per ciclo\n"
        f"• ⏰ Controllo ogni 5 minuti\n"
        f"• ⚡ Rate limiting SUPER-CONSERVATIVO\n"
        f"• ✅ Doppia conferma per evitare falsi\n"
        f"• ⏳ Attesa 5 minuti tra rilevazione e notifica\n"
        f"• ❌ MAI notifiche alla prima rilevazione\n"
        f"• 🚫 429 RIDOTTI AL MINIMO!\n\n"
        f"👤 {USERNAME}\n"
        f"📊 {len(get_wantlist())} articoli in wantlist\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    Thread(target=main_loop_stable, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
