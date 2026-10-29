"""Read-only study: does where a bill stood when a member traded (and how they voted on it) predict how the trade did?

Run it from Actions -> Research (read-only) -> script:
    bill_stage_study.py                                   114th Congress (2015) to now
    bill_stage_study.py --first 112 --last 113 --label pre114    earlier Congresses as their own section

Uses the saved scores and prices from the last full update; downloads bill histories (GovInfo) and roll-call votes
(Voteview) itself. Nothing is saved back and no orders are placed. Prints only aggregate results (the run log is
public); per-trade rows go to out/ (the run's downloadable files, kept 7 days).

What "tradable" means here: the app can only act on a trade once it is disclosed, so a signal must be known on the
filing date. Bill steps and votes dated on or before the filing date qualify; anything after the filing is shown
separately as hindsight (it says whether members traded ahead of bills, but cannot be traded on).
If any download fails the report says so at the top and the run ends with an error, so it is never silently thin."""
import sys, os, argparse, datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
import congress_signals.engine as E
from congress_signals import bill_stages as B

ap = argparse.ArgumentParser()
ap.add_argument("--first", type=int, default=B.FIRST_CONGRESS)
ap.add_argument("--last", type=int, default=B.current_congress())
ap.add_argument("--label", default="")
ap.add_argument("--hold", type=int, default=None, help="main holding period in trading days (default: the app's)")
args = ap.parse_args()

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
HOLD = args.hold or cfg["HOLD_DAYS"]
HOLDS = sorted({20, HOLD, 125, 250})
OUT = "out"
os.makedirs(OUT, exist_ok=True)
SUF = f"_{args.label}" if args.label else ""
problems, lines = [], []


def say(s=""):
    print(s)
    lines.append(s)


# ---------------------------------------------------------------------------- data
sc_path, px_path = "data/cache/scored.pkl", "data/cache/prices.pkl"
for p in (sc_path, px_path):
    if not os.path.exists(p):
        sys.exit(f"MISSING saved data: {p} (the Research run restores it from the daily run's cache)")
sc = pd.read_pickle(sc_path)
px = pd.read_pickle(px_path)
lo, _ = B.congress_span(args.first)
_, hi = B.congress_span(args.last)
sc = sc[(pd.to_datetime(sc["trade_date"]) >= lo) & (pd.to_datetime(sc["trade_date"]) < hi)].copy()
sc = sc[sc["tx_type"].isin(["buy", "sell"]) & sc["ticker"].notna()]

cache = os.path.join(cfg["DATA_DIR"], "cache", "bill_stages")
cong = range(args.first, args.last + 1)
tables = B.build_bills(cache, cong, E.POLICY_SECTORS, log=print, problems=problems)
votes = B.build_votes(os.path.join(cache, "voteview"), cong, log=print, problems=problems)

# committee seats each member held on the trade date (quarterly roster snapshots; same as the daily run)
committees = E.load_committees(cfg)
history = E.load_committee_history(cfg, committees)
if not history:
    problems.append("Committee rosters over time: none loaded; committee links are MISSING")
mems = E.committee_records(sc, history, committees)
sc["cids"] = [list((m or {}).get("cids") or []) for m in mems]
if "bioguide" not in sc:
    sc["bioguide"] = [(m or {}).get("bioguide") for m in mems]
no_cids = sc["cids"].map(len).eq(0) & sc["bioguide"].notna()
if no_cids.mean() > 0.2:
    problems.append(f"Committee seats unknown for {no_cids.mean():.0%} of trades by identified members")

feat = B.trade_bill_features(sc, tables, votes, log=print)
sc = sc.join(feat)

E.register_opens(cfg, px)                         # buy/sell at the open, like the backtest (closes if none saved)
for h in HOLDS:
    fr = E.forward_returns(sc, px, h)
    sc[f"x{h}"] = np.where(fr["closed"], fr["excess"], np.nan)
sc["sign"] = np.where(sc["tx_type"] == "sell", -1.0, 1.0)
for h in HOLDS:
    sc[f"y{h}"] = sc["sign"] * sc[f"x{h}"]           # sells scored as if avoided/shorted: a fall counts as a win
