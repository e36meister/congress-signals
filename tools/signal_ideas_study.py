"""Nov 3 (read-only): new buy-signal ideas built on the few that showed promise in signal_value_audit.py
(proven_member, cluster, donations, committee_cluster; unusual_volume_low as the clear negative).
Same rigor as the audit:
  1. Each idea: flagged buys minus unflagged buys filed the same month in the same sector, at 20/60/125/250 trading
     days (E.forward_returns), all years / earlier / last 3 years, Newey-West t on the monthly series.
  2. Luck: placebo signals with the ideas' firing rates, clumped by member+month, set the bars and say how many of
     this many ideas would pass by chance (many sets, so the count is an average, plus the chance of 1+ passing).
  3. Does the extra condition add anything? For a combo of a parent signal, the combo vs random member+month slices
     of the parent with the same share (60d and 125d): a combo that only rides its parent lands mid-pack.
  4. Supply: picks per year (last 3 years) against the ~260/year needed for ~5 picks a week.
Ideas use existing scored.pkl columns only. No writes, no account data."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
print("scored.pkl columns (" + str(len(sc.columns)) + "): " + ", ".join(map(str, sc.columns)))
WHO = "who" if "who" in sc else ("bioguide" if "bioguide" in sc else "member")
sc["filed_date"] = pd.to_datetime(sc["filed_date"])

# ---- facts computed from the whole history (past only) --------------------------------------------------------
# days since this member's previous filed trade (any type)
o = sc.sort_values("filed_date", kind="stable")
sc["gap_days"] = o.groupby(o[WHO].astype(str))["filed_date"].diff().dt.days.reindex(sc.index)
# parties among the members buying the same stock in the cluster window (filed in (d - window, d])
win = np.timedelta64(int(cfg.get("CLUSTER_WINDOW_DAYS", 30)), "D")
both = np.zeros(len(sc), bool)
if "party" in sc:
    bb = sc[sc["tx_type"] == "buy"]
    pos = {ix: i for i, ix in enumerate(sc.index)}
    for t, g in bb.groupby("ticker"):
        if len(g) < 2:
            continue
        g = g.sort_values("filed_date", kind="stable")
        d = g["filed_date"].values
        pa = g["party"].astype(object).fillna("").astype(str).str[:1].str.upper().values
        lo = np.searchsorted(d, d - win, side="right")
        hi = np.searchsorted(d, d, side="right")
        for j, ix in enumerate(g.index):
            s = set(pa[lo[j]:hi[j]])
            both[pos[ix]] = "D" in s and "R" in s
sc["bipartisan"] = both

b = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna() & (sc["lag_days"].fillna(999) <= 45)].copy()
fd = pd.to_datetime(b["filed_date"])
b["m"] = fd.dt.to_period("M").astype(str)
b["y"] = fd.dt.year
b["wk"] = fd.dt.to_period("W").astype(str)
b["cell"] = b["m"] + "|" + b["sector"].fillna("?").astype(str)
cut = (pd.Timestamp.today() - pd.DateOffset(years=3)).strftime("%Y-%m")
H = (20, 60, 125, 250)
for h in H:
    fr = E.forward_returns(b, px, h)
    b[f"x{h}"] = np.where(fr["closed"].values, np.clip(fr["excess"].values.astype(float), -1, 3), np.nan)
print(f"\nbuys filed within 45 days, with prices: {len(b):,}; last-3-years cut {cut}")
early_m, recent_m = (b["m"] < cut).values, (b["m"] >= cut).values


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


def lift(flag, h, part=None, base_mask=None):
    """flagged minus the same cell's unflagged average -> (mean, t, n). base_mask limits the comparison group."""
    col = f"x{h}"
    f = np.asarray(flag, bool)
    ok = b[col].notna().values & (part if part is not None else True)
    d = b.loc[ok, [col, "cell", "m"]]
    fl = f[ok]
    bm = ~fl if base_mask is None else (~fl & np.asarray(base_mask, bool)[ok])
    base = d[bm].groupby("cell")[col].mean()
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


def col(k):
    return b[k].astype(float).fillna(0) if k in b else pd.Series(0.0, index=b.index)


def flag_of(k):
    """the audit's flag: nonzero for 0/1 signals, else top 20% of the nonzero values"""
    v = col(f"f_{k}")
    binary = set(np.unique(v.values)).issubset({0.0, 1.0})
    if binary or (v > 0).sum() <= 50:
        return (v > 0).values
    return ((v >= v[v > 0].quantile(0.8)) & (v > 0)).values


