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
v = rel.stack()
print(f"Compared {int(both.values.sum())} prices on {len(days)} days: within 0.1% {(v < .001).mean():.1%}, within 1% {(v < .01).mean():.1%}")
miss = int((a.notna() & b.isna()).values.sum())
print(f"Saved price present but refresh returned nothing: {miss}")
