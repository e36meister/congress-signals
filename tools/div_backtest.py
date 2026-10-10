"""Read-only: what dividends do in the backtest.
 A. Every holding period with dividends (total return, as now) and without them (price only, for the picks and the
    S&P alike): does the best holding period change, and how much of each comes from dividends?
 B. Taxes: share of the dividends that would count as "qualified" (held 61+ days around the ex-date) for each
    holding period; qualified ones are taxed at the lower long-term rate.
 C. Dividend yield as a signal: did higher-yield purchases do better, and does scoring them up or down help?
Prints aggregates only."""
import sys, os, time
sys.path.insert(0, ".")
import numpy as np, pandas as pd
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "TIME_BUDGET_MIN": 0}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
px = px[[c for c in px.columns if c in set(sc["ticker"]) | set(E.BENCHMARK_ETFS) | {"SPY"}]]
div = E.load_actions(cfg)["div"]
a = E.load_adaptive(cfg)
pol = dict(a["policy"])
print("policy now:", {k: pol.get(k) for k in ("hold_other", "hold_small", "price_exit", "extend", "sizing", "signals_on")})

# ---------- price-only prices: undo the dividend adjustment ----------
t0 = time.time()
F = np.ones(px.shape, dtype=float)          # F[i, j]: product of the dividend factors after day i (adj = raw * F)
idx = px.index.values
dyield = {}                                  # ticker -> list of (ex row, ex date, yield on the actual price)
for j, t in enumerate(px.columns):
    if t not in div.columns:
        continue
    d = div[t].dropna()
    d = d[d > 0].sort_index()
    if not len(d):
        continue
    s = px[t].values
    g = np.ones(len(idx))
    fac = 1.0
    ys = []
    for exd, amt in zip(d.index[::-1], d.values[::-1]):
        k = np.searchsorted(idx, np.datetime64(exd), side="left")      # first trading day on/after the ex-date
        if k <= 0 or k >= len(idx):
            continue
        pre = s[k - 1]
        if not np.isfinite(pre) or pre <= 0:
            continue
        raw_pre = pre / fac + amt
        f = max(1e-6, 1 - amt / raw_pre)
        g[k] *= f
        fac *= f
        ys.append((k, exd, amt / raw_pre))
    # F[i] = product of g[k] for k > i
    rc = np.cumprod(g[::-1])[::-1]               # rc[i] = prod g[i:]
    F[:, j] = np.append(rc[1:], 1.0)
    dyield[t] = sorted(ys)
px_price = pd.DataFrame(px.values / F, index=px.index, columns=px.columns)
print(f"Price-only table built in {time.time() - t0:.0f}s; {len(dyield)} tickers paid dividends")
spy_tot = px["SPY"].dropna(); spy_pr = px_price["SPY"].dropna()
yrs = (spy_tot.index[-1] - spy_tot.index[0]).days / 365.25
print(f"S&P 500 per year: with dividends {((spy_tot.iloc[-1] / spy_tot.iloc[0]) ** (1 / yrs) - 1) * 100:.2f}%, "
      f"price only {((spy_pr.iloc[-1] / spy_pr.iloc[0]) ** (1 / yrs) - 1) * 100:.2f}%")

op_path = "data/cache/opens.pkl"
opens = pd.read_pickle(op_path).reindex(index=px.index, columns=px.columns) if os.path.exists(op_path) else None


def use(table, which):
    """register this table's opening prices (adjusted opens / same factor for the price-only table)"""
    E.register_opens(cfg, px)               # builds cleaned adjusted opens for px
    o = E._OPEN_REG.get("O")
    if o is None:
        return
    if which == "price":
        o = o / F
    E._OPEN_REG.update({"id": id(table), "shape": table.shape, "O": o})


bw, sw = E.active_weights(cfg)
scored = E.apply_scores(sc, cfg, buy_w=bw, short_w=sw)
base = dict(cfg, _nested=True)


def run(table, which, p):
    use(table, which)
    bt = E.run_backtest(E.apply_hold_policy(scored, table, cfg, p), table, base)
    perf = bt["perf"]
    return bt, float(perf.loc["Per year vs S&P 500", "Long picks"]), float(perf.loc["Annual return (CAGR)", "Long picks"])


print("\nA. Holding period with and without dividends (main picks; the S&P is measured the same way in each)")
print(f"{'hold':>10} | {'with dividends: /yr, vs S&P':>28} | {'price only: /yr, vs S&P':>26} | dividends add")
holds = [("now", None)] + [(f"{h} days", h) for h in (20, 40, 60, 90, 125, 180, 250)]
res = {}
for lab, h in holds:
    p = dict(pol) if h is None else dict(pol, hold_other=h, hold_small=h, extend="none", price_exit="none")
    bt_t, vs_t, py_t = run(px, "total", p)
    bt_p, vs_p, py_p = run(px_price, "price", p)
    res[lab] = (bt_t, bt_p, vs_t, vs_p)
    sys.stdout.flush()
    print(f"{lab:>10} | {py_t * 100:12.2f}% {vs_t * 100:+8.2f}% | {py_p * 100:11.2f}% {vs_p * 100:+8.2f}% | "
          f"{(py_t - py_p) * 100:+.2f}%/yr")
