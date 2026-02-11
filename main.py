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
CHECK_INTERVAL = 180  # 3 minuti
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

SEEN_FILE = "stats_seen.json"
LOG_FILE = "discogs_stats.log"
STATS_CACHE_FILE = "stats_cache.json"

# ================== EMERGENZA STOP ==================
EMERGENCY_STOP = False  # Di default False

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

# ================== TELEGRAM CON BLOCCATORE ==================
def send_telegram(msg):
    # 🚫 BLOCCATORE DI EMERGENZA
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
    """Carica la cache con valori PRECEDENTI"""
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
    """Salva la cache con valori CORRENTI"""
    try:
        with open(STATS_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
        logger.info(f"💾 Cache salvata: {len(cache)} release")
    except Exception as e:
        logger.error(f"❌ Errore salvataggio cache: {e}")

# ================== DISCOGS API CON FIX ==================
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
            "User-Agent": "DiscogsStatsBot/5.0-FIX"
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

def get_release_stats_fixed(release_id):
    """
    VERSIONE FIX - NON SI FIDA DI STATS=0
    Verifica sempre se la pagina delle listings esiste
    """
    url = f"https://api.discogs.com/marketplace/stats/{release_id}"
    headers = {"User-Agent": "DiscogsStatsBot/5.0-FIX"}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        # Rate limiting
        remaining = int(response.headers.get('X-Discogs-Ratelimit-Remaining', 60))
        if remaining < 10:
            time.sleep(2)
        elif remaining < 20:
            time.sleep(1)
        else:
            time.sleep(0.5)
        
        if response.status_code == 200:
            data = response.json()
            if data is None:
                return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}
            
            stats_count = data.get('num_for_sale', 0) if isinstance(data, dict) else 0
            lowest = data.get('lowest_price', {}) if isinstance(data, dict) else {}
            price = lowest.get('value', 'N/D') if isinstance(lowest, dict) else 'N/D'
            currency = lowest.get('currency', '') if isinstance(lowest, dict) else ''
            
            # 🔴 FIX CRITICO: Se stats dice 0, VERIFICHIAMO CON HEAD REQUEST
            if stats_count == 0:
                check_url = f"https://www.discogs.com/sell/list?release_id={release_id}"
                try:
                    head_response = requests.head(check_url, timeout=5, allow_redirects=True)
                    if head_response.status_code == 200:
                        # La pagina esiste! Registriamo come 1 per la cache
                        logger.warning(f"   ⚠️ Stats=0 ma pagina esiste! Forzo a 1 per cache")
                        stats_count = 1
                        price = "Verifica manuale"
                        currency = ""
                except Exception as e:
                    logger.debug(f"   ℹ️ Head request fallita: {e}")
            
            return {
                'num_for_sale': stats_count,
                'price': price,
                'currency': currency
            }
            
        elif response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 30))
            logger.warning(f"⏳ 429, aspetto {retry_after}s")
            time.sleep(retry_after)
            return get_release_stats_fixed(release_id)
            
    except Exception as e:
        logger.error(f"❌ Errore stats {release_id}: {e}")
    
    return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

