"""Read-only probe for the Oct 22 health repair (short version; output flushed line by line): openFDA 'not found'
answers, USAspending connection errors, Wayback pacing, and a few of the House press sites that fail hourly."""
import sys, time, functools, faulthandler
sys.path.insert(0, ".")
import requests

print = functools.partial(print, flush=True)
faulthandler.dump_traceback_later(900, exit=True)
BOT = {"User-Agent": "CapitolCapital research (congress trade research; contact via GitHub e36meister/congress-signals)"}
T0 = time.time()
el = lambda: f"{time.time() - T0:5.0f}s"

print("=== openFDA", el())
for q in ['sponsor_name:"pfizer"', 'sponsor_name:"eli lilly"', 'sponsor_name:"unitedhealth group"', 'sponsor_name:"intuitive surgical"']:
    try:
        r = requests.get("https://api.fda.gov/drug/drugsfda.json", params={"search": q, "limit": 5}, headers=BOT, timeout=20)
        print(f"  {q}: {r.status_code} {r.text[:120]!r}")
    except Exception as e:
        print(f"  {q}: {type(e).__name__}")

print("=== USAspending", el())
body = lambda key: {"filters": {"recipient_search_text": [key], "award_type_codes": ["A", "B", "C", "D"],
                                "time_period": [{"start_date": "2024-01-01", "end_date": "2026-10-22"}]},
                    "fields": ["Action Date", "Transaction Amount", "Recipient Name"], "sort": "Transaction Amount",
                    "order": "desc", "limit": 100, "page": 1}


def one(k):
    t = time.time()
    try:
        r = requests.post("https://api.usaspending.gov/api/v2/search/spending_by_transaction/", json=body(k), timeout=30)
        return f"{r.status_code}/{time.time() - t:.0f}s"
    except Exception as e:
        return f"{type(e).__name__}/{time.time() - t:.0f}s"


keys = ["lockheed martin", "boeing", "raytheon", "general dynamics"]
print("  sequential:", [one(k) for k in keys])
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(4) as ex:
    print("  parallel 4:", list(ex.map(one, keys * 2)))

print("=== Wayback", el())
out = []
for i in range(6):
    t = time.time()
    try:
        r = requests.get("https://web.archive.org/web/2023id_/https://www.house.gov/", headers=BOT, timeout=20)
        out.append(f"{r.status_code}/{time.time() - t:.0f}s")
    except Exception as e:
        out.append(f"{type(e).__name__}/{time.time() - t:.0f}s")
    time.sleep(1)
print("  ", out)

print("=== House press sites (plain GET, 3 s apart)", el())
UA = {"User-Agent": "Mozilla/5.0 (compatible; CapitolCapital research; congress press releases)"}
for u in ["https://ansari.house.gov/media/press-releases", "https://casten.house.gov/media/press-releases",
          "https://castro.house.gov/media-center/press-releases", "https://takano.house.gov/newsroom/press-releases",
          "https://torres.house.gov/media-center/press-releases", "https://levin.house.gov/media/press-releases"]:
    t = time.time()
    try:
        r = requests.get(u, headers=UA, timeout=25)
        print(f"  {u}: {r.status_code} {len(r.content)}B {time.time() - t:.0f}s final={r.url} server={r.headers.get('server')}")
    except Exception as e:
        print(f"  {u}: {type(e).__name__} {time.time() - t:.0f}s")
    time.sleep(3)
print("done", el())