sc["fq"] = pd.to_datetime(sc["filed_date"]).dt.to_period("Q").astype(str)
sc["half"] = np.where(pd.to_datetime(sc["filed_date"]) < pd.Timestamp("2021-01-01"), "2015-2020", "2021 on")
if "score" in sc:
    sc["score_q"] = sc.groupby("tx_type")["score"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) if s.notna().sum() > 50 else 0)
else:
    sc["score_q"] = 0

# ---------------------------------------------------------------------------- stats
def ols_cluster(y, d, ctrl=None, cl=None):
    """Effect of indicator d on y (with optional control dummies), standard error clustered by filing quarter."""
    y, d = np.asarray(y, float), np.asarray(d, float)
    X = [np.ones_like(d), d]
    if ctrl is not None:
        c = pd.get_dummies(pd.Series(ctrl).astype(str), drop_first=True).values.astype(float)
        X += [c[:, j] for j in range(c.shape[1])]
    X = np.column_stack(X)
    ok = np.isfinite(y) & np.isfinite(X).all(1)
    X, y, g = X[ok], y[ok], np.asarray(cl)[ok]
    if len(y) < 30 or X[:, 1].sum() < 10 or (1 - X[:, 1]).sum() < 10:
        return np.nan, np.nan
    XtX = np.linalg.pinv(X.T @ X)
    b = XtX @ X.T @ y
    e = y - X @ b
    meat = np.zeros((X.shape[1], X.shape[1]))
    for gv in np.unique(g):
        m = g == gv
        s = X[m].T @ e[m]
        meat += np.outer(s, s)
    G = len(np.unique(g))
    V = XtX @ meat @ XtX * (G / max(G - 1, 1))
    return float(b[1]), float(np.sqrt(V[1, 1]))


def pct(v):
    return "   n/a" if not np.isfinite(v) else f"{v * 100:+6.2f}%"


def group_line(df, mask, h):
    g = df[mask & df[f"y{h}"].notna()]
    if len(g) < 30:
        return f"n {len(g):>5}  (too few)"
    se = E._cluster_se(g[f"y{h}"].values, g["filed_date"].values)
    return f"n {len(g):>5}  avg {pct(g[f'y{h}'].mean())}  t {g[f'y{h}'].mean() / se:5.1f}"


def lift(df, on, h, ctrl=True):
    b, se = ols_cluster(df[f"y{h}"], on.astype(float), df["score_q"] if ctrl else None, df["fq"])
    return b, se


STAGE_BUCKETS = [("none / not in play", lambda s: s.isna() | (s < 1)), ("introduced/referred", lambda s: s == 1),
                 ("hearing or markup", lambda s: s.isin([2, 3])), ("reported out of committee", lambda s: s == 4),
                 ("passed one or both chambers", lambda s: s.isin([5, 6])), ("sent to President / law", lambda s: s >= 7)]

# ---------------------------------------------------------------------------- report
say(f"# Bills at the time of the trade{(' - ' + args.label) if args.label else ''}")
say(f"Congresses {args.first}-{args.last} ({lo.date()} to {min(hi, pd.Timestamp.today()).date()}); "
    f"run {dt.date.today()}; main hold {HOLD} trading days; returns vs S&P from the first open after filing.")
say("Sells are scored as avoided/shorted (a fall after the sale counts as a win).")
say("")
if problems:
    say("## DATA PROBLEMS (results below are incomplete)")
    for p in problems:
        say(f"- {p}")
    say("")
nb = len(tables["bills"])
say(f"Data: {nb:,} bills, {len(tables['events']):,} dated steps, {len(votes):,} member votes on bills "
    f"(votes through {votes['date'].max().date() if len(votes) else 'n/a'}).")
say(f"Trades analyzed: {len(sc):,} ({(sc.tx_type == 'buy').sum():,} buys, {(sc.tx_type == 'sell').sum():,} sales); "
    f"with a {HOLD}-day result: {sc[f'y{HOLD}'].notna().sum():,}.")