# ================== MONITORAGGIO CON FIX ANTI-SPAM ==================
def monitor_stats_fixed():
    """Monitoraggio con FIX - NOTIFICHE SOLO PER CAMBIAMENTI REALI"""
    logger.info("📊 Monitoraggio con FIX ANTI-SPAM...")
    
    wants = get_wantlist()
    if not wants:
        return 0
    
    # CARICA LA CACHE PRECEDENTE
    stats_cache = load_stats_cache()
    changes_detected = 0
    notifications_sent = 0
    
    # Controlla 50 release
    check_count = min(50, len(wants))
    recent = wants[:20]
    
    if len(wants) > 20:
        try:
            random_sample = random.sample(wants[20:], min(30, len(wants[20:])))
            releases_to_check = recent + random_sample
        except ValueError:
            releases_to_check = recent
    else:
        releases_to_check = recent
    
    random.shuffle(releases_to_check)
    
    logger.info(f"🔍 Controllo {len(releases_to_check)} release...")
    
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
            
            # Ottieni stats CORRENTI con la VERSIONE FIX
            current = get_release_stats_fixed(release_id)
            current_count = current['num_for_sale']
            
            # Recupera stats PRECEDENTI dalla cache
            previous = stats_cache.get(release_id, {})
            previous_count = previous.get('num_for_sale', -1)
            
            # === FIX ANTI-SPAM: PRIMA RILEVAZIONE = APPRENDIMENTO, MAI NOTIFICARE ===
            if previous_count == -1:
                logger.info(f"   📝 APPRENDIMENTO: {current_count} copie (nessuna notifica)")
                # 🔴 NON NOTIFICARE MAI ALLA PRIMA RILEVAZIONE!
                
            # === SOLO CAMBIAMENTI REALI GENERANO NOTIFICHE ===
            elif current_count != previous_count:
                diff = current_count - previous_count
                
                if diff > 0:
                    emoji = "🆕"
                    action = f"+{diff} NUOVE COPIE"
                else:
                    emoji = "📉"
                    action = f"{diff} copie"
                
                price_display = f"{current['currency']} {current['price']}" if current['price'] != 'N/D' else 'N/D'
                
                msg = (
                    f"{emoji} <b>CAMBIAMENTO MARKETPLACE</b>\n\n"
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
                    logger.info(f"   🎯 CAMBIAMENTO REALE: {action} (ora: {current_count}) - NOTIFICA #{notifications_sent}")
                    time.sleep(1)
            
            elif current_count > 0 and current_count == previous_count:
                logger.info(f"   ℹ️ Stabili: {current_count} copie (nessuna notifica)")
            
            # === AGGIORNA CACHE SOLO SE CAMBIA ===
            if previous_count != current_count:
                stats_cache[release_id] = {
                    'num_for_sale': current_count,
                    'price': current['price'],
                    'currency': current['currency'],
                    'artist': artist,
                    'title': title,
                    'last_change': datetime.now().isoformat() if previous_count != -1 else None,
                    'first_seen': datetime.now().isoformat(),
                    'last_check': time.time()
                }
                logger.info(f"   💾 Cache aggiornata: {previous_count} → {current_count}")
            
        except Exception as e:
            logger.error(f"❌ Errore release {i+1}: {e}")
        
        time.sleep(random.uniform(0.8, 1.2))
    
    # === SALVA CACHE SOLO ALLA FINE ===
    save_stats_cache(stats_cache)
    
    logger.info(f"✅ Rilevati {changes_detected} cambiamenti REALI, {notifications_sent} notifiche inviate")
    return changes_detected

# ================== FLASK APP ==================
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
    send_telegram("✅ Bot RIATTIVATO - Notifiche solo per cambiamenti REALI")
    return "<h1>✅ Bot riattivato</h1>", 200

# === ENDPOINT DI EMERGENZA RECUPERO ===
@app.route("/fix-now")
def fix_now():
    """FORZA IL CONTROLLO E RECUPERA ARTICOLI NON RILEVATI"""
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
            
            check_url = f"https://www.discogs.com/sell/list?release_id={release_id}"
            head_response = requests.head(check_url, timeout=5, allow_redirects=True)
            
            if head_response.status_code == 200:
                msg = (
                    f"🆘 <b>RECUPERO EMERGENZA</b>\n\n"
                    f"🎸 <b>{artist}</b>\n"
                    f"💿 {title}\n\n"
                    f"⚠️ Questa release HA UNA PAGINA DI VENDITA\n"
                    f"🔗 <a href='{check_url}'>VERIFICA MANUALMENTE</a>"
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
    status_color = "green" if not EMERGENCY_STOP else "red"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📊 Discogs Monitor - ANTI-SPAM</title>
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; }}
            .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
            .card {{ background: #4CAF50; color: white; padding: 20px; border-radius: 10px; }}
            .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 20px; margin: 20px 0; }}
            .success {{ background: #d4edda; border-left: 4px solid #28a745; padding: 20px; margin: 20px 0; }}
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 12px 24px; 
                    text-decoration: none; border-radius: 5px; margin: 5px; }}
            .btn-stop {{ background: #dc3545; }}
            .btn-start {{ background: #28a745; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Discogs Monitor - ANTI-SPAM</h1>
            
            <div style="margin-bottom: 20px; padding: 15px; background: {status_color}; color: white; border-radius: 10px; text-align: center;">
                <h2 style="margin:0;">{status}</h2>
            </div>
            
            <div class="stats">
                <div class="card">
                    <h3>📈 Release Monitorate</h3>
                    <p style="font-size: 2em;">{monitored}</p>
                </div>
                <div class="card" style="background: #f44336;">
                    <h3>🛒 Con Copie</h3>
                    <p style="font-size: 2em;">{with_stats}</p>
                </div>
            </div>
            
            <div class="success">
                <h3>✅ ANTI-SPAM ATTIVO:</h3>
                <ul>
                    <li>❌ MAI notifiche alla prima rilevazione</li>
                    <li>✅ Notifiche SOLO per CAMBIAMENTI REALI</li>
                    <li>🔴 Endpoint /stop per bloccare emergenza</li>
                    <li>🟢 Endpoint /start per riattivare</li>
                </ul>
            </div>
            
            <h3>🔧 Controlli</h3>
            <a class="btn" href="/check">🚀 Controllo</a>
            <a class="btn btn-stop" href="/stop">🔴 STOP EMERGENZA</a>
            <a class="btn btn-start" href="/start">🟢 START</a>
            <a class="btn" href="/fix-now">🆘 Recupero</a>
            <a class="btn" href="/test">🧪 Test</a>
            <a class="btn" href="/reset">🔄 Reset Cache</a>
            <a class="btn" href="/logs">📄 Logs</a>
            
            <h3>📊 Info</h3>
            <p><strong>Utente:</strong> {USERNAME}</p>
            <p><strong>Stato:</strong> {status}</p>
            <p><strong>Regola:</strong> 📢 Notifiche SOLO per cambiamenti REALI</p>
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
    Thread(target=monitor_stats_fixed, daemon=True).start()
    return "<h1>🚀 Monitoraggio avviato!</h1><p>✅ Notifiche SOLO per cambiamenti REALI.</p><a href='/'>↩️ Home</a>", 200

@app.route("/check", methods=['HEAD'])
def check_head():
    return "", 200

# === RESET ===
@app.route("/reset")
def reset_cache():
    save_stats_cache({})
    logger.warning("🔄 CACHE RESETTATA!")
    return "<h1>🔄 Cache resettata!</h1><p>Ora TUTTE le release sono in fase di APPRENDIMENTO - Nessuna notifica alla prima rilevazione.</p><a href='/'>↩️ Home</a>", 200

@app.route("/reset", methods=['HEAD'])
def reset_head():
    return "", 200

# === DEBUG ===
@app.route("/debug")
def debug_release():
    release_id = request.args.get('id', '14809291')
    stats = get_release_stats_fixed(release_id)
    cache = load_stats_cache()
    cached = cache.get(release_id, {})
    
    check_url = f"https://www.discogs.com/sell/list?release_id={release_id}"
    page_exists = False
    try:
        head = requests.head(check_url, timeout=5, allow_redirects=True)
        page_exists = head.status_code == 200
    except:
        pass
    
    html = f"<h2>🔍 Debug Release {release_id}</h2>"
    html += f"<h3>📊 Stats Correnti:</h3>"
    html += f"<p>Copie: <b>{stats['num_for_sale']}</b></p>"
    html += f"<p>Prezzo: <b>{stats['currency']} {stats['price']}</b></p>"
    html += f"<p>Pagina esiste: <b>{'✅ SÌ' if page_exists else '❌ NO'}</b></p>"
    html += f"<h3>💾 Cache:</h3>"
    html += f"<p>Copie memorizzate: <b>{cached.get('num_for_sale', 'Mai vista')}</b></p>"
    html += f"<p>Prima rilevazione: <b>{cached.get('first_seen', 'Mai')}</b></p>"
    html += f"<p><b>{'🔴 IN APPRENDIMENTO' if not cached else '✅ MONITORATA'}</b></p>"
    html += "<br><a href='/'>↩️ Home</a>"
    
    return html, 200

@app.route("/debug", methods=['HEAD'])
def debug_head():
    return "", 200

# === TEST ===
@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test Monitor - ANTI-SPAM</b>\n\n"
        f"✅ Sistema online\n"
        f"• ❌ MAI notifiche alla prima rilevazione\n"
        f"• ✅ Solo CAMBIAMENTI REALI\n"
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
    html = f"<h2>💾 Cache ({len(cache)} release)</h2><ul>"
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
def main_loop_fixed():
    time.sleep(10)
    while True:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔄 Monitoraggio ANTI-SPAM - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            monitor_stats_fixed()
            
            logger.info(f"💤 Pausa 3 minuti...")
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
    logger.info("📊 DISCOGS MONITOR - VERSIONE ANTI-SPAM DEFINITIVA")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release/ciclo: 50")
    logger.info(f"✅ REGOLA: MAI notifiche prima rilevazione")
    logger.info(f"✅ REGOLA: Solo CAMBIAMENTI REALI generano notifiche")
    logger.info('='*70)
    
    send_telegram(
        f"📊 <b>Discogs Monitor - ANTI-SPAM DEFINITIVO</b>\n\n"
        f"✅ <b>REGOLE FINALI:</b>\n"
        f"• ❌ MAI notifiche alla prima rilevazione\n"
        f"• ✅ Solo CAMBIAMENTI REALI generano notifiche\n"
        f"• 🔴 /stop per bloccare emergenza\n"
        f"• 🟢 /start per riattivare\n\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controllo ogni 3 minuti\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    Thread(target=main_loop_fixed, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
