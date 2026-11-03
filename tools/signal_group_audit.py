"""Nov 3 (read-only, owner: "should signals be tested on each company separately?" -> test by group, "do both
[sector and industry] and compare and contrast").
Every buy signal tested inside each SECTOR (11) and inside each INDUSTRY (Yahoo industry, those with enough buys),
the same way as tools/signal_value_audit.py:
  - flagged buys minus unflagged buys of the same group filed in the same period (sector: same month; industry:
    same quarter, since month x industry cells are too thin), at 60/125/250 trading days, all years, before and
    from the fixed holdout (HOLDOUT_START 2023-11); Newey-West t on the period series.
  - LUCK: the same tests on random placebo signals (matched firing rate per group, clumped by member+month). For
    each grouping the placebo sets the bars AND says how many (signal, group) "hits" chance alone produces; a
    grouping is only worth using if it finds clearly more hits than luck.
  - hit = past the placebo 95% bar at 2+ holds with the earlier and the holdout years both pointing the same way.
Then compares the two groupings: hits vs luck, whether industry hits sit inside sector hits, and signals that
work in one group while doing nothing (or the opposite) overall. No writes, no account data."""
import json
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
try:
    meta = json.load(open("data/cache/ticker_meta.json"))
except Exception:
    meta = {}
bw, _ = E.active_weights(cfg)
b = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna() & (sc["lag_days"].fillna(999) <= 45)].copy().reset_index(drop=True)
fd = pd.to_datetime(b["filed_date"])
b["m"] = fd.dt.to_period("M").astype(str)
b["q"] = fd.dt.to_period("Q").astype(str)
b["sector"] = b["sector"].fillna("?").astype(str)
ind = b["industry"] if "industry" in b else b["ticker"].map(lambda t: (meta.get(t) or {}).get("industry"))
b["industry"] = pd.Series(ind).fillna("?").astype(str).values
HOLD = getattr(E, "HOLDOUT_START", "2023-11-01")[:7]
early_m, recent_m = (b["m"] < HOLD).values, (b["m"] >= HOLD).values
H = (60, 125, 250)
for h in H:
    fr = E.forward_returns(b, px, h)
    b[f"x{h}"] = np.where(fr["closed"].values, np.clip(fr["excess"].values.astype(float), -1, 3), np.nan)
sigs = sorted(set(k for k in list(cfg["BUY_WEIGHTS"]) + list(E.TRIAL_SIGNALS) if f"f_{k}" in b
                  and pd.api.types.is_numeric_dtype(b[f"f_{k}"])))
who = ((b["bioguide"] if "bioguide" in b else b["ticker"]).astype(object).fillna("?").astype(str) + "|" + b["m"]).values
uniq, inv = np.unique(who, return_inverse=True)
MIN_BUYS, MIN_FLAG = 400, 40
NPL = 3            # placebo sets per grouping (the luck count is their average)
print(f"buys: {len(b):,}; signals: {len(sigs)}; holdout from {HOLD}")


def nw_t(k, lags):
    k = pd.Series(k).dropna().values.astype(float)
    n = len(k)
    if n < 6:
        return np.nan
    e = k - k.mean()
    v = e @ e / n
    for L in range(1, min(lags, n - 1) + 1):
        v += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return float(k.mean() / np.sqrt(v / n)) if v > 0 else np.nan


def flag_of(k):
    v = b[f"f_{k}"].astype(float).fillna(0)
    binary = set(np.unique(v.values)).issubset({0.0, 1.0})
    if binary or (v > 0).sum() <= 50:
        return (v > 0).values
    return ((v >= v[v > 0].quantile(0.8)) & (v > 0)).values


FLAGS = {k: flag_of(k) for k in sigs}


def lift(idx, f, h, per, part=None):
    """within rows idx: flagged minus same-period unflagged mean -> (mean, t, n)"""
    x = b[f"x{h}"].values[idx]
    ok = ~np.isnan(x)
    if part is not None:
        ok &= part[idx]
    ff = f[idx][ok]
    if ff.sum() < MIN_FLAG or (~ff).sum() < MIN_FLAG:
        return None, None, int(ff.sum())
    d = pd.DataFrame({"x": x[ok], "p": b[per].values[idx][ok], "f": ff})
    base = d[~d["f"]].groupby("p")["x"].mean()
    y = d[d["f"]].join(base.rename("b"), on="p").dropna(subset=["b"])
    if len(y) < MIN_FLAG:
        return None, None, len(y)
    dd = y["x"] - y["b"]
    lags = int(np.ceil(h / (21 if per == "m" else 63)))
    return float(dd.mean()), nw_t(dd.groupby(y["p"]).mean().sort_index(), lags), len(y)


def stats(idx, f, per):
    r = {}
    for h in H:
        r[f"l{h}"], r[f"t{h}"], r[f"n{h}"] = lift(idx, f, h, per)
        r[f"e{h}"] = lift(idx, f, h, per, early_m)[0]
        r[f"r{h}"] = lift(idx, f, h, per, recent_m)[0]
    return r


def hit(r, bar, sgn):
    """+1 helps, -1 hurts, 0 neither (2+ holds past the bar, earlier and holdout years agree)"""
    for s in (1, -1):
        hs = [h for h in H if r[f"t{h}"] is not None and np.isfinite(r[f"t{h}"]) and r[f"t{h}"] * sgn * s >= bar[h]]
        if len(hs) >= 2 and all((r[f"e{h}"] or 0) * sgn * s > 0 and (r[f"r{h}"] or 0) * sgn * s > 0 for h in hs[:2]):
            return s
    return 0


