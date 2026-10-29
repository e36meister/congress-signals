"""Read-only: pick the strongest bill signals in one period, combine them, and test the combination in the other.

Reads the per-trade file bill_stage_study.py writes (out/bill_trade_features.csv.gz, or --features PATH), so it needs
no downloads and runs in seconds. Signals are chosen and given their direction only from the training years; the
test years are never looked at while choosing. Both ways round: train 2015-2020 / test 2021 on, and the reverse.
Prints only aggregate results."""
import argparse
import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--features", default="out/bill_trade_features.csv.gz")
ap.add_argument("--max-signals", type=int, default=6)
ap.add_argument("--min-t", type=float, default=2.0, help="training |t| a signal needs to be picked")
args = ap.parse_args()

df = pd.read_csv(args.features, parse_dates=["trade_date", "filed_date"], low_memory=False)
HOLDS = [int(c[1:]) for c in df if c.startswith("x") and c[1:].isdigit()]
df["sign"] = np.where(df["tx_type"] == "sell", -1.0, 1.0)
for h in HOLDS:
    df[f"y{h}"] = df["sign"] * df[f"x{h}"]
df["fq"] = df["filed_date"].dt.to_period("Q").astype(str)
df["half"] = np.where(df["filed_date"] < pd.Timestamp("2021-01-01"), "A", "B")
df["score_q"] = df.groupby("tx_type")["score"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)) \
    if "score" in df and df["score"].notna().sum() > 100 else 0


def col(n, fill=0):
    return df[n].fillna(fill) if n in df else pd.Series(fill, index=df.index)


def flag(n):
    return col(n, False).astype(bool)


# the tradable signals of bill_stage_study.py (known on the filing date)
S = {}
for link in ("sponsor", "committee"):
    for k, nm in ((1, "in play"), (2, "hearing+"), (4, "reported+"), (5, "passed a chamber+")):
        S[f"{link} bill {nm} on trade date"] = col(f"{link}_s_t") >= k
        S[f"{link} bill (focused) {nm} on trade date"] = col(f"{link}_focused_s_t") >= k
    S[f"{link} bill moved in 30 days before trade"] = flag(f"{link}_adv_pre30")
    S[f"{link} bill moved trade->filing"] = flag(f"{link}_adv_t2f")
    S[f"{link} bill (focused) moved trade->filing"] = flag(f"{link}_focused_adv_t2f")
    S[f"{link} bill passed a chamber+ trade->filing"] = col(f"{link}_top_t2f") >= 5
S["lead sponsor of a bill in play"] = col("sponsor_n_own") > 0
for w, wn in (("pre", "90d before trade"), ("t2f", "trade->filing")):
    for kind, kn in (("linked", "own/committee bills"), ("all", "any industry bill"), ("other_contested", "close votes")):
        c = f"vote_{w}_{kind}_align"
        S[f"vote aligned, {kn}, {wn}"] = col(c) > 0
        S[f"vote contrary, {kn}, {wn}"] = col(c) < 0
S = {k: v.astype(bool) for k, v in S.items() if v.any()}


def ols(y, X, cl):
    """Coefficients and quarter-clustered standard errors; X already has its columns (an intercept is added)."""
    X = np.column_stack([np.ones(len(y))] + list(X))
    ok = np.isfinite(y) & np.isfinite(X).all(1)
    X, y, g = X[ok], y[ok], np.asarray(cl)[ok]
    if len(y) < 50:
        return None, None
    XtX = np.linalg.pinv(X.T @ X)
    b = XtX @ X.T @ y
    e = y - X @ b
    meat = np.zeros((X.shape[1],) * 2)
    for gv in np.unique(g):
        m = g == gv
        s = X[m].T @ e[m]
        meat += np.outer(s, s)
    G = len(np.unique(g))
    return b, np.sqrt(np.diag(XtX @ meat @ XtX * G / max(G - 1, 1)))


def lift(d, m, h):
    """Effect of one indicator, controlling for score quintile."""
    q = pd.get_dummies(d["score_q"].astype(str), drop_first=True).values.astype(float)
    b, se = ols(d[f"y{h}"].values, [m.astype(float).values] + [q[:, j] for j in range(q.shape[1])], d["fq"].values)
    if b is None or m.sum() < 30 or (~m).sum() < 30:
        return np.nan, np.nan
    return b[1], b[1] / se[1] if se[1] else np.nan


def pick(d, h):
    """Strongest signals in the training rows, skipping ones that mostly overlap a signal already picked."""
    rows = []
    for k, v in S.items():
        b, t = lift(d, v[d.index], h)
        if np.isfinite(t) and abs(t) >= args.min_t and v[d.index].sum() >= 100:
            rows.append((abs(t), k, np.sign(b), b, t))
    rows.sort(reverse=True)
    chosen = []
    for _, k, sg, b, t in rows:
        m = S[k][d.index]
        if any((m & S[c][d.index]).sum() / max((m | S[c][d.index]).sum(), 1) > 0.6 for c, *_ in chosen):
            continue
        chosen.append((k, sg, b, t))
        if len(chosen) >= args.max_signals:
            break
    return chosen


def pct(v):
    return "   n/a" if not np.isfinite(v) else f"{v * 100:+6.2f}%"


print("Pick the strongest bill signals in one period, combine them (each counts +1 or -1 in the direction it had "
      "when picked), test in the other period. Lift = extra result vs S&P per trade, controlling for today's score.")
verdict = []
for h in HOLDS:
    if h < 60:
        continue
    for side in ("buy", "sell"):
        for tr, te, lab in (("A", "B", "pick 2015-20, test 2021 on"), ("B", "A", "pick 2021 on, test 2015-20")):
            d = df[(df.tx_type == side) & df[f"y{h}"].notna()]
            dtr, dte = d[d.half == tr], d[d.half == te]
            ch = pick(dtr, h)
            print(f"\n== {h}-day hold, {side}s, {lab}")
            if not ch:
                print("  nothing reached the training bar")
                continue
            for k, sg, b, t in ch:
                bt, tt = lift(dte, S[k][dte.index], h)
                print(f"  picked: {k:<52} train {pct(b)} t {t:5.1f} | test {pct(bt)} t {tt:5.1f}")
            combo = sum(sg * S[k].astype(float) for k, sg, *_ in ch)
            for nm, dd in (("train", dtr), ("TEST", dte)):
                c = combo[dd.index]
                b1, t1 = lift(dd, c >= 1, h)
                b2, t2 = lift(dd, c >= 2, h) if (c >= 2).sum() >= 30 else (np.nan, np.nan)
                bn, tn = lift(dd, c <= -1, h)
                print(f"  combined {nm:<5}: score >= 1 {pct(b1)} t {t1:5.1f} (n {(c >= 1).sum():,}) | "
                      f">= 2 {pct(b2)} t {t2:5.1f} (n {(c >= 2).sum():,}) | <= -1 {pct(bn)} t {tn:5.1f} (n {(c <= -1).sum():,})")
                if nm == "TEST":
                    verdict.append((h, side, lab, b1, t1, bn, tn))

print("\n== Summary: does the combination hold up in the years it was not picked on?")
for h, side, lab, b1, t1, bn, tn in verdict:
    good = (np.isfinite(t1) and t1 >= 2) or (np.isfinite(tn) and tn <= -2)
    print(f"  {h:>3}d {side:<4} {lab:<28} favored {pct(b1)} t {t1:5.1f} | disfavored {pct(bn)} t {tn:5.1f}"
          f"{'   <- holds up' if good else ''}")