has_sp = sc["sponsor_s_t"].fillna(0) >= 1 if "sponsor_s_t" in sc else pd.Series(False, index=sc.index)
has_cm = sc["committee_s_t"].fillna(0) >= 1 if "committee_s_t" in sc else pd.Series(False, index=sc.index)
has_v = sc[[c for c in ("vote_pre_all_n", "vote_t2f_all_n") if c in sc]].fillna(0).sum(axis=1) > 0
say("")
say("## How often a trade links to a bill in play")
say("| Year | Trades | Backs a bill in the sector | Bill (past referral) in their committee | Voted on a sector bill (by filing) |")
say("|---|---|---|---|---|")
yr = pd.to_datetime(sc["trade_date"]).dt.year
for y, g in sc.groupby(yr):
    say(f"| {y} | {len(g):,} | {has_sp[g.index].mean():.0%} | {has_cm[g.index].mean():.0%} | {has_v[g.index].mean():.0%} |")

cands = []      # (name, mask, tradable)


def col(name, fill=0):
    """A feature column, or all-`fill` when no trade had it (e.g. no votes in range)."""
    return sc[name].fillna(fill) if name in sc else pd.Series(fill, index=sc.index)


def flag(name):
    return col(name, False).astype(bool)


def section_stage(link):
    st_ = col(f"{link}_s_t")
    say("")
    say(f"## Stage of the most advanced {link}-linked bill on the trade date")
    for side in ("buy", "sell"):
        d = sc[sc.tx_type == side]
        say(f"**{side}s** ({HOLD}-day result vs S&P)")
        say("```")
        for nm, fn in STAGE_BUCKETS:
            say(f"  {nm:<30} {group_line(d, fn(st_[d.index]), HOLD)}")
        say("```")
    for k, nm in ((2, "hearing+"), (4, "reported+"), (5, "passed a chamber+")):
        cands.append((f"{link} bill at {nm} on trade date", st_ >= k, True))
        cands.append((f"{link} bill (focused) at {nm} on trade date", col(f"{link}_focused_s_t") >= k, True))
    cands.append((f"any {link}-linked bill in play", st_ >= 1, True))


def section_moves(link):
    say("")
    say(f"## When the {link}-linked bill moved (a step up the ladder), relative to the trade")
    rows = [("moved in the 30 days before the trade", f"{link}_adv_pre30", True),
            ("moved between trade and filing", f"{link}_adv_t2f", True),
            ("moved within 30 days after the trade (part after filing)", f"{link}_adv_t30", False),
            ("moved in the 30 days after filing (hindsight)", f"{link}_adv_f30", False),
            ("moved in the 90 days after filing (hindsight)", f"{link}_adv_f90", False)]
    linked = col(f"{link}_s_t") >= 1
    moved_any = pd.concat([flag(r[1]) for r in rows], axis=1).any(axis=1)
    for side in ("buy", "sell"):
        d = sc[(sc.tx_type == side) & linked]
        say(f"**{side}s with a {link}-linked bill** ({HOLD}-day result vs S&P)")
        say("```")
        say(f"  {'linked, bill did not move in any window':<58} {group_line(d, ~moved_any[d.index], HOLD)}")
        for nm, c_, _ in rows:
            say(f"  {nm:<58} {group_line(d, flag(c_)[d.index], HOLD)}")
        say("```")
    for nm, c_, trad in rows:
        cands.append((f"{link} bill {nm}", flag(c_), trad))
    cands.append((f"{link} bill (focused) moved between trade and filing", flag(f"{link}_focused_adv_t2f"), True))
    cands.append((f"{link} bill (focused) moved in the 90 days after filing (hindsight)",
                  flag(f"{link}_focused_adv_f90"), False))


section_stage("sponsor")
section_moves("sponsor")
section_stage("committee")
section_moves("committee")
cands.append(("member is the lead sponsor of a bill in play", col("sponsor_n_own") > 0, True))

say("")
say("## How the member voted on bills in the trade's industry")
say("Aligned = bought and voted for the bill, or sold and voted against it. Contested = the roll call was close "
    "(35-65% yea); near-unanimous votes say little about a member.")
