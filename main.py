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

# ================== DISCOGS API ==================
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
            "User-Agent": "DiscogsStatsBot/4.0"
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

def get_release_stats(release_id):
    """Ottieni statistiche per una release"""
    url = f"https://api.discogs.com/marketplace/stats/{release_id}"
    headers = {"User-Agent": "DiscogsStatsBot/4.0"}
    
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
            
            num_for_sale = data.get('num_for_sale', 0) if isinstance(data, dict) else 0
            lowest = data.get('lowest_price', {}) if isinstance(data, dict) else {}
            price = lowest.get('value', 'N/D') if isinstance(lowest, dict) else 'N/D'
            currency = lowest.get('currency', '') if isinstance(lowest, dict) else ''
            
            return {
                'num_for_sale': num_for_sale,
                'price': price,
                'currency': currency
            }
            
        elif response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 30))
            logger.warning(f"⏳ 429, aspetto {retry_after}s")
            time.sleep(retry_after)
            return get_release_stats(release_id)
            
    except Exception as e:
        logger.error(f"❌ Errore stats {release_id}: {e}")
    
    return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

# ================== MONITORAGGIO CON ANTI-SPAM ==================
def monitor_stats_antispam():
    """Monitoraggio con ANTI-SPAM - Non notifica più di una volta per release"""
    logger.info("📊 Monitoraggio con ANTI-SPAM...")
    
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
            
            # Ottieni stats CORRENTI
            current = get_release_stats(release_id)
            current_count = current['num_for_sale']
            
            # Recupera stats PRECEDENTI dalla cache
            previous = stats_cache.get(release_id, {})
            previous_count = previous.get('num_for_sale', -1)
            
            # === ANTI-SPAM: SOLO CAMBIAMENTI REALI ===
            if previous_count == -1:
                # PRIMA VOLTA: NON notificare, solo registra
                logger.info(f"   📝 Prima rilevazione: {current_count} copie")
                
            elif current_count != previous_count:
                # CAMBIAMENTO REALE! 🎉
                diff = current_count - previous_count
                
                if diff > 0:
                    emoji = "🆕"
                    action = f"+{diff} NUOVE COPIE"
                else:
                    emoji = "📉"
                    action = f"{diff} copie"
                
                # Prezzo
                price_display = f"{current['currency']} {current['price']}" if current['price'] != 'N/D' else 'N/D'
                
                # SOLO se il numero di copie è CAMBIATO DAVVERO
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
                    logger.info(f"   🎯 CAMBIAMENTO: {action} (ora: {current_count}) - NOTIFICA #{notifications_sent}")
                    
                    # PAUSA TRA NOTIFICHE per non floodare
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
                    'last_change': datetime.now().isoformat(),
                    'last_check': time.time()
                }
                logger.info(f"   💾 Cache aggiornata: {previous_count} → {current_count}")
            
        except Exception as e:
            logger.error(f"❌ Errore release {i+1}: {e}")
        
        time.sleep(random.uniform(0.8, 1.2))
    
    # === SALVA CACHE SOLO ALLA FINE ===
    save_stats_cache(stats_cache)
    
    logger.info(f"✅ Rilevati {changes_detected} cambiamenti, {notifications_sent} notifiche inviate")
    return changes_detected

# ================== FLASK APP ==================
app = Flask(__name__)

@app.route("/")
def home():
    cache = load_stats_cache()
    monitored = len(cache)
    with_stats = sum(1 for v in cache.values() if v.get('num_for_sale', 0) > 0)
    
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
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 12px 24px; 
                    text-decoration: none; border-radius: 5px; margin: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Discogs Monitor - ANTI-SPAM</h1>
            
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
            
            <div class="warning">
                <h3>✅ ANTI-SPAM ATTIVO:</h3>
                <ul>
                    <li>❌ NON notifica alla prima rilevazione</li>
                    <li>✅ Notifica SOLO quando il numero CAMBIA</li>
                    <li>💾 Cache persistente (salva su file)</li>
                    <li>📊 50 release ogni 3 minuti</li>
                </ul>
            </div>
            
            <h3>🔧 Controlli</h3>
            <a class="btn" href="/check">🚀 Controllo ANTI-SPAM</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            <a class="btn" href="/logs">📄 Logs</a>
            <a class="btn" href="/reset">🔄 Reset Cache</a>
            <a class="btn" href="/debug">🔍 Test Release</a>
            
            <h3>📊 Info</h3>
            <p><strong>Utente:</strong> {USERNAME}</p>
            <p><strong>Cache file:</strong> {STATS_CACHE_FILE}</p>
            <p><strong>Ultimo aggiornamento:</strong> {datetime.now().strftime('%H:%M %d/%m/%Y')}</p>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=monitor_stats_antispam, daemon=True).start()
    return "<h1>🚀 Monitoraggio ANTI-SPAM avviato!</h1><p>Notifiche solo per cambiamenti REALI.</p><a href='/'>↩️ Home</a>", 200

