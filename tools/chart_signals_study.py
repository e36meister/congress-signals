"""Nov 3 (read-only): chart signals with the most outside evidence, tested on member trades (owner asked).
  1. 12-month momentum: the stock's return from 12 months to 1 month before entry, ranked against every stock with
     prices that day. Winner = top 30%, loser = bottom 30%.
  2. 52-week high closeness: price within 5% of its 1-year high (and the mirror for sells: within 5% of the low).
  3. Buy-the-dip timing: instead of buying the morning after the filing, wait up to 20 trading days for the price to
     dip 3% / 5% below that first entry; "else day 20" buys at day 20 when no dip comes, "dip only" skips it.
     Compared on the SAME exit date as the regular entry, so it's purely an entry-timing test.
Signals 1-2 use prices up to the day before entry only. Returns: close to close vs SPY, 20/60/125 trading days,
clipped like the other studies. Flagged minus unflagged trades filed the same month in the same sector, Newey-West t
on monthly means, earlier years vs last 3 years, a shuffled-within-month placebo for luck, supply per year.
Sells (short side, in the background): same signals, short return = SPY minus the stock. No writes."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

rng = np.random.default_rng(11)
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl").sort_index()
sc["filed_date"] = pd.to_datetime(sc["filed_date"])
pf = px.ffill(limit=5)
P = pf.values
n = len(px.index)
col = {c: i for i, c in enumerate(px.columns)}
spy = pf["SPY"].values
print("prices:", P.shape)
mom = (pf.shift(21) / pf.shift(252) - 1)
mom = mom.where(pf.shift(252).notna() & (pf.shift(21) > 1))
mom_rank = mom.rank(axis=1, pct=True).values
hi252 = pf.rolling(252, min_periods=200).max().values
lo252 = pf.rolling(252, min_periods=200).min().values
H = (20, 60, 125)
cut = (pd.Timestamp.today() - pd.DateOffset(years=3)).strftime("%Y-%m")


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


def ret(cc, a, b, sign):
    ok = (b < n) & (a < n)
    ai, bi = np.where(ok, a, 0), np.where(ok, b, 0)
    r = P[bi, cc] / P[ai, cc] - 1
    s = spy[bi] / spy[ai] - 1
    v = np.where(ok & np.isfinite(r) & np.isfinite(s) & (P[ai, cc] > 0), sign * (r - s), np.nan)
    return np.clip(v, -1, 3)


def build(tx_type):
    t = sc[(sc["tx_type"] == tx_type) & sc["entry_px"].notna() & (sc["lag_days"].fillna(999) <= 45)].copy()
    ent = E._entry_positions(px.index, E.entry_basis(t))
    ci = t["ticker"].map(col)
    ok = ci.notna().values & (ent < n - 1) & (ent > 252)
    t, ent, ci = t[ok].copy(), ent[ok], ci[ok].astype(int).values
    sign = 1.0 if tx_type == "buy" else -1.0
    d0 = ent - 1                                        # known the evening before entry
    t["mrank"] = mom_rank[d0, ci]
    p0 = P[d0, ci]
    if tx_type == "buy":
        t["mom"] = t["mrank"] >= 0.7
        t["anti"] = t["mrank"] <= 0.3
        t["near_hi"] = p0 >= 0.95 * hi252[d0, ci]
    else:   # for a short, the trend is with you when the stock is a loser / near its low
        t["mom"] = t["mrank"] <= 0.3
        t["anti"] = t["mrank"] >= 0.7
        t["near_hi"] = p0 <= 1.05 * lo252[d0, ci]
    t["both"] = t["mom"] & t["near_hi"]
    t["m"] = t["filed_date"].dt.to_period("M").astype(str)
    t["cell"] = t["m"] + "|" + t["sector"].fillna("?").astype(str)
    for h in H:
        t[f"x{h}"] = ret(ci, ent, ent + h, sign)
    # buy-the-dip timing (for sells: wait for a bounce up before shorting)
    pe = P[ent, ci]
    for pctd in (0.03, 0.05):
        lvl = pe * (1 - sign * pctd)
        dip = np.full(len(t), -1)
        for j in range(1, 21):
            pj = P[np.minimum(ent + j, n - 1), ci]
            hit = (dip < 0) & (ent + j < n) & np.isfinite(pj) & ((pj <= lvl) if sign > 0 else (pj >= lvl))
            dip[hit] = ent[hit] + j
        tag = f"d{int(pctd*100)}"
        t[tag + "_hit"] = dip > 0
        for h in H:
            ex = ent + h
            alt = np.where(dip > 0, dip, ent + 20)
            v = ret(ci, alt, ex, sign)
            v = np.where(alt < ex, v, np.nan)            # hold must still be open at the later entry
            t[f"{tag}_else_x{h}"] = v - t[f"x{h}"].values
            t[f"{tag}_only_x{h}"] = np.where(dip > 0, v - t[f"x{h}"].values, np.nan)
    return t


def lift(df, flag, h, part=None):
    c = f"x{h}"
    g = df[df[c].notna()]
    f = np.asarray(flag, bool)[df[c].notna().values]
    if part is not None:
        keep = part(g).values
        g, f = g[keep], f[keep]
    base = g[~f].groupby("cell")[c].mean()
    x = g[f].join(base.rename("base"), on="cell").dropna(subset=["base"])
    if len(x) < 30:
        return None, None, len(x)
    dd = x[c] - x["base"]
    return float(dd.mean()), nw_t(dd.groupby(x["m"]).mean().sort_index(), int(np.ceil(h / 21))), len(x)


def timing(df, colname, part=None):
    g = df[df[colname].notna()]
    if part is not None:
        g = g[part(g)]
    if len(g) < 30:
        return None, None, len(g)
    return float(g[colname].mean()), nw_t(g.groupby("m")[colname].mean().sort_index(), 3), len(g)


def report(tx_type):
    df = build(tx_type)
    word = "BUYS" if tx_type == "buy" else "SELLS (short side)"
    print(f"\n\n######## {word}: {len(df):,} trades, {df['m'].min()} to {df['m'].max()}; last-3-years cut {cut}")
    print("baseline avg excess: " + ", ".join(f"{h}d {df[f'x{h}'].mean()*100:+.2f}%" for h in H))
    early, recent = (lambda g: g["m"] < cut), (lambda g: g["m"] >= cut)
    f = lambda z: "      n/a       " if z[0] is None else f"{z[0]*100:+6.2f}% t{z[1]:+5.1f}"
    names = {"mom": "12-mo winner" if tx_type == "buy" else "12-mo loser",
             "anti": "12-mo loser" if tx_type == "buy" else "12-mo winner",
             "near_hi": "near 52w high" if tx_type == "buy" else "near 52w low",
             "both": "both"}
    print("\nsignal          hold  flagged%   vs same month+sector (all / earlier / last 3y)")
    cells = []
    for k, nm in names.items():
        for h in H:
            a, e_, r_ = lift(df, df[k], h), lift(df, df[k], h, early), lift(df, df[k], h, recent)
            cells.append((k, h, a, e_, r_))
            print(f"{nm:15s} {h:4d}d  {df[k].mean()*100:6.1f}%   {f(a)}  {f(e_)}  {f(r_)}  n={a[2]:,}")
    real = sum(1 for _, _, a, e_, r_ in cells if a[1] is not None and abs(a[1]) >= 2 and e_[0] is not None
               and r_[0] is not None and np.sign(e_[0]) == np.sign(r_[0]) == np.sign(a[0]))
    counts = []
    for _ in range(40):
        hits = 0
        for k, h, *_ in cells:
            fl = df.groupby("m")[k].transform(lambda s: pd.Series(rng.permutation(s.values), index=s.index))
            a = lift(df, fl, h)
            if a[1] is not None and abs(a[1]) >= 2:
                hits += 1
        counts.append(hits)
    print(f"luck: {real} of {len(cells)} pass (|t|>=2, same sign early and recent); by chance avg "
          f"{np.mean(counts):.1f} (95th pct {np.percentile(counts, 95):.0f})")
    print("per year, last 3 years: " + ", ".join(f"{names[k]} {int(df.loc[recent(df), k].sum() / 3):,}"
                                                  for k in names))
    what = "dip" if tx_type == "buy" else "bounce"
    print(f"\nentry timing: wait up to 20 days for a {what}, same exit date (gain vs buying the morning after)")
    for tag in ("d3", "d5"):
        print(f"  {tag[1]}% {what}: comes within 20 days for {df[tag + '_hit'].mean()*100:.0f}% of trades")
        for kind in ("else", "only"):
            for h in H:
                c_ = f"{tag}_{kind}_x{h}"
                print(f"    {('wait, else day 20' if kind == 'else' else 'wait, skip if none'):18s} {h:4d}d  "
                      f"{f(timing(df, c_))}  {f(timing(df, c_, early))}  {f(timing(df, c_, recent))}")


report("buy")
report("sell")
