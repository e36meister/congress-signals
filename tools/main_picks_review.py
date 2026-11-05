"""Read-only review (owner, Nov 5, before the live account): do the main picks really beat the S&P?

1. What the app shows: the What works numbers in the app's own data (Cloudflare copy of dashboard_data.json):
   backtest, never-seen-years test, small-company portfolio. Performance tables only, no account data.
2. Recomputed here from the saved scores and prices, the app's own way (run_backtest / small_cap_backtest: current
   hold policy, buy and sell at the open, costs, idle money in T-bills):
   - main picks per trade (policy holds) and at fixed holds 20/60/125/250, by period and year
   - main-pick portfolio vs S&P by year, all years and from 2022 / from the Nov 2023 holdout
   - small-company portfolio the same way
   - the account as run: 70% main / 30% small-company
Prints aggregates only; writes out/main_picks_review.json."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
import cloud_store

out = {}
pc = lambda v: "–" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v * 100:+.2f}%"

print("== 1. What the app shows (What works)")
try:
    d = json.loads(cloud_store.get("dashboard_data.json", log=print) or b"{}")
    for sec, key in (("backtest", "perf"), ("walk_forward", "perf"), ("small", "perf")):
        perf = (d.get(sec) or {}).get(key) or []
        print(f"  [{sec}] updated {(d.get(sec) or {}).get('updated')}")
        for p in perf:
            print("   ", p.get("name"), {k: (round(v, 4) if isinstance(v, (int, float)) else v) for k, v in p.items() if k != "name"})
        ts = (d.get(sec) or {}).get("trade_stats")
        if ts:
            print("    trade stats:", json.dumps(ts)[:900])
    pol = (d.get("adaptive") or {}).get("policy")
    print("  policy:", json.dumps(pol)[:600])
    out["app"] = {s: (d.get(s) or {}).get("perf") for s in ("backtest", "walk_forward", "small")}
except Exception as e:
    print("  couldn't read the app's data:", type(e).__name__, e)

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "_nested": True}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
pol = E.load_adaptive(cfg)["policy"]
cost = cfg.get("COST_BPS", 0) / 1e4
print(f"\n== 2. Recomputed (saved data; prices to {px.index.max():%Y-%m-%d}; idle money: {E.IDLE}; policy holds main "
      f"{pol.get('hold_other')}, small {pol.get('hold_sleeve')}; price_exit {pol.get('price_exit')}, extend {pol.get('extend')})")
bt = E.run_backtest(sc, px, cfg)
L = bt["longs"].copy()
L["y"] = pd.to_datetime(L["filed_date"]).dt.year
L["q"] = pd.to_datetime(L["filed_date"]).dt.to_period("Q").astype(str)


def tstat(x, grp):
    qm = x.groupby(grp).mean()
    se = qm.std() / np.sqrt(len(qm)) if len(qm) > 2 else np.nan
    return float(x.mean() / se) if se and np.isfinite(se) else None


def per_trade(D, col="excess", closed="closed"):
    c = D[D[closed].astype(bool) & D[col].notna()]
    if len(c) < 10:
        return None
    return {"n": int(len(c)), "avg": float(c[col].mean()), "med": float(c[col].median()), "beat": float((c[col] > 0).mean()),
            "t": tstat(c[col], c["q"])}


fmt = lambda s: "n/a" if not s else f"{pc(s['avg'])} (t {s['t'] or np.nan:.1f}, median {pc(s['med'])}, beat S&P {s['beat'] * 100:.0f}%, n {s['n']})"
print("\n-- main picks, per trade vs S&P (the app's holds, costs in)")
out["per_trade"] = {}
for lab, m in (("all years", L.y >= 0), ("2014-2021", L.y < 2022), ("2022-now", L.y >= 2022),
               ("holdout (filed Nov 2023 on)", pd.to_datetime(L.filed_date) >= E.HOLDOUT_START)):
    s = per_trade(L[m]); out["per_trade"][lab] = s
    print(f"  {lab:28s} {fmt(s)}")
print("  by year:", {int(y): f"{g[g.closed.astype(bool)].excess.mean() * 100:+.1f}% (n {int(g.closed.astype(bool).sum())})" for y, g in L.groupby("y")})
print("\n-- main picks at fixed holds (same picks)")
base = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()]
pk, _ = E._select(base, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"], per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"),
                  skip_late=cfg.get("SKIP_LATE_FILINGS"), net_sell=cfg.get("NET_SELL_FILTER"))
pk["y"] = pd.to_datetime(pk["filed_date"]).dt.year
pk["q"] = pd.to_datetime(pk["filed_date"]).dt.to_period("Q").astype(str)
out["fixed"] = {}
for h in (20, 60, 125, 250):
    fr = E.forward_returns(pk, px, h, cost=cost)
    D = pk[["y", "q"]].assign(excess=fr["excess"].values, closed=fr["closed"].values)
    a, b = per_trade(D[D.y < 2022]), per_trade(D[D.y >= 2022])
    out["fixed"][h] = {"2014-2021": a, "2022-now": b}
    print(f"  {h:>3} days: 2014-21 {fmt(a)}\n            2022-now {fmt(b)}")

curves = bt["curves"]
daily = curves.pct_change().fillna(0)
lp, spy = daily["Long picks"], daily["S&P 500 (SPY)"]
cp = daily.get("Copy every purchase")
print("\n-- main-pick portfolio (as the app runs it) vs S&P")
out["portfolio"] = {}
for lab, start in (("all years", None), ("2022-now", "2022-01-01"), ("holdout (Nov 2023 on)", E.HOLDOUT_START)):
    m = lp.index >= pd.Timestamp(start) if start else np.ones(len(lp), bool)
    a, lo, hi = E.excess_range(lp[m], spy[m])
    yrs = m.sum() / 252
    cagr = lambda r: float((1 + r[m]).prod() ** (1 / yrs) - 1)
    out["portfolio"][lab] = {"picks": cagr(lp), "spy": cagr(spy), "vs": a, "lo": lo, "hi": hi,
                             "copy_all": cagr(cp) if cp is not None else None}
    print(f"  {lab:24s} picks {pc(cagr(lp))}/yr, S&P {pc(cagr(spy))}/yr -> {pc(a)}/yr vs S&P (95% range {pc(lo)} to {pc(hi)})"
          + (f"; copying every purchase {pc(cagr(cp))}/yr" if cp is not None else ""))
yr = pd.DataFrame({"picks": lp, "spy": spy}).groupby(lp.index.year).apply(lambda g: (1 + g).prod() - 1)
out["portfolio_years"] = {int(y): {"picks": float(r.picks), "spy": float(r.spy)} for y, r in yr.iterrows()}
print("  by year (picks vs S&P):", {int(y): f"{r.picks * 100:+.1f} vs {r.spy * 100:+.1f}" for y, r in yr.iterrows()})
print(f"  average positions open: {bt.get('avg_open', 0):.0f} of {bt.get('slots')} slots")

print("\n-- small-company portfolio vs S&P")
sb = E.small_cap_backtest(sc, px, cfg)
sp = sb["curves"].pct_change().fillna(0)
small = sp["Small-company picks"]
for lab, start in (("all years", None), ("2022-now", "2022-01-01"), ("holdout (Nov 2023 on)", E.HOLDOUT_START)):
    m = small.index >= pd.Timestamp(start) if start else np.ones(len(small), bool)
    a, lo, hi = E.excess_range(small[m], sp["S&P 500 (SPY)"][m])
    print(f"  {lab:24s} {pc(a)}/yr vs S&P (95% range {pc(lo)} to {pc(hi)})")
S = sb["picks"].copy(); S["y"] = pd.to_datetime(S["filed_date"]).dt.year; S["q"] = pd.to_datetime(S["filed_date"]).dt.to_period("Q").astype(str)
print(f"  per trade ({sb['hold']}-day hold): 2014-21 {fmt(per_trade(S[S.y < 2022]))}\n                         2022-now {fmt(per_trade(S[S.y >= 2022]))}")

print("\n-- the account as run: 70% main picks / 30% small-company")
sh = float(os.environ.get("SMALL_SLEEVE_PCT") or 30) / 100
both = pd.concat([lp.rename("m"), small.rename("s"), spy.rename("spy")], axis=1).dropna()
acct = (1 - sh) * both.m + sh * both.s
out["account"] = {}
for lab, start in (("all years", None), ("2022-now", "2022-01-01"), ("holdout (Nov 2023 on)", E.HOLDOUT_START)):
    m = both.index >= pd.Timestamp(start) if start else np.ones(len(both), bool)
    a, lo, hi = E.excess_range(acct[m], both.spy[m])
    p, _ = E._perf(acct[m]); s, _ = E._perf(both.spy[m])
    out["account"][lab] = {"vs": a, "lo": lo, "hi": hi, "cagr": p["Annual return (CAGR)"], "spy": s["Annual return (CAGR)"],
                           "dd": p["Worst drawdown"], "spy_dd": s["Worst drawdown"]}
    print(f"  {lab:24s} {pc(p['Annual return (CAGR)'])}/yr vs S&P {pc(s['Annual return (CAGR)'])}/yr -> {pc(a)}/yr "
          f"(95% range {pc(lo)} to {pc(hi)}); worst drop {pc(p['Worst drawdown'])} vs S&P {pc(s['Worst drawdown'])}")
ya = pd.DataFrame({"acct": acct, "spy": both.spy}).groupby(both.index.year).apply(lambda g: (1 + g).prod() - 1)
out["account_years"] = {int(y): {"acct": float(r.acct), "spy": float(r.spy)} for y, r in ya.iterrows()}
print("  by year (account vs S&P):", {int(y): f"{r.acct * 100:+.1f} vs {r.spy * 100:+.1f}" for y, r in ya.iterrows()})
os.makedirs("out", exist_ok=True)
json.dump(out, open("out/main_picks_review.json", "w"), indent=1, default=str)
