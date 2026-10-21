"""Read-only: which daily price source is most accurate - Yahoo, Alpaca or Tiingo?
Same stocks from all three (adjusted closes, 2016 on). Each day, a source is "outvoted" when its daily move differs
from the other two by more than 1 point while those two agree within 0.25 points. Also: on each set-aside stock's
flagged day (the 3x spike or jump), which sources show it.
Tiingo's free plan allows ~500 different stocks a month (the daily job uses up to 480 for delisted stocks), so this
uses at most 45."""
import sys, os, json, time
sys.path.insert(0, ".")
import numpy as np
import pandas as pd
import requests
import congress_signals.engine as E

KEY = os.environ.get("TIINGO_API_KEY", "").strip()
START = "2016-01-01"
bad = json.load(open("data/state/bad_prices.json"))
tx = pd.read_pickle("data/cache/all_tx.pkl")
cnt = tx["ticker"].value_counts()
disputed = sorted([t for t in bad if t in cnt.index], key=lambda t: -cnt[t])[:22]
normal = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "LMT", "BA", "DIS", "PFE", "T", "KO",
          "WMT", "INTC", "PLTR", "F", "CVX", "UNH", "COST", "NFLX", "BAC"]
tickers = (disputed + normal)[:45]
print("disputed:", disputed)
print("normal:", normal)


def yahoo(ts):
    import yfinance as yf
    d = yf.download(ts, start=START, auto_adjust=True, progress=False, threads=True)
    c = d["Close"] if isinstance(d.columns, pd.MultiIndex) else d[["Close"]].rename(columns={"Close": ts[0]})
    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
    return c


def tiingo(ts):
    out, n = {}, 0
    for t in ts:
        try:
            r = requests.get(f"https://api.tiingo.com/tiingo/daily/{t.replace('-', '.')}/prices",
                             params={"startDate": START, "token": KEY}, timeout=60)
            n += 1
            if r.status_code == 429:
                print("  Tiingo limit reached after", n)
                break
            js = r.json() if r.status_code == 200 else []
            if isinstance(js, list) and js:
                out[t] = pd.Series({pd.Timestamp(x["date"][:10]): x.get("adjClose") for x in js}).dropna()
        except Exception as e:
            print("  tiingo", t, type(e).__name__)
        time.sleep(1.5)
    return pd.DataFrame(out)


Y = yahoo(tickers)
A = E._alpaca_close(tickers, START, side_effects=False)
A.index = pd.to_datetime(A.index).normalize()
T = tiingo(tickers) if KEY else pd.DataFrame()
print(f"have: Yahoo {Y.shape[1]}, Alpaca {A.shape[1]}, Tiingo {T.shape[1]} of {len(tickers)}")

tot = {"Yahoo": 0, "Alpaca": 0, "Tiingo": 0}
days_all = 0
per = []
for t in tickers:
    srcs = {k: d[t].dropna() for k, d in (("Yahoo", Y), ("Alpaca", A), ("Tiingo", T)) if t in d.columns}
    srcs = {k: s[s > 0] for k, s in srcs.items() if len(s) > 60}
    if len(srcs) < 3:
        per.append({"t": t, "note": "only " + ",".join(srcs)})
        continue
    idx = srcs["Yahoo"].index.intersection(srcs["Alpaca"].index).intersection(srcs["Tiingo"].index)
    r = pd.DataFrame({k: np.log(s.reindex(idx)).diff() for k, s in srcs.items()}).dropna()
    ov = {}
    for k in r.columns:
        o = [c for c in r.columns if c != k]
        agree = (r[o[0]] - r[o[1]]).abs() <= 0.0025
        off = (r[k] - r[o].mean(axis=1)).abs() > 0.01
        ov[k] = int((agree & off).sum())
        tot[k] += ov[k]
    days_all += len(r)
    per.append({"t": t, "days": len(r), **ov, "disputed": t in bad})
print(f"\n=== Days a source was outvoted by the other two (of {days_all:,} stock-days)")
for k, v in tot.items():
    print(f"  {k}: {v:,} ({v / max(1, days_all) * 100:.3f}%)")
for grp, flag in (("normal", False), ("disputed", True)):
    g = [p for p in per if p.get("disputed") == flag and "days" in p]
    print(f"  {grp} stocks ({len(g)}): " + ", ".join(f"{k} {sum(p[k] for p in g)}" for k in tot))
print("\nper stock:")
for p in per:
    print("ROW", json.dumps(p))

print("\n=== Flagged day on set-aside stocks: who shows the big move")
for t in disputed:
    y = Y[t].dropna() if t in Y else pd.Series(dtype=float)
    y = y[y > 0]
    if len(y) < 30:
        print(f"  {t}: Yahoo has no data now")
        continue
    d = np.log(y).diff().abs()
    day = d.idxmax()
    line = f"  {t} ({bad[t]}): Yahoo biggest 1-day move since 2016 {np.exp(d[day]):.2f}x on {day.date()}"
    for k, D in (("Alpaca", A), ("Tiingo", T)):
        if t in D.columns:
            s = D[t].dropna()
            s = s[s > 0]
            if len(s) and s.index.min() <= day <= s.index.max():
                ds = np.log(s).diff().abs()
                w = ds[(ds.index >= day - pd.Timedelta(days=3)) & (ds.index <= day + pd.Timedelta(days=3))]
                line += f" | {k} {np.exp(w.max()):.2f}x" if len(w) else f" | {k} no days near it"
            else:
                line += f" | {k} no data that day"
        else:
            line += f" | {k} doesn't carry it"
    print(line)
