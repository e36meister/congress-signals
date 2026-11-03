"""Nov 3 (read-only, owner: "what other signals could be useless now, or need changing? need to debug").
Every buy signal (main weights and trial), judged the same way the bill signals were:
  1. on its own: flagged buys vs unflagged buys filed the same month in the same sector (company-mix and market
     timing cancel), at 20/60/125/250 trading days; earlier years vs the last 3 years
  2. together: per-quarter cross-sectional regression of the result on every signal at once (Fama-MacBeth), so a
     signal that only looks good because it rides along with another one shows up
  3. warnings: fires on most buys (can't tell picks apart), weight points the wrong way, no edge at any hold,
     early and recent disagree, near-duplicates of another signal
No writes, no account data."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
bw, _ = E.active_weights(cfg)
b = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna() & (sc["lag_days"].fillna(999) <= 45)].copy()
b["m"] = pd.to_datetime(b["filed_date"]).dt.to_period("M").astype(str)
b["q"] = pd.to_datetime(b["filed_date"]).dt.to_period("Q").astype(str)
b["cell"] = b["m"] + "|" + b["sector"].fillna("?").astype(str)
cut = (pd.Timestamp.today() - pd.DateOffset(years=3)).strftime("%Y-%m")
H = (20, 60, 125, 250)
for h in H:
    fr = E.forward_returns(b, px, h)
    b[f"x{h}"] = np.where(fr["closed"].values, np.clip(fr["excess"].values.astype(float), -1, 3), np.nan)
sigs = sorted(set(k for k in list(cfg["BUY_WEIGHTS"]) + list(E.TRIAL_SIGNALS) if f"f_{k}" in b
                  and pd.api.types.is_numeric_dtype(b[f"f_{k}"])))
print(f"buys filed within 45 days, with prices: {len(b):,}; signals: {len(sigs)}; last-3-years cut {cut}")


def p_(res):
    m, t, n = res
    return "n/a" if m is None else f"{m * 100:+.2f}% (t {t:.1f}, n {n:,})"


def lift(flag, col):
    """flagged minus the same cell's unflagged average; (mean, t by month, n)"""
    d = b[[col, "cell", "m"]].assign(f=flag.values).dropna(subset=[col])
    base = d[~d["f"]].groupby("cell")[col].mean()
    x = d[d["f"]].join(base.rename("base"), on="cell").dropna(subset=["base"])
    if len(x) < 30:
        return None, None, len(x)
    dd = x[col] - x["base"]
    k = dd.groupby(x["m"]).mean()
    se = k.std() / np.sqrt(len(k)) if len(k) > 2 else np.nan
    return float(dd.mean()), float(dd.mean() / se) if se else np.nan, len(x)


rows = []
for k in sigs:
    v = b[f"f_{k}"].astype(float).fillna(0)
    binary = set(np.unique(v.values)).issubset({0.0, 1.0})
    flag = (v > 0) if binary else (v >= v[v > 0].quantile(0.8) if (v > 0).sum() > 50 else v > 0)
    r = {"signal": k, "w": float(bw.get(k, 0) or 0), "main": k in cfg["BUY_WEIGHTS"], "share": float((v > 0).mean()),
         "binary": binary}
    for h in H:
        r[f"l{h}"], r[f"t{h}"], r[f"n{h}"] = lift(flag, f"x{h}")
        early = flag & (b["m"] < cut)
        recent = flag & (b["m"] >= cut)
        r[f"e{h}"] = lift(early, f"x{h}")[0]
        r[f"r{h}"] = lift(recent, f"x{h}")[0]
    rows.append(r)
R = pd.DataFrame(rows)
# the whole score, as a yardstick: buys in the top 20% of scores vs the rest of the same month and sector
if "score" in b:
    top = b["score"] >= b["score"].quantile(0.8)
    print("whole score, top 20% vs same month and sector: " + ", ".join(
        f"{h}d {p_(lift(top, f'x{h}'))}" for h in H))

# all signals together, per quarter (Fama-MacBeth), 60 and 250 days
fm = {}
X_cols = [f"f_{k}" for k in sigs]
for h in (60, 250):
    coefs = []
    for q, g in b.dropna(subset=[f"x{h}"]).groupby("q"):
        if len(g) < len(X_cols) + 40:
            continue
        X = g[X_cols].astype(float).fillna(0)
        X = X - X.groupby(g["sector"].fillna("?")).transform("mean")        # within sector
        y = g[f"x{h}"] - g.groupby(g["sector"].fillna("?"))[f"x{h}"].transform("mean")
        keep = X.std() > 0
        cf = pd.Series(np.nan, index=X_cols)
        sol = np.linalg.lstsq(X.loc[:, keep].values, y.values, rcond=None)[0]
        cf[keep[keep].index] = sol
        coefs.append(cf)
    C = pd.DataFrame(coefs)
    fm[h] = (C.mean(), C.mean() / (C.std() / np.sqrt(C.notna().sum())), C.notna().sum())
    print(f"regression at {h} days: {len(C)} quarters")
