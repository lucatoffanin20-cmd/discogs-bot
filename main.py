import os
import json
import requests
import time
import random
from datetime import datetime, timedelta
from flask import Flask, request
from threading import Thread
import logging
from logging.handlers import RotatingFileHandler

# ================== CONFIG ==================
CHECK_INTERVAL = 300  # 5 minuti
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("CHAT_ID_GRUPPO")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
USERNAME = os.environ.get("DISCOGS_USERNAME")

SEEN_FILE = "notified_ids.json"
LOG_FILE = "discogs_stats.log"
STATS_CACHE_FILE = "stats_cache.json"

NOTIFIED_RETENTION_DAYS = 14  # dopo quanti giorni un ID notificato può essere dimenticato

# ================== BLACKLIST (release da ignorare) ==================
# Inserisci qui gli ID delle release che vuoi IGNORARE COMPLETAMENTE
# Li trovi nell'URL su Discogs: discogs.com/release/[QUESTO_NUMERO]...
BLACKLIST = [
    "1926862",
    "24289031",
    "32683710",
    "24289031",
    "32176203",
    "29078458",
    "25699975",
    "13393019",
    "15355417",
    "31967132",
    "7251704",
    "15111102",
    "3616613",
    "16190517",
    "24830360",
    "24830447",
    "26151683",
    "37811475",
    "37735470",
    "37734066",
    "37733295",
    "37402458",
    "37402317",
    "36377644",
    "7334987",
    "7393838",
    "11935046",
    "10824631",
    "11328672",
    "11327775",
    "11328417",
    "9294722",
    "11328413",
    "13591107",
    "4906061",
    "1502804",
    "2326719",
    "10549926",
    "11190722",
    "11328771",
    "7754493",
    "9294723",
    "9051625",
    "5656607",
    "9633848",
    "8625216",
    "24270317",
    "5167965",
    "11328410",
    "16827897",
    "4462645",
    "16827588",
    "16823001",
    "16826910",
    "16826571",
    "11251680",
    "16820928",
    "2155975",
    "8625216",
    "8625216",
    "3951390",
    "1497901",
    "32204835",
    "590728",
    "33739242",
    "1502825",
    "24500651",
    "24808826",
    "3434846",
    "4306963",
    "2336047",
    "16799832",
    "16778310",
    "2125001",
    "1497917",
    "1502819",
    "1497749",
    "6235393",
    "9854777",
    "1495887",
    "1494322",
    "1495895",
    "820771",
    "8477283",
    "11544012",
    "4812899",
    "1497800",
    "2625615",
    "1855878",
    "9576939",
    "1975203",
    "1498331",
]
# Set per lookup O(1) — la lista sopra resta invariata, si usa solo questo per i controlli
BLACKLIST_SET = set(BLACKLIST)

# ================== VARIABILI GLOBALI ==================
EMERGENCY_STOP = False
CHECK_IN_PROGRESS = False  # Impedisce check multipli

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3),
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
    except Exception as e:
        logger.error(f"❌ Errore invio Telegram: {e}")
        return False

# ================== GESTIONE ID NOTIFICATI (ANTI-SPAM) ==================
def load_notified():
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
    except Exception as e:
        logger.error(f"❌ Errore caricamento notified_ids: {e}")
    return set()

def prune_notified(notified_ids, days=NOTIFIED_RETENTION_DAYS):
    """Rimuove gli ID di notifica più vecchi di N giorni, per non far crescere il file all'infinito."""
    cutoff = datetime.now() - timedelta(days=days)
    pruned = set()
    for nid in notified_ids:
        try:
            date_str = nid.rsplit('_', 1)[-1]
            entry_date = datetime.strptime(date_str, '%Y%m%d')
            if entry_date >= cutoff:
                pruned.add(nid)
        except Exception:
            # formato inatteso: lo scarto invece di tenerlo per sempre
            continue
    removed = len(notified_ids) - len(pruned)
    if removed > 0:
        logger.info(f"🧹 Pulizia notified_ids: rimossi {removed} ID più vecchi di {days} giorni")
    return pruned

def save_notified(notified):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(notified), f, indent=2)
    except Exception as e:
        logger.error(f"❌ Errore salvataggio notified_ids: {e}")

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
            "User-Agent": "DiscogsStatsBot/11.0-FINAL"
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

