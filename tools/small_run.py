"""Read-only: which small-company picks drove the Jul 18 - Oct 24, 2025 run, and whose purchases they came from."""
import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
pol = E.load_adaptive(cfg)["policy"]
hold = int(pol.get("hold_sleeve", 250)); cov = bool(pol.get("sleeve_coverage"))
rows = E.small_cap_rows(sc, coverage=cov, hold=hold)
A, B = pd.Timestamp("2025-07-18"), pd.Timestamp("2025-10-24")
days = px.index
out = []
for _, r in rows.iterrows():
    t = r["ticker"]
    if t not in px.columns:
        continue
    i0 = days.searchsorted(pd.Timestamp(r["filed_date"]) + pd.Timedelta(days=1))
    if i0 >= len(days):
        continue
    i1 = min(i0 + hold, len(days) - 1)
    d0, d1 = days[i0], days[i1]
    if d1 < A or d0 > B:
        continue
    s, e = max(d0, A), min(d1, B)
    p = px[t].loc[s:e].dropna()
    if len(p) < 2:
        continue
    ret = p.iloc[-1] / p.iloc[0] - 1
    out.append({"t": t, "co": r.get("company"), "member": r["member"], "bought": str(r["trade_date"])[:10],
                "filed": str(r["filed_date"])[:10], "in": str(s.date()), "out": str(e.date()), "ret": ret,
                "sector": r.get("sector"), "cap": r.get("market_cap")})
df = pd.DataFrame(out)
n_avg = len(df)
print(f"positions held at some point in the window: {len(df)} (hold {hold} days, skip uncovered: {cov})")
df["contrib"] = df["ret"] / max(1, len(df))      # equal weight across what was held
tot = df["contrib"].sum()
print(f"equal-weight average return of these positions over their time in the window: {df['ret'].mean():+.1%} (median {df['ret'].median():+.1%})")
spy = px["SPY"].loc[A:B]
print(f"S&P over the window: {spy.iloc[-1] / spy.iloc[0] - 1:+.1%}")
df = df.sort_values("ret", ascending=False)
print("\nTOP 15:")
for _, r in df.head(15).iterrows():
    cap = f"${r['cap'] / 1e6:,.0f}M" if pd.notna(r["cap"]) else "?"
    print(f"  {r['t']:<6} {str(r['co'])[:30]:<30} {r['ret']:+7.1%}  {r['member']:<28} bought {r['bought']} filed {r['filed']}  {r['sector']}  {cap}")
print("\nBOTTOM 8:")
for _, r in df.tail(8).iterrows():
    print(f"  {r['t']:<6} {str(r['co'])[:30]:<30} {r['ret']:+7.1%}  {r['member']}")
s = df["ret"].sort_values(ascending=False)
print("\nshare of total gain from top 3 / 5 / 10:", [f"{s.head(k).clip(lower=0).sum() / s.clip(lower=0).sum():.0%}" for k in (3, 5, 10)])
print("average without the top 3:", f"{s.iloc[3:].mean():+.1%}", "| without top 5:", f"{s.iloc[5:].mean():+.1%}")
print("winners vs losers:", int((df["ret"] > 0).sum()), "/", int((df["ret"] <= 0).sum()))
print("\nby member (positions, avg return):")
g = df.groupby("member")["ret"].agg(["count", "mean"]).sort_values("count", ascending=False).head(12)
for m, rr in g.iterrows():
    print(f"  {m:<30} {int(rr['count']):>3}  {rr['mean']:+.1%}")
print("\nby sector:", df.groupby("sector")["ret"].agg(["count", "mean"]).sort_values("count", ascending=False).round(3).to_dict("index"))