for w, wn, trad in (("pre", "in the 90 days before the trade", True), ("t2f", "between trade and filing", True),
                    ("post", "in the 90 days after filing (hindsight)", False)):
    for kind, kn in (("linked", "bills they back / from their committee"), ("all", "any bill in the industry"),
                     ("other_contested", "close votes on other industry bills")):
        vc = f"vote_{w}_{kind}_align" if kind != "other_contested" else f"vote_{w}_other_contested_align"
        if vc not in sc:
            continue
        say(f"**{kn}, {wn}** ({HOLD}-day result vs S&P)")
        say("```")
        for side in ("buy", "sell"):
            d = sc[sc.tx_type == side]
            a = d[vc].fillna(0)
            say(f"  {side:<5} aligned   {group_line(d, a > 0, HOLD)}")
            say(f"  {side:<5} contrary  {group_line(d, a < 0, HOLD)}")
            say(f"  {side:<5} no vote   {group_line(d, a == 0, HOLD)}")
        say("```")
        cands.append((f"vote aligned: {kn}, {wn}", col(vc) > 0, trad))
        cands.append((f"vote contrary: {kn}, {wn}", col(vc) < 0, trad))

# a few combinations of the pieces above
cands += [
    ("committee bill at hearing+ AND it moved between trade and filing",
     (col("committee_s_t") >= 2) & flag("committee_adv_t2f"), True),
    ("backs a bill that moved between trade and filing AND voted aligned by filing",
     flag("sponsor_adv_t2f") & ((col("vote_pre_linked_align") > 0) | (col("vote_t2f_linked_align") > 0)), True),
    ("committee bill moved in the 30 days before the trade AND close vote aligned before the trade",
     flag("committee_adv_pre30") & (col("vote_pre_other_contested_align") > 0), True),
    # bigger moves: a linked bill passed a chamber (or more) between the trade and the filing / after filing
    ("sponsor bill passed a chamber+ between trade and filing", col("sponsor_top_t2f") >= 5, True),
    ("committee bill passed a chamber+ between trade and filing", col("committee_top_t2f") >= 5, True),
    ("sponsor bill passed a chamber+ in the 90 days after filing (hindsight)", col("sponsor_top_f90") >= 5, False),
    ("committee bill passed a chamber+ in the 90 days after filing (hindsight)", col("committee_top_f90") >= 5, False),
]

# ---------------------------------------------------------------------------- ranking
say("")
say("## Candidate signals, ranked")
say(f"Lift = extra {HOLD}-day result vs S&P for trades with the signal compared with trades without it, "
    "controlling for the trade's current score (quintile), so it shows what the bill data adds to today's model. "
    "t: clustered by filing quarter. Many signals are tested at once, so a few will reach |t| 2 by luck alone: "
    "a keeper needs |t| >= 3, a false-discovery rate (q) under 10%, 100+ trades and the same sign in both halves "
    "of the period; |t| 2-3 with the same sign in both halves is 'promising' (re-test, don't trade yet).")
res = []
for name, mask, trad in cands:
    mask = pd.Series(mask, index=sc.index).fillna(False).astype(bool)
    for side in ("buy", "sell"):
        d = sc[sc.tx_type == side]
        m = mask[d.index]
        n_on = int((m & d[f"y{HOLD}"].notna()).sum())
        b, se = lift(d, m, HOLD)
        b1, _ = lift(d[d.half == "2015-2020"], m[d.half == "2015-2020"], HOLD)
        b2, _ = lift(d[d.half == "2021 on"], m[d.half == "2021 on"], HOLD)
        bl = {h: lift(d, m, h)[0] for h in HOLDS if h != HOLD}
        res.append({"signal": name, "side": side, "tradable": trad, "n_with": n_on,
                    "lift": b, "t": b / se if se else np.nan, "lift_2015_2020": b1, "lift_2021_on": b2,
                    **{f"lift_{h}d": v for h, v in bl.items()}})
