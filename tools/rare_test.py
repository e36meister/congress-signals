"""Read-only: runs the per-trade rare-signal test on the saved scores and prints every rare signal's result."""
import sys, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
for hold in (None, 250):
    res = E.rare_signal_test(sc, px, cfg, hold=hold)
    print(f"\n=== hold {res['hold']} trading days; rare = on <= 10% of timely purchases ===")
    f = lambda v, p=True: "–" if v is None else (f"{v * 100:+.2f}%" if p else f"{v:.1f}")
    for r in res["rows"]:
        print(f"{'PASS ' if r['passes'] else '     '}{r['signal']:<26} n {r['n']:>5}  lift {f(r['lift'])}  "
              f"earlier {f(r['lift_early'])} (t {f(r['t_early'], False)}, n {r['n_early']})  "
              f"last3y {f(r['lift_recent'])} (n {r['n_recent']})  {'[trial]' if r['trial'] else '[in use]'}")
