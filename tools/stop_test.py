"""Read-only, owner's question (Oct 29): is the 15% stop on main picks clearly better than no stop?

Same test the weekly check uses for every automatic switch, with the burden on the stop: it stays only if, against
no stop, it wins on the years before the last 3 (+1%/yr and sure enough) AND is also better over the last 3 years,
which played no part in choosing. Everything else is the current policy (hold-longer rule, sizing, signals).
Uses the saved scores and prices; nothing is saved back and no orders are placed. Prints aggregate results only."""
import sys, os
import pandas as pd

sys.path.insert(0, ".")
import run_daily as R
import congress_signals.engine as E

cfg = R.cfg
sc = pd.read_pickle(E._p(cfg, "cache", "scored.pkl"))
px = pd.read_pickle(E._p(cfg, "cache", "prices.pkl"))
E.register_opens(cfg, px)
pol = dict(E.load_adaptive(cfg)["policy"])
print(f"Current policy: price_exit={pol.get('price_exit')}, extend={pol.get('extend')}, hold={pol.get('hold_other')}/"
      f"{pol.get('hold_small')}, sizing={pol.get('sizing')}, signals_on={len(pol.get('signals_on', []))}, "
      f"lock={pol.get('price_exit_lock')}")
base = dict(cfg, _nested=True)
bw, sw = E.active_weights(cfg, signals_on=pol.get("signals_on", []))
sc = E.apply_scores(sc, cfg, buy_w=bw, short_w=sw)


def run(pe):
    return E.run_backtest(E.apply_hold_policy(sc, px, cfg, dict(pol, price_exit=pe)), px, base)


def risk(b):
    lg = b.get("longs")
    closed = lg[lg["closed"]] if lg is not None and len(lg) else lg
    return (float(b["perf"].loc["Per year vs S&P 500", "Long picks"]), float(b["perf"].loc["Worst drawdown", "Long picks"]),
            float(closed["ret"].min()), float((closed["ret"] <= -0.25).mean()), len(closed))


res = {pe: run(pe) for pe in ("stop15", "none")}
print("\n| Rule | Per year vs S&P | Worst drop | Worst trade | Trades down 25%+ | Closed picks |")
print("|---|---|---|---|---|---|")
for pe, b in res.items():
    v, dd, wt, big, n = risk(b)
    print(f"| {E.PRICE_EXITS[pe]} | {v * 100:+.2f}% | {dd * 100:.1f}% | {wt * 100:.1f}% | {big * 100:.1f}% | {n:,} |")

d_stop, d_none = E._long_daily(res["stop15"]), E._long_daily(res["none"])
c = E._compare(d_stop, d_none)
print("\nStop vs no stop (positive = the stop does better):")
if not c:
    print("  not enough data")
    sys.exit(1)
print(f"  all years: {c['gain'] * 100:+.2f}%/yr, sureness t {c['t']:.2f}")
print(f"  years before the last 3 (used to decide): {c['train_gain'] * 100:+.2f}%/yr, t {c['train_t']:.2f} "
      f"(needs >= +1.00%/yr and t >= {E.sure_bar(1):.2f})")
print(f"  last 3 years (held back to confirm): {c['recent'] * 100:+.2f}%/yr")
diff = (d_stop - d_none).dropna()
yr = diff.groupby(diff.index.year).sum()
print("  by year: " + ", ".join(f"{y}: {v * 100:+.1f}%" for y, v in yr.items()))
keep = E.clearly_better(c, 1)
print(f"\nVERDICT: {'KEEP the 15% stop (clearly better)' if keep else 'TURN OFF the 15% stop (not clearly better)'}")
os.makedirs("out", exist_ok=True)
open("out/stop_test.txt", "w").write(f"keep={keep}\n{c}\n")