R = pd.DataFrame(res)
R["stable"] = np.sign(R["lift_2015_2020"]) == np.sign(R["lift_2021_on"])
# Benjamini-Hochberg q-values over every test run (normal approximation for the p-values)
from math import erfc, sqrt
p_ = R["t"].abs().map(lambda t: erfc(t / sqrt(2)) if np.isfinite(t) else 1.0).values
order = np.argsort(p_)
q_ = np.empty_like(p_)
q_[order] = np.minimum.accumulate((p_[order] * len(p_) / np.arange(1, len(p_) + 1))[::-1])[::-1]
R["q"] = np.minimum(q_, 1.0)
base = R["tradable"] & R["stable"] & (R["n_with"] >= 100)
R["keeper"] = base & (R["t"].abs() >= 3) & (R["q"] < 0.10)
R["promising"] = base & ~R["keeper"] & (R["t"].abs() >= 2)
R["abs_t"] = R["t"].abs()
R = R.sort_values(["tradable", "keeper", "abs_t"], ascending=False).drop(columns="abs_t")
R.to_csv(f"{OUT}/bill_candidate_signals{SUF}.csv", index=False)
other = [c for c in R if c.startswith("lift_") and c.endswith("d")]
for trad, title in ((True, "Tradable (known on the filing date)"), (False, "Hindsight only (explains, cannot be traded)")):
    say("")
    say(f"### {title}")
    say("| Signal | Side | Trades with it | Lift | t | q | 2015-20 | 2021 on | " + " | ".join(o.replace("lift_", "") for o in other) + " | Verdict |")
    say("|---|---|---|---|---|---|---|---|" + "---|" * len(other) + "---|")
    for r in R[R.tradable == trad].itertuples():
        v = "keeper" if r.keeper else "promising" if r.promising else ""
        say(f"| {r.signal} | {r.side} | {r.n_with:,} | {pct(r.lift)} | {r.t:.1f} | {r.q:.2f} | {pct(r.lift_2015_2020)} | "
            f"{pct(r.lift_2021_on)} | " + " | ".join(pct(getattr(r, o)) for o in other) + f" | {v} |")

keep, prom = R[R.keeper], R[R.promising]
masks = {(n, t_): m for n, m, t_ in cands}
# does a keeper add anything new, or is it mostly trades the engine's existing bill features already flag?
EXISTING = [c for c in ("f_bills", "f_bill_advanced", "f_sector_bill_momentum", "f_markup_after", "f_vote_sector")
            if c in sc]


def overlap(r):
    m = pd.Series(masks[(r.signal, r.tradable)], index=sc.index).fillna(False).astype(bool) & (sc.tx_type == r.side)
    if not EXISTING or not m.any():
        return ""
    return "; already flagged by " + ", ".join(f"{c} {(sc.loc[m, c].fillna(0) > 0).mean():.0%}" for c in EXISTING)


say("")
say("## Verdict")
if len(keep):
    say("Worth moving forward with (tradable, 100+ trades, |t| >= 3, q < 10%, same direction in both halves):")
    for r in keep.itertuples():
        say(f"- {r.signal} ({r.side}s): {pct(r.lift)} per trade, t {r.t:.1f}, {r.n_with:,} trades{overlap(r)}")
if len(prom):
    say("Promising, re-test before using (|t| 2-3, same direction in both halves):")
    for r in prom.itertuples():
        say(f"- {r.signal} ({r.side}s): {pct(r.lift)} per trade, t {r.t:.1f}, {r.n_with:,} trades{overlap(r)}")
if not len(keep):
    say("No tradable bill signal cleared the keeper bar. "
        "Look at the hindsight table: if trades still lead bill movement there, members are trading ahead of bills, "
        "but the free sector-level links are too coarse to catch it at filing time (that is where ticker-level "
        "tagging would help).")
say("")
say("Limits: links are sector-level (a drug-pricing bill links to every drug maker, helpful or not); subject terms "
    "and bill sectors are assigned by CRS later; committee seats come from quarterly snapshots; a bill whose text "
    "rode on another bill (a companion or vehicle) is tracked under its own number only.")

cols = ["ticker", "tx_type", "trade_date", "filed_date", "bioguide", "sector", "score"] + \
       [c for c in feat.columns] + [f"x{h}" for h in HOLDS]
sc[[c for c in cols if c in sc]].to_csv(f"{OUT}/bill_trade_features{SUF}.csv.gz", index=False)
open(f"{OUT}/bill_stage_study{SUF}.md", "w").write("\n".join(lines) + "\n")
print(f"\nWrote {OUT}/bill_stage_study{SUF}.md, {OUT}/bill_candidate_signals{SUF}.csv, {OUT}/bill_trade_features{SUF}.csv.gz")
if problems:
    sys.exit("Data problems above: the study ran on incomplete data (see the top of the report)")
