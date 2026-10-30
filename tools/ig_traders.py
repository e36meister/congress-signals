"""Oct 30 (read-only, owner): of the members on the Instagram check list, which have ever disclosed a trade.
No Meta calls; reads the saved trades and the Instagram status kept in R2."""
import sys
import pandas as pd
sys.path.insert(0, ".")
from congress_signals import social as S

cfg = {"SOCIAL_DIR": "social"}
st = S._state(cfg)
leg = S.legislators(cfg, st)
lines = S.ig_unreadable(leg)
names = {}
for b, m in leg.items():
    if m.get("current"):
        names[f"{m.get('name')} ({m.get('state')}, {m.get('chamber')})"] = b
listed = {}
for ln in lines:
    for who, b in names.items():
        if who in ln:
            listed[b] = (who, "can't read" if "can't read" in ln else "none on file")
print(f"check list: {len(lines)} lines, {len(listed)} members matched")

tx = None
for p in ("data/cache/scored.pkl", "data/cache/trades.pkl"):
    try:
        tx = pd.read_pickle(p)
        print("trades from", p, len(tx), "rows; columns:", [c for c in tx.columns if c in ("bio_id", "bioguide", "member")])
        break
    except Exception as e:
        print("can't read", p, e)
if tx is None:
    sys.exit("no saved trades")
col = "bio_id" if "bio_id" in tx.columns else "bioguide"
tx["d"] = pd.to_datetime(tx["trade_date"], errors="coerce").fillna(pd.to_datetime(tx["filed_date"], errors="coerce"))
g = tx[tx[col].isin(listed)].groupby(col).agg(n=("d", "size"), last=("d", "max"))
print(f"\nTRADERS ({len(g)} of {len(listed)}):")
for b, r in g.sort_values("last", ascending=False).iterrows():
    who, kind = listed[b]
    print(f"  {who} | {kind} | {int(r.n)} trades | last {r['last'].date() if pd.notna(r['last']) else '?'}")
print("\nNO TRADES:", "; ".join(sorted(listed[b][0] for b in listed if b not in g.index)))