best_t = max((k for k in res if k != "now"), key=lambda k: res[k][2])
best_p = max((k for k in res if k != "now"), key=lambda k: res[k][3])
print(f"Best fixed hold vs the S&P: with dividends {best_t}, price only {best_p}")
c = E._compare(E._long_daily(res["now"][1]), E._long_daily(res["now"][0]))
if c:
    print(f"Current setting, price only vs with dividends: {c['gain'] * 100:+.2f}%/yr (sure {c['t']:.1f})")

print("\nB. Qualified dividends (held 61+ days within the 121 days around the ex-date) by holding period")
picks, _ = E._select(scored[(scored["tx_type"] == "buy") & scored["entry_px"].notna()], "score", cfg["PICK_PERCENTILE"],
                     cfg["PICKS_PER_WEEK"], per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"),
                     skip_late=cfg.get("SKIP_LATE_FILINGS"), net_sell=cfg.get("NET_SELL_FILTER"))
ent_rows = np.searchsorted(idx, picks["entry_date"].values.astype("datetime64[ns]"), side="left")
for h in (20, 40, 60, 90, 125, 250):
    tot = qual = 0.0
    n_pay = 0
    for t, e in zip(picks["ticker"].values, ent_rows):
        if t not in dyield or e >= len(idx):
            continue
        x = min(e + h, len(idx) - 1)
        d0, d1 = pd.Timestamp(idx[e]), pd.Timestamp(idx[x])
        for k, exd, y in dyield[t]:
            if e < k <= x:                       # owned at the close before the ex-date
                held = (min(d1, exd + pd.Timedelta(days=60)) - max(d0, exd - pd.Timedelta(days=60))).days
                tot += y
                qual += y if held > 60 else 0
                n_pay += 1
    print(f"  hold {h:>3} days: {n_pay:5d} payouts, dividends {tot / max(len(picks), 1) * 100:.2f}% per pick, "
          f"qualified {qual / tot * 100 if tot else 0:.0f}%")

print("\nC. Dividend yield at the time of the filing (last 12 months of dividends / price the day before)")
buys = scored[(scored["tx_type"] == "buy") & scored["excess"].notna()].copy()
fd = buys["filed_date"].values.astype("datetime64[ns]")
yv = np.zeros(len(buys))
pxp = px_price
col = {c: i for i, c in enumerate(pxp.columns)}
P = pxp.values
for i, (t, d) in enumerate(zip(buys["ticker"].values, fd)):
    ys = dyield.get(t)
    j = col.get(t)
    if not ys or j is None:
        continue
    r = np.searchsorted(idx, d, side="left") - 1          # last close before the filing day
    if r < 0:
        continue
    lo = d - np.timedelta64(365, "D")
    tot = 0.0
    for k, exd, y in ys:
        if lo <= np.datetime64(exd) < d:
            tot += y
    yv[i] = tot
buys["div_yield"] = yv
bins = [-1, 0, 0.01, 0.02, 0.035, 0.05, 1]
labels = ["none", "0-1%", "1-2%", "2-3.5%", "3.5-5%", "5%+"]
buys["yb"] = pd.cut(buys["div_yield"], bins=bins, labels=labels)
HO = pd.Timestamp.today() - pd.DateOffset(years=3)
g = buys.groupby("yb", observed=True).agg(n=("excess", "size"), avg=("excess", "mean"))
g2 = buys[buys["filed_date"] >= HO].groupby("yb", observed=True)["excess"].mean()
g1 = buys[buys["filed_date"] < HO].groupby("yb", observed=True)["excess"].mean()
for k, r in g.iterrows():
    print(f"  yield {k:>7}: {int(r['n']):6,} buys, vs S&P per hold {r['avg'] * 100:+.2f}% "
          f"(earlier years {g1.get(k, np.nan) * 100:+.2f}%, last 3 yrs {g2.get(k, np.nan) * 100:+.2f}%)")

sc2 = sc.copy()
yall = np.zeros(len(sc2))
m = sc2.index.isin(buys.index)
yall[m] = buys["div_yield"].reindex(sc2.index[m]).values
cur_bt = res["now"][0]
tests = {"payer": (yall > 0).astype(float), "yield_2pct": (yall >= 0.02).astype(float),
         "yield_3_5pct": (yall >= 0.035).astype(float)}
print("\n  As a score input (backtest with dividends; earlier years choose, last 3 confirm):")
use(px, "total")
for name, f in tests.items():
    for w in (0.3, -0.3):
        sc2[f"f_{name}"] = f
        E.TRIAL_SIGNALS[name] = w
        bw2, sw2 = E.active_weights(cfg, signals_on=list(pol.get("signals_on", [])) + [name])
        alt = E.run_backtest(E.apply_hold_policy(E.apply_scores(sc2, cfg, buy_w=bw2, short_w=sw2), px, cfg, pol), px, base)
        c = E._compare(E._long_daily(alt), E._long_daily(cur_bt))
        del E.TRIAL_SIGNALS[name]
        if c:
            print(f"    {name:12s} weight {w:+.1f}: gain {c['gain'] * 100:+.2f}%/yr (sure {c['t']:.2f}); earlier "
                  f"{c['train_gain'] * 100:+.2f}% (sure {c['train_t']:.2f}, bar {E.sure_bar(6):.2f}); last 3 yrs "
                  f"{c['recent'] * 100:+.2f}% -> {'CLEARLY BETTER' if E.clearly_better(c, 6) else 'not clearly better'}")
share = (buys["div_yield"] > 0).mean()
print(f"\n  Buys in dividend payers: {share * 100:.0f}%; median yield among payers {buys.loc[buys['div_yield'] > 0, 'div_yield'].median() * 100:.2f}%")
