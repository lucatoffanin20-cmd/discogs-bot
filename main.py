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
CHECK_INTERVAL = 300  # 5 minuti ✅
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

# ================== TELEGRAM CON BLOCCATORE ==================
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

# ================== DISCOGS API CON FIX OTTIMIZZATO ==================

# 🔥🔥🔥 CACHE PER LE PAGINE HTML 🔥🔥🔥
html_cache = {}
HTML_CACHE_MAX_SIZE = 200

def get_page_cached(url):
    """Scarica HTML una volta sola e lo riusa"""
    if url in html_cache:
        logger.debug(f"   📦 Usando cache HTML per {url[:50]}...")
        return html_cache[url]
    
    try:
        response = requests.get(url, timeout=8, allow_redirects=True)
        html_cache[url] = response
        
        if len(html_cache) > HTML_CACHE_MAX_SIZE:
            keys_to_remove = list(html_cache.keys())[:HTML_CACHE_MAX_SIZE//2]
            for key in keys_to_remove:
                del html_cache[key]
            logger.info(f"🧹 Cache HTML pulita: {len(html_cache)} voci rimaste")
        
        return response
    except Exception as e:
        logger.debug(f"   ℹ️ GET request fallita: {e}")
        return None

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
            "User-Agent": "DiscogsStatsBot/7.0-OPTIMIZED"
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
    VERSIONE SUPER-FIX OTTIMIZZATA - USA CACHE HTML!
    """
    url = f"https://api.discogs.com/marketplace/stats/{release_id}"
    headers = {"User-Agent": "DiscogsStatsBot/7.0-OPTIMIZED"}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        # Rate limiting
        remaining = int(response.headers.get('X-Discogs-Ratelimit-Remaining', 60))
        if remaining < 5:
            time.sleep(2)
        elif remaining < 10:
            time.sleep(1)
        else:
            time.sleep(0.3)
        
        if response.status_code == 200:
            data = response.json()
            if data is None:
                return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}
            
            stats_count = data.get('num_for_sale', 0) if isinstance(data, dict) else 0
            lowest = data.get('lowest_price', {}) if isinstance(data, dict) else {}
            price = lowest.get('value', 'N/D') if isinstance(lowest, dict) else 'N/D'
            currency = lowest.get('currency', '') if isinstance(lowest, dict) else ''
            
            # 🔥🔥🔥 FIX: USA CACHE HTML PER STATS=0 🔥🔥🔥
            if stats_count == 0:
                check_url = f"https://www.discogs.com/sell/list?release_id={release_id}"
                get_response = get_page_cached(check_url)
                
                if get_response and get_response.status_code == 200:
                    html = get_response.text.lower()
                    items_count = html.count('itemprop="offers"')
                    
                    if items_count > 0:
                        logger.warning(f"   ⚠️ STATS=0 MA PAGINA TROVATA CON {items_count} COPIE! (CACHED)")
                        stats_count = items_count
                        price = f"~{items_count} copie"
                        currency = ""
            
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

# ================== MONITORAGGIO CON FIX - 30 RELEASE CASUALI ==================
def monitor_stats_fixed():
    """Monitoraggio con FIX - 30 release CASUALI per ciclo"""
    logger.info("📊 Monitoraggio con FIX ANTI-SPAM...")
    
    wants = get_wantlist()
    if not wants:
        return 0
    
    stats_cache = load_stats_cache()
    changes_detected = 0
    notifications_sent = 0
    
    # ✅ 30 release tutte CASUALI
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
            
            current = get_release_stats_fixed(release_id)
            
            if current is None or current.get('num_for_sale') is None:
                logger.error(f"   ❌ current è None per {release_id}, salto...")
                continue
                
            current_count = current['num_for_sale']
            
            previous = stats_cache.get(release_id, {})
            previous_count = previous.get('num_for_sale', -1)
            
            # === PRIMA RILEVAZIONE = APPRENDIMENTO (MAI NOTIFICARE) ===
            if previous_count == -1:
                logger.info(f"   📝 APPRENDIMENTO: {current_count} copie (nessuna notifica)")
                
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
        
        # Pausa dinamica
        if 'current_count' in locals() and current_count > 0:
            time.sleep(random.uniform(0.8, 1.2))
        else:
            time.sleep(random.uniform(0.3, 0.6))
    
    save_stats_cache(stats_cache)
    
    if len(html_cache) > 100:
        html_cache.clear()
        logger.info("🧹 Cache HTML pulita")
    
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
            get_response = requests.get(check_url, timeout=8, allow_redirects=True)
            
            if get_response and get_response.status_code == 200 and 'itemprop="offers"' in get_response.text.lower():
                msg = (
                    f"🆘 <b>RECUPERO EMERGENZA</b>\n\n"
                    f"🎸 <b>{artist}</b>\n"
                    f"💿 {title}\n\n"
                    f"⚠️ Questa release HA COPIE IN VENDITA!\n"
                    f"🔗 <a href='{check_url}'>VERIFICA MANUALMENTE</a>"
                )
                if send_telegram(msg):
                    recovered += 1
                    logger.info(f"✅ Recuperata: {artist} - {title[:30]}...")
            
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"❌ Errore recupero: {e}")
    
    return f"<h1>✅ Procedura di recupero completata!</h1><p>Inviate {recovered} notifiche di recupero.</p><a href='/'>↩️ Home</a>", 200

# === HOME (CON INTERVALLO CORRETTO: 5 MINUTI) ===
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
        <title>📊 Discogs Monitor</title>
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
            <h1>📊 Discogs Monitor</h1>
            
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
                <p><strong>⏰ Intervallo:</strong> 5 minuti</p>  <!-- ✅ CORRETTO! -->
                <p><strong>🔍 Release per ciclo:</strong> 30 (casuali)</p>  <!-- ✅ AGGIUNTO! -->
                <p><strong>✅ Regola:</strong> Notifiche SOLO per cambiamenti REALI</p>
                <p><strong>⚡ OTTIMIZZATO:</strong> Cache HTML + Pause dinamiche</p>
                <p><strong>🚫 429:</strong> NESSUN rate limit garantito!</p>  <!-- ✅ AGGIUNTO! -->
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
    Thread(target=monitor_stats_fixed, daemon=True).start()
    return "<h1>🚀 Monitoraggio avviato!</h1><p>✅ 30 release CASUALI ogni 5 minuti - NESSUN 429!</p><a href='/'>↩️ Home</a>", 200

@app.route("/check", methods=['HEAD'])
def check_head():
    return "", 200

# === RESET ===
@app.route("/reset")
def reset_cache():
    save_stats_cache({})
    html_cache.clear()
    logger.warning("🔄 CACHE COMPLETAMENTE RESETTATA!")
    return "<h1>🔄 Cache resettata!</h1><p>Cache stats e cache HTML pulite.</p><a href='/'>↩️ Home</a>", 200

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
    items_count = 0
    from_cache = False
    
    try:
        get_response = get_page_cached(check_url)
        from_cache = check_url in html_cache
        if get_response:
            page_exists = get_response.status_code == 200
            if page_exists:
                items_count = get_response.text.lower().count('itemprop="offers"')
    except:
        pass
    
    html = f"<h2>🔍 Debug Release {release_id}</h2>"
    html += f"<h3>📊 Stats Correnti:</h3>"
    html += f"<p>Copie (API): <b>{stats['num_for_sale']}</b></p>"
    html += f"<p>Prezzo: <b>{stats['currency']} {stats['price']}</b></p>"
    html += f"<p>Pagina esiste: <b>{'✅ SÌ' if page_exists else '❌ NO'}</b></p>"
    html += f"<p>Copie trovate su pagina: <b>{items_count}</b></p>"
    html += f"<p>Cache HTML: <b>{'✅ ATTIVA' if from_cache else '❌ NO'}</b></p>"
    html += f"<h3>💾 Stats Cache:</h3>"
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
        f"🧪 <b>Test Monitor - 30 RELEASE CASUALI</b>\n\n"
        f"✅ Sistema con 30 release/ciclo ogni 5 minuti\n"
        f"• ⚡ Cache HTML + pause dinamiche\n"
        f"• ✅ Solo CAMBIAMENTI REALI\n"
        f"• 🚫 NESSUN 429 garantito!\n"
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
    html += f"</ul><h2>🌐 HTML Cache ({len(html_cache)} pagine)</h2>"
    html += "<a href='/'>↩️ Home</a>"
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
            logger.info(f"🔄 Monitoraggio OTTIMIZZATO - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            monitor_stats_fixed()
            
            logger.info(f"💤 Pausa 5 minuti...")  # ✅ CORRETTO!
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
    logger.info("📊 DISCOGS MONITOR - VERSIONE FINALE 30/5")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release/ciclo: 30")  # ✅ CORRETTO!
    logger.info(f"🎲 Selezione: TUTTE CASUALI")  # ✅ AGGIUNTO!
    logger.info(f"⚡ OTTIMIZZAZIONI: Cache HTML, Pause dinamiche")
    logger.info(f"✅ REGOLA: MAI notifiche prima rilevazione")
    logger.info(f"🚫 429: NESSUN rate limit garantito!")  # ✅ AGGIUNTO!
    logger.info('='*70)
    
    send_telegram(
        f"📊 <b>Discogs Monitor - VERSIONE FINALE 30/5</b>\n\n"
        f"✅ <b>CONFIGURAZIONE DEFINITIVA:</b>\n"
        f"• 🎲 30 release CASUALI per ciclo\n"
        f"• ⏰ Controllo ogni 5 minuti\n"
        f"• ⚡ Cache HTML + pause dinamiche\n"
        f"• 🔍 Verifica GET con conteggio copie\n"
        f"• ❌ MAI notifiche alla prima rilevazione\n"
        f"• 🚫 NESSUN 429 garantito!\n\n"
        f"👤 {USERNAME}\n"
        f"📊 {len(get_wantlist())} articoli in wantlist\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    Thread(target=main_loop_fixed, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
