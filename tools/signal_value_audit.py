"""Nov 3 (read-only, owner: "what other signals could be useless now, or need changing? need to debug").
Every buy signal (main weights and trial), judged the way the bill signals were, with the luck measured:
  1. On its own: flagged buys minus unflagged buys filed the same month in the same sector (company mix and market
     timing cancel), at 20/60/125/250 trading days, for all years, the earlier years and the last 3 years.
     t-values use Newey-West errors on the monthly series (a 250-day hold overlaps the next 12 months' trades).
  2. Luck: the same test on random "placebo" signals with the same firing rates as the real ones. The placebo
     results set the bars, and say how many real signals would get each verdict by chance alone.
  3. Together: per-quarter regression of the result on every signal at once, within sector (Fama-MacBeth), so a
     signal that only looks good because it rides along with another shows up.
  4. Mechanics: share of buys it fires on, years with no firing, near-duplicates.
Verdicts: STRONG (beats the best of the random signals at a hold, earlier and recent years agree), WORKS (past the
placebo 95% bar at 2+ holds, earlier and recent agree), HARMFUL (the same, against its weight), weak, no edge.
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
fd = pd.to_datetime(b["filed_date"])
b["m"] = fd.dt.to_period("M").astype(str)
b["q"] = fd.dt.to_period("Q").astype(str)
b["y"] = fd.dt.year
b["cell"] = b["m"] + "|" + b["sector"].fillna("?").astype(str)
cut = (pd.Timestamp.today() - pd.DateOffset(years=3)).strftime("%Y-%m")
H = (20, 60, 125, 250)
for h in H:
    fr = E.forward_returns(b, px, h)
    b[f"x{h}"] = np.where(fr["closed"].values, np.clip(fr["excess"].values.astype(float), -1, 3), np.nan)
sigs = sorted(set(k for k in list(cfg["BUY_WEIGHTS"]) + list(E.TRIAL_SIGNALS) if f"f_{k}" in b
                  and pd.api.types.is_numeric_dtype(b[f"f_{k}"])))
print(f"buys filed within 45 days, with prices: {len(b):,}; signals: {len(sigs)}; last-3-years cut {cut}")
early_m, recent_m = (b["m"] < cut).values, (b["m"] >= cut).values
cell_codes = {h: None for h in H}


def nw_t(k, lags):
    """t of a monthly series' mean with Newey-West errors."""
    k = pd.Series(k).dropna().values.astype(float)
    n = len(k)
    if n < 6:
        return np.nan
    e = k - k.mean()
    v = e @ e / n
    for L in range(1, min(lags, n - 1) + 1):
        v += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return float(k.mean() / np.sqrt(v / n)) if v > 0 else np.nan


def lift(flag, h, part=None):
    """flagged minus the same cell's unflagged average -> (mean, t, n)."""
    col = f"x{h}"
    f = np.asarray(flag, bool)
    ok = b[col].notna().values & (part if part is not None else True)
    d = b.loc[ok, [col, "cell", "m"]]
    fl = f[ok]
    base = d[~fl].groupby("cell")[col].mean()
    x = d[fl].join(base.rename("base"), on="cell").dropna(subset=["base"])
    if len(x) < 30:
        return None, None, len(x)
    dd = x[col] - x["base"]
    return float(dd.mean()), nw_t(dd.groupby(x["m"]).mean().sort_index(), int(np.ceil(h / 21))), len(x)


def stats(flag):
    r = {}
    for h in H:
        r[f"l{h}"], r[f"t{h}"], r[f"n{h}"] = lift(flag, h)
        r[f"e{h}"] = lift(flag, h, early_m)[0]
        r[f"r{h}"] = lift(flag, h, recent_m)[0]
    return r


def flag_of(k):
    v = b[f"f_{k}"].astype(float).fillna(0)
    binary = set(np.unique(v.values)).issubset({0.0, 1.0})
    if binary or (v > 0).sum() <= 50:
        return (v > 0).values, binary
    return (v >= v[v > 0].quantile(0.8)).values & (v > 0).values, binary        # top 20% of the nonzero values