for h in (60, 250):
    R[f"fm{h}"] = [fm[h][0].get(f"f_{k}") for k in sigs]
    R[f"fmt{h}"] = [fm[h][1].get(f"f_{k}") for k in sigs]

# near-duplicates
F = b[X_cols].astype(float).fillna(0)
cor = F.corr()
dups = []
for i, a in enumerate(X_cols):
    for c in X_cols[i + 1:]:
        if abs(cor.loc[a, c]) >= 0.7:
            dups.append(f"{a[2:]} ~ {c[2:]} ({cor.loc[a, c]:+.2f})")


def p(x, d=2):
    return "   n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.{d}f}"


def warn(r):
    """Warnings need a sure result (|t| 1.65+), so random noise across ~95 signals doesn't flag half of them."""
    w = []
    if r["share"] > 0.5:
        w.append("fires on most buys")
    sgn = np.sign(r["w"] or E.TRIAL_SIGNALS.get(r["signal"], 0) or 1)
    ts = [(r[f"t{h}"] or 0) * sgn for h in H if r[f"t{h}"] is not None and np.isfinite(r[f"t{h}"])]
    if not ts:
        w.append("too few trades")
        return w
    if sum(1 for t in ts if t <= -1.65) >= 2:
        w.append("HARMFUL: works against its weight (2+ holds, sure)")
    elif max(ts) < 1.65:
        w.append("no clear edge at any hold")
    if (r["e60"] or 0) * sgn > 0.005 and (r["r60"] or 0) * sgn < -0.005:
        w.append("worked before, reversed in the last 3 years (60d)")
    if r["fmt60"] is not None and np.isfinite(r["fmt60"]) and r["fmt60"] * sgn < -2:
        w.append("hurts once the other signals are counted")
    return w


R["warn"] = [warn(r) for r in R.to_dict("records")]
R = R.sort_values(["main", "w"], ascending=[False, False])
print("\nPer signal: lift vs same-month, same-sector buys, in % (t), at 20/60/125/250 days; early|recent at 60d; "
      "together-regression coef % (t) at 60/250d; warnings")
print("signal | weight now | fires | 20d | 60d | 125d | 250d | 60d early|recent | together 60d | together 250d | warnings")
for r in R.to_dict("records"):
    cells = [f"{p(r[f'l{h}'])} ({r[f't{h}']:.1f})" if r[f"l{h}"] is not None else "n/a" for h in H]
    print(f"{r['signal']}{'' if r['binary'] else ' (top 20%)'} | {r['w']:+.2f}{'' if r['main'] else ' trial'} | "
          f"{r['share'] * 100:.0f}% | " + " | ".join(cells) +
          f" | {p(r['e60'])}|{p(r['r60'])} | {p(r['fm60'])} ({(r['fmt60'] or 0):.1f}) | {p(r['fm250'])} ({(r['fmt250'] or 0):.1f}) | "
          + "; ".join(r["warn"]))
print("\nNEAR-DUPLICATE SIGNALS (correlation 0.7+): " + ("; ".join(dups) if dups else "none"))
on = R[R["w"] != 0]
print(f"\nSUMMARY: {len(on)} signals count in the score now.")
for lab, f in (("harmful (works against its weight)", lambda w: any("HARMFUL" in x for x in w)),
               ("no clear edge at any hold", lambda w: any("no clear edge" in x for x in w)),
               ("hurts once others are counted", lambda w: any("hurts once" in x for x in w)),
               ("reversed in the last 3 years", lambda w: any("reversed" in x for x in w)),
               ("fires on most buys", lambda w: any("most buys" in x for x in w))):
    print(f"  {lab}: " + ", ".join(on[on["warn"].map(f)]["signal"]))
tr = R[(R["w"] == 0)]
good = tr[[any((r[f"t{h}"] or 0) * np.sign(E.TRIAL_SIGNALS.get(r["signal"], 1) or 1) >= 2 for h in H)
           for r in tr.to_dict("records")]]
print("  (with ~95 signals, about 5 will look 'sure' by chance alone; treat single-hold results with care)")
print("  switched-off signals with t>=2 in their direction at some hold: " + ", ".join(good["signal"]))