PROVEN, CLUSTER, DON, CC = flag_of("proven_member"), flag_of("cluster"), flag_of("donations"), flag_of("committee_cluster")
UV = flag_of("unusual_volume_low")
BIG = flag_of("size")
TRACK = flag_of("track_record") & (col("track_n").values >= 10 if "track_n" in b else True)
NEWSEC = col("f_new_sector").values > 0
COMM = col("f_committee").values > 0
nbuy = col("n_buyers").values
lag = b["lag_days"].astype(float).values
gap = b["gap_days"].astype(float).values
print("parent flags: proven_member %.1f%%, cluster %.1f%% (n_buyers>=%s), donations %.1f%%, committee_cluster %.1f%%, "
      "unusual_volume %.1f%%, top size %.1f%%" % (PROVEN.mean() * 100, CLUSTER.mean() * 100,
      int(nbuy[CLUSTER].min()) if CLUSTER.any() else "?", DON.mean() * 100, CC.mean() * 100, UV.mean() * 100,
      BIG.mean() * 100))

# ---- the ideas: (name, flag, parent flag or None, one-line why) -------------------------------------------------
IDEAS = [
    ("proven_and_cluster", PROVEN & CLUSTER, PROVEN, "a proven member's buy that other members are also buying"),
    ("proven_big", PROVEN & BIG, PROVEN, "proven member putting a top-20% size amount in"),
    ("proven_new_sector", PROVEN & NEWSEC, PROVEN, "proven member buying in a sector they never traded before"),
    ("proven_usual_sector", PROVEN & ~NEWSEC, PROVEN, "proven member buying inside sectors they already trade"),
    ("proven_no_volume", PROVEN & ~UV, PROVEN, "proven member, no unusual volume before the trade (drops the bad case)"),
    ("proven_fast", PROVEN & (lag <= 7), PROVEN, "proven member's buy filed within 7 days (fresher information)"),
    ("proven_committee", PROVEN & COMM, PROVEN, "proven member buying in a sector their committee oversees"),
    ("cluster3", nbuy >= 3, CLUSTER, "3+ different members bought the stock within the cluster window"),
    ("cluster_bipartisan", CLUSTER & b["bipartisan"].values, CLUSTER, "the cluster has buyers from both parties"),
    ("cluster_no_volume", CLUSTER & ~UV, CLUSTER, "cluster, no unusual volume before the trade"),
    ("donations_committee", DON & COMM, DON, "donor-company money and the member's committee oversees the sector"),
    ("donations_or_cc", DON | CC, None, "either of the two smaller positives (more supply)"),
    ("gap_first_buy", (gap >= 180) & np.isfinite(gap), None, "member's first trade after 180+ days of no filings"),
    ("big_good_track", BIG & TRACK, None, "top-size buy by a member with a top-20% track record (10+ past buys)"),
    ("two_of_four", (PROVEN.astype(int) + CLUSTER + DON + CC) >= 2, None,
     "at least two of proven_member, cluster, donations, committee_cluster"),
]
print("\nIDEAS:")
for nm, f, par, why in IDEAS:
    print(f"  {nm}: {why} (fires on {f.mean() * 100:.2f}% of buys)")

# ---- placebo: random clumped signals at the ideas' firing rates -------------------------------------------------
NSETS = 20
rng = np.random.default_rng(11)
who = (b[WHO].astype(object).fillna("?").astype(str) + "|" + b["m"]).values
uniq, inv = np.unique(who, return_inverse=True)
shares = [max(0.003, float(f.mean())) for _, f, _, _ in IDEAS]
P = []
for s in range(NSETS):
    for sh in shares:
        r = stats((rng.random(len(uniq)) < sh)[inv])
        r["set"] = s
        P.append(r)
PT = {h: np.array([abs(p[f"t{h}"]) for p in P if p[f"t{h}"] is not None and np.isfinite(p[f"t{h}"])]) for h in H}
BAR = {h: max(1.65, float(np.quantile(PT[h], 0.95))) if len(PT[h]) else 1.65 for h in H}
TOP = {h: float(np.quantile(PT[h], 0.995)) if len(PT[h]) else 3.3 for h in H}
print("\nplacebo bars |t| (95% / 99.5%): " + ", ".join(f"{h}d {BAR[h]:.2f}/{TOP[h]:.2f}" for h in H))


def verdict(r, sgn=1):
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


pv = pd.DataFrame({"set": [p["set"] for p in P], "v": [verdict(p).split(" ")[0] for p in P]})
luck = pv["v"].value_counts() / NSETS
pass_sets = pv.assign(ok=pv["v"].isin(["STRONG", "WORKS"])).groupby("set")["ok"].sum()
print(f"LUCK: per set of {len(IDEAS)} random ideas, on average: " + ", ".join(f"{k} {v:.2f}" for k, v in luck.items())
      + f"; sets with 1+ STRONG/WORKS by luck: {(pass_sets >= 1).mean() * 100:.0f}% (max in one set {int(pass_sets.max())})")

