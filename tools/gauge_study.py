"""Oct 29 (read-only): the moving-to-safety gauge on the saved data. Monthly Congress-wide tilt between stocks and
bonds/Treasuries (dated by filing, so only what was public), the latest readings by group, and a test: does the
tilt known at a month's end say anything about the S&P 500 over the next 1 and 3 months? Nothing is written."""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E, congress_signals.gauge as G, congress_signals.other_assets as OA
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
print("scored rows:", len(sc), "columns with party/committees:", [c for c in ("who", "party", "committees") if c in sc])
oth = OA.load(cfg)
print("bond/other rows on file:", len(oth), dict(oth["kind"].value_counts()) if len(oth) else {})
d = G.trades(sc, oth)
print("gauge rows:", len(d), dict(d["side"].value_counts()))
print("bond-fund tickers seen:", d.loc[d["side"].eq("safety") & d["ticker"].notna(), "ticker"].value_counts().head(15).to_dict())
s = G.tilt_series(d)
pd.set_option("display.width", 200)
print(s.tail(30).round(3).to_string())
# S&P 500 next 1 and 3 months after each month-end reading
px = pd.read_pickle("data/cache/prices.pkl")
spy = px["SPY"] if isinstance(px, pd.DataFrame) and "SPY" in px else None
if spy is None and isinstance(px, dict):
    spy = px.get("SPY")
spy = pd.Series(spy).dropna().sort_index()
spy.index = pd.to_datetime(spy.index)
me = spy.resample("ME").last()
r1 = me.shift(-1) / me - 1
r3 = me.shift(-3) / me - 1
s2 = s.copy()
s2.index = s2.index + pd.offsets.MonthEnd(0)
s2["z"] = (s2["tilt"] - s2["tilt"].rolling(12, min_periods=6).mean()) / s2["tilt"].rolling(12, min_periods=6).std()
j = s2.join(pd.DataFrame({"r1": r1, "r3": r3}), how="inner").dropna(subset=["tilt", "r1"])
for col in ("tilt", "z"):
    for h in ("r1", "r3"):
        x = j[[col, h]].dropna()
        if len(x) > 12:
            c = x[col].corr(x[h])
            n_eff = len(x) / (3 if h == "r3" else 1)
            t = c * np.sqrt(max(n_eff - 2, 1) / max(1e-9, 1 - c * c))
            hi, lo = x[x[col] > x[col].quantile(0.8)][h].mean(), x[x[col] < x[col].quantile(0.2)][h].mean()
            print(f"{col} vs S&P {h}: corr {c:+.3f}, t~{t:+.2f} (n={len(x)}); top-fifth months {hi:+.2%}, bottom-fifth {lo:+.2%}")
ev, names = E.trading_pairs(sc)
pg = G.partner_groups(ev, names)
print("trading-partner groups:", len(pg), list(pg)[:8])
gs = G.groups(d, pg)
cut = d["filed_date"].max() - pd.Timedelta(days=90)
rows = []
for name, g in gs.items():
    g3 = g[g["filed_date"] > cut]
    if g3["who"].nunique() < 3:
        continue
    gross = g3["usd"].sum()
    tilt = (g3.loc[g3.side == "safety", "signed"].sum() - g3.loc[g3.side == "stock", "signed"].sum()) / gross if gross else np.nan
    rows.append((name, round(tilt, 3), g3["who"].nunique(), int((g3.side == "safety").sum()), round(gross / 1e6, 1)))
print("\nlast 90 days by group (tilt, members, safety trades, $M):")
for r in sorted(rows, key=lambda r: -abs(r[1]))[:40]:
    print("  ", r)