@app.route("/reset")
def reset_cache():
    """Resetta COMPLETAMENTE la cache"""
    save_stats_cache({})
    logger.warning("🔄 CACHE RESETTATA!")
    return "<h1>🔄 Cache resettata!</h1><p>Ora tutte le release saranno considerate 'prime rilevazioni' e NON riceverai notifiche finché non cambiano.</p><a href='/'>↩️ Home</a>", 200

@app.route("/debug")
def debug_release():
    release_id = request.args.get('id', '14809291')
    stats = get_release_stats(release_id)
    cache = load_stats_cache()
    cached = cache.get(release_id, {})
    
    html = f"<h2>🔍 Debug Release {release_id}</h2>"
    html += f"<h3>📊 Stats Correnti:</h3>"
    html += f"<p>Copie: <b>{stats['num_for_sale']}</b></p>"
    html += f"<p>Prezzo: <b>{stats['currency']} {stats['price']}</b></p>"
    html += f"<h3>💾 Cache:</h3>"
    html += f"<p>Copie: <b>{cached.get('num_for_sale', 'N/A')}</b></p>"
    html += f"<p>Ultimo cambio: <b>{cached.get('last_change', 'Mai')}</b></p>"
    html += f"<p><b>{'✅ Notificherà SOLO se cambia' if cached else '📝 Prima rilevazione - NON notificherà'}</b></p>"
    html += "<br><a href='/'>↩️ Home</a>"
    
    return html, 200

@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test Monitor - ANTI-SPAM</b>\n\n"
        f"✅ Sistema online - Notifiche solo per cambiamenti REALI\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controllo ogni 3 minuti\n"
        f"💾 Cache persistente attiva\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    return "✅ Test inviato" if success else "❌ Errore", 200

@app.route("/logs")
def view_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = f.read().splitlines()[-100:]
        return "<pre style='background:#000; color:#0f0; padding:20px;'>" + "<br>".join(logs) + "</pre><br><a href='/'>↩️ Home</a>", 200
    except:
        return "<pre>Nessun log</pre><a href='/'>↩️ Home</a>", 200

@app.route("/cache")
def view_cache():
    cache = load_stats_cache()
    html = f"<h2>💾 Cache ({len(cache)} release)</h2><ul>"
    for rid, data in list(cache.items())[:20]:
        html += f"<li>{rid}: {data.get('num_for_sale', 0)} copie - {data.get('artist', '')[:20]}</li>"
    html += "</ul><a href='/'>↩️ Home</a>"
    return html, 200

# ================== MAIN LOOP ==================
def main_loop_antispam():
    time.sleep(10)
    while True:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔄 Monitoraggio ANTI-SPAM - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            monitor_stats_antispam()
            
            logger.info(f"💤 Pausa 3 minuti...")
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Loop error: {e}")
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    # Verifica variabili
    required = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [var for var in required if not os.environ.get(var)]
    
    if missing:
        logger.error(f"❌ Variabili mancanti: {missing}")
        exit(1)
    
    logger.info('='*70)
    logger.info("📊 DISCOGS MONITOR - VERSIONE ANTI-SPAM")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release/ciclo: 50")
    logger.info(f"💾 Cache file: {STATS_CACHE_FILE}")
    logger.info('='*70)
    
    # Notifica avvio
    send_telegram(
        f"📊 <b>Discogs Monitor - ANTI-SPAM</b>\n\n"
        f"✅ ANTI-SPAM ATTIVO!\n"
        f"• ❌ Nessuna notifica alla prima rilevazione\n"
        f"• ✅ Notifiche SOLO per cambiamenti REALI\n"
        f"• 💾 Cache persistente su file\n\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controllo ogni 3 minuti\n"
        f"📊 50 release/ciclo\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    Thread(target=main_loop_antispam, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
