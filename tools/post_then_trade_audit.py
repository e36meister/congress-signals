"""Read-only backtest (owner, Nov 5): the "trade after another member's post" alert. For every disclosed buy or sale,
was the company named in a post by a DIFFERENT member (not a repost) in the 30 days before the trade? Do those trades
do better (in the trade's direction, vs the S&P 500) than the same member group's other trades, and than other trades
of the same stocks? Measured from the trade date and from the filing date (when the alert could fire).
Also: how often the alert would fire, and the last 30 days' matches under today's rules. Public data only."""
import sys, json
from collections import defaultdict
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
import congress_signals.social as S

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl").ffill()
idx = S.load_index(cfg)
if not idx:
    sys.exit("no posts index (social cache not restored)")
men = idx.get("mentions") or {}

# who named each ticker when (other members' own posts, not reposts)
named = defaultdict(list)         # ticker -> [(date, bioguide)]
first_post = None
for b, m in men.items():
    if not isinstance(b, str) or len(b) != 7:
        continue                   # offices, committees, campaign accounts: not a member
    for t, lst in m.items():
        for d, tone, rp in lst:
            if not rp:
                named[t].append((str(d)[:10], b))
                first_post = min(first_post or str(d)[:10], str(d)[:10])
for t in named:
    named[t].sort()
print(f"posts naming companies: {sum(len(v) for v in named.values()):,} mentions of {len(named):,} companies, from {first_post}")

tr = sc[sc["tx_type"].isin(["buy", "sell"]) & sc["bioguide"].apply(lambda x: isinstance(x, str))].copy()
tr["td"] = pd.to_datetime(tr["trade_date"].where(tr["trade_date"].notna(), tr["filed_date"]))
tr["fd"] = pd.to_datetime(tr["filed_date"])
tr = tr[tr["td"] >= pd.Timestamp(first_post) + pd.Timedelta(days=30)].reset_index(drop=True)

arr, cols, dates = px.to_numpy(dtype=float), {c: i for i, c in enumerate(px.columns)}, px.index.values
spy = arr[:, cols["SPY"]]


def excess(t, d, h):
    c = cols.get(t)
    if c is None:
        return np.nan
    p = np.searchsorted(dates, np.datetime64(d), side="left")
    if p + h >= len(dates):
        return np.nan
    a0, a1, s0, s1 = arr[p, c], arr[p + h, c], spy[p], spy[p + h]
    if not all(np.isfinite([a0, a1, s0, s1])) or a0 <= 0 or s0 <= 0:
        return np.nan
    return a1 / a0 - s1 / s0


sign = np.where(tr["tx_type"] == "sell", -1.0, 1.0)
for h, lab in ((21, "1m"), (63, "3m")):
    tr[f"t{lab}"] = sign * np.array([excess(t, d, h) for t, d in zip(tr["ticker"], tr["td"])])
    tr[f"f{lab}"] = sign * np.array([excess(t, d, h) for t, d in zip(tr["ticker"], tr["fd"])])

n_post, posters = [], []
for t, d, b in zip(tr["ticker"], tr["td"], tr["bioguide"]):
    lo, hi = (d - pd.Timedelta(days=30)).strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d")
    who = {pb for pd_, pb in named.get(t, []) if lo <= pd_ <= hi and pb != b}
    n_post.append(len(who))
    posters.append(sorted(who))
tr["n_post"] = n_post
tr["matched"] = tr["n_post"] > 0
print(f"trades checked: {len(tr):,} ({tr['td'].min().date()} to {tr['td'].max().date()}); "
      f"after another member's post naming the company: {int(tr['matched'].sum()):,} ({tr['matched'].mean() * 100:.0f}%)")


def stat(s):
    s = s.dropna()
    if len(s) < 5:
        return f"n {len(s)}"
    t_ = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if s.std(ddof=1) > 0 else 0
    return f"avg {s.mean() * 100:+.2f}% | median {s.median() * 100:+.2f}% | beat S&P {(s > 0).mean() * 100:.0f}% | n {len(s):,} | t {t_:+.1f}"


print("\nIn the trade's direction vs the S&P 500 (sales: positive = the stock did worse than the S&P after the sale)")
for col, lab in (("f1m", "1 month from the filing date (when the alert fires)"), ("f3m", "3 months from the filing date"),
                 ("t3m", "3 months from the trade date")):
    print(f"\n{lab}")
    for name, m in (("after a post", tr["matched"]), ("no post", ~tr["matched"])):
        for ty in ("buy", "sell"):
            print(f"   {name:13} {ty:4}: {stat(tr.loc[m & (tr['tx_type'] == ty), col])}")
    for k, lab2 in ((1, "1 poster"), (2, "2 posters"), (3, "3+ posters")):
        m = (tr["n_post"] == k) if k < 3 else (tr["n_post"] >= 3)
        print(f"   {lab2:13} all : {stat(tr.loc[m, col])}")

# same stocks: matched minus unmatched trades of the same ticker and direction (popular stocks get posted about more)
print("\nSame stock, same direction: trades after a post minus that stock's other trades (3 months from filing)")
diffs, w = [], []
for (t, ty), g in tr.dropna(subset=["f3m"]).groupby(["ticker", "tx_type"]):
    a, b_ = g.loc[g["matched"], "f3m"], g.loc[~g["matched"], "f3m"]
    if len(a) and len(b_):
        diffs.append(a.mean() - b_.mean())
        w.append(min(len(a), len(b_)))
if diffs:
    d_, w_ = np.array(diffs), np.array(w, dtype=float)
    mean = (d_ * w_).sum() / w_.sum()
    print(f"   weighted difference {mean * 100:+.2f}% over {len(d_):,} stock/direction pairs; "
          f"after-post trades did better in {(d_ > 0).mean() * 100:.0f}% of them")

# by year
print("\nBy year (3 months from filing, after a post minus no post)")
for y, g in tr.groupby(tr["td"].dt.year):
    a, b_ = g.loc[g["matched"], "f3m"].dropna(), g.loc[~g["matched"], "f3m"].dropna()
    if len(a) >= 10 and len(b_) >= 10:
        print(f"   {y}: {(a.mean() - b_.mean()) * 100:+.2f}% (after a post n {len(a):,}, no post n {len(b_):,})")

# how often it would fire: last 90 days, alerts per week
rec = tr[tr["fd"] >= tr["fd"].max() - pd.Timedelta(days=90)]
print(f"\nLast 90 days of filings: {int(rec['matched'].sum())} of {len(rec)} trades would alert "
      f"(about {rec['matched'].sum() / 13:.0f} a week)")
out = tr[tr["fd"] >= tr["fd"].max() - pd.Timedelta(days=30)]
out = out[out["matched"]][["member", "ticker", "tx_type", "td", "fd", "amt_lo", "amt_hi", "n_post", "f1m"]]
print(f"\nLast 30 days of filings, matched trades ({len(out)}):")
for r in out.sort_values("fd").itertuples():
    print(f"   {r.member[:22]:22} {r.tx_type:4} {r.ticker:6} traded {r.td.date()} filed {r.fd.date()} "
          f"${int(r.amt_lo or 0):,}-{int(r.amt_hi or 0):,} · {r.n_post} other member(s) named it in the 30 days before")
