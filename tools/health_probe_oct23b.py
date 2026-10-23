"""Read-only probe (Oct 23, part 2): which stocks Yahoo returned nothing for in the daily price refresh, and why."""
import sys, time, functools, faulthandler, os, logging
sys.path.insert(0, ".")
print = functools.partial(print, flush=True)
faulthandler.dump_traceback_later(900, exit=True)
import pandas as pd
import congress_signals.engine as E
import yfinance as yf
print("yfinance", yf.__version__)
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
px = pd.read_pickle(E._p(cfg, "cache", "prices.pkl"))
print("prices", px.shape, px.index.max())
since = (px.index.max() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
cols = list(px.columns)
al = E._alpaca_close(cols, since, side_effects=False)
last_seen = px.apply(pd.Series.last_valid_index)
cut = px.index.max() - pd.Timedelta(days=30)
rest = [c for c in cols if c not in set(al.columns) and pd.notna(last_seen.get(c)) and last_seen[c] >= cut]
print("alpaca", al.shape[1], "rest for Yahoo", len(rest))
print("rest:", rest)
logging.getLogger("yfinance").setLevel(logging.ERROR)
for i in range(0, len(rest), 200):
    d = yf.download(rest[i:i+200], start=since, auto_adjust=True, progress=False, threads=True)
    got = d["Close"].dropna(how="all", axis=1).columns.tolist() if len(d) else []
    print(f"batch {i}: {len(got)} of {len(rest[i:i+200])} came back")
    miss = [t for t in rest[i:i+200] if t not in got]
    print("  missing:", miss)
    for t in miss[:30]:
        try:
            h = yf.Ticker(t).history(start=since, auto_adjust=True)
            print(f"   {t}: single {len(h)} rows; last seen in file {last_seen.get(t)}")
        except Exception as e:
            print(f"   {t}: {type(e).__name__} {str(e)[:150]}")
        time.sleep(0.5)
print("done")