# ---- reference rows: the parents as the audit flags them ---------------------------------------------------------


def p(x):
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.2f}"


def cells(r):
    return " | ".join(f"{p(r[f'l{h}'])} ({r[f't{h}']:.1f}, n {r[f'n{h}']})" if r[f"l{h}"] is not None else "n/a"
                      for h in H)


print("\nPARENTS (reference, not counted): 20d | 60d | 125d | 250d (lift % vs same month+sector, t, n)")
for nm, f in (("proven_member", PROVEN), ("cluster", CLUSTER), ("donations", DON), ("committee_cluster", CC),
              ("unusual_volume_low", UV)):
    r = stats(f)
    print(f"  {nm} | {f.mean() * 100:.1f}% | {cells(r)} | {verdict(r)}")


def supply(f):
    rec = f & recent_m
    if not rec.any():
        return 0.0, 0.0, 0.0
    yrs = max(0.5, (fd[recent_m].max() - fd[recent_m].min()).days / 365.25)
    picks = b.loc[rec, ["ticker", "wk"]].drop_duplicates()
    nwk = b.loc[recent_m, "wk"].nunique()
    return rec.sum() / yrs, len(picks) / yrs, picks["wk"].nunique() / max(1, nwk)


def vs_parent(f, par, h, n=60):
    """idea's lift vs random clumped slices of its parent with the same share -> (real, percentile)"""
    real = lift(f, h)[0]
    if real is None or par is None or not par.any() or (f & ~par).any() or f.sum() == par.sum():
        return real, None
    sh = f[par].mean()
    pu, pi = np.unique(who[par], return_inverse=True)
    sims = []
    for _ in range(n):
        g = np.zeros(len(b), bool)
        g[np.where(par)[0]] = (rng.random(len(pu)) < sh)[pi]
        x = lift(g, h)[0]
        if x is not None:
            sims.append(x)
    return real, (float((np.array(sims) < real).mean() * 100) if sims else None)


rows = []
for nm, f, par, why in IDEAS:
    r = {"idea": nm, "share": float(f.mean()), **stats(f)}
    r["verdict"] = verdict(r)
    if par is not None and ((f & ~par).any() or f.sum() == par.sum()):
        print(f"  note: {nm} is not a strict subset of its parent; no parent comparison")
        par = None
    r["rest60"] = lift(par & ~f, 60)[0] if par is not None else None
    r["vp60"], r["vp125"] = vs_parent(f, par, 60)[1], vs_parent(f, par, 125)[1]
    r["per_yr"], r["picks_yr"], r["wk_cover"] = supply(f)
    rows.append(r)
R = pd.DataFrame(rows)

print("\nIDEAS: fires | 20d | 60d | 125d | 250d (lift %, t, n) | 60d early|recent | parent-rest 60d | "
      "beats random parent slices 60d/125d (percentile) | buys/yr, picks/yr (ticker-weeks), weeks with a pick "
      "(last 3y) | verdict")
for r in R.to_dict("records"):
    vp = "/".join("n/a" if r[k] is None or not np.isfinite(r[k]) else f"{r[k]:.0f}" for k in ("vp60", "vp125"))
    print(f"{r['idea']} | {r['share'] * 100:.2f}% | {cells(r)} | {p(r['e60'])}|{p(r['r60'])} | {p(r['rest60'])} | "
          f"{vp} | {r['per_yr']:.0f}, {r['picks_yr']:.0f}, {r['wk_cover'] * 100:.0f}% | {r['verdict']}")

NEED = 5 * 52
print(f"\nSUPPLY NEED: ~5 picks a week = ~{NEED}/year (a portfolio of ~h positions at an h-trading-day hold: "
      f"60 at 60d, 125 at 125d).")
won = R[R["verdict"].str.startswith(("STRONG", "WORKS"))]
print(f"SUMMARY: {len(won)} of {len(IDEAS)} ideas pass (STRONG/WORKS); luck alone gives about "
      f"{luck.get('STRONG', 0) + luck.get('WORKS', 0):.2f} per {len(IDEAS)} ideas.")
for r in won.to_dict("records"):
    print(f"  {r['idea']}: {r['verdict']}; {r['picks_yr']:.0f} picks/yr ({r['picks_yr'] / NEED * 100:.0f}% of need); "
          f"vs random slices of its parent: 60d {r['vp60']}, 125d {r['vp125']} percentile")
for lab in ("HARMFUL", "weak", "no edge", "too few"):
    print(f"  {lab}: " + ", ".join(R[R["verdict"].str.startswith(lab)]["idea"]))
