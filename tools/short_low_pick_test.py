"""Nov 3 (read-only): the owner's proper pick-then-test check for the short idea "a member sells a stock that is
still near its low". Two Nov 3 studies found it on ALL years, so here the rule is re-chosen without the test years:

  Variants (36): low window 60-day / 52-week; within 2% / 5% / 10% of that low; judged the day before the regular
  entry (day 0) or 10 trading days later; held 60 / 125 / 250 trading days.
  Picks as the live tool would make them: member sales filed within 45 days, in filing order (higher short_score
  first on the same day), at most SHORTS_PER_WEEK (2) new shorts a week first come first served, never a second
  short on a ticker already shorted. Entry at the close after the check day.
  Costs: 0.10% each way plus 1%/yr to borrow the shares.
  Returns: hedged (short the stock, long SPY: the stock lagging SPY is profit) and plain (short only).

  1. Pick on 2014-2020 only -> the variant with the best hedged t on monthly means (100+ picks). Frozen, then tested
     on 2021 to now, which the choice never saw. Same check for "short every sale" with the same weekly cap.
  2. Walk forward: each year from 2019, choose the variant on all earlier years, use it that year only.
  3. Account view for the chosen variant: fixed-size slots (2 a week x hold / 5), daily P&L, yearly return and the
     worst drop, hedged and plain.
Caveat printed with the results: the idea itself came from studies that saw every year, so even the frozen test
years aren't fully fresh. No writes, no account data."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG}
PER_WEEK = int(cfg.get("SHORTS_PER_WEEK", 2))
COST = 2 * cfg.get("COST_BPS", 10) / 1e4
BORROW = 0.01
SPLIT = pd.Timestamp("2021-01-01")

sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl").sort_index()
sc["filed_date"] = pd.to_datetime(sc["filed_date"])
pf = px.ffill(limit=5)
P = pf.values
idx = px.index
n = len(idx)
col = {c: i for i, c in enumerate(px.columns)}
spy = pf["SPY"].values
lows = {60: pf.rolling(60, min_periods=45).min().values, 252: pf.rolling(252, min_periods=200).min().values}

s = sc[(sc["tx_type"] == "sell") & sc["entry_px"].notna() & (sc["lag_days"].fillna(999) <= 45)].copy()
ent0 = E._entry_positions(idx, E.entry_basis(s))
ci = s["ticker"].map(col)
ok = ci.notna().values & (ent0 > 260) & (ent0 < n - 1)
s, ent0, ci = s[ok].copy(), ent0[ok], ci[ok].astype(int).values
s["ent0"], s["ci"] = ent0, ci
if "short_score" not in s:
    s["short_score"] = 0.0
s = s.sort_values(["filed_date", "short_score"], ascending=[True, False], kind="stable").reset_index(drop=True)
print(f"member sales filed within 45 days, with prices: {len(s):,} ({s['filed_date'].min():%Y-%m} to "
      f"{s['filed_date'].max():%Y-%m}); weekly cap {PER_WEEK}; costs {COST*100:.2f}% + borrow {BORROW*100:.0f}%/yr")


def nw_t(k, lags):
    k = pd.Series(k).dropna().values.astype(float)
    m = len(k)
    if m < 6:
        return np.nan
    e = k - k.mean()
    v = e @ e / m
    for L in range(1, min(lags, m - 1) + 1):
        v += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / m
    return float(k.mean() / np.sqrt(v / m)) if v > 0 else np.nan


def pick(win, near, k, hold, use_filter=True):
    """The live tool's way: in filing order, 2 a week, no second short on an open ticker. -> picks DataFrame."""
    chk = s["ent0"].values + k - 1                       # judged at this close (day before the regular entry when k=0)
    e = chk + 1
    x = e + hold
    c = s["ci"].values
    good = (e < n)
    p_chk = P[np.minimum(chk, n - 1), c]
    lo = lows[win][np.minimum(chk, n - 1), c]
    flag = good & np.isfinite(p_chk) & np.isfinite(lo) & (p_chk <= lo * (1 + near)) if use_filter else good
    wk = s["filed_date"].dt.to_period("W-FRI").values
    open_until, taken, cur, out = {}, 0, None, []
    for i in np.flatnonzero(flag):
        if wk[i] != cur:
            cur, taken = wk[i], 0
        if taken >= PER_WEEK:
            continue
        t = c[i]
        if open_until.get(t, -1) >= e[i]:
            continue
        open_until[t] = x[i]
        taken += 1
        out.append(i)
    out = np.array(out, int)
    if not len(out):
        return pd.DataFrame()
    e_, x_, c_ = e[out], x[out], c[out]
    closed = x_ < n
    xi = np.where(closed, x_, n - 1)
    r = P[xi, c_] / P[e_, c_] - 1
    sr = spy[xi] / spy[e_] - 1
    days = (xi - e_).astype(float)
    fee = COST + BORROW * days / 252
    d = pd.DataFrame({"filed": s["filed_date"].values[out], "e": e_, "x": xi, "c": c_, "closed": closed,
                      "hedged": np.clip(-(r - sr), -3, 1) - fee, "plain": np.clip(-r, -3, 1) - fee})
    d["m"] = pd.to_datetime(d["filed"]).dt.to_period("M").astype(str)
    d["y"] = pd.to_datetime(d["filed"]).dt.year
    return d[d["closed"]]


