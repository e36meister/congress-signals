"""Nov 2 (read-only, owner: "do that"): the stricter bill link. Does it matter when the COMPANY lobbied on a bill
the trading member backs (sponsor or cosponsor), or that sits in one of the member's committees?
Reads the lobbying history again with bill numbers kept (engine.lda_bills; nothing is saved back), for the most-bought
companies first, within ~95 minutes. Then, point in time for every buy of those companies:
  L  = bills the company named in lobbying reports posted from 2 years before the trade up to the filing date
  A  = the member backed one of them (sponsor, or cosponsor who joined by the filing date and hadn't withdrawn)
  C  = one of them was referred to a committee the member sat on at the trade (when committee ids are on file)
and compares the 60-trading-day result vs SPY with the other buys of the same companies (so company effects cancel).
Prints aggregate results and example links only."""
import glob, os, random, sys, time
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

t0 = time.time()
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "LDA_API_KEY": os.environ.get("LDA_API_KEY", ""), "TIME_BUDGET_MIN": 155}
E._RUN_START = time.time()
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
meta = __import__("json").load(open("data/cache/ticker_meta.json"))
b = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()].copy()
b["filed_date"], b["trade_date"] = pd.to_datetime(b["filed_date"]), pd.to_datetime(b["trade_date"])
order = list(b["ticker"].value_counts().index)
print(f"buys with prices: {len(b):,} of {len(order):,} companies; LDA key set: {bool(cfg['LDA_API_KEY'])}")

lb = E.collect_lobbying(cfg, meta, order, deep=True)
cache = __import__("json").load(open("data/cache/lobbying.json"))
done = [t for t in order if (cache.get(t) or {}).get("bills")]
print(f"\nlobbying re-read with bill numbers: {len(done):,} companies in {(time.time() - t0) / 60:.0f} min "
      f"({b['ticker'].isin(done).mean() * 100:.0f}% of buys)")
lb = lb[lb["ticker"].isin(done)]
nb = lb["bills"].map(len)
print(f"reports: {len(lb):,}; naming at least one bill: {(nb > 0).mean() * 100:.0f}%; bills named per report (when any): "
      f"{nb[nb > 0].median() if (nb > 0).any() else 0:.0f}; companies naming any bill: "
      f"{lb[nb > 0]['ticker'].nunique():,} of {lb['ticker'].nunique():,} that lobby")

# bills: backers with join/withdraw dates, committee referrals, titles
bills, backers, refs = [], [], []
for pk in sorted(glob.glob("data/cache/bill_stages/bills-*.pkl")):
    part = pd.read_pickle(pk)
    bills += part["bills"]; backers += part["backers"]; refs += part["refs"]
title = {r[0]: r[3] for r in bills}
bk = pd.DataFrame(backers, columns=["bill", "bioguide", "role", "joined", "withdrawn"])
bk["joined"], bk["withdrawn"] = pd.to_datetime(bk["joined"], errors="coerce"), pd.to_datetime(bk["withdrawn"], errors="coerce")
back = {}
for r in bk.itertuples():
    back.setdefault((r.bill, r.bioguide), []).append((r.joined, r.withdrawn, r.role))
rf = {}
for bill, cid, d in refs:
    rf.setdefault(bill, set()).add(str(cid).upper())
named = sum(1 for x in set().union(*lb["bills"]) if x in title) if len(lb) else 0
print(f"bill numbers that match a bill on file: {named:,} of {len(set().union(*lb['bills'])) if len(lb) else 0:,}")

# per company: (posted, bills) sorted
lob = {t: sorted(((p, set(bs)) for p, bs in zip(g["posted"], g["bills"]) if bs and pd.notna(p)), key=lambda x: x[0])
       for t, g in lb.groupby("ticker")}
