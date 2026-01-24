import os

# fallback manuale per evitare crash se Railway non passa la variabile
DISCOGS_USER = os.getenv("DISCOGS_USER") or "tuo_username_discogs"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCOGS_USER_TOKEN = os.getenv("DISCOGS_USER_TOKEN")


print("===== DEBUG ENV START =====")

for k, v in os.environ.items():
    if "DISCOGS" in k or "TELEGRAM" in k:
        print(f"{k} = {repr(v)}")

print("===== DEBUG ENV END =====")

time.sleep(30)