# ---- placebo: random signals with the real firing rates -----------------------------------------------------------
rng = np.random.default_rng(11)
flags = {k: flag_of(k) for k in sigs}
shares = [max(0.005, float(f.mean())) for f, _ in flags.values()]
P = []
# placebos clump like real signals do: flag whole (member, month) groups, not single trades
who = ((b["bioguide"] if "bioguide" in b else b["ticker"]).astype(object).fillna("?").astype(str) + "|" + b["m"]).values
uniq, inv = np.unique(who, return_inverse=True)
for i, sh in enumerate(shares * 2):                  # two placebo sets
    P.append(stats((rng.random(len(uniq)) < sh)[inv]))
PT = {h: np.array([abs(p[f"t{h}"]) for p in P if p[f"t{h}"] is not None and np.isfinite(p[f"t{h}"])]) for h in H}
BAR = {h: max(1.65, float(np.quantile(PT[h], 0.95))) if len(PT[h]) else 1.65 for h in H}
TOP = {h: float(np.quantile(PT[h], 0.995)) if len(PT[h]) else 3.3 for h in H}
print("placebo bars |t| (95% / 99.5%): " + ", ".join(f"{h}d {BAR[h]:.2f}/{TOP[h]:.2f}" for h in H))


def verdict(r, sgn):
    hs = [h for h in H if r[f"t{h}"] is not None and np.isfinite(r[f"t{h}"])]
    if not hs:
        return "too few trades"
    up = [h for h in hs if r[f"t{h}"] * sgn >= BAR[h]]
    down = [h for h in hs if r[f"t{h}"] * sgn <= -BAR[h]]
    strong = [h for h in hs if r[f"t{h}"] * sgn >= TOP[h]]
    bad = [h for h in hs if r[f"t{h}"] * sgn <= -TOP[h]]

    def agree(h, s):
        return (r[f"e{h}"] or 0) * sgn * s > 0 and (r[f"r{h}"] or 0) * sgn * s > 0
    if any(agree(h, -1) for h in bad) or (len(down) >= 2 and all(agree(h, -1) for h in down[:2])):
        return "HARMFUL (" + ", ".join(f"{h}d" for h in (bad or down)) + ")"
    if any(agree(h, 1) for h in strong):
        return "STRONG (" + ", ".join(f"{h}d" for h in strong) + ")"
    if len(up) >= 2 and all(agree(h, 1) for h in up[:2]):
        return "WORKS (" + ", ".join(f"{h}d" for h in up) + ")"
    if up or down:
        return "weak (" + ", ".join([f"{h}d +" for h in up] + [f"{h}d -" for h in down]) + ")"
    return "no edge"


luck = pd.Series([verdict(p, 1).split(" ")[0] for p in P]).value_counts() / 2
print("verdicts random signals got by luck (per set of " + str(len(sigs)) + "): "
      + ", ".join(f"{k} {v:g}" for k, v in luck.items()))

# ---- real signals ------------------------------------------------------------------------------------------------
rows = []
for k in sigs:
    f, binary = flags[k]
    v = b[f"f_{k}"].astype(float).fillna(0)
    yrs = b.loc[v.values > 0, "y"].value_counts()
    span = range(int(b["y"].min()), int(b["y"].max()) + 1)
    gaps = [y for y in span if yrs.get(y, 0) == 0 and yrs[yrs.index < y].sum() > 0 and yrs[yrs.index > y].sum() > 0]
    r = {"signal": k, "w": float(bw.get(k, 0) or 0), "main": k in cfg["BUY_WEIGHTS"], "share": float((v > 0).mean()),
         "binary": binary, "gaps": gaps, "last": int(yrs.index.max()) if len(yrs) else None, **stats(f)}
    r["sgn"] = np.sign(r["w"] or E.TRIAL_SIGNALS.get(k, 0) or 1)
    r["verdict"] = verdict(r, r["sgn"])
    rows.append(r)
R = pd.DataFrame(rows)

