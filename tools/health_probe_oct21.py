"""Read-only probe for the Oct 21 health repair: Substack from GitHub, GDELT pacing, Google News RSS as a news
backup, and the stocks set aside for bad price data (compared with Alpaca)."""
import sys, os, json, time, glob
sys.path.insert(0, ".")
import requests
import pandas as pd

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36", "Accept": "application/rss+xml,application/xml,text/xml,*/*"}
BOT = {"User-Agent": "CapitolCapital research (congress trade research; contact via GitHub e36meister/congress-signals)"}


def show(label, url, h=None, **kw):
    try:
        r = requests.get(url, headers=h or BOT, timeout=30, **kw)
        print(f"  {label}: HTTP {r.status_code} {len(r.content)}B {r.headers.get('server','')} cf-mitigated={r.headers.get('cf-mitigated')} :: {r.text[:120]!r}")
        return r
    except Exception as e:
        print(f"  {label}: {type(e).__name__} {e}")


print("=== Substack")
for sub in ("jaredgolden", "senatorhickenlooper"):
    show(f"{sub} feed bot UA", f"https://{sub}.substack.com/feed")
    show(f"{sub} feed browser UA", f"https://{sub}.substack.com/feed", BROWSER)
    show(f"{sub} api archive", f"https://{sub}.substack.com/api/v1/archive?sort=new&limit=5", BROWSER)
    show(f"{sub} substack.com/api profile", f"https://substack.com/api/v1/publication/search?query={sub}&limit=1", BROWSER)
    show(f"{sub} rss2json", "https://api.rss2json.com/v1/api.json", BROWSER, params={"rss_url": f"https://{sub}.substack.com/feed"})
    time.sleep(2)

print("=== GDELT pacing (6 s apart)")
ok = 0
for i, q in enumerate(['"Nvidia"', '"Microsoft"', '"Lockheed Martin"', '"Pelosi" "Nvidia"', '"Palantir"', '"Boeing"']):
    r = show(f"gdelt {q}", "https://api.gdeltproject.org/api/v2/doc/doc", BOT,
             params={"query": q, "mode": "artlist", "maxrecords": 50, "timespan": "30d", "format": "json"})
    ok += bool(r is not None and r.status_code == 200)
    time.sleep(6)
print(f"  gdelt ok {ok}/6")

print("=== Google News RSS")
for q in ('"Nvidia"', '"Pelosi" "Nvidia"'):
    r = show(f"gnews {q}", "https://news.google.com/rss/search", BROWSER,
             params={"q": f"{q} when:30d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if r is not None and r.status_code == 200:
        print("   items:", r.text.count("<item>"))
    time.sleep(2)

print("=== Bad price stocks")
paths = glob.glob("data/**/bad_prices.json", recursive=True)
print("  files:", paths)
if paths:
    bad = json.load(open(paths[0]))
    print("  count", len(bad), pd.Series(list(bad.values())).value_counts().to_dict())
    print("  tickers:", sorted(bad)[:120])
    try:
        from congress_signals import engine as E
        sample = sorted(bad)[:40]
        a = E._alpaca_close(sample, "2016-01-01")
        print("  alpaca has", (0 if a is None or a.empty else len(a.columns)), "of", len(sample))
        if a is not None and not a.empty:
            for t in a.columns[:40]:
                s = a[t].dropna()
                s = s[s > 0]
                import numpy as np
                d = np.diff(np.log(s.values)) if len(s) > 2 else []
                print(f"   {t}: {bad.get(t)} | alpaca days {len(s)}, max 1-day move x{float(np.exp(np.abs(d).max())) if len(d) else 0:.2f}")
    except Exception as e:
        print("  alpaca compare failed:", type(e).__name__, e)
