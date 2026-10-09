"""Read-only test of the institutional signals on the saved trades (nothing traded or saved).
For each signal: how many buys it touches, their average result vs the S&P, and the backtest with the signal on vs
off, judged the same way the tool's own weekly check does (earlier years to choose, last 3 years to confirm)."""
import sys, os, time
sys.path.insert(0, ".")
import numpy as np, pandas as pd
import congress_signals.engine as E
import congress_signals.institutions as I

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "INST_DIR": "./inst", "INST_PARTIAL_OK": True, "TIME_BUDGET_MIN": 0,
       "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", "")}
print("on file:", I.status(cfg))
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
t0 = time.time()
sc = I.inst_features(sc, cfg)
print(f"features in {time.time() - t0:.0f}s")
a = E.load_adaptive(cfg)
pol = a["policy"]
on_now = list(pol.get("signals_on", []))
print("trial signals on now:", on_now)

buys = sc[(sc["tx_type"] == "buy") & sc["excess"].notna()]
buys = buys.assign(excess=pd.to_numeric(buys["excess"], errors="coerce"))
print(f"\nBuys with a result: {len(buys):,}; average vs S&P {buys['excess'].mean() * 100:+.2f}% per hold")
NEW = ["activist_13d", "new_5pct", "funds_adding", "funds_leaving", "short_heavy", "short_jump"]
for k in NEW:
    m = buys[f"f_{k}"] > 0
    cover = {"short_heavy": "2018-02-01", "short_jump": "2018-02-01"}.get(k)
    base = buys[buys["filed_date"] >= cover] if cover else buys
    mm = base[f"f_{k}"] > 0
    print(f"  {k:14s}: {int(m.sum()):6,} buys ({mm.mean() * 100:4.1f}% of buys in its years); "
          f"with {base.loc[mm, 'excess'].mean() * 100:+6.2f}%  without {base.loc[~mm, 'excess'].mean() * 100:+6.2f}%  "
          f"(median {base.loc[mm, 'excess'].median() * 100:+.2f}% vs {base.loc[~mm, 'excess'].median() * 100:+.2f}%)")

print("\nshort days-to-cover spread among buys:", buys["short_dtc"].describe(percentiles=[.5, .75, .9, .95]).round(1).to_dict())
print("fund-count change spread among buys:", buys["funds_chg"].describe(percentiles=[.1, .25, .5, .75, .9]).round(3).to_dict())

base = dict(cfg, _nested=True)
vs = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Long picks"])


def run(sig):
    bw, sw = E.active_weights(cfg, signals_on=sig)
    return E.run_backtest(E.apply_hold_policy(E.apply_scores(sc, cfg, buy_w=bw, short_w=sw), px, cfg, pol), px, base)


t0 = time.time()
cur = run(on_now)
print(f"\nBacktest now: {vs(cur) * 100:+.2f}%/yr vs S&P ({time.time() - t0:.0f}s per backtest)")
res = {}
for k in NEW:
    alt = run(on_now + [k])
    c = E._compare(E._long_daily(alt), E._long_daily(cur))
    res[k] = c
    if not c:
        print(f"  {k}: not comparable")
        continue
    print(f"  {k:14s} on: {vs(alt) * 100:+.2f}%/yr | gain {c['gain'] * 100:+.2f}%/yr (sure {c['t']:.2f}); "
          f"earlier years {c['train_gain'] * 100 if c['train_gain'] is not None else float('nan'):+.2f}% "
          f"(sure {c['train_t'] if c['train_t'] is not None else float('nan'):.2f}); last 3 yrs {c['recent'] * 100:+.2f}% "
          f"-> {'CLEARLY BETTER' if E.clearly_better(c) else 'not clearly better'}")
good = [k for k in NEW if E.clearly_better(res.get(k))]
if len(good) > 1:
    alt = run(on_now + good)
    c = E._compare(E._long_daily(alt), E._long_daily(cur))
    print(f"\nAll winners together {good}: {vs(alt) * 100:+.2f}%/yr | gain {c['gain'] * 100:+.2f}%/yr, last 3 yrs {c['recent'] * 100:+.2f}%")
print("\nwinners:", good)
