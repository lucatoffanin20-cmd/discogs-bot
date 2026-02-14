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
CHECK_INTERVAL = 300  # 5 minuti tra un ciclo e l'altro
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

# File di cache
STATS_CACHE_FILE = "stats_cache.json"
INDEX_FILE = "last_index.txt"
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

# ================== VARIABILI GLOBALI ==================
EMERGENCY_STOP = False  # Se True, blocca l'invio di messaggi

# ================== FUNZIONI TELEGRAM ==================
def send_telegram(msg):
    """Invia un messaggio su Telegram, se non in emergenza."""
    if EMERGENCY_STOP:
        logger.info("🚫 Notifica bloccata (modalità emergenza)")
        return False
    if not TG_TOKEN or not TG_CHAT:
        logger.error("❌ Token o Chat ID Telegram mancanti")
        return False

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"❌ Errore Telegram: {e}")
        return False

# ================== GESTIONE CACHE STATS ==================
def load_cache():
    """Carica la cache delle statistiche (numero copie, prezzi, etc.)."""
    if os.path.exists(STATS_CACHE_FILE):
        with open(STATS_CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_cache(cache):
    """Salva la cache delle statistiche."""
    with open(STATS_CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

# ================== GESTIONE INDICE SEQUENZIALE ==================
def load_index():
    """Carica l'indice da cui partire nel prossimo ciclo."""
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'r') as f:
            return int(f.read().strip())
    return 0

def save_index(idx):
    """Salva l'indice corrente."""
    with open(INDEX_FILE, 'w') as f:
        f.write(str(idx))

# ================== API DISCOGS ==================
def get_wantlist():
    """
    Scarica l'intera wantlist, ordinata dalla più RECENTE alla più VECCHIA.
    Questo ordine rispecchia quello che vedi sul sito.
    """
    all_wants = []
    page = 1

    logger.info("📥 Scaricamento wantlist ordinata...")

    while True:
        url = f"https://api.discogs.com/users/{USERNAME}/wants"
        params = {
            'page': page,
            'per_page': 100,
            'sort': 'added',
            'sort_order': 'desc'
        }
        headers = {
            "Authorization": f"Discogs token={DISCOGS_TOKEN}",
            "User-Agent": "DiscogsBot/12.0-FINAL"
        }

        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                logger.error(f"❌ Errore nel scaricare pagina {page}: {r.status_code}")
                break

            data = r.json()
            wants = data.get('wants', [])
            if not wants:
                break

            all_wants.extend(wants)
            logger.info(f"📄 Pagina {page}: {len(wants)} articoli")
            page += 1

            # Se non ci sono altre pagine, esci
            if page > data.get('pagination', {}).get('pages', 1):
                break

            time.sleep(0.5)  # pausa tra pagine

        except Exception as e:
            logger.error(f"❌ Errore in get_wantlist: {e}")
            break

    logger.info(f"✅ Wantlist caricata: {len(all_wants)} articoli")
    return all_wants

def get_release_stats_safe(release_id):
    """
    Ottiene le statistiche di una release (numero copie, prezzo minimo)
    con PAUSE LUNGHE per evitare 429.
    """
    # 🔴🔴🔴 PAUSA FISSA OBBLIGATORIA: 2 secondi tra una richiesta e l'altra
    time.sleep(2)

    url = f"https://api.discogs.com/marketplace/stats/{release_id}"
    headers = {"User-Agent": "DiscogsBot/12.0-FINAL"}

    try:
        r = requests.get(url, headers=headers, timeout=30)

        # Controlla il rate limit residuo
        remaining = int(r.headers.get('X-Discogs-Ratelimit-Remaining', 60))
        if remaining < 10:
            logger.warning(f"⚠️ Rate limit basso ({remaining}), aspetto 5 secondi extra")
            time.sleep(5)
        elif remaining < 20:
            time.sleep(3)

        if r.status_code == 200:
            data = r.json()
            if not data:
                return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

            num = data.get('num_for_sale', 0)
            low = data.get('lowest_price', {})
            price = low.get('value', 'N/D')
            curr = low.get('currency', '')

            return {'num_for_sale': num, 'price': price, 'currency': curr}

        elif r.status_code == 429:
            retry = int(r.headers.get('Retry-After', 60))
            logger.warning(f"⏳ 429: aspetto {retry}s")
            time.sleep(retry)
            return get_release_stats_safe(release_id)  # riprova

        else:
            logger.error(f"❌ API error {r.status_code} per {release_id}")

    except Exception as e:
        logger.error(f"❌ Errore stats {release_id}: {e}")

    return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

# ================== CICLO PRINCIPALE ==================
def run_check_cycle():
    """
    Esegue un singolo ciclo di controllo:
    1. Carica wantlist ordinata
    2. Prende 30 release consecutive (in base all'indice)
    3. Per ognuna, confronta con la cache e notifica se cambiato
    """
    logger.info("🔄 Avvio ciclo di controllo...")
    wants = get_wantlist()
    if not wants:
        logger.error("❌ Wantlist vuota, impossibile continuare")
        return

    cache = load_cache()
    changes = 0
    notifications = 0

    # Determina quante e quali release controllare (30 per ciclo)
    total = len(wants)
    batch_size = 30
    start = load_index()

    # Prendi 30 release a partire da 'start'
    to_check = wants[start:start + batch_size]
    # Se siamo in fondo, ricomincia da capo prendendo le prime mancanti
    if len(to_check) < batch_size:
        remaining = batch_size - len(to_check)
        to_check += wants[:remaining]
        start = remaining
    else:
        start += batch_size

    # Salva il nuovo indice (modulo totale, per sicurezza)
    save_index(start % total)

    logger.info(f"🔍 Controllo {len(to_check)} release (posizione {start})...")

    for idx, item in enumerate(to_check):
        rid = str(item['id'])
        info = item.get('basic_information', {})
        title = info.get('title', 'Sconosciuto')
        artists = info.get('artists', [{}])
        artist = artists[0].get('name', 'Sconosciuto') if artists else 'Sconosciuto'

        logger.info(f"[{idx+1}/{len(to_check)}] {artist} - {title[:40]}...")

        # Ottieni i dati attuali
        current = get_release_stats_safe(rid)

        # Se la risposta è malformata, salta
        if current is None or current.get('num_for_sale') is None:
            logger.error(f"   ❌ Dati non validi per {rid}, salto")
            continue

        curr_count = current['num_for_sale']

        # Recupera i dati precedenti dalla cache
        prev = cache.get(rid, {})
        prev_count = prev.get('num_for_sale', -1)

        # Prima rilevazione: apprendimento, nessuna notifica
        if prev_count == -1:
            logger.info(f"   📝 APPRENDIMENTO: {curr_count} copie")
            changes += 1  # lo consideriamo un cambiamento per la cache

        # Cambiamento reale (numero copie diverso)
        elif curr_count != prev_count:
            diff = curr_count - prev_count
            emoji = "🆕" if diff > 0 else "📉"
            action = f"+{diff} NUOVE" if diff > 0 else f"{diff} vendute"
            price_str = f"{current['currency']} {current['price']}" if current['price'] != 'N/D' else 'N/D'

            msg = (
                f"{emoji} <b>CAMBIAMENTO</b>\n\n"
                f"🎸 <b>{artist}</b>\n💿 {title}\n\n"
                f"📊 {action}\n💰 Prezzo più basso: {price_str}\n"
                f"📦 Totale: {curr_count} copie\n\n"
                f"🔗 <a href='https://www.discogs.com/sell/list?release_id={rid}'>VEDI COPIE</a>"
            )
            if send_telegram(msg):
                notifications += 1
                changes += 1
                logger.info(f"   🎯 NOTIFICA #{notifications}: {action}")
                time.sleep(1)  # pausa tra notifiche

        # Stabile
        elif curr_count > 0:
            logger.info(f"   ℹ️ Stabili: {curr_count} copie")

        # Aggiorna la cache se necessario
        if prev_count != curr_count:
            cache[rid] = {
                'num_for_sale': curr_count,
                'price': current['price'],
                'currency': current['currency'],
                'artist': artist,
                'title': title,
                'last_check': time.time()
            }
            logger.info(f"   💾 Cache aggiornata: {prev_count} → {curr_count}")

        # Pausa dinamica: se ci sono copie, aspetta di più
        if curr_count > 0:
            time.sleep(random.uniform(0.8, 1.2))
        else:
            time.sleep(random.uniform(0.3, 0.6))

    # Salva la cache aggiornata
    save_cache(cache)
    logger.info(f"✅ Ciclo completato: {changes} cambi, {notifications} notifiche")

# ================== FLASK APP ==================
app = Flask(__name__)

@app.route("/")
def home():
    cache = load_cache()
    wants = get_wantlist()
    monitored = len(cache)
    with_sales = sum(1 for v in cache.values() if v.get('num_for_sale', 0) > 0)
    pos = load_index()
    status = "🟢 ONLINE" if not EMERGENCY_STOP else "🔴 BLOCCATO"

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>📊 Discogs Bot - FINALE</title>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; }}
        h1 {{ color: #333; border-bottom: 3px solid #4CAF50; }}
        .btn {{ display: inline-block; background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 5px; }}
        .btn-stop {{ background: #dc3545; }}
        .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
        .stat-card {{ background: #4CAF50; color: white; padding: 20px; border-radius: 10px; text-align: center; }}
    </style>
    </head>
    <body>
    <div class="container">
        <h1>📊 Discogs Bot - VERSIONE FINALE</h1>
        <p><strong>Stato:</strong> {status}</p>
        <div class="stats">
            <div class="stat-card"><h3>📈 Monitorate</h3><p style="font-size:2.5em;">{monitored}</p></div>
            <div class="stat-card" style="background:#dc3545;"><h3>🛒 Con copie</h3><p style="font-size:2.5em;">{with_sales}</p></div>
        </div>
        <p><strong>👤 Utente:</strong> {USERNAME}</p>
        <p><strong>📌 Posizione:</strong> {pos}/{len(wants)}</p>
        <p><strong>⏰ 30 release ogni 5 minuti (rallentato, zero 429)</strong></p>
        <a class="btn" href="/check">🚀 Controllo manuale</a>
        <a class="btn btn-stop" href="/stop">🔴 STOP</a>
        <a class="btn" href="/start">🟢 START</a>
        <a class="btn" href="/reset">🔄 Reset cache</a>
        <a class="btn" href="/progress">📊 Progresso</a>
        <a class="btn" href="/logs">📄 Logs</a>
    </div>
    </body>
    </html>
    """

@app.route("/check")
def manual_check():
    Thread(target=run_check_cycle, daemon=True).start()
    return "<h1>🚀 Controllo avviato</h1><a href='/'>Home</a>"

@app.route("/stop")
def stop():
    global EMERGENCY_STOP
    EMERGENCY_STOP = True
    logger.critical("🛑 BOT BLOCCATO MANUALMENTE")
    return "<h1>🔴 Bot bloccato</h1><a href='/start'>Riattiva</a>"

@app.route("/start")
def start():
    global EMERGENCY_STOP
    EMERGENCY_STOP = False
    logger.warning("✅ Bot riattivato")
    send_telegram("✅ Bot riattivato")
    return "<h1>🟢 Bot riattivato</h1><a href='/'>Home</a>"

@app.route("/reset")
def reset():
    save_cache({})
    save_index(0)
    logger.warning("🔄 Cache e indice resettati")
    return "<h1>🔄 Reset completato</h1><a href='/'>Home</a>"

@app.route("/progress")
def progress():
    cache = load_cache()
    wants = get_wantlist()
    cached_ids = set(cache.keys())
    all_ids = {str(w['id']): w['basic_information']['title'] for w in wants}
    missing = [(rid, title) for rid, title in all_ids.items() if rid not in cached_ids]
    html = f"<h2>📊 Progresso</h2><p>Apprese: {len(cache)}/{len(wants)}</p>"
    if missing:
        html += "<h3>🎯 Mancanti:</h3><ul>"
        for rid, title in missing:
            html += f"<li>{rid} - {title[:60]}</li>"
        html += "</ul>"
    html += "<a href='/'>Home</a>"
    return html

@app.route("/logs")
def view_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            logs = f.read().splitlines()[-100:]
        return "<pre>" + "<br>".join(logs) + "</pre><br><a href='/'>Home</a>"
    return "<pre>Nessun log</pre><a href='/'>Home</a>"

# ================== MAIN LOOP ==================
def main_loop():
    """Avvia i cicli ogni 5 minuti."""
    time.sleep(10)
    while True:
        if not EMERGENCY_STOP:
            run_check_cycle()
        else:
            logger.info("⏸️ Bot in pausa (emergenza), nessun controllo eseguito")
        for _ in range(CHECK_INTERVAL):
            time.sleep(1)

# ================== AVVIO ==================
if __name__ == "__main__":
    required = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        logger.error(f"❌ Variabili mancanti: {missing}")
        exit(1)

    logger.info("="*60)
    logger.info("🤖 DISCOGS BOT - VERSIONE FINALE SUPER-LENTA")
    logger.info("="*60)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info("✅ PAUSA FISSA DI 2 SECONDI tra le richieste – ZERO 429")
    logger.info("="*60)

    send_telegram(
        f"🤖 <b>Bot finale avviato</b>\n\n"
        f"✅ 30 release ogni 5 minuti (rallentato)\n"
        f"⏱️ Pausa 2 secondi tra richieste – zero 429\n"
        f"👤 {USERNAME}"
    )

    Thread(target=main_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
