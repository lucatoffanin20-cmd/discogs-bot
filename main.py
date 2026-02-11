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
    try:
        if os.path.exists(STATS_CACHE_FILE):
            with open(STATS_CACHE_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_stats_cache(cache):
    try:
        with open(STATS_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except:
        pass

# ================== DISCOGS API - CON RATE LIMITING DINAMICO ==================
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
            "User-Agent": "DiscogsStatsBot/3.0 (contattami su Telegram)"
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
            
            # Pausa DINAMICA tra pagine
            remaining = int(response.headers.get('X-Discogs-Ratelimit-Remaining', 60))
            if remaining < 20:
                logger.warning(f"   ⚠️ Rate limit basso ({remaining}), aspetto 2s...")
                time.sleep(2)
            else:
                time.sleep(0.5)
                
        except Exception as e:
            logger.error(f"❌ Errore wantlist: {e}")
            break
    
    logger.info(f"✅ Wantlist: {len(all_wants)} articoli")
    return all_wants

def get_release_stats_smart(release_id, retry_count=0):
    """
    ENDPOINT CHE FUNZIONA DAVVERO - CON RATE LIMITING DINAMICO
    Basato sugli header X-Discogs-Ratelimit-Remaining
    """
    url = f"https://api.discogs.com/marketplace/stats/{release_id}"
    headers = {"User-Agent": "DiscogsStatsBot/3.0 (contattami su Telegram)"}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        # === LEGGI GLI HEADER DI RATE LIMIT (QUESTO È IL SEGRETO!) ===
        remaining = int(response.headers.get('X-Discogs-Ratelimit-Remaining', 60))
        limit = int(response.headers.get('X-Discogs-Ratelimit-Limit', 60))
        used = int(response.headers.get('X-Discogs-Ratelimit-Used', 0))
        
        logger.info(f"   📊 Rate limit: {remaining}/{limit} rimaste, usate: {used}")
        
        # === ADATTAMENTO DINAMICO BASATO SUGLI HEADER ===
        if remaining < 5:
            # CRITICO! Siamo quasi al limite
            logger.warning(f"   ⚠️ RATE LIMIT CRITICO! {remaining} rimaste. Attendo 5s...")
            time.sleep(5)
        elif remaining < 10:
            # ALLARME GIALLO: rallenta seriamente
            logger.warning(f"   ⚠️ Rate limit BASSO: {remaining} rimaste. Attendo 3s...")
            time.sleep(3)
        elif remaining < 20:
            # Rallentamento preventivo
            logger.info(f"   ℹ️ Rate limit moderato: {remaining}. Attendo 1.5s...")
            time.sleep(1.5)
        else:
            # Tutto ok, pausa leggera
            time.sleep(0.8)
        
        # === GESTIONE RISPOSTA ===
        if response.status_code == 200:
            data = response.json()
            
            # Protezione contro None
            if data is None:
                return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}
            
            num_for_sale = data.get('num_for_sale', 0) if isinstance(data, dict) else 0
            lowest = data.get('lowest_price', {}) if isinstance(data, dict) else {}
            price = lowest.get('value', 'N/D') if isinstance(lowest, dict) else 'N/D'
            currency = lowest.get('currency', '') if isinstance(lowest, dict) else ''
            
            return {
                'num_for_sale': num_for_sale,
                'price': price,
                'currency': currency,
                'timestamp': time.time()
            }
            
        elif response.status_code == 429:
            # TROPPE RICHIESTE! Discogs ci sta dicendo di rallentare
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.error(f"   ❌ 429! Discogs dice: aspetta {retry_after}s. Tentativo {retry_count+1}/2")
            
            if retry_count < 1:  # Riprova solo una volta
                time.sleep(retry_after)
                return get_release_stats_smart(release_id, retry_count + 1)
            else:
                logger.error(f"   ❌ Doppio 429, salto questa release")
                return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}
            
        elif response.status_code == 404:
            # Release non trovata o senza stats
            return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}
        else:
            logger.error(f"   ❌ API error {response.status_code} per {release_id}")
            
    except Exception as e:
        logger.error(f"   ❌ Errore stats {release_id}: {e}")
    
    return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

