"""Read-only study for the owner: the best-performing member trades that have a linked post — the member (or their
office) named the company or one of its executives in a post in the 90 days before the trade. Results measured
from the trade date vs the S&P 500 (sales: positive = the stock did worse than the S&P after the sale).
Uses public data only (disclosed trades and public posts); prints no account data."""
import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
import congress_signals.members as M
import congress_signals.social as S

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
idx = S.load_index(cfg)
if not idx:
    sys.exit("no posts index (social cache not restored)")
men = idx.get("mentions") or {}
pages = idx.get("pages") or {}

tr = sc[sc["tx_type"].isin(["buy", "sell"])].copy().reset_index(drop=True)
rets = M.trade_returns(tr, px)
for k, v in rets.items():
    tr[k] = v
tr["td"] = pd.to_datetime(tr["trade_date"].where(tr["trade_date"].notna(), tr["filed_date"]))
sign = np.where(tr["tx_type"] == "sell", -1, 1)
tr["e3m"], tr["e1y"] = sign * tr["x3m"], sign * tr["x1y"]

rows = []
for i, r in tr.iterrows():
    b = r.get("bioguide")
    if not isinstance(b, str):
        continue
    ms = (men.get(b) or {}).get(r["ticker"]) or []
    lo = (r["td"] - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    hi = r["td"].strftime("%Y-%m-%d")
    near = [m for m in ms if lo <= m[0] <= hi]
    if near:
        rows.append(i)
linked = tr.loc[rows].copy()
print(f"Trades with a post naming the company (or its executives) in the 90 days before: {len(linked):,} "
      f"of {len(tr):,} trades, {linked['bioguide'].nunique()} members\n")


def posts_for(r, n=3):
    lo = (r["td"] - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    hi = r["td"].strftime("%Y-%m-%d")
    out = [p for p in pages.get(r["bioguide"], []) if lo <= p[0] <= hi and r["ticker"] in (p[3] or [])]
    return out[-n:]


def show(df, col, title, n=15):
    d = df[df[col].notna()].sort_values(col, ascending=False).head(n)
    print("=" * 100 + f"\n{title}\n" + "=" * 100)
    for _, r in d.iterrows():
        amt = f"${int(r['amt_lo']):,}-{int(r['amt_hi']):,}" if pd.notna(r.get("amt_lo")) and pd.notna(r.get("amt_hi")) else ""
        own = str(r.get("owner") or "")
        print(f"\n{r['member']} | {'BOUGHT' if r['tx_type'] == 'buy' else 'SOLD'} {r['ticker']} ({str(r.get('company') or '')[:40]}) "
              f"on {r['td'].date()} {amt} {('owner: ' + own) if own and own.lower() not in ('self', 'nan') else ''}")
        print(f"   vs S&P after: 3 months {r['e3m'] * 100:+.1f}% | 1 year "
              + (f"{r['e1y'] * 100:+.1f}%" if pd.notna(r['e1y']) else "n/a")
              + f" | filed {pd.Timestamp(r['filed_date']).date()}")
        for p in posts_for(r):
            days = (r["td"] - pd.Timestamp(p[0])).days
            print(f"   post {days} days before ({p[0]}, {p[1]}): {str(p[2])[:230]!s}" + (f"  <{p[5]}>" if p[5] else ""))


show(linked, "e1y", "BEST BY 1 YEAR vs S&P (trade date to 1 year later)")
show(linked, "e3m", "BEST BY 3 MONTHS vs S&P")

# how linked trades did overall vs all trades
for col, lab in (("e3m", "3 months"), ("e1y", "1 year")):
    a, b_ = linked[col].dropna(), tr[col].dropna()
    print(f"\nAverage vs S&P after {lab}: trades with a linked post {a.mean() * 100:+.2f}% (n {len(a):,}, "
          f"{(a > 0).mean() * 100:.0f}% beat it) | all trades {b_.mean() * 100:+.2f}% (n {len(b_):,})")
