def bot_loop():
    send_telegram("🧪 Bot Discogs TEST (senza memoria)")

    d = init_discogs()
    user = d.user(DISCOGS_USER)

    wantlist = list(user.wantlist)
    release_ids = [w.release.id for w in wantlist]

    while True:
        print("👂 TEST – Controllo annunci...")

        for rid in release_ids:
            try:
                listings = get_latest_listings(rid)
                for listing in listings:
                    msg = (
                        f"🧪 TEST Annuncio\n\n"
                        f"📀 {listing['title']}\n"
                        f"💰 {listing['price']['value']} {listing['price']['currency']}\n"
                        f"🔗 {listing['uri']}"
                    )
                    send_telegram(msg)
                    time.sleep(1)

            except Exception as e:
                print(f"⚠️ Errore release {rid}: {e}")

        time.sleep(CHECK_INTERVAL)
