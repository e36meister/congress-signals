"""Read-only study (owner, Nov 4): the "company is based in their state" signal, the tool's picks only (main picks
by the backtest's own rule, plus the small-company portfolio's buys), at holds of 20, 40, 60, 90, 120 and 250
trading days. Split: all, under $2B, $2B and up. Each group is shown next to the same picks without the signal.
Buys at the first open after the filing, sells at the open, minus the backtest's trading cost. Prints aggregates
only (the run log is public) and writes out/home_state_holds.json."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
buys = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()]
main, _ = E._select(buys, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"],
                    per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), skip_late=cfg.get("SKIP_LATE_FILINGS"),
                    net_sell=cfg.get("NET_SELL_FILTER"))
pol = E.load_adaptive(cfg)["policy"]
small = E.small_cap_rows(sc, bool(pol.get("sleeve_coverage")))
picks = pd.concat([main.assign(src="main"), small.assign(src="small")])
picks = picks[~picks.index.duplicated(keep="first")].copy()      # a buy picked by both counts once
picks["sig"] = picks["f_home_state"].fillna(0) > 0
picks["under2b"] = picks["f_small_cap"].fillna(0) >= 1
picks["q"] = pd.to_datetime(picks["filed_date"]).dt.to_period("Q")
print(f"Tool's picks: {len(picks)} ({(picks.src == 'main').sum()} main, {(picks.src == 'small').sum()} small-company); "
      f"with the home-state signal {int(picks.sig.sum())}; filed {picks.filed_date.min():%Y-%m-%d} to {picks.filed_date.max():%Y-%m-%d}")

H = (20, 40, 60, 90, 120, 250)
cost = cfg.get("COST_BPS", 0) / 1e4
for h in H:
    fr = E.forward_returns(picks, px, h, cost=cost)
    picks[f"r{h}"] = pd.to_numeric(fr["ret"], errors="coerce").values
    picks[f"x{h}"] = pd.to_numeric(fr["excess"], errors="coerce").values
    picks[f"c{h}"] = fr["closed"].fillna(False).astype(bool).values


def stats(d, h):
    d = d[d[f"c{h}"] & d[f"x{h}"].notna()]
    if len(d) < 5:
        return {"n": int(len(d))}
    k = d.groupby("q")[f"x{h}"].mean()
    se = k.std() / np.sqrt(len(k)) if len(k) > 2 else np.nan
    return {"n": int(len(d)), "ret": float(d[f"r{h}"].mean()), "ex": float(d[f"x{h}"].mean()),
            "med": float(d[f"x{h}"].median()), "win": float((d[f"x{h}"] > 0).mean()),
            "t": float(d[f"x{h}"].mean() / se) if se and np.isfinite(se) else None}


out = {"holds": list(H), "groups": {}}
for gname, gmask in (("all", slice(None)), ("under $2B", picks.under2b), ("$2B and up", ~picks.under2b)):
    g = picks[gmask] if not isinstance(gmask, slice) else picks
    out["groups"][gname] = {"with": [stats(g[g.sig], h) for h in H], "without": [stats(g[~g.sig], h) for h in H]}
    print(f"\n{gname}:  hold | WITH signal: n  avg  vs S&P  median  beat S&P  t | WITHOUT: n  vs S&P")
    for h, a, b in zip(H, out["groups"][gname]["with"], out["groups"][gname]["without"]):
        f = lambda v, p=True: "–" if v is None else (f"{v * 100:+.1f}%" if p else f"{v:.1f}")
        print(f"  {h:>3}d | {a['n']:4d} {f(a.get('ret'))} {f(a.get('ex'))} {f(a.get('med'))} "
              f"{"–" if a.get("win") is None else format(a["win"] * 100, ".0f") + "%"} {f(a.get('t'), False)} | {b['n']:4d} {f(b.get('ex'))}")
y = picks[picks.sig & picks["c60"]].groupby(pd.to_datetime(picks["filed_date"]).dt.year)["x60"].agg(["count", "mean"])
out["by_year_60"] = {int(k): {"n": int(v["count"]), "ex": float(v["mean"])} for k, v in y.iterrows()}
print("\nWith signal, 60-day vs S&P by filing year:", {k: f"{v['ex'] * 100:+.1f}% (n {v['n']})" for k, v in out["by_year_60"].items()})
os.makedirs("out", exist_ok=True)
json.dump(out, open("out/home_state_holds.json", "w"), indent=1)
