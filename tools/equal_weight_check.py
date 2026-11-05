"""Read-only (owner, Nov 5): yearly returns of the equal-weight S&P 500 (RSP) next to the S&P 500 (SPY), with
dividends, from Alpaca daily bars, to see how much of the account's gap is big stocks vs. typical stocks."""
import os, requests, pandas as pd
H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
def daily(sym):
    out, tok = [], None
    while True:
        p = {"timeframe": "1Day", "start": "2013-12-20", "end": "2026-11-04", "limit": 10000, "adjustment": "all", "feed": "sip"}
        if tok: p["page_token"] = tok
        js = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/bars", headers=H, params=p, timeout=60).json()
        out += js.get("bars") or []; tok = js.get("next_page_token")
        if not tok: break
    s = pd.Series({pd.Timestamp(b["t"][:10]): b["c"] for b in out})
    return s
r = pd.DataFrame({k: daily(k) for k in ("SPY", "RSP")}).dropna()
y = r.groupby(r.index.year).last()
prev = r.groupby(r.index.year).last().shift(1)
yr = (y / prev - 1).dropna()
for k, v in yr.iterrows():
    print(f"{k}: SPY {v.SPY * 100:+.1f}  RSP {v.RSP * 100:+.1f}")
n = (r.index[-1] - pd.Timestamp("2014-01-01")).days / 365.25
a = r[r.index >= "2014-01-01"]
print("2014-now /yr: SPY {:+.2f}  RSP {:+.2f}".format(((a.SPY.iloc[-1] / a.SPY.iloc[0]) ** (1 / n) - 1) * 100, ((a.RSP.iloc[-1] / a.RSP.iloc[0]) ** (1 / n) - 1) * 100))
