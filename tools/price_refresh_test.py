"""Read-only: time the recent-price refresh (Alpaca first, Yahoo for the rest) on a copy of the saved price table
with its last 5 trading days removed, then compare what came back with the saved values. Public market data only."""
import os, sys, shutil, time
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
import congress_signals.engine as E

src = "data/cache/prices.pkl"
work = "/tmp/pxtest"
shutil.rmtree(work, ignore_errors=True)
shutil.copytree("data/cache", f"{work}/cache")
px = pd.read_pickle(src)
cut = px.iloc[:-5]
cut.to_pickle(f"{work}/cache/prices.pkl")
os.utime(f"{work}/cache/prices.pkl", (time.time() - 86400 * 2,) * 2)
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": work, "TIME_BUDGET_MIN": 300, "TIINGO_API_KEY": ""}
t0 = time.time()
new = E.load_prices(cfg, list(px.columns))
print(f"Refresh took {time.time() - t0:.0f}s for {px.shape[1]} tickers")
days = px.index[-5:]
a, b = px.loc[days], new.reindex(days)[px.columns]
both = a.notna() & b.notna()
rel = (b / a - 1).abs()[both]
v = rel.stack().dropna()
print(f"Compared {int(both.values.sum())} prices on {len(days)} days: within 0.1% {(v < .001).mean():.1%}, within 1% {(v < .01).mean():.1%}")
miss = int((a.notna() & b.isna()).values.sum())
print(f"Saved price present but refresh returned nothing: {miss}")

# where do the differences come from?
from_al = set()
_orig = E._alpaca_close
bad = rel > .001
by_day = bad.sum(axis=1)
print("Differing prices by day:", {str(d.date()): int(n) for d, n in by_day.items()})
tick_bad = bad.any()
tb = list(tick_bad[tick_bad].index)
print(f"Tickers with any difference: {len(tb)}; examples:")
for t in tb[:12]:
    print(" ", t, "saved", [round(x, 2) if x == x else None for x in a[t]], "new", [round(x, 2) if x == x else None for x in b[t]])
r2 = E._alpaca_close(tb[:200], str(days[0].date()))
print(f"Alpaca carries {r2.shape[1]} of the first {min(200, len(tb))} differing tickers")

print(f"Alpaca dividends/splits in the window: {len(E._ALP_ACTED)} tickers; examples {sorted(E._ALP_ACTED.items())[:5]}")
import json as _j
q = _j.load(open(f"{work}/cache/price_refresh_queue.json")) if os.path.exists(f"{work}/cache/price_refresh_queue.json") else []
print(f"Still queued for re-download after this run: {len(q)}")
dv = pd.read_pickle(f"{work}/cache/dividends.pkl")
print("dividends.pkl newest ex-dates:", list(dv.dropna(how='all').index[-3:].date))
import broker
api = broker.Alpaca(broker.settings(60))
d = broker.dividend_estimate(api)
print("dividend estimate:", None if d is None else {"since": d["since"], "payouts": d["n"], "has_total": d["total"] > 0,
                                                    "received_any": d["received"] > 0})