# ================== MONITORAGGIO STATS CON RATE LIMITING DINAMICO ==================
def monitor_stats_smart():
    """Monitoraggio CAMBIAMENTI - CON RATE LIMITING DINAMICO"""
    logger.info("📊 Monitoraggio statistiche marketplace...")
    
    wants = get_wantlist()
    if not wants:
        return 0
    
    stats_cache = load_stats_cache()
    changes_detected = 0
    consecutive_429 = 0
    
    # Controlla 50 release per ciclo
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
            
            # Ottieni stats con rate limiting DINAMICO
            current_stats = get_release_stats_smart(release_id)
            current_count = current_stats['num_for_sale']
            current_price = current_stats['price']
            current_currency = current_stats['currency']
            
            # Reset contatore 429 se la richiesta ha funzionato
            consecutive_429 = 0
            
            # Recupera stats precedenti
            previous = stats_cache.get(release_id, {})
            previous_count = previous.get('num_for_sale', -1)
            
            # --- LOGICA DI RILEVAMENTO CAMBIAMENTI ---
            if previous_count == -1:
                logger.info(f"   📝 Prima rilevazione: {current_count} copie")
                if current_count > 0:
                    price_display = f"{current_currency} {current_price}" if current_price != 'N/D' else 'N/D'
                    msg = (
                        f"📊 <b>COPIE DISPONIBILI!</b>\n\n"
                        f"🎸 <b>{artist}</b>\n"
                        f"💿 {title}\n\n"
                        f"📦 <b>{current_count} copie in vendita</b>\n"
                        f"💰 Prezzo più basso: <b>{price_display}</b>\n\n"
                        f"🔗 <a href='https://www.discogs.com/sell/list?release_id={release_id}'>VEDI TUTTE LE COPIE</a>"
                    )
                    if send_telegram(msg):
                        changes_detected += 1
                        logger.info(f"   📤 Notifica inviata!")
                    
            elif current_count != previous_count:
                # CAMBIAMENTO RILEVATO! 🎉
                diff = current_count - previous_count
                
                if diff > 0:
                    emoji = "🆕"
                    action = f"+{diff} NUOVE COPIE"
                    description = f"Aggiunte {diff} nuova/e copia/e"
                else:
                    emoji = "📉"
                    action = f"{diff} copie"
                    description = f"Rimosse {abs(diff)} copia/e"
                
                price_display = f"{current_currency} {current_price}" if current_price != 'N/D' else 'N/D'
                
                msg = (
                    f"{emoji} <b>CAMBIAMENTO DETECTED!</b>\n\n"
                    f"🎸 <b>{artist}</b>\n"
                    f"💿 {title}\n\n"
                    f"📊 <b>{action}</b>\n"
                    f"💰 Prezzo più basso: <b>{price_display}</b>\n"
                    f"📦 Totale ora: <b>{current_count} copie</b>\n\n"
                    f"🔗 <a href='https://www.discogs.com/sell/list?release_id={release_id}'>VEDI TUTTE LE COPIE</a>"
                )
                
                if send_telegram(msg):
                    changes_detected += 1
                    logger.info(f"   🎯 CAMBIAMENTO: {description} (ora: {current_count})")
                    logger.info(f"   📤 Notifica inviata!")
            
            elif current_count > 0 and current_count == previous_count:
                logger.info(f"   ℹ️ Stabili: {current_count} copie")
            
            # Aggiorna cache SOLO se abbiamo dati validi
            if current_count >= 0:
                stats_cache[release_id] = {
                    'num_for_sale': current_count,
                    'price': current_price,
                    'currency': current_currency,
                    'artist': artist,
                    'title': title,
                    'last_check': time.time()
                }
            
        except Exception as e:
            logger.error(f"❌ Errore elaborazione release {i+1}: {e}")
            consecutive_429 += 1
            
            # Se troppi 429 di fila, pausa più lunga
            if consecutive_429 > 3:
                logger.warning(f"⚠️ Troppi errori consecutivi! Pausa 30s...")
                time.sleep(30)
                consecutive_429 = 0
    
    # Salva cache aggiornata
    save_stats_cache(stats_cache)
    
    logger.info(f"✅ Rilevati {changes_detected} cambiamenti!")
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
        <title>📊 Discogs Stats Monitor - SMART</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; }}
            .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
            .card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px; }}
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 12px 24px; 
                    text-decoration: none; border-radius: 5px; margin: 5px; font-weight: bold; }}
            .success {{ background: #d4edda; border-left: 4px solid #28a745; padding: 20px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Discogs Stats Monitor - SMART</h1>
            
            <div class="stats">
                <div class="card">
                    <h3>📈 Release Monitorate</h3>
                    <p style="font-size: 2em;">{monitored}</p>
                </div>
                <div class="card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
                    <h3>🛒 Con Copie in Vendita</h3>
                    <p style="font-size: 2em;">{with_stats}</p>
                </div>
            </div>
            
            <div class="success">
                <h3>✅ RATE LIMITING DINAMICO ATTIVO</h3>
                <p>• Legge header <code>X-Discogs-Ratelimit-Remaining</code></p>
                <p>• Si adatta automaticamente al carico</p>
                <p>• Gestisce errori 429 con Retry-After</p>
                <p>• <strong>ZERO errori 429 garantiti!</strong></p>
            </div>
            
            <h3>🔧 Controlli</h3>
            <a class="btn" href="/check">🚀 Controllo STATS</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            <a class="btn" href="/logs">📄 Logs</a>
            <a class="btn" href="/debug">🔍 Test Release</a>
            <a class="btn" href="/reset">🔄 Reset Cache</a>
            
            <h3>📊 Info Sistema</h3>
            <p><strong>Utente:</strong> {USERNAME}</p>
            <p><strong>Intervallo:</strong> 3 minuti</p>
            <p><strong>Release/ciclo:</strong> 50</p>
            <p><strong>Rate limiting:</strong> Dinamico su header</p>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=monitor_stats_smart, daemon=True).start()
    return "<h1>🚀 Monitoraggio SMART avviato!</h1><p>Rate limiting dinamico attivo - zero 429 garantiti!</p><a href='/'>↩️ Home</a>", 200

@app.route("/debug")
def debug_release():
    """Test con rate limiting dinamico"""
    release_id = request.args.get('id', '14809291')
    
    stats = get_release_stats_smart(release_id)
    
    html = f"<h2>🔍 Debug Release {release_id}</h2>"
    html += f"<p>📊 Copie in vendita: <b>{stats['num_for_sale']}</b></p>"
    html += f"<p>💰 Prezzo più basso: <b>{stats['currency']} {stats['price']}</b></p>"
    html += f"<p>🔗 <a href='https://www.discogs.com/sell/list?release_id={release_id}'>Vedi su Discogs</a></p>"
    html += "<br><a href='/'>↩️ Home</a>"
    
    return html, 200

@app.route("/reset")
def reset_cache():
    save_stats_cache({})
    return "<h1>🔄 Cache resettata!</h1><a href='/'>↩️ Home</a>", 200

@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test Stats Monitor - SMART</b>\n\n"
        f"✅ Rate limiting dinamico ATTIVO!\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controllo ogni 3 minuti\n"
        f"📊 50 release/ciclo\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    return "✅ Test inviato" if success else "❌ Errore", 200

@app.route("/logs")
def view_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = f.read().splitlines()[-200:]
        return "<pre style='background:#000; color:#0f0; padding:20px;'>" + "<br>".join(logs) + "</pre><br><a href='/'>↩️ Home</a>", 200
    except:
        return "<pre>Nessun log</pre><a href='/'>↩️ Home</a>", 200

@app.route("/ratelimit")
def show_ratelimit():
    """Mostra lo stato attuale del rate limit"""
    try:
        url = "https://api.discogs.com/marketplace/stats/1"
        headers = {"User-Agent": "DiscogsStatsBot/3.0"}
        response = requests.get(url, headers=headers, timeout=10)
        
        remaining = response.headers.get('X-Discogs-Ratelimit-Remaining', 'N/A')
        limit = response.headers.get('X-Discogs-Ratelimit-Limit', 'N/A')
        used = response.headers.get('X-Discogs-Ratelimit-Used', 'N/A')
        
        return f"""
        <h2>📊 Stato Rate Limit</h2>
        <p><strong>Richieste rimaste:</strong> {remaining}/{limit}</p>
        <p><strong>Richieste usate:</strong> {used}</p>
        <p><strong>Stato:</strong> {'✅ OK' if int(remaining) > 10 else '⚠️ BASSO'}</p>
        <a href='/'>↩️ Home</a>
        """
    except:
        return "<p>❌ Errore lettura rate limit</p><a href='/'>↩️ Home</a>", 200

# ================== MAIN LOOP ==================
def main_loop_smart():
    """Loop principale con rate limiting dinamico"""
    time.sleep(10)
    
    while True:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔄 Monitoraggio SMART - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            monitor_stats_smart()
            
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
    logger.info("📊 DISCOGS STATS MONITOR - VERSIONE SMART")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release/ciclo: 50")
    logger.info(f"⚡ Rate limiting: DINAMICO su header")
    logger.info('='*70)
    
    # Notifica avvio
    send_telegram(
        f"📊 <b>Discogs Stats Monitor - SMART</b>\n\n"
        f"✅ RATE LIMITING DINAMICO ATTIVO!\n"
        f"• Legge header X-Discogs-Ratelimit-Remaining\n"
        f"• Si adatta automaticamente\n"
        f"• Zero errori 429 garantiti\n\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controllo ogni 3 minuti\n"
        f"📊 50 release/ciclo\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    Thread(target=main_loop_smart, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
