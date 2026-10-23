"""Read-only probe (Oct 23, part 3): why long-gone stocks (SGEN, SPLK, CA...) have a saved price on 2026-09-25."""
import sys, functools, json, os
sys.path.insert(0, ".")
print = functools.partial(print, flush=True)
import pandas as pd
import congress_signals.engine as E
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
px = pd.read_pickle(E._p(cfg, "cache", "prices.pkl"))
last = px.apply(pd.Series.last_valid_index)
on = last[last == pd.Timestamp("2026-09-25")].index.tolist()
print("stocks whose last price is 2026-09-25:", len(on))
for t in ["SGEN", "SPLK", "CA", "ANSS", "NKLA", "WBD", "QRVO", "ABB", "TEF", "FL"] :
    if t in px:
        s = px[t].dropna()
        print(t, "rows", len(s), "tail:", [(str(i.date()), round(float(v), 4)) for i, v in s.tail(4).items()])
# how many stocks have any value on 9-25 vs the days around it
for d in ["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-29"]:
    if pd.Timestamp(d) in px.index:
        print(d, int(px.loc[d].notna().sum()))
for f in ("no_price_tickers.json", "price_actions_seen.json"):
    p = E._p(cfg, "cache", f)
    if os.path.exists(p):
        j = json.load(open(p))
        print(f, len(j), {k: j[k] for k in list(j)[:5]} if isinstance(j, dict) else j[:5])
op = E._p(cfg, "cache", "opens.pkl")
print("done")
