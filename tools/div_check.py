"""Read-only: (1) how much of the backtest picks' returns came from dividends, (2) whether the Alpaca paper account
has ever been credited a dividend (prints counts only, no amounts or holdings)."""
import sys, os
sys.path.insert(0, ".")
import numpy as np, pandas as pd, requests
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "TIME_BUDGET_MIN": 0}
sc = pd.read_pickle("data/cache/scored.pkl")
acts = E.load_actions(cfg)
div = acts.get("div") if isinstance(acts, dict) else None
print("dividend table:", type(div), getattr(div, "shape", None))
px = pd.read_pickle("data/cache/prices.pkl")

picks, _ = E._select(sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()], "score", cfg["PICK_PERCENTILE"],
                     cfg["PICKS_PER_WEEK"], per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"),
                     skip_late=cfg.get("SKIP_LATE_FILINGS"), net_sell=cfg.get("NET_SELL_FILTER"))
hold = 60
rows = []
for _, r in picks.iterrows():
    t, d0 = r["ticker"], pd.Timestamp(r["entry_date"])
    if t not in px.columns:
        continue
    s = px[t].dropna()
    i = s.index.searchsorted(d0)
    if i + hold >= len(s):
        continue
    d1 = s.index[i + hold]
    dv = div[t].dropna() if t in div.columns else pd.Series(dtype=float)
    dv = dv[dv > 0].sort_index()
    # each payment as a % of that day's actual (unadjusted) price: adjusted prices are lowered by every later
    # dividend, so undo the later ones first (newest to oldest)
    fac, yl = 1.0, {}
    for exd, amt in zip(dv.index[::-1], dv.values[::-1]):
        pre = s.asof(exd - pd.Timedelta(days=1))
        if np.isfinite(pre) and pre > 0:
            raw_pre = pre / fac + amt
            yl[exd] = amt / raw_pre
            fac *= max(1e-6, 1 - amt / raw_pre)
    paid = [y for e, y in yl.items() if d0 < e <= d1]
    rows.append((t, d0, float(sum(paid)), len(paid)))
d = pd.DataFrame(rows, columns=["t", "d", "yield", "n"])
print(f"Picks checked: {len(d)}; paid a dividend during the 60-day hold: {(d['n'] > 0).mean():.0%}")
print(f"Average dividend per 60-day hold: {d['yield'].mean() * 100:.2f}% (about {d['yield'].mean() * 252 / 60 * 100:.2f}%/yr)")
print(f"Median per hold: {d['yield'].median() * 100:.2f}%; top 10% of picks: {d['yield'].quantile(.9) * 100:.2f}%")
print("S&P 500 (SPY) dividend yield for comparison: ~1.2-1.5%/yr")

k, s_ = os.environ.get("ALPACA_PAPER_KEY_ID"), os.environ.get("ALPACA_PAPER_SECRET_KEY")
if k and s_:
    h = {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s_}
    r = requests.get("https://paper-api.alpaca.markets/v2/account/activities", headers=h,
                     params={"activity_types": "DIV,DIVCGL,DIVCGS,DIVNRA,DIVROC,DIVTXEX", "page_size": 100}, timeout=60)
    print("Alpaca paper dividend credits ever:", r.status_code, len(r.json()) if r.status_code == 200 else r.text[:120])
    r = requests.get("https://paper-api.alpaca.markets/v2/account/activities", headers=h,
                     params={"activity_types": "FILL", "page_size": 100, "direction": "asc"}, timeout=60)
    if r.status_code == 200 and r.json():
        print("first fill date:", r.json()[0].get("transaction_time", "")[:10])
