"""Read-only probe for the Oct 22 health repair: Bluesky re-read state and timing, the 23 member press sites that fail
hourly (read slowly, one at a time), openFDA 'not found' answers, USAspending connection errors, Wayback pacing."""
import sys, os, json, time
sys.path.insert(0, ".")
import requests

BOT = {"User-Agent": "CapitolCapital research (congress trade research; contact via GitHub e36meister/congress-signals)"}
T0 = time.time()
import faulthandler
faulthandler.dump_traceback_later(1500, exit=True)
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
POOL = ThreadPoolExecutor(4)


def el():
    return f"{time.time() - T0:6.0f}s"


print("=== openFDA", el())
for q in ['sponsor_name:"pfizer"', 'sponsor_name:"eli lilly"', 'sponsor_name:"unitedhealth group"', 'sponsor_name:"intuitive surgical"']:
    try:
        r = requests.get("https://api.fda.gov/drug/drugsfda.json", params={"search": q, "limit": 5}, headers=BOT, timeout=30)
        print(f"  {q}: {r.status_code} {r.text[:140]!r}")
    except Exception as e:
        print(f"  {q}: {type(e).__name__}")
    time.sleep(1)

print("=== USAspending", el())
body = lambda key: {"filters": {"recipient_search_text": [key], "award_type_codes": ["A", "B", "C", "D"],
                                "time_period": [{"start_date": "2024-01-01", "end_date": "2026-10-22"}]},
                    "fields": ["Action Date", "Transaction Amount", "Recipient Name"], "sort": "Transaction Amount",
                    "order": "desc", "limit": 100, "page": 1}
keys = ["lockheed martin", "boeing", "raytheon", "general dynamics", "northrop grumman", "palantir", "microsoft", "oracle"]
for mode in ("sequential", "parallel4"):
    ok = fail = 0
    errs = []
    t = time.time()

    def one(k):
        try:
            r = requests.post("https://api.usaspending.gov/api/v2/search/spending_by_transaction/", json=body(k), timeout=60)
            return r.status_code, r.headers.get("retry-after")
        except Exception as e:
            return type(e).__name__ + ":" + str(e)[:120], None
    if mode == "sequential":
        res = [one(k) for k in keys]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as ex:
            res = list(ex.map(one, keys * 3))
    print(f"  {mode}: {len(res)} calls in {time.time() - t:.0f}s: {res}")
    time.sleep(5)

print("=== Wayback pacing", el())
for gap in (0.5, 2.0):
    out = []
    for i in range(10):
        try:
            r = requests.get("https://web.archive.org/web/2023id_/https://www.house.gov/", headers=BOT, timeout=60,
                             allow_redirects=True)
            out.append(r.status_code)
        except Exception as e:
            out.append(type(e).__name__)
        time.sleep(gap)
    print(f"  gap {gap}s: {out}")

print("=== failing member press sites, one at a time, 4 s apart", el())
FAIL = "ansari auchincloss blakemoore casten castro correa foushee frost hoyle huffman levin mcgarvey mciver menendez morrison reschenthaler sarajacobs sykes takano thanedar tokuda torres watsoncoleman".split()
try:
    from python_statement import Scraper
    from python_statement.config import SCRAPER_CONFIG
    from congress_signals import press_sites as PS
    for n in FAIL:
        c = SCRAPER_CONFIG.get(n) or {}
        url = PS.BACKUP.get(n) or c.get("url_base") or c.get("url") or ""
        if isinstance(url, (list, tuple)):
            url = url[0] if url else ""
        t = time.time()
        fut = POOL.submit(lambda: Scraper.run_scraper(n, 1) or [])
        try:
            res = fut.result(timeout=90)
            a = f"lib {len(res)}"
        except FTimeout:
            a = "lib still running after 90s"
        except Exception as e:
            a = f"lib {type(e).__name__}: {str(e)[:80]}"
        t1 = time.time() - t
        try:
            r = requests.get(url, headers=PS.UA, timeout=30) if url else None
            b = f"GET {url} -> {r.status_code if r is not None else '-'} {len(r.content) if r is not None else 0}B final={r.url if r is not None else ''}"
        except Exception as e:
            b = f"GET {url} -> {type(e).__name__}"
        fut = POOL.submit(lambda: PS.read_site(url) if url else {})
        try:
            rs = fut.result(timeout=90)
            c2 = f"backup how={rs.get('how')} links={len(rs.get('links') or [])}"
        except FTimeout:
            c2 = "backup still running after 90s"
        except Exception as e:
            c2 = f"backup {type(e).__name__}"
        print(f"  {n:14s} {a} ({t1:.0f}s) | {b} | {c2}")
        time.sleep(4)
except Exception:
    import traceback; traceback.print_exc()


print("=== social state")
try:
    st = json.load(open("social/state.json"))
    import congress_signals.social as S
    print("  TAGGER_VERSION", S.TAGGER_VERSION, "bsky_v", st.get("bsky_v"), "tagger_v", st.get("tagger_v"),
          "keys", sorted(st)[:40])
except Exception as e:
    print("  state:", type(e).__name__, e)

print("=== Bluesky: 25 accounts incremental, timed")
try:
    import congress_signals.engine as E
    cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social", "TIME_BUDGET_MIN": 0}
    st = S._state(cfg)
    leg = S.legislators(cfg, st)
    acc = S.discover_accounts(cfg, leg, st, time.time() + 40)
    hmap = S.bluesky_accounts(cfg, leg, acc, st, time.time() + 40)
    print("  accounts", len(hmap))
    import os.path as P
    d = S._dir(cfg, "posts", "bsky")
    have = sum(1 for h in hmap if P.exists(P.join(d, __import__("re").sub(r"[^a-z0-9.-]", "_", h) + ".jsonl.gz")))
    print("  files on hand", have)
    sub = dict(sorted(hmap.items())[:25])
    tg = S.Tagger(cfg)
    t = time.time()
    st2 = dict(st)
    S.collect_bluesky(cfg, st2, tg, sub, time.time() + 120)
    print(f"  25 accounts took {time.time() - t:.1f}s (bsky_v now {st2.get('bsky_v')})")
except Exception as e:
    import traceback; traceback.print_exc()


print("done", el(), flush=True)
os._exit(0)
