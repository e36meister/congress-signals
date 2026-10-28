"""Oct 28 health check (read-only): stooq.com now answers scripts with a JavaScript browser check, so the price
backups moved to Tiingo (SPY) and Alpaca (stocks Yahoo missed twice). Check both new paths return history."""
import sys
sys.path.insert(0, ".")
import pandas as pd
from congress_signals import engine as E
s = E._tiingo_close("SPY", "2013-12-23")
print("tiingo SPY:", None if s is None else (len(s), s.index.min().date(), s.index.max().date(), round(float(s.iloc[-1]), 2)))
a = E._alpaca_close(["SPY", "AAPL", "BRK-B", "NOSUCHTKR"], "2013-12-23", side_effects=False)
print("alpaca:", a.shape, list(a.columns), a.index.min().date(), a.index.max().date())
y = E._yf_close(["SPY"], "2026-09-01", tries=2)
if len(y) and s is not None:
    j = pd.concat([y["SPY"].rename("y"), s.rename("t")], axis=1).dropna()
    print("yahoo vs tiingo SPY since Sep 1: max diff %", round(float(((j.y / j.t - 1).abs()).max() * 100), 3), len(j))