def get_release_stats_stable(release_id, max_retries=3):
    """
    ✅ VERSIONE CON RATE LIMITING DINAMICO
    Ora autenticata (token Discogs anche sulla chiamata stats, non solo wantlist):
    le richieste autenticate hanno un limite più alto di quelle anonime, quindi
    dovrebbe aiutare proprio a ridurre i 429, non solo il rallentamento manuale.
    Il retry sui 429 è a ciclo invece che ricorsivo, per evitare chiamate annidate
    se capitano più 429 di fila.
    """
    global request_timestamps

    for attempt in range(max_retries):
        # 1. Pulisci i timestamp vecchi (più di 60 secondi)
        now = time.time()
        request_timestamps = [ts for ts in request_timestamps if now - ts < 60]

        # 2. Se abbiamo già fatto più di 50 richieste nell'ultimo minuto, aspetta
        if len(request_timestamps) >= 50:
            oldest = min(request_timestamps)
            wait_time = 60 - (now - oldest)
            if wait_time > 0:
                logger.warning(f"⏳ Rallento per {wait_time:.1f}s (già fatte {len(request_timestamps)} richieste)")
                time.sleep(wait_time)

        # 3. Registra questa richiesta
        request_timestamps.append(time.time())

        url = f"https://api.discogs.com/marketplace/stats/{release_id}"
        headers = {
            "Authorization": f"Discogs token={DISCOGS_TOKEN}",
            "User-Agent": "DiscogsStatsBot/11.0-FINAL"
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)

            # 4. Leggi il rate limit dalla risposta
            remaining = int(response.headers.get('X-Discogs-Ratelimit-Remaining', 60))
            used = int(response.headers.get('X-Discogs-Ratelimit-Used', 0))
            logger.info(f"   📊 Rate limit: {remaining} rimaste, {used} usate")

            # 5. Se siamo sotto 10, rallenta per il prossimo ciclo
            if remaining < 10:
                sleep_time = 5
                logger.warning(f"⚠️ Rate limit basso ({remaining}), aspetto {sleep_time}s extra")
                time.sleep(sleep_time)
            elif remaining < 20:
                time.sleep(2)
            else:
                time.sleep(1)

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
                logger.warning(f"⏳ 429, aspetto {retry_after}s (tentativo {attempt + 1}/{max_retries})")
                time.sleep(retry_after)
                continue

            else:
                logger.warning(f"⚠️ Status code inatteso {response.status_code} per {release_id}")
                break

        except Exception as e:
            logger.error(f"❌ Errore stats {release_id}: {e}")
            break

    return {'num_for_sale': 0, 'price': 'N/D', 'currency': ''}

