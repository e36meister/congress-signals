"""Read-only: the institutional flags as hard skips (a flagged buy is never picked; the next-best fills in),
judged like the tool's weekly check (earlier years choose, last 3 years confirm). Also the small-company sleeve."""
import sys, os
sys.path.insert(0, ".")
import numpy as np, pandas as pd
import congress_signals.engine as E
import congress_signals.institutions as I

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "INST_DIR": "./inst", "TIME_BUDGET_MIN": 0}
sc = I.inst_features(pd.read_pickle("data/cache/scored.pkl"), cfg)
px = pd.read_pickle("data/cache/prices.pkl")
pol = E.load_adaptive(cfg)["policy"]
on_now = list(pol.get("signals_on", []))
bw, sw = E.active_weights(cfg, signals_on=on_now)
scored = E.apply_scores(sc, cfg, buy_w=bw, short_w=sw)
base = dict(cfg, _nested=True)
vs = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Long picks"])


def run(skip):
    s = scored.copy()
    if skip:
        m = np.zeros(len(s), bool)
        for k in skip:
            m |= s[f"f_{k}"].values > 0
        s.loc[m & (s["tx_type"] == "buy").values, "score"] = 0.0
    return E.run_backtest(E.apply_hold_policy(s, px, cfg, pol), px, base)


cur = run([])
print(f"Now: {vs(cur) * 100:+.2f}%/yr vs S&P")
tests = [["activist_13d"], ["short_heavy"], ["short_jump"], ["funds_leaving"], ["new_5pct"],
         ["activist_13d", "short_heavy"], ["activist_13d", "short_heavy", "short_jump"]]
for sk in tests:
    alt = run(sk)
    c = E._compare(E._long_daily(alt), E._long_daily(cur))
    print(f"skip {'+'.join(sk):40s}: {vs(alt) * 100:+.2f}%/yr | gain {c['gain'] * 100:+.2f}% (sure {c['t']:.2f}); earlier "
          f"{c['train_gain'] * 100:+.2f}% (sure {c['train_t']:.2f}, bar {E.sure_bar(len(tests)):.2f}); last 3 yrs {c['recent'] * 100:+.2f}% "
          f"-> {'CLEARLY BETTER' if E.clearly_better(c, len(tests)) else 'not clearly better'}")

# how many of the actual picks carried each flag
picks, _ = E._select(scored[(scored["tx_type"] == "buy") & scored["entry_px"].notna()], "score", cfg["PICK_PERCENTILE"],
                     cfg["PICKS_PER_WEEK"], per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"),
                     skip_late=cfg.get("SKIP_LATE_FILINGS"), net_sell=cfg.get("NET_SELL_FILTER"))
picks = picks.assign(excess=pd.to_numeric(picks["excess"], errors="coerce"))
print(f"\nPicks: {len(picks)}, average vs S&P per hold {picks['excess'].mean() * 100:+.2f}%")
for k in ["activist_13d", "new_5pct", "funds_adding", "funds_leaving", "short_heavy", "short_jump"]:
    m = picks[f"f_{k}"] > 0
    print(f"  {k:14s}: {int(m.sum()):4} picks flagged, with {picks.loc[m, 'excess'].mean() * 100:+.2f}% without {picks.loc[~m, 'excess'].mean() * 100:+.2f}%")

