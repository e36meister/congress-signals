"""Nov 3 (read-only): "late entry on momentum" (owner's idea). For member trades filed in the last ~3 months,
if the stock is still climbing some days after the filing, is buying THEN worth it? Sells in the background: if the
stock is still falling after a member's sale, would shorting then have paid?

Setup, for every buy (and sell) filed within 45 days of the trade, with prices:
  - check days k = 10, 20, 40, 60 trading days after the first possible entry (all inside ~3 months).
  - "climbing" rules, judged on closes up to day k only (no look-ahead), test picks the best:
      ma50     close above its 50-day average and the average higher than 10 days earlier
      up5/up10 up 5% / 10% since the first possible entry, and the close above the close 10 days before
      high60   close within 1% of its 60-day high
      combo    ma50 and up5 and high60
    For sells the mirror ("still falling"): below a falling 50-day average, down 5%/10%, near a 60-day low.
  - entry at the close of day k+1 (the morning run would buy at that open; close is the stand-in here), forward
    return over 20/60/125 trading days minus SPY over the same days, clipped like the other studies.
Comparisons (each flagged late entry minus the average of the same group's unflagged late entries at the same k,
filed the same month): does the rule pick the better late entries? And flagged late entries vs buying fresh
(the regular day-after-filing entry, same month): is a late momentum entry as good as a fresh pick?
Newey-West t on monthly means; placebo flags (same rate, shuffled within month) to show how many of the
rule x k x hold cells would pass by chance. Earlier years vs last 3 years. Supply per year.
No writes, no account data."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

rng = np.random.default_rng(7)
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl").sort_index()
sc["filed_date"] = pd.to_datetime(sc["filed_date"])
pf = px.ffill()
P = pf.values
col = {c: i for i, c in enumerate(px.columns)}
spy = pf["SPY"].values
n = len(px.index)
ma50 = pf.rolling(50, min_periods=40).mean().values
hi60 = pf.rolling(60, min_periods=40).max().values
lo60 = pf.rolling(60, min_periods=40).min().values

K = (10, 20, 40, 60)
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


def build(tx_type):
    t = sc[(sc["tx_type"] == tx_type) & sc["entry_px"].notna() & (sc["lag_days"].fillna(999) <= 45)].copy()
    ent = E._entry_positions(px.index, E.entry_basis(t))
    ci = t["ticker"].map(col)
    ok = ci.notna().values & (ent < n)
    t, ent, ci = t[ok], ent[ok], ci[ok].astype(int).values
    sign = 1.0 if tx_type == "buy" else -1.0           # a short profits when the stock lags SPY
    rows = []
    for k in [0] + list(K):
        d = ent + k                                    # judged at this day's close
        e = d + 1                                      # entered at the next day's close
        good = e < n
        rec = {"ix": t.index[good], "k": k, "m": t["filed_date"].dt.to_period("M").astype(str).values[good],
               "y": t["filed_date"].dt.year.values[good], "ticker": t["ticker"].values[good]}
        dd, ee, cc, en = d[good], e[good], ci[good], ent[good]
        px_d, px_e, px_0 = P[dd, cc], P[ee, cc], P[en, cc]
        px_10 = P[np.maximum(dd - 10, 0), cc]
        ma, ma_10 = ma50[dd, cc], ma50[np.maximum(dd - 10, 0), cc]
        since = px_d / px_0 - 1
        if tx_type == "buy":
            rec["ma50"] = (px_d > ma) & (ma > ma_10)
            rec["up5"] = (since >= 0.05) & (px_d > px_10)
            rec["up10"] = (since >= 0.10) & (px_d > px_10)
            rec["high60"] = px_d >= 0.99 * hi60[dd, cc]
        else:
            rec["ma50"] = (px_d < ma) & (ma < ma_10)
            rec["up5"] = (since <= -0.05) & (px_d < px_10)
            rec["up10"] = (since <= -0.10) & (px_d < px_10)
            rec["high60"] = px_d <= 1.01 * lo60[dd, cc]
        rec["combo"] = rec["ma50"] & rec["up5"] & rec["high60"]
        for h in H:
            x = ee + h
            closed = x < n
            xi = np.where(closed, x, 0)
            r = P[xi, cc] / px_e - 1
            s = spy[xi] / spy[ee] - 1
            v = np.where(closed & np.isfinite(r) & np.isfinite(s) & (px_e > 0), sign * (r - s), np.nan)
            rec[f"x{h}"] = np.clip(v, -1, 3)
        rows.append(pd.DataFrame(rec))
    return pd.concat(rows, ignore_index=True)


RULES = ("ma50", "up5", "up10", "high60", "combo")


def lift(df, k, rule, h, part=None, vs_fresh=False, flag=None):
    c = f"x{h}"
    g = df[(df["k"] == k) & df[c].notna()]
    if part is not None:
        g = g[part(g)]
    f = (g[rule].values if flag is None else flag).astype(bool)
    if vs_fresh:
        z = df[(df["k"] == 0) & df[c].notna()]
        if part is not None:
            z = z[part(z)]
        base = z.groupby("m")[c].mean()
    else:
        base = g[~f].groupby("m")[c].mean()
    x = g[f].join(base.rename("base"), on="m").dropna(subset=["base"])
    if len(x) < 30:
        return None, None, len(x)
    dlt = x[c] - x["base"]
    return float(dlt.mean()), nw_t(dlt.groupby(x["m"]).mean().sort_index(), int(np.ceil(h / 21))), len(x)


def report(tx_type):
    df = build(tx_type)
    word = "climbing" if tx_type == "buy" else "falling (short side)"
    print(f"\n\n######## {tx_type.upper()}S: late entry when still {word} ########")
    print(f"trades: {df[df.k == 0].shape[0]:,}; months {df['m'].min()} to {df['m'].max()}; last-3-years cut {cut}")
    fresh = {h: df.loc[(df.k == 0), f"x{h}"].mean() for h in H}
    print("fresh entry (day after filing), avg excess vs SPY: " + ", ".join(f"{h}d {fresh[h]*100:+.2f}%" for h in H))
    early = lambda g: g["m"] < cut
    recent = lambda g: g["m"] >= cut
    cells, best = [], []
    for rule in RULES:
        for k in K:
            g = df[df.k == k]
            rate = g[rule].mean()
            for h in H:
                a = lift(df, k, rule, h)
                if a[0] is None:
                    continue
                e_ = lift(df, k, rule, h, early)
                r_ = lift(df, k, rule, h, recent)
                fr = lift(df, k, rule, h, vs_fresh=True)
                cells.append((rule, k, h, rate, a, e_, r_, fr))
    print("\nrule    k  hold  flagged%   vs unflagged late (all / earlier / last 3y)         vs fresh entry")
    for rule, k, h, rate, a, e_, r_, fr in cells:
        f = lambda z: "      n/a       " if z[0] is None else f"{z[0]*100:+6.2f}% t{z[1]:+5.1f}"
        print(f"{rule:7s}{k:3d} {h:4d}d  {rate*100:6.1f}%   {f(a)}  {f(e_)}  {f(r_)}   {f(fr)}  n={a[2]:,}")
    # luck: placebo flags with each cell's rate, shuffled within month; how many cells pass |t|>=2 by chance
    print("\nluck check: same flag rates, shuffled within month, 50 rounds")
    real = sum(1 for c in cells if c[4][1] is not None and c[4][1] >= 2 and c[5][1] is not None and c[5][1] > 0
               and c[6][1] is not None and c[6][1] > 0)
    counts = []
    for _ in range(50):
        hits = 0
        for rule, k, h, rate, *_ in cells:
            g = df[(df.k == k) & df[f"x{h}"].notna()]
            fl = g.groupby("m")[rule].transform(lambda s: pd.Series(rng.permutation(s.values), index=s.index)).values
            a = lift(df, k, rule, h, flag=fl)
            if a[1] is not None and a[1] >= 2:
                hits += 1
        counts.append(hits)
    print(f"cells passing (t>=2 overall, same direction early and recent): {real} of {len(cells)}; "
          f"by chance on average {np.mean(counts):.1f} (95th pct {np.percentile(counts, 95):.0f})")
    # supply
    print("\nflagged per year (last 3 years), at k=20:")
    g = df[(df.k == 20) & (df["m"] >= cut)]
    print("  " + ", ".join(f"{r} {int(g[r].sum() / 3):,}/yr" for r in RULES))


report("buy")
report("sell")