has_cids = "cids" in b.columns
X = b[b["ticker"].isin(done)].copy()
A, C, ex = np.zeros(len(X)), np.zeros(len(X)), []
for i, r in enumerate(X.itertuples()):
    L = set()
    for p, bs in lob.get(r.ticker, []):
        if r.trade_date - pd.Timedelta(days=730) < p <= r.filed_date:
            L |= bs
    if not L:
        continue
    for bill in L:
        for j, w, role in back.get((bill, r.bioguide), []):
            if pd.notna(j) and j <= r.filed_date and (pd.isna(w) or w > r.trade_date):
                A[i] = 1
                if len(ex) < 400:
                    ex.append((r.ticker, getattr(r, "member", ""), role, bill, title.get(bill, "")[:90], str(r.trade_date.date())))
                break
        if has_cids and isinstance(r.cids, (list, tuple, set, np.ndarray)) and rf.get(bill, set()) & {str(c).upper() for c in r.cids}:
            C[i] = 1
X["A"], X["C"] = A, C
X["Lany"] = [1.0 if any(r.trade_date - pd.Timedelta(days=730) < p <= r.filed_date for p, _ in lob.get(r.ticker, [])) else 0.0
             for r in X.itertuples()]
fr = E.forward_returns(X, px, 60)
X["x"] = fr["excess"].values
X = X[X["x"].notna()]
X["q"] = X["filed_date"].dt.to_period("Q")


def line(lab, d):
    if len(d) < 5:
        return f"  {lab}: n {len(d)}"
    k = d.groupby("q")["x"].mean()
    se = k.std() / np.sqrt(len(k)) if len(k) > 2 else np.nan
    return f"  {lab}: {d['x'].mean() * 100:+.2f}% (t {d['x'].mean() / se if se else np.nan:.1f} by quarter), beat SPY {(d['x'] > 0).mean() * 100:.0f}%, n {len(d):,}"


print(f"\n60-day result vs SPY, buys of these {X['ticker'].nunique():,} companies:")
print(line("all", X))
print(line("company lobbied on any named bill around the trade", X[X["Lany"] == 1]))
print(line("A: member backs a bill the company lobbied on", X[X["A"] == 1]))
print(line("   same companies, other buys", X[(X["A"] == 0) & X["ticker"].isin(X.loc[X["A"] == 1, "ticker"])]))
if has_cids:
    print(line("C: a lobbied bill sits in the member's committee", X[X["C"] == 1]))
    print(line("   same companies, other buys", X[(X["C"] == 0) & X["ticker"].isin(X.loc[X["C"] == 1, "ticker"])]))
    print(line("A or C", X[(X["A"] == 1) | (X["C"] == 1)]))
else:
    print("  (no committee ids on the trades; C not measured)")
# company-adjusted: each link trade minus the average of the same company's other buys in the same year
for lab, col in (("A", "A"),) + ((("C", "C"),) if has_cids else ()):
    X["y"] = X["filed_date"].dt.year
    base = X[X[col] == 0].groupby(["ticker", "y"])["x"].mean()
    d = X[X[col] == 1].join(base.rename("base"), on=["ticker", "y"]).dropna(subset=["base"])
    diff = d["x"] - d["base"]
    print(f"  {lab} minus the same company's other buys that year: {diff.mean() * 100:+.2f}% "
          f"(±{1.96 * diff.std() / np.sqrt(max(len(diff), 1)) * 100:.2f}), n {len(diff):,}")
    for half, g in d.groupby(d["filed_date"] < d["filed_date"].median()):
        dd = g["x"] - g["base"]
        print(f"     {'earlier' if half else 'later'} half: {dd.mean() * 100:+.2f}%, n {len(dd):,}")
print(f"\nshare of buys with A: {X['A'].mean() * 100:.1f}%" + (f", with C: {X['C'].mean() * 100:.1f}%" if has_cids else ""))
print("\nEXAMPLE A LINKS (ticker | member | role | bill | title | trade date):")
random.seed(5)
for e in random.sample(ex, min(40, len(ex))):
    print("  " + " | ".join(str(v) for v in e))
print(f"\ndone in {(time.time() - t0) / 60:.0f} min")
