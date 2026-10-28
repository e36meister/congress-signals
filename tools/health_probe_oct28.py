"""Oct 28 health check (read-only): stooq.com's CSV download now answers with an HTML page instead of prices.
Print that page's text, and check that the backups (Alpaca bars, Tiingo) return SPY history."""
import os, re, sys, time, requests
sys.path.insert(0, ".")
UA = {"User-Agent": "Mozilla/5.0"}
r = requests.get("https://stooq.com/q/d/l/?s=spy.us&i=d", headers=UA, timeout=30)
print("stooq body:", r.status_code, repr(r.text[:800]))
print("stooq text:", re.sub(r"<[^>]+>", " ", r.text)[:400])
r = requests.get("https://stooq.com/q/d/l/?s=spy.us&i=d&apikey=", headers=UA, timeout=30)
print("stooq with apikey param:", r.status_code, repr(r.text[:120]))
import pandas as pd
from congress_signals import engine as E
a = E._alpaca_close(["SPY"], "2013-12-23", side_effects=False)
print("alpaca SPY:", a.shape, a.index.min() if len(a) else None, a.index.max() if len(a) else None)
key = os.environ.get("TIINGO_API_KEY", "")
r = requests.get("https://api.tiingo.com/tiingo/daily/spy/prices", params={"startDate": "2013-12-23", "token": key}, timeout=60)
js = r.json() if r.ok else None
print("tiingo SPY:", r.status_code, (len(js), js[0]["date"], js[-1]["date"], list(js[0].keys())) if isinstance(js, list) and js else r.text[:200].replace(key, "***"))