# ================== MONITORAGGIO - VERSIONE CORRETTA CON NOTIFICHE ==================
def monitor_stats_stable():
    """Monitoraggio - VERSIONE CORRETTA con notifiche per aumenti"""
    global CHECK_IN_PROGRESS, EMERGENCY_STOP

    if CHECK_IN_PROGRESS:
        logger.warning("⏭️ Check già in corso, salto questo ciclo")
        return 0

    if EMERGENCY_STOP:
        logger.info("⏸️ Bot in stop, salto ciclo")
        return 0

    CHECK_IN_PROGRESS = True
    logger.info("📊 Monitoraggio (notifiche attive)...")

    try:
        wants = get_wantlist()
        if not wants:
            return 0

        stats_cache = load_stats_cache()
        notified_ids = load_notified()
        changes_detected = 0
        notifications_sent = 0

        # 30 release tutte CASUALI
        check_count = min(30, len(wants))

        try:
            releases_to_check = random.sample(wants, check_count)
        except ValueError:
            releases_to_check = wants

        logger.info(f"🔍 Controllo {len(releases_to_check)} release CASUALI...")

        for i, item in enumerate(releases_to_check):
            current_count = None  # reset esplicito ad ogni iterazione, usato solo per la pausa finale
            try:
                release_id = str(item.get('id'))
                if not release_id:
                    continue

                # 🔴🔴🔴 CONTROLLO BLACKLIST 🔴🔴🔴
                if release_id in BLACKLIST_SET:
                    logger.info(f"   ⏭️ Release {release_id} in blacklist, saltata")
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

                # 🔴 ANTI-SPAM: genera ID univoco per evitare notifiche doppie
                notification_id = f"{release_id}_{current_count}_{current_price}_{datetime.now().strftime('%Y%m%d')}"

                # 🔴 PRIMA RILEVAZIONE - apprendimento, nessuna notifica
                if previous_count == -1:
                    logger.info(f"   📝 APPRENDIMENTO: {current_count} copie (nessuna notifica)")

                # 🔴 NOTIFICHE SOLO PER AUMENTI REALI (e non già notificati)
                elif current_count > previous_count and notification_id not in notified_ids:
                    diff = current_count - previous_count
                    emoji = "🆕"
                    action = f"+{diff} NUOVE COPIE"

                    price_display = f"{current_currency} {current_price}" if current_price != 'N/D' else 'N/D'

                    msg = (
                        f"{emoji} <b>NUOVO ANNUNCIO RILEVATO!</b>\n\n"
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
                        notified_ids.add(notification_id)
                        logger.info(f"   🎯 NOTIFICA INVIATA: {action}")
                        time.sleep(1)

                # 🔴 DIMINUZIONI - nessuna notifica
                elif current_count < previous_count:
                    logger.info(f"   📉 Diminuzione copie: {previous_count} → {current_count} (nessuna notifica)")

                # 🔴 VARIAZIONI PREZZO - nessuna notifica
                elif current_price != previous_price:
                    logger.info(f"   💰 Variazione prezzo: {previous_price} → {current_price} (nessuna notifica)")

                # 🔴 STABILE
                elif current_count > 0:
                    logger.info(f"   ℹ️ Stabili: {current_count} copie (nessuna notifica)")

                # AGGIORNA CACHE (SEMPRE)
                if previous_count != current_count or previous_price != current_price:
                    stats_cache[release_id] = {
                        'num_for_sale': current_count,
                        'price': current_price,
                        'currency': current_currency,
                        'artist': artist,
                        'title': title,
                        'last_change': datetime.now().isoformat() if previous_count != -1 else None,
                        'first_seen': previous.get('first_seen', datetime.now().isoformat()),
                        'last_check': time.time()
                    }
                    logger.info(f"   💾 Cache aggiornata: {previous_count} copie → {current_count} copie")

            except Exception as e:
                logger.error(f"❌ Errore release {i+1}: {e}")

            # Pausa dinamica
            if current_count is not None and current_count > 0:
                time.sleep(random.uniform(0.8, 1.2))
            else:
                time.sleep(random.uniform(0.3, 0.6))

        notified_ids = prune_notified(notified_ids)
        save_stats_cache(stats_cache)
        save_notified(notified_ids)

        logger.info(f"✅ Rilevati {changes_detected} AUMENTI, {notifications_sent} notifiche inviate")
        return changes_detected

    except Exception as e:
        logger.error(f"❌ Errore in monitor_stats_stable: {e}")
        return 0
    finally:
        CHECK_IN_PROGRESS = False

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
    send_telegram("✅ Bot RIATTIVATO - Notifiche attive")
    return "<h1>✅ Bot riattivato</h1>", 200

# === ENDPOINT DI EMERGENZA RECUPERO ===
@app.route("/fix-now")
def fix_now():
    if CHECK_IN_PROGRESS:
        return "<h1>⏳ Check già in corso!</h1><p>Attendi il completamento.</p><a href='/'>↩️ Home</a>", 429

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
    check_status = "⏳ In corso" if CHECK_IN_PROGRESS else "✅ Libero"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📊 Discogs Monitor - VERSIONE FINALE</title>
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
            <h1>📊 Discogs Monitor - VERSIONE FINALE</h1>

            <div style="margin: 20px 0; text-align: center;">
                <span class="status" style="background: {'#28a745' if not EMERGENCY_STOP else '#dc3545'};">
                    {status}
                </span>
                <span class="status" style="background: {'#28a745' if not CHECK_IN_PROGRESS else '#ffc107'}; margin-left: 10px;">
                    {check_status}
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
                <p><strong>⚡ Rate Limiting:</strong> DINAMICO (autenticato)</p>
                <p><strong>✅ Stato:</strong> NOTIFICHE ATTIVE</p>
                <p><strong>🛡️ ANTI-SPAM:</strong> Attivo (nessuna notifica doppia, storico pulito ogni {NOTIFIED_RETENTION_DAYS} giorni)</p>
                <p><strong>🔒 Check multipli:</strong> Bloccati</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route("/", methods=['HEAD'])
def home_head():
    return "", 200

@app.route("/check")
def manual_check():
    if CHECK_IN_PROGRESS:
        return "<h1>⏳ Check già in corso!</h1><p>Attendi il completamento prima di farne un altro.</p><a href='/'>↩️ Home</a>", 429
    Thread(target=monitor_stats_stable, daemon=True).start()
    return "<h1>🚀 Monitoraggio avviato!</h1><p>✅ Notifiche ATTIVE</p><a href='/'>↩️ Home</a>", 200

@app.route("/check", methods=['HEAD'])
def check_head():
    return "", 200

@app.route("/reset")
def reset_cache():
    save_stats_cache({})
    save_notified(set())
    logger.warning("🔄 CACHE E STORICO NOTIFICHE RESETTATI!")
    return "<h1>🔄 Reset completo!</h1><p>Cache stats e storico notifiche puliti.</p><a href='/'>↩️ Home</a>", 200

@app.route("/reset", methods=['HEAD'])
def reset_head():
    return "", 200

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
    html += f"<p><i>✅ Notifiche ATTIVE per aumenti</i></p>"
    html += "<br><a href='/'>↩️ Home</a>"

    return html, 200

@app.route("/debug", methods=['HEAD'])
def debug_head():
    return "", 200

@app.route("/test")
def test_telegram():
    success = send_telegram(
        f"🧪 <b>Test - VERSIONE FINALE</b>\n\n"
        f"✅ Sistema attivo - NOTIFICHE FUNZIONANTI\n"
        f"👤 {USERNAME}\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )
    return "✅ Test inviato" if success else "❌ Errore", 200

@app.route("/test", methods=['HEAD'])
def test_head():
    return "", 200

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

@app.route("/health")
def health_check():
    return "OK", 200

@app.route("/health", methods=['HEAD'])
def health_head():
    return "", 200

# ================== MAIN LOOP ==================
def main_loop_stable():
    global CHECK_IN_PROGRESS, EMERGENCY_STOP
    time.sleep(10)
    while True:
        try:
            if not EMERGENCY_STOP and not CHECK_IN_PROGRESS:
                logger.info(f"\n{'='*70}")
                logger.info(f"🔄 Monitoraggio automatico - {datetime.now().strftime('%H:%M:%S')}")
                logger.info('='*70)

                monitor_stats_stable()
            elif CHECK_IN_PROGRESS:
                logger.info("⏳ Check manuale in corso, aspetto il prossimo ciclo")

            logger.info(f"💤 Pausa 5 minuti...")
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"❌ Loop error: {e}")
            time.sleep(60)

# ================== STARTUP ==================
if __name__ == "__main__":
    required = ["TELEGRAM_TOKEN", "CHAT_ID_GRUPPO", "DISCOGS_TOKEN", "DISCOGS_USERNAME"]
    missing = [var for var in required if not os.environ.get(var)]

    if missing:
        logger.error(f"❌ Variabili mancanti: {missing}")
        exit(1)

    logger.info('='*70)
    logger.info("📊 DISCOGS MONITOR - VERSIONE FINALE CON NOTIFICHE")
    logger.info('='*70)
    logger.info(f"👤 Utente: {USERNAME}")
    logger.info(f"⏰ Intervallo: {CHECK_INTERVAL//60} minuti")
    logger.info(f"🔍 Release/ciclo: 30")
    logger.info(f"🎲 Selezione: CASUALE")
    logger.info(f"⚡ Rate Limiting: DINAMICO (autenticato)")
    logger.info(f"✅ NOTIFICHE: ATTIVE per AUMENTI")
    logger.info(f"🛡️ ANTI-SPAM: ATTIVO")
    logger.info('='*70)

    send_telegram(
        f"📊 <b>Discogs Monitor - VERSIONE FINALE</b>\n\n"
        f"✅ <b>CONFIGURAZIONE:</b>\n"
        f"• 🎲 30 release CASUALI per ciclo\n"
        f"• ⏰ Controllo ogni 5 minuti\n"
        f"• ⚡ Rate limiting DINAMICO (autenticato)\n"
        f"• ✅ NOTIFICHE ATTIVE per aumenti\n"
        f"• 🛡️ ANTI-SPAM attivo\n\n"
        f"👤 {USERNAME}\n"
        f"📊 {len(get_wantlist())} articoli in wantlist\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )

    Thread(target=main_loop_stable, daemon=True).start()

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
