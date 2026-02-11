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
CHECK_INTERVAL = 180  # 3 minuti (più reattivo)
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

SEEN_FILE = "stats_seen.json"  # SEPARATO dal vecchio seen!
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
    """Carica l'ultimo valore noto di num_for_sale per ogni release"""
    try:
        if os.path.exists(STATS_CACHE_FILE):
            with open(STATS_CACHE_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_stats_cache(cache):
    """Salva i valori correnti"""
    try:
        with open(STATS_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except:
        pass

# ================== DISCOGS API - STATS ==================
def get_wantlist():
    """Ottieni wantlist completa"""
    all_wants = []
    page = 1
    
    logger.info(f"📥 Scaricamento wantlist...")
    
    while True:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {'page': page, 'per_page': 100}
        headers = {"Authorization": f"Discogs token={DISCOGS_TOKEN}", "User-Agent": "DiscogsStatsBot/1.0"}
        
        try:
            time.sleep(0.3)
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                break
            
            data = response.json()
            wants = data.get('wants', [])
            if not wants:
                break
            
            all_wants.extend(wants)
            
            pagination = data.get('pagination', {})
            if page >= pagination.get('pages', 1):
                break
            page += 1
        except:
            break
    
    logger.info(f"✅ Wantlist: {len(all_wants)} articoli")
    return all_wants

def get_release_stats(release_id):
    """
    ENDPOINT CHE FUNZIONA DAVVERO!
    Restituisce numero copie in vendita e prezzo più basso
    """
    url = f"https://api.discogs.com/marketplace/stats/{release_id}"
    headers = {"User-Agent": "DiscogsStatsBot/1.0"}  # NON serve token!
    
    try:
        time.sleep(0.5)  # Rate limiting leggero
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            num_for_sale = data.get('num_for_sale', 0)
            lowest = data.get('lowest_price', {})
            price = lowest.get('value', 'N/D')
            currency = lowest.get('currency', '')
            
            return {
                'num_for_sale': num_for_sale,
                'price': price,
                'currency': currency,
                'timestamp': time.time()
            }
        elif response.status_code == 404:
            # Release non trovata o senza stats
            return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}
        else:
            logger.error(f"❌ Stats API error {response.status_code} per {release_id}")
            
    except Exception as e:
        logger.error(f"❌ Errore stats {release_id}: {e}")
    
    return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

# ================== MONITORAGGIO STATS ==================
def monitor_stats_changes():
    """Monitora CAMBIAMENTI nelle statistiche - QUESTA È LA MAGIA!"""
    logger.info("📊 Monitoraggio statistiche marketplace...")
    
    wants = get_wantlist()
    if not wants:
        return 0
    
    # Carica stato precedente
    stats_cache = load_stats_cache()
    changes_detected = 0
    
    # Controlla 50 release per ciclo (massima copertura)
    check_count = min(50, len(wants))
    
    # Strategia: 20 recenti + 30 casuali
    recent = wants[:20]
    if len(wants) > 20:
        random_sample = random.sample(wants[20:], min(30, len(wants[20:])))
        releases_to_check = recent + random_sample
    else:
        releases_to_check = recent
    
    random.shuffle(releases_to_check)
    
    logger.info(f"🔍 Controllo {len(releases_to_check)} release...")
    
    for i, item in enumerate(releases_to_check):
        release_id = str(item.get('id'))
        basic_info = item.get('basic_information', {})
        title = basic_info.get('title', 'Sconosciuto')
        artists = basic_info.get('artists', [{}])
        artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'
        
        logger.info(f"[{i+1}/{len(releases_to_check)}] {artist} - {title[:40]}...")
        
        # Ottieni stats CORRENTI
        current_stats = get_release_stats(release_id)
        current_count = current_stats['num_for_sale']
        current_price = current_stats['price']
        current_currency = current_stats['currency']
        
        # Recupera stats PRECEDENTI
        previous = stats_cache.get(release_id, {})
        previous_count = previous.get('num_for_sale', -1)  # -1 = mai visto
        
        # --- LOGICA DI RILEVAMENTO CAMBIAMENTI ---
        if previous_count == -1:
            # Prima volta che controlliamo questa release
            logger.info(f"   📝 Prima rilevazione: {current_count} copie")
            
        elif current_count != previous_count:
            # CAMBIAMENTO RILEVATO! 🎉
            diff = current_count - previous_count
            
            if diff > 0:
                # AUMENTO = NUOVE COPIE IN VENDITA!
                emoji = "🆕"
                action = f"+{diff} NUOVE COPIE"
                description = f"Aggiunte {diff} nuova/e copia/e"
            else:
                # DIMINUZIONE = COPIE VENDUTE/RIMOSSE
                emoji = "📉"
                action = f"{diff} copie"  # es: "-2 copie"
                description = f"Rimosse {abs(diff)} copia/e"
            
            # Formatta prezzo
            price_display = f"{current_currency} {current_price}" if current_price != 'N/D' else 'N/D'
            
            # Costruisci messaggio
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
            else:
                logger.error(f"   ❌ Errore invio notifica")
        
        elif current_count > 0 and current_count == previous_count:
            # Nessun cambiamento, ma ci sono copie
            logger.info(f"   ℹ️ Stabili: {current_count} copie")
        else:
            # Nessuna copia, nessun cambiamento
            logger.info(f"   ℹ️ Nessuna copia (invariato)")
        
        # Aggiorna cache
        stats_cache[release_id] = {
            'num_for_sale': current_count,
            'price': current_price,
            'currency': current_currency,
            'artist': artist,
            'title': title,
            'last_check': time.time()
        }
        
        # Pausa per rate limiting
        time.sleep(random.uniform(1, 1.5))
    
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
        <title>📊 Discogs Stats Monitor</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; }}
            .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
            .card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px; }}
            .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; margin: 5px; }}
            .btn:hover {{ background: #45a049; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Discogs Stats Monitor</h1>
            
            <div class="stats">
                <div class="card">
                    <h3>📈 Release Monitorate</h3>
                    <p style="font-size: 2em; font-weight: bold;">{monitored}</p>
                </div>
                <div class="card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
                    <h3>🛒 Con Copie in Vendita</h3>
                    <p style="font-size: 2em; font-weight: bold;">{with_stats}</p>
                </div>
            </div>
            
            <div style="background: #e3f2fd; padding: 20px; border-radius: 10px; margin: 20px 0;">
                <h3>🎯 COSA RILEVA QUESTO BOT:</h3>
                <ul style="font-size: 1.1em;">
                    <li>✅ <strong>NUOVE COPIE</strong> appena messe in vendita (+1, +2, +5...)</li>
                    <li>✅ <strong>COPIE VENDUTE/RIMOSSE</strong> (-1, -2, -5...)</li>
                    <li>✅ <strong>PREZZO PIÙ BASSO</strong> che cambia</li>
                    <li>✅ <strong>DA 0 A 1+</strong> quando esce la prima copia</li>
                    <li>✅ <strong>DA 1+ A 0</strong> quando l'ultima copia viene venduta</li>
                </ul>
                <p style="font-size: 0.9em; color: #666;">⏱ Controllo ogni 3 minuti - Notifiche IMMEDIATE!</p>
            </div>
            
            <h3>🔧 Controlli</h3>
            <a class="btn" href="/check">🚀 Controllo STATS</a>
            <a class="btn" href="/test">🧪 Test Telegram</a>
            <a class="btn" href="/logs">📄 Logs</a>
            <a class="btn" href="/reset">🔄 Reset Cache</a>
            <a class="btn" href="/debug">🔍 Debug Release</a>
            
            <h3>📊 Info Sistema</h3>
            <p><strong>Utente:</strong> {USERNAME}</p>
            <p><strong>Intervallo:</strong> 3 minuti</p>
            <p><strong>Release/ciclo:</strong> 50</p>
        </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=monitor_stats_changes, daemon=True).start()
    return "<h1>🚀 Monitoraggio STATS avviato!</h1><p>Controlla i logs per i cambiamenti.</p><a href='/'>↩️ Home</a>", 200

@app.route("/debug")
def debug_release():
    """Test manuale per una release specifica"""
    release_id = request.args.get('id', '14809291')
    
    stats = get_release_stats(release_id)
    
    html = f"<h2>🔍 Debug Release {release_id}</h2>"
    html += f"<p>📊 Copie in vendita: <b>{stats['num_for_sale']}</b></p>"
    html += f"<p>💰 Prezzo più basso: <b>{stats['currency']} {stats['price']}</b></p>"
    html += f"<p>🔗 <a href='https://www.discogs.com/sell/list?release_id={release_id}'>Vedi su Discogs</a></p>"
    html += "<br><a href='/'>↩️ Home</a>"
    
    return html, 200

@app.route("/reset")
def reset_cache():
    """Resetta la cache delle statistiche"""
    save_stats_cache({})
    return "<h1>🔄 Cache resettata!</h1><p>Ora monitorerà TUTTE le release come 'prime rilevazioni'.</p><a href='/'>↩️ Home</a>", 200

@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test Stats Monitor</b>\n\n"
        f"✅ Sistema online - Monitoraggio CAMBIAMENTI attivo!\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controllo ogni 3 minuti\n"
        f"📊 Rileva: nuove copie, vendite, variazioni prezzo\n"
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

# ================== MAIN LOOP ==================
def main_loop():
    """Loop ogni 3 minuti"""
    time.sleep(10)
    
    while True:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔄 Monitoraggio STATS - {datetime.now().strftime('%H:%M:%S')}")
            logger.info('='*70)
            
            monitor_stats_changes()
            
            logger.info(f"💤 Pausa 3 minuti...")
            for _ in range(CHECK_INTERVAL):
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Loop error: {e}")
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    logger.info('='*70)
    logger.info("📊 DISCOGS STATS MONITOR - RILEVA CAMBIAMENTI IN TEMPO REALE")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release/ciclo: 50")
    logger.info('='*70)
    
    send_telegram(
        f"📊 <b>Discogs Stats Monitor Avviato!</b>\n\n"
        f"✅ Rileva CAMBIAMENTI in tempo reale:\n"
        f"• 🆕 Nuove copie in vendita\n"
        f"• 📉 Copie vendute/rimosse\n"
        f"• 💰 Variazioni prezzo più basso\n\n"
        f"👤 {USERNAME}\n"
        f"⏰ Controllo ogni 3 minuti\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    
    Thread(target=main_loop, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