def run(group_col, per):
    groups = [g for g, n in b[group_col].value_counts().items() if n >= MIN_BUYS and g != "?"]
    gidx = {g: np.flatnonzero(b[group_col].values == g) for g in groups}
    rng = np.random.default_rng(7)
    # placebo: for each group, as many random signals as real ones, with the real per-group firing rates
    pl = []
    for g in [g for _ in range(NPL) for g in groups]:
        for k in sigs:
            sh = max(0.01, float(FLAGS[k][gidx[g]].mean()))
            if FLAGS[k][gidx[g]].sum() < MIN_FLAG:
                continue
            pl.append(stats(gidx[g], (rng.random(len(uniq)) < sh)[inv], per))
    PT = {h: np.array([abs(p[f"t{h}"]) for p in pl if p[f"t{h}"] is not None and np.isfinite(p[f"t{h}"])]) for h in H}
    bar = {h: max(1.65, float(np.quantile(PT[h], 0.95))) if len(PT[h]) else 9 for h in H}
    luck = sum(1 for p in pl if hit(p, bar, 1) != 0)
    rows = []
    for g in groups:
        for k in sigs:
            f = FLAGS[k]
            if f[gidx[g]].sum() < MIN_FLAG:
                continue
            r = stats(gidx[g], f, per)
            sgn = np.sign(bw.get(k) or E.TRIAL_SIGNALS.get(k, 0) or (cfg["BUY_WEIGHTS"].get(k) or 1))
            rows.append({"group": g, "signal": k, "sgn": sgn, "hit": hit(r, bar, sgn), "n": r["n60"], **r})
    R = pd.DataFrame(rows)
    return groups, R, bar, luck, len(pl)


overall = {}
allidx = np.arange(len(b))
for k in sigs:
    overall[k] = stats(allidx, FLAGS[k], "m")


def pc(x):
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.1f}"


def fmt(r):
    return " ".join(f"{h}d {r[f'l{h}'] * 100:+.1f}({r[f't{h}']:.1f})" if r[f"l{h}"] is not None else f"{h}d n/a" for h in H)


res = {}
for name, col, per in (("SECTOR", "sector", "m"), ("INDUSTRY", "industry", "q")):
    groups, R, bar, luck, npl = run(col, per)
    res[name] = R
    tested = len(R)
    hits = R[R["hit"] != 0]
    print(f"\n===== {name}: {len(groups)} groups with {MIN_BUYS}+ buys; {tested} signal-group tests "
          f"(placebo tests {npl}); bars |t| " + ", ".join(f"{h}d {bar[h]:.2f}" for h in H))
    print(f"hits: {len(hits)} ({(hits['hit'] > 0).sum()} as intended, {(hits['hit'] < 0).sum()} opposite of the weight); "
          f"luck alone gives about {luck * tested / max(npl, 1):.1f} per {tested} tests")
    for r in hits.sort_values(["hit", "group"], ascending=[False, True]).to_dict("records"):
        o = overall[r["signal"]]
        print(f"  {'AS INTENDED' if r['hit'] > 0 else 'OPPOSITE OF ITS WEIGHT'} | {r['signal']} in {r['group']} (n {r['n']}) | {fmt(r)} | "
              f"earlier|holdout 60d {pc(r['e60'])}|{pc(r['r60'])} | overall {fmt(o)}")

# ---- compare and contrast ----------------------------------------------------------------------------------------
S, I = res["SECTOR"], res["INDUSTRY"]
sec_of_ind = b.groupby("industry")["sector"].agg(lambda s: s.value_counts().index[0]).to_dict()
print("\n===== COMPARE")
ih = I[I["hit"] != 0]
inside = [r for r in ih.to_dict("records")
          if ((S["signal"] == r["signal"]) & (S["group"] == sec_of_ind.get(r["group"])) & (S["hit"] == r["hit"])).any()]
print(f"industry hits that their sector also shows: {len(inside)} of {len(ih)}")
sh = S[S["hit"] != 0]
spread = []
for r in sh.to_dict("records"):
    sub = I[(I["signal"] == r["signal"]) & I["group"].map(lambda g: sec_of_ind.get(g) == r["group"])]
    if len(sub):
        same = (np.sign(sub["l60"].fillna(0)) == r["hit"] * r["sgn"]).mean()
        spread.append((r["signal"], r["group"], len(sub), same))
print("sector hits: share of that sector's industries pointing the same way at 60d (broad = real, narrow = one "
      "industry driving it):")
for s, g, n, same in spread:
    print(f"  {s} in {g}: {same * 100:.0f}% of {n} industries")
# signals with no edge overall that work in a group
print("signals with no clear edge overall but a hit in a group:")
for name, R in res.items():
    for r in R[R["hit"] != 0].to_dict("records"):
        o = overall[r["signal"]]
        ts = [abs(o[f"t{h}"] or 0) for h in H]
        if max(ts) < 2:
            print(f"  {name.lower()}: {r['signal']} in {r['group']} ({'as intended' if r['hit'] > 0 else 'opposite of its weight'})")
# same signal opposite in different sectors
print("signals that help in one sector and hurt in another (60d, |t| >= 2 each):")
for k, g in S.groupby("signal"):
    pos = g[(g["t60"].fillna(0) * g["sgn"] >= 2)]["group"].tolist()
    neg = g[(g["t60"].fillna(0) * g["sgn"] <= -2)]["group"].tolist()
    if pos and neg:
        print(f"  {k}: helps in {', '.join(pos)}; hurts in {', '.join(neg)}")
