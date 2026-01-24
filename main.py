import os
import time

print("===== DEBUG ENV START =====")

for k, v in os.environ.items():
    if "DISCOGS" in k or "TELEGRAM" in k:
        print(f"{k} = {repr(v)}")

print("===== DEBUG ENV END =====")

time.sleep(30)
