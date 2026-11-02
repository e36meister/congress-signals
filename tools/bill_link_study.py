"""Nov 2 (read-only, owner question): the two bill signals ("sponsored a <policy area> bill" and "a bill they back
passed a chamber after the trade") match a bill to a stock only by broad category. SNOW's two bills (NO FAKES Act,
AM Radio for Every Vehicle Act) had nothing to do with Snowflake. How common is that across every trade the signals
fired on, and would a stricter link work? For each (trade, bill) pair:
  - the bill's CRS subject terms (bill_stages cache) and whether they point at the stock's sector (subject match)
    or only the policy area does (category only);
  - whether the bill names the company;
  - how the trades in each group did afterwards (60 trading days vs SPY).
Prints the most-credited bills and a random sample to judge by hand. No writes, no account data."""
import glob, json, os, random, re, sys
from collections import Counter, defaultdict
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
from congress_signals import bill_stages as B

random.seed(7)
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
print("scored columns with bill/industry:", [c for c in sc.columns if "bill" in c or c in ("industry", "sector", "company")])
b = sc[(sc["tx_type"] == "buy")].copy()
b["filed_date"], b["trade_date"] = pd.to_datetime(b["filed_date"]), pd.to_datetime(b["trade_date"])
meta = {}
for p in ("data/cache/meta.json", "data/cache/ticker_meta.json"):
    if os.path.exists(p):
        meta = json.load(open(p)); print("meta from", p, len(meta)); break

# all bills with subject terms and backers
bills, backers = [], []
for pk in sorted(glob.glob("data/cache/bill_stages/bills-*.pkl")):
    part = pd.read_pickle(pk)
    bills += part["bills"]; backers += part["backers"]
bl = pd.DataFrame(bills, columns=["bill", "congress", "type", "title", "introduced", "policy", "subjects"]).drop_duplicates("bill")
bk = pd.DataFrame(backers, columns=["bill", "bioguide", "role", "joined", "withdrawn"])
print(f"bill_stages: {len(bl):,} bills, {bl['subjects'].map(len).gt(0).mean() * 100:.0f}% have subject terms")
info = bl.set_index("bill")[["title", "policy", "subjects"]].to_dict("index")
norm = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()[:100]
sp = bk[bk["role"] == "sponsor"].merge(bl[["bill", "title"]], on="bill")
by_title = {(r.bioguide, norm(r.title)): r.bill for r in sp.itertuples()}

def subj_sectors(bill):
    i = info.get(bill) or {}
    out = set()
    for s in i.get("subjects") or ():
        sl = s.lower()
        if B.SUBJECT_IGNORE.search(sl):
            continue
        for rx, v in B._SUBJECT_RX:
            if rx.search(sl):
                out.update(v)
    return out

def names_company(bill, t):
    k = E.company_key((meta.get(t) or {}).get("name")) if meta else ""
    i = info.get(bill) or {}
    txt = norm(i.get("title", "")) + " " + " ".join(norm(s) for s in i.get("subjects") or ())
    return bool(k) and len(k) >= 4 and re.search(r"\b" + re.escape(k) + r"\b", txt) is not None

# 1. sponsored-bill signal: rebuild the pairs exactly like engine (sponsor, 180 days before filing, policy -> sector)
mb = json.load(open("data/cache/bills.json"))
pairs, unmatched = [], 0
for r in b[b.get("f_bills", 0) > 0].itertuples():
    for d, pol, role, title in mb.get(r.bioguide, {}).get("rows", []):
        d = pd.Timestamp(d)
        if role != "sponsor" or not (r.filed_date - pd.Timedelta(days=180) < d <= r.filed_date):
            continue
        if r.sector not in E.POLICY_SECTORS.get(pol, []):
            continue
        bid = by_title.get((r.bioguide, norm(title)))
        unmatched += bid is None
        pairs.append(("sponsored", r.Index, r.ticker, r.sector, bid, title, pol))
# 2. bill-advanced signal: the bill whose action set it (engine's rule: first action after the trade, by filing)
ba = pd.read_pickle("data/cache/bill_actions.pkl").dropna(subset=["date"])
ba["date"] = pd.to_datetime(ba["date"])
for r in b[b.get("f_bill_advanced", 0) > 0].itertuples():
    m = ba[(ba["date"] > r.trade_date) & (ba["date"] <= r.filed_date)]
    m = m[[r.bioguide in (x or {}) for x in m["backers"]]]
    m = m[[r.sector in E.POLICY_SECTORS.get(p, []) for p in m["policy"]]].sort_values("date")
    if len(m):
        x = m.iloc[0]
        pairs.append(("advanced", r.Index, r.ticker, r.sector, x["bill"].replace("-", "-", 1), x["title"], x["policy"]))
