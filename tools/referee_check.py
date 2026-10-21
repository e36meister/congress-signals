"""Read-only: run the Tiingo referee on today's set-aside stocks and print each verdict (nothing is saved back;
the daily update repeats it for real)."""
import sys, os, json
sys.path.insert(0, ".")
import pandas as pd
import congress_signals.engine as E
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "TIINGO_API_KEY": os.environ.get("TIINGO_API_KEY", "")}
bad = json.load(open("data/state/bad_prices.json"))
px = pd.read_pickle("data/cache/prices.pkl")
tx = pd.read_pickle("data/cache/all_tx.pkl")
cnt = tx["ticker"].value_counts()
keep, settled, series = E.tiingo_referee(cfg, bad, px, priority=cnt.to_dict())
v = json.load(open("data/cache/tiingo_referee.json")) if os.path.exists("data/cache/tiingo_referee.json") else {}
for t in sorted(bad, key=lambda t: -cnt.get(t, 0)):
    print(f"VERDICT {t:6} trades {int(cnt.get(t, 0)):3}  {bad[t][:34]:34}  -> {(v.get(t) or {}).get('verdict', 'not checked (limit)')}")
print(f"settled {len(settled)} ({sum(1 for x in settled.values() if x == 'real')} real, {len(series)} repaired), still set aside {len(keep)}")
print("trades back in:", int(sum(cnt.get(t, 0) for t in settled)))