def summ(d, part=None):
    if part is not None:
        d = d[part(d)]
    if len(d) < 20:
        return None
    t = nw_t(d.groupby("m")["hedged"].mean().sort_index(), 6)
    return {"n": len(d), "h": d["hedged"].mean(), "p": d["plain"].mean(), "t": t, "win": (d["hedged"] > 0).mean()}


def fmt(z):
    if z is None:
        return "too few"
    return (f"n {z['n']:4d}  hedged {z['h']*100:+6.2f}%/trade (t {z['t']:+4.1f}, wins {z['win']*100:3.0f}%)  "
            f"short-only {z['p']*100:+6.2f}%")


VARS = [(w, nr, k, h) for w in (60, 252) for nr in (0.02, 0.05, 0.10) for k in (0, 10) for h in (60, 125, 250)]
res = {v: pick(*v) for v in VARS}
base = {h: pick(60, 0, 0, h, use_filter=False) for h in (60, 125, 250)}
train = lambda d: pd.to_datetime(d["filed"]) < SPLIT
test = lambda d: pd.to_datetime(d["filed"]) >= SPLIT

print("\n=== 1. choose on 2014-2020 only")
print("variant (low window, within, check day, hold)    training years")
best, best_t = None, -np.inf
for v in VARS:
    z = summ(res[v], train)
    print(f"  {v[0]:3d}d low, {v[1]*100:2.0f}%, day {v[2]:2d}, hold {v[3]:3d}   {fmt(z)}")
    if z and z["n"] >= 100 and np.isfinite(z["t"]) and z["t"] > best_t:
        best, best_t = v, z["t"]
print(f"\nchosen without the test years: {best[0]}-day low, within {best[1]*100:.0f}%, day {best[2]}, hold {best[3]}")
for name, part in (("training 2014-2020", train), ("TEST 2021-now (never seen by the choice)", test)):
    print(f"  {name:42s} {fmt(summ(res[best], part))}")
print(f"  same weekly cap, short every sale, hold {best[3]}:")
for name, part in (("training 2014-2020", train), ("TEST 2021-now", test)):
    print(f"  {name:42s} {fmt(summ(base[best[3]], part))}")
print("\n  test years one by one (chosen variant | short every sale):")
for y in sorted(res[best]["y"].unique()):
    if pd.Timestamp(f"{y}-01-01") < SPLIT:
        continue
    a = res[best][res[best]["y"] == y]
    b = base[best[3]][base[best[3]]["y"] == y]
    print(f"    {y}: n {len(a):3d} hedged {a['hedged'].mean()*100:+6.2f}% plain {a['plain'].mean()*100:+6.2f}%"
          f"   |  n {len(b):3d} hedged {b['hedged'].mean()*100:+6.2f}% plain {b['plain'].mean()*100:+6.2f}%")

print("\n=== 2. walk forward: each year uses the variant that was best on all earlier years")
wf = []
for y in range(2019, pd.Timestamp.today().year + 1):
    cut = pd.Timestamp(f"{y}-01-01")
    bv, bt = None, -np.inf
    for v in VARS:
        d = res[v]
        z = summ(d[pd.to_datetime(d["filed"]) < cut])
        if z and z["n"] >= 80 and np.isfinite(z["t"]) and z["t"] > bt:
            bv, bt = v, z["t"]
    if bv is None:
        continue
    d = res[bv]
    a = d[d["y"] == y]
    wf.append(a)
    print(f"  {y}: uses {bv[0]}d low, {bv[1]*100:.0f}%, day {bv[2]}, hold {bv[3]}  ->  n {len(a):3d}  "
          f"hedged {a['hedged'].mean()*100:+6.2f}%  plain {a['plain'].mean()*100:+6.2f}%")
if wf:
    allwf = pd.concat(wf)
    print(f"  all walk-forward years: {fmt(summ(allwf))}")

print("\n=== 3. account view, chosen variant (fixed slots, daily P&L, not compounded)")
d = res[best]
slots = max(1, int(PER_WEEK * best[3] / 5))
for kind in ("hedged", "plain"):
    pnl = np.zeros(n)
    for e_, x_, c_ in zip(d["e"].values, d["x"].values, d["c"].values):
        seg = P[e_:x_ + 1, c_]
        sr = spy[e_:x_ + 1]
        dr = np.diff(seg) / seg[:-1]
        ds = np.diff(sr) / sr[:-1]
        day = -(dr - ds) if kind == "hedged" else -dr
        pnl[e_ + 1:x_ + 1] += np.nan_to_num(np.clip(day, -1, 1)) / slots
    ser = pd.Series(pnl, index=idx)
    ser = ser[ser.index >= d["filed"].min()]
    eq = ser.cumsum()
    yr = ser.groupby(ser.index.year).sum()
    dd = (eq - eq.cummax()).min()
    print(f"  {kind:7s} ({slots} slots): yearly " + ", ".join(f"{y} {v*100:+.1f}%" for y, v in yr.items())
          + f"; worst drop {dd*100:.1f}% of the short book")
print("\nCaveat: the idea came from studies that saw every year, so the test years aren't fully fresh; costs are"
      " estimates (hard-to-borrow stocks can cost far more than 1%/yr); shorting needs a margin account.")
