"""Read-only: run every member-site press reader (python-statement) once, page 1, and report the ones that
don't work: error, no results, results with no usable links, or newest release older than 45 days.
For each bad one it also fetches the site's press page itself and prints the HTTP status, so we can tell
'site blocks us' from 'site was redesigned'. Prints a JSON summary at the end."""
import sys, os, re, json, time, datetime as dt, traceback
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
import requests
from python_statement import Scraper
from python_statement.config import SCRAPER_CONFIG

UA = {"User-Agent": "Mozilla/5.0 (compatible; CapitolCapital research; congress press releases)"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
old_cut = (dt.date.today() - dt.timedelta(days=45)).isoformat()


def one(name):
    t0 = time.time()
    try:
        res = Scraper.run_scraper(name, 1) or []
        err = None
    except Exception as e:
        res, err = [], f"{type(e).__name__}: {str(e)[:160]}"
    urls = [r.get("url") for r in res if r.get("url")]
    dates = sorted(str(r.get("date") or "")[:10] for r in res if r.get("date"))
    newest = dates[-1] if dates else None
    if err: why = "error"
    elif not res: why = "empty"
    elif not urls: why = "no links"
    elif newest and newest < old_cut: why = "stale"
    else: why = None
    return name, why, err, len(res), newest, round(time.time() - t0, 1)


def page_status(url):
    out = {}
    for lab, h in (("ours", UA), ("browser", BROWSER)):
        try:
            r = requests.get(url, headers=h, timeout=30, allow_redirects=True)
            out[lab] = f"{r.status_code} {len(r.text)}b -> {r.url}"
        except Exception as e:
            out[lab] = f"{type(e).__name__}"
    return out


names = sorted(SCRAPER_CONFIG)
print(f"{len(names)} readers", flush=True)
with ThreadPoolExecutor(max_workers=16) as ex:
    rows = list(ex.map(one, names))
bad = [r for r in rows if r[1]]
print(f"\n{len(bad)} not working:\n", flush=True)
summary = []
for name, why, err, n, newest, secs in bad:
    cfg = SCRAPER_CONFIG.get(name) or {}
    url = cfg.get("url_base") or cfg.get("url") or cfg.get("base_url") or ""
    if isinstance(url, (list, tuple)): url = url[0] if url else ""
    st = page_status(url) if url and url.startswith("http") else {}
    print(f"- {name}: {why} ({n} results, newest {newest}, {secs}s) method={cfg.get('method')} url={url}")
    if err: print(f"    error: {err}")
    for k, v in st.items(): print(f"    {k}: {v}")
    summary.append({"name": name, "why": why, "err": err, "n": n, "newest": newest, "method": cfg.get("method"),
                    "url": url, "status": st, "cfg": {k: v for k, v in cfg.items() if isinstance(v, (str, int, float))}})
print("\nJSON_SUMMARY " + json.dumps(summary))