# together: within-sector cross-sectional regression per quarter
X_cols = [f"f_{k}" for k in sigs]
for h in (60, 250):
    coefs = []
    for q, g in b.dropna(subset=[f"x{h}"]).groupby("q"):
        if len(g) < len(X_cols) + 40:
            continue
        sec = g["sector"].fillna("?")
        X = g[X_cols].astype(float).fillna(0)
        X = X - X.groupby(sec).transform("mean")
        y = g[f"x{h}"] - g.groupby(sec)[f"x{h}"].transform("mean")
        keep = X.std() > 0
        cf = pd.Series(np.nan, index=X_cols)
        cf[keep[keep].index] = np.linalg.lstsq(X.loc[:, keep].values, y.values, rcond=None)[0]
        coefs.append(cf)
    C = pd.DataFrame(coefs)
    R[f"fm{h}"] = [C[f"f_{k}"].mean() if len(C) else np.nan for k in sigs]
    R[f"fmt{h}"] = [nw_t(C[f"f_{k}"], int(np.ceil(h / 63))) if len(C) else np.nan for k in sigs]
    print(f"together-regression at {h} days: {len(C)} quarters")

cor = b[X_cols].astype(float).fillna(0).corr()
dups = [f"{a[2:]} ~ {c[2:]} ({cor.loc[a, c]:+.2f})" for i, a in enumerate(X_cols) for c in X_cols[i + 1:]
        if abs(cor.loc[a, c]) >= 0.7]


def p(x):
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.2f}"


def notes(r):
    n = []
    if r["share"] > 0.5:
        n.append("fires on most buys")
    if r["gaps"]:
        n.append("no firing in " + ",".join(map(str, r["gaps"])))
    if r["last"] and r["last"] < b["y"].max() - 1:
        n.append(f"last fired {r['last']}")
    e, rc = (r["e60"] or 0) * r["sgn"], (r["r60"] or 0) * r["sgn"]
    if e > 0.005 and rc < -0.005:
        n.append("worked before, reversed in the last 3 years")
    if np.isfinite(r["fmt60"] or np.nan) and r["fmt60"] * r["sgn"] <= -BAR[60]:
        n.append("hurts once the other signals are counted")
    if np.isfinite(r["fmt60"] or np.nan) and r["fmt60"] * r["sgn"] >= BAR[60] and r["verdict"].startswith(("no", "weak")):
        n.append("helps once the other signals are counted")
    return "; ".join(n)


R["notes"] = [notes(r) for r in R.to_dict("records")]
R = R.sort_values(["main", "w"], ascending=[False, False])
if "score" in b:
    top = b["score"] >= b["score"].quantile(0.8)
    print("\nwhole score, top 20% vs same month and sector: " + ", ".join(
        f"{h}d {p(lift(top.values, h)[0])}% (t {lift(top.values, h)[1]:.1f})" for h in H))
print("\nsignal | weight | fires | 20d | 60d | 125d | 250d (lift % vs same month+sector, t) | 60d early|recent | "
      "together 60d | together 250d | verdict | notes")
for r in R.to_dict("records"):
    cells = [f"{p(r[f'l{h}'])} ({r[f't{h}']:.1f})" if r[f"l{h}"] is not None else "n/a" for h in H]
    print(f"{r['signal']}{'' if r['binary'] else ' (top 20%)'} | {r['w']:+.2f}{'' if r['main'] else ' trial'} | "
          f"{r['share'] * 100:.0f}% | " + " | ".join(cells) + f" | {p(r['e60'])}|{p(r['r60'])} | "
          f"{p(r['fm60'])} ({(r['fmt60'] if np.isfinite(r['fmt60'] or np.nan) else 0):.1f}) | "
          f"{p(r['fm250'])} ({(r['fmt250'] if np.isfinite(r['fmt250'] or np.nan) else 0):.1f}) | {r['verdict']} | {r['notes']}")
print("\nNEAR-DUPLICATES (correlation 0.7+): " + ("; ".join(dups) if dups else "none"))
on, off = R[R["w"] != 0], R[R["w"] == 0]
print(f"\nSUMMARY: {len(on)} signals count in the score now; luck alone gives about "
      + ", ".join(f"{v:g} {k}" for k, v in luck.items() if k != "no") + f" per {len(sigs)} signals.")
for lab in ("STRONG", "WORKS", "HARMFUL", "weak", "no edge", "too few"):
    print(f"  in the score, {lab}: " + ", ".join(on[on["verdict"].str.startswith(lab)]["signal"]))
for lab in ("STRONG", "WORKS", "HARMFUL"):
    print(f"  switched off, {lab}: " + ", ".join(off[off["verdict"].str.startswith(lab)]["signal"]))
print("  notes: " + " | ".join(f"{r['signal']}: {r['notes']}" for r in R.to_dict("records") if r["notes"]))
