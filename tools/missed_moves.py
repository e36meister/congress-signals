"""Read-only (owner, Nov 5): how MSFT, APP, VZ and FITB moved from the first open after each high-scoring filing
(when the tool would have bought) to the latest close, next to the S&P over the same days. Prices only."""
import sys, pandas as pd, numpy as np
sys.path.insert(0, ".")
import congress_signals.engine as E
px = pd.read_pickle("data/cache/prices.pkl").ffill()
O = E._opens(pd.read_pickle("data/cache/prices.pkl"))
print("latest price date", px.index.max().date())
rows = [("MSFT", "2026-09-14"), ("MSFT", "2026-10-01"), ("MSFT", "2026-10-13"), ("APP", "2026-10-13"), ("VZ", "2026-10-13"), ("FITB", "2026-10-16")]
col = {c: i for i, c in enumerate(px.columns)}
for t, f in rows:
    i = px.index.searchsorted(pd.Timestamp(f) + pd.Timedelta(days=1))
    o = O[i, col[t]] if O is not None and np.isfinite(O[i, col[t]]) else px[t].iloc[i]
    so = O[i, col["SPY"]] if O is not None and np.isfinite(O[i, col["SPY"]]) else px["SPY"].iloc[i]
    r, s = px[t].iloc[-1] / o - 1, px["SPY"].iloc[-1] / so - 1
    print(f"{t:5s} filed {f}  bought {px.index[i].date()} at {o:.2f}  now {px[t].iloc[-1]:.2f}  {r * 100:+.1f}%  S&P {s * 100:+.1f}%  vs S&P {(r - s) * 100:+.1f}")