P = pd.DataFrame(pairs, columns=["sig", "row", "ticker", "sector", "bill", "title", "policy"])
P["bill"] = P["bill"].fillna("")
print(f"\npairs: {len(P):,} ({(P.sig == 'sponsored').sum():,} sponsored, {(P.sig == 'advanced').sum():,} advanced); "
      f"sponsored bills not found in bill_stages: {unmatched:,}")
print(f"trades: sponsored signal on {b.get('f_bills', pd.Series(0)).gt(0).sum():,}, advanced on {b.get('f_bill_advanced', pd.Series(0)).gt(0).sum():,}")
P["has_subj"] = [bool((info.get(x) or {}).get("subjects")) for x in P["bill"]]
P["subj_hit"] = [r.sector in subj_sectors(r.bill) for r in P.itertuples()]
P["names_co"] = [names_company(r.bill, r.ticker) for r in P.itertuples()]
P["cls"] = np.where(P["names_co"], "names company", np.where(P["subj_hit"], "subject terms match sector",
                    np.where(P["has_subj"], "category only (subjects elsewhere)", "no subject terms yet")))
for sig in ("sponsored", "advanced"):
    print(f"\n{sig}: link strength")
    print(P[P.sig == sig]["cls"].value_counts().to_string())

# returns per class (trade level: a trade counts in its best class)
rank = {"names company": 0, "subject terms match sector": 1, "category only (subjects elsewhere)": 2, "no subject terms yet": 3}
for sig in ("sponsored", "advanced"):
    q = P[P.sig == sig].assign(k=lambda d: d["cls"].map(rank)).sort_values("k").drop_duplicates("row")
    rows = b.loc[q["row"]].copy(); rows["cls"] = q["cls"].values
    rows = rows[rows["entry_px"].notna()]
    fr = E.forward_returns(rows, px, 60)
    rows["x"] = fr["excess"].values
    allb = b[b["entry_px"].notna() & (b.get(("f_bills" if sig == "sponsored" else "f_bill_advanced"), 0) == 0)]
    base = E.forward_returns(allb.sample(min(len(allb), 20000), random_state=1), px, 60)["excess"]
    print(f"\n{sig}: 60-day result vs SPY by link (buys without the signal: {base.mean() * 100:+.2f}%, n {base.notna().sum():,})")
    for c, g in rows.groupby("cls"):
        x = g["x"].dropna()
        se = x.std() / np.sqrt(len(x)) if len(x) > 2 else np.nan
        print(f"  {c}: {x.mean() * 100:+.2f}% (±{1.96 * se * 100:.2f}), beat SPY {(x > 0).mean() * 100:.0f}%, n {len(x):,}")

# most-credited bills
print("\nMOST-CREDITED BILLS (bill | policy | signal | trades | tickers | link | subjects):")
g = P.groupby(["bill", "title", "policy", "sig"]).agg(n=("row", "nunique"), tk=("ticker", lambda s: ", ".join(pd.Series(s).value_counts().index[:6])),
                                                      cls=("cls", lambda s: s.value_counts().index[0])).reset_index()
for r in g.sort_values("n", ascending=False).head(40).itertuples():
    sj = "; ".join(((info.get(r.bill) or {}).get("subjects") or ())[:8])
    print(f"  {r.bill} | {r.title[:90]} | {r.policy} | {r.sig} | {r.n} | {r.tk} | {r.cls} | {sj[:220]}")

# random sample to judge by hand
print("\nRANDOM SAMPLE (signal | ticker | company | industry | bill | title | subjects):")
for r in P.sample(min(90, len(P)), random_state=3).itertuples():
    m = meta.get(r.ticker) or {}
    sj = "; ".join(((info.get(r.bill) or {}).get("subjects") or ())[:10])
    print(f"  {r.sig} | {r.ticker} | {(m.get('name') or '')[:30]} | {m.get('industry')} | {r.bill} | {r.title[:80]} | {r.cls} | {sj[:260]}")
