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
    if t not in px.columns or div is None or t not in div:
        continue
    s = px[t].dropna()
    i = s.index.searchsorted(d0)
    if i + hold >= len(s):
        continue
    d1 = s.index[i + hold]
    dv = div[t] if isinstance(div, dict) else div.get(t)
    dv = pd.Series(dv) if not isinstance(dv, pd.Series) else dv
    dv.index = pd.to_datetime(dv.index)
    dv = dv.dropna()
    dv = dv[dv > 0]
    paid = dv[(dv.index > d0) & (dv.index <= d1)]
    # raw price at entry ~ adjusted close (dividends after entry make adjusted < raw, so this slightly overstates yield)
    rows.append((t, d0, float(paid.sum()) / float(s.iloc[i]) if len(paid) else 0.0, len(paid)))
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
