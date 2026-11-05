"""Read-only study (owner, Nov 5): "keep the S&P, lean toward the picks".

The S&P is roughly its ~100 biggest companies (S&P 100 fund, OEF) plus the rest. The picks beat typical stocks
but lost to the S&P when a few giants ran (2023-24). Test: an account that holds the giants through OEF and lets
the picks stand in for the rest:
    account = a x OEF + (1 - a) x picks           (rebalanced monthly; a = 0.5, 0.6, 0.7, 0.8)
Baseline with no picks: the same mix with the equal-weight S&P (RSP) in place of the picks; the gap between the two
is what the picks add. Picks: main picks, small-company, and the 70/30 account, each as the app's backtest runs
them (curves from run_backtest / small_cap_backtest: buy at the open, costs, idle money in T-bills).
Judged like every idea: a chosen on 2016-2021, tested on 2022-now and on the Nov 2023 holdout.
Prints aggregates only; writes out/sp_tilt_test.json."""
import os, sys, json, requests, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}


def daily(sym):
    out, tok = [], None
    while True:
        p = {"timeframe": "1Day", "start": "2015-12-01", "end": "2026-11-05", "limit": 10000, "adjustment": "all", "feed": "sip"}
        if tok:
            p["page_token"] = tok
        js = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/bars", headers=H, params=p, timeout=60).json()
        out += js.get("bars") or []
        tok = js.get("next_page_token")
        if not tok:
            break
    return pd.Series({pd.Timestamp(b["t"][:10]): b["c"] for b in out})


cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "_nested": True}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
bt = E.run_backtest(sc, px, cfg)
sb = E.small_cap_backtest(sc, px, cfg)
lp = bt["curves"]["Long picks"]
sp = sb["curves"]["Small-company picks"]
eq = pd.DataFrame({"main": lp, "small": sp}).dropna()
d = eq.pct_change().dropna()
d["account"] = 0.7 * d["main"] + 0.3 * d["small"]
etf = pd.DataFrame({k: daily(k) for k in ("SPY", "OEF", "RSP")}).dropna()
print(f"Fund prices {etf.index.min():%Y-%m-%d} to {etf.index.max():%Y-%m-%d}; picks to {eq.index.max():%Y-%m-%d}")
ME = "ME" if tuple(int(x) for x in pd.__version__.split(".")[:2]) >= (2, 2) else "M"
m_etf = etf.resample(ME).last().pct_change()
m_pk = (1 + d).resample(ME).prod() - 1
M = pd.concat([m_etf, m_pk], axis=1).dropna()
M = M[M.index >= "2016-01-31"]
print(f"Months: {len(M)} ({M.index.min():%Y-%m} to {M.index.max():%Y-%m})")


def stats(r, b):
    x = (r - b).dropna()
    if len(x) < 12:
        return None
    yrs = len(x) / 12
    ann = (1 + r).prod() ** (1 / yrs) - (1 + b).prod() ** (1 / yrs)
    se = E._nw_se(x.values, lags=3) * 12
    eqc = (1 + r).cumprod()
    return {"vs": float(ann), "lo": float(ann - 1.96 * se), "hi": float(ann + 1.96 * se), "t": float(ann / se) if se else None,
            "cagr": float((1 + r).prod() ** (1 / yrs) - 1), "dd": float((eqc / eqc.cummax() - 1).min()),
            "beat_years": f"{int(((1 + r).groupby(r.index.year).prod() > (1 + b).groupby(b.index.year).prod()).sum())}/{r.index.year.nunique()}"}


A = (0.5, 0.6, 0.7, 0.8)
periods = {"2016-2021": M.index < "2022-01-01", "2022-now": M.index >= "2022-01-01", "holdout Nov 2023-now": M.index >= "2023-11-01",
           "all": M.index >= "2016-01-01"}
pc = lambda v: "–" if v is None else f"{v * 100:+.2f}%"
out = {"months": len(M), "rows": {}}
print("\nEach line: account vs S&P per year (95% range, t) | worst drop | years ahead of the S&P")
for p_ in ("account", "main", "small"):
    for a in A:
        mix = a * M["OEF"] + (1 - a) * M[p_]
        base = a * M["OEF"] + (1 - a) * M["RSP"]
        row = {}
        for lab, msk in periods.items():
            s, b = stats(mix[msk], M["SPY"][msk]), stats(base[msk], M["SPY"][msk])
            add = stats(mix[msk], base[msk])
            row[lab] = {"mix": s, "no_picks": b, "picks_add": add}
        out["rows"][f"{p_}|{a}"] = row
        print(f"\n{p_} picks, {int(a * 100)}% S&P 100 fund / {int((1 - a) * 100)}% picks")
        for lab in periods:
            s, b, add = row[lab]["mix"], row[lab]["no_picks"], row[lab]["picks_add"]
            print(f"  {lab:21s} {pc(s['vs'])}/yr ({pc(s['lo'])} to {pc(s['hi'])}, t {s['t']:.1f}) | drop {pc(s['dd'])} | "
                  f"ahead {s['beat_years']} || same mix with RSP {pc(b['vs'])}/yr | picks add {pc(add['vs'])}/yr (t {add['t']:.1f})")
print("\nChosen on 2016-2021 (best vs S&P), then tested:")
out["chosen"] = {}
for p_ in ("account", "main", "small"):
    a = max(A, key=lambda a: out["rows"][f"{p_}|{a}"]["2016-2021"]["mix"]["vs"])
    r = out["rows"][f"{p_}|{a}"]
    t2, t3 = r["2022-now"]["mix"], r["holdout Nov 2023-now"]["mix"]
    passed = t2["vs"] > 0 and (t2["t"] or 0) >= 1.65
    out["chosen"][p_] = {"a": a, "passes": passed}
    print(f"  {p_:8s} a={a}: 2016-21 {pc(r['2016-2021']['mix']['vs'])}/yr | 2022-now {pc(t2['vs'])}/yr (t {t2['t']:.1f}) | "
          f"holdout {pc(t3['vs'])}/yr (t {t3['t']:.1f})  =>  {'PASSES' if passed else 'fails'}")
yr = lambda r: (1 + r).groupby(r.index.year).prod() - 1
best = out["chosen"]["account"]["a"]
Y = pd.DataFrame({"mix": yr(best * M["OEF"] + (1 - best) * M["account"]), "SPY": yr(M["SPY"]), "OEF": yr(M["OEF"]), "RSP": yr(M["RSP"]),
                  "account": yr(M["account"])})
print(f"\nBy year, account version a={best}:")
for y, r in Y.iterrows():
    print(f"  {y}: mix {r.mix * 100:+.1f}  S&P {r.SPY * 100:+.1f}  | S&P 100 fund {r.OEF * 100:+.1f}  equal-weight {r.RSP * 100:+.1f}  picks account alone {r.account * 100:+.1f}")
out["years"] = {int(y): {k: float(v) for k, v in r.items()} for y, r in Y.iterrows()}
os.makedirs("out", exist_ok=True)
json.dump(out, open("out/sp_tilt_test.json", "w"), indent=1, default=str)
