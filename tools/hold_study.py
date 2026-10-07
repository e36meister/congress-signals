"""Read-only study for the owner's question: why did top-scored picks make good 1-year holds, and does that still
hold now? Uses the saved scores and prices from the last full update (no new data, no trading). Prints only
aggregate results (no account data), since the run log is public."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
buys = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()]
picks, _ = E._select(buys, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"],
                     per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), skip_late=cfg.get("SKIP_LATE_FILINGS"),
                     net_sell=cfg.get("NET_SELL_FILTER"))
print(f"Main picks (the backtest's own rule): {len(picks)}")
H = (20, 60, 125, 250, 375)
for h in H:
    fr = E.forward_returns(picks, px, h)
    picks[f"x{h}"], picks[f"c{h}"] = fr["excess"].values, fr["closed"].values
picks["q"] = pd.to_datetime(picks["filed_date"]).dt.to_period("Q")
picks["y"] = pd.to_datetime(picks["filed_date"]).dt.year


def agg(d, col):
    d = d[d[col].notna()]
    if len(d) < 10:
        return "n/a"
    k = d.groupby("q")[col].mean()
    se = k.std() / np.sqrt(len(k)) if len(k) > 2 else np.nan
    return f"{d[col].mean() * 100:+.2f}% (t {d[col].mean() / se if se else np.nan:.1f}, n {len(d)})"


print("\nAverage vs S&P per pick, by holding period (t uses filing-quarter clusters):")
for h in H:
    d = picks[picks[f"c{h}"]]
    print(f"  {h:>3} days: all {agg(d, f'x{h}')} | before 2022 {agg(d[d.y < 2022], f'x{h}')} | 2022 on {agg(d[d.y >= 2022], f'x{h}')}")

d = picks[picks["c250"]].copy()
d["later"] = (1 + d["x250"]) - (1 + d["x60"])           # approx: what days 60-250 added vs the S&P
d["ahead60"] = np.where(d["x60"] > 0, "ahead at day 60", "behind at day 60")
print("\nDays 60 to 250, split by how the pick was doing at day 60 (does an early trend predict the rest?):")
for k, g in d.groupby("ahead60"):
    print(f"  {k}: {agg(g, 'later')}")
d["band"] = pd.qcut(d["prank"].rank(method="first"), 3, labels=["lower third of picks", "middle", "top third"]) if "prank" in d else "all"
print("\nDays 60 to 250 by entry score within the picks:")
for k, g in d.groupby("band", observed=True):
    print(f"  {k}: {agg(g, 'later')}")
print("\nBy filing year: 60-day vs 250-day vs S&P, and what days 60-250 added:")
for y, g in d.groupby("y"):
    print(f"  {y}: n {len(g):3d}  60d {g.x60.mean() * 100:+6.2f}%  250d {g.x250.mean() * 100:+6.2f}%  added {g.later.mean() * 100:+6.2f}%")
# market backdrop: small vs large, growth of the whole market in each year

print("\nTrend rule detail (days 60-250 added), before 2022 vs 2022 on:")
for k, g in d.groupby("ahead60"):
    print(f"  {k}: before 2022 {agg(g[g.y < 2022], 'later')} | 2022 on {agg(g[g.y >= 2022], 'later')}")
d["rule"] = np.where(d["x60"] > 0, d["x250"], d["x60"])       # keep if ahead at day 60, else sell at 60
for lab, g in (("all", d), ("before 2022", d[d.y < 2022]), ("2022 on", d[d.y >= 2022])):
    print(f"  {lab}: always 60d {g.x60.mean() * 100:+.2f}% | always 250d {g.x250.mean() * 100:+.2f}% | "
          f"keep-if-ahead {g.rule.mean() * 100:+.2f}%  (n {len(g)})")
# a stricter version: ahead by 5%+
for th in (0.0, 0.05, 0.10):
    r = np.where(d["x60"] > th, d["x250"], d["x60"])
    print(f"  keep if ahead by more than {th * 100:.0f}% at day 60: {np.mean(r) * 100:+.2f}%  (kept {np.mean(d['x60'] > th) * 100:.0f}%)")
# per-year for keep-if-ahead minus always-60
print("  by year, keep-if-ahead minus always-60:", ", ".join(f"{y}: {(g.rule - g.x60).mean() * 100:+.1f}" for y, g in d.groupby("y")))
