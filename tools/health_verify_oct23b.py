"""Read-only check (Oct 23): with 'last day the price changed', which stocks would still go to Yahoo, and does
Yahoo answer for all of them."""
import sys, functools, logging
sys.path.insert(0, ".")
print = functools.partial(print, flush=True)
import pandas as pd
import congress_signals.engine as E
import yfinance as yf
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
px = pd.read_pickle(E._p(cfg, "cache", "prices.pkl"))
since = (px.index.max() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
al = E._alpaca_close(list(px.columns), since, side_effects=False)
def _last_move(col):
    v = col.dropna()
    if v.empty:
        return None
    ch = v[v.diff().fillna(1) != 0]
    return ch.index[-1] if len(ch) else v.index[0]
old = px.apply(pd.Series.last_valid_index)
new = px.apply(_last_move)
cut = px.index.max() - pd.Timedelta(days=30)
r_old = [c for c in px.columns if c not in set(al.columns) and pd.notna(old.get(c)) and old[c] >= cut]
r_new = [c for c in px.columns if c not in set(al.columns) and pd.notna(new.get(c)) and new[c] >= cut]
print("to Yahoo: before", len(r_old), "now", len(r_new))
gone = sorted(set(r_old) - set(r_new))
print("no longer asked:", len(gone), gone)
miss = []
for i in range(0, len(r_new), 200):
    d = yf.download(r_new[i:i+200], start=since, auto_adjust=True, progress=False, threads=True)
    got = set(d["Close"].dropna(how="all", axis=1).columns) if len(d) else set()
    miss += [t for t in r_new[i:i+200] if t not in got]
print("Yahoo gave nothing for", len(miss), miss)
print("done")
