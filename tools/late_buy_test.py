"""Read-only pick-then-test (owner, Nov 5): is a top-scored filing still worth buying 1-4 weeks after it was
disclosed (APP, VZ, FITB, MSFT were on the Buy list but past the 2-day buying window)?

Groups (main picks' rule, daily prices, buy at the open, sell at the open after the main hold, minus costs):
  picks     - the tool's own main picks (the backtest rule), bought k trading days late instead of on time
  cap-cut   - filings that cleared the bar but were left out by the 5-a-week / 2-per-member caps
  all       - both
Delays k: 0 (on time, for reference), 5, 10, 15, 20 trading days. Judged like every other idea: the delay is
chosen on 2016-2021 only and tested on 2022-now, plus walk-forward; a late buy has to beat the S&P clearly
(t >= 1.65 on the test years). Also split by how much the stock had already moved before the late buy.
Prints aggregates only; writes out/late_buy_test.json."""
import os, sys, json, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
pol = E.load_adaptive(cfg)["policy"]
hold = int(pol.get("hold_other", 60))
cost = cfg.get("COST_BPS", 0) / 1e4
buys = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()]
kw = dict(skip_late=cfg.get("SKIP_LATE_FILINGS"), net_sell=cfg.get("NET_SELL_FILTER"))
picks, _ = E._select(buys, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"], per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), **kw)
every, _ = E._select(buys, "score", cfg["PICK_PERCENTILE"], 10 ** 6, per_member=None, **kw)
key = lambda d: d["ticker"] + "|" + pd.to_datetime(d["filed_date"]).dt.strftime("%Y-%m-%d")
cut = every[~key(every).isin(set(key(picks)))]
G = pd.concat([picks.assign(grp="picks"), cut.assign(grp="cap-cut")]).reset_index(drop=True)
base = E.entry_basis(G)
G = G.drop(columns=[c for c in ("seen_at",) if c in G])
print(f"Hold {hold} trading days. Filings: {len(picks)} picks, {len(cut)} cut by the caps; filed "
      f"{G.filed_date.min():%Y-%m-%d} to {G.filed_date.max():%Y-%m-%d}")

K = (0, 5, 10, 15, 20)
for k in K:
    fr = E.forward_returns(G.assign(filed_date=base + pd.offsets.BDay(k)), px, hold, cost=cost)
    G[f"x{k}"] = fr["excess"].values
    G[f"c{k}"] = fr["closed"].values
    G[f"p{k}"] = fr["entry_px"].values
G["y"] = pd.to_datetime(G["filed_date"]).dt.year
G["q"] = pd.to_datetime(G["filed_date"]).dt.to_period("Q").astype(str)


def st(d, k):
    d = d[d[f"c{k}"].astype(bool) & d[f"x{k}"].notna()]
    if len(d) < 20:
        return None
    x = d[f"x{k}"]
    qm = x.groupby(d["q"]).mean()
    se = qm.std() / np.sqrt(len(qm)) if len(qm) > 2 else np.nan
    return {"n": int(len(x)), "ex": float(x.mean()), "t": float(x.mean() / se) if se else None,
            "med": float(x.median()), "beat": float((x > 0).mean())}


out = {"hold": hold, "groups": {}}
f = lambda s: "  n/a" if not s else f"{s['ex'] * 100:+.2f}% (t {s['t'] or np.nan:.1f}, median {s['med'] * 100:+.1f}%, beat S&P {s['beat'] * 100:.0f}%, n {s['n']})"
for gname in ("picks", "cap-cut", "all"):
    d = G if gname == "all" else G[G.grp == gname]
    tr, te = d[d.y < 2022], d[d.y >= 2022]
    out["groups"][gname] = {str(k): {"train": st(tr, k), "test": st(te, k)} for k in K}
    print(f"\n{gname}: average vs S&P per buy, {hold}-day hold")
    for k in K:
        print(f"  {'on time' if k == 0 else f'{k} days late':12s} 2016-21 {f(st(tr, k))}")
        print(f"  {'':12s} 2022-now {f(st(te, k))}")
    late = [k for k in K if k]
    best = max(late, key=lambda k: (st(tr, k) or {}).get("ex", -9))
    tb = st(te, best) or {}
    passed = bool(tb and tb["ex"] > 0 and (tb["t"] or 0) >= 1.65)
    out["groups"][gname]["chosen"] = {"k": best, "test": tb, "passes": passed}
    print(f"  -> chosen on 2016-2021: {best} days late; 2022-now {f(tb)}  =>  {'PASSES' if passed else 'fails'}")
    wf = []
    for y in range(2019, int(d.y.max()) + 1):
        past, cur = d[d.y < y], d[(d.y == y)]
        kk = max(late, key=lambda k: (st(past, k) or {}).get("ex", -9))
        c = cur[cur[f"c{kk}"].astype(bool) & cur[f"x{kk}"].notna()]
        wf.append(c.assign(g=c[f"x{kk}"])[["q", "g"]])
    W = pd.concat(wf)
    qm = W.groupby("q")["g"].mean()
    wt = W.g.mean() / (qm.std() / np.sqrt(len(qm)))
    out["groups"][gname]["walk_forward"] = {"ex": float(W.g.mean()), "t": float(wt), "n": int(len(W))}
    print(f"     walk forward 2019-now: {W.g.mean() * 100:+.2f}% (t {wt:.1f}, n {len(W)})")
    # already moved before the late buy: price at the late buy vs on time
    d2 = te[te[f"c{best}"].astype(bool) & te[f"x{best}"].notna() & te["p0"].notna()].copy()
    d2["run"] = d2[f"p{best}"] / d2["p0"] - 1
    for lab, m in (("fell before the late buy", d2.run < -0.02), ("about flat (within 2%)", d2.run.abs() <= 0.02),
                   ("rose 2%+ before the late buy", d2.run > 0.02)):
        s = st(d2[m], best)
        print(f"     2022-now, {lab:30s} {f(s)}")
os.makedirs("out", exist_ok=True)
json.dump(out, open("out/late_buy_test.json", "w"), indent=1, default=str)
