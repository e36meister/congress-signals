"""Read-only: which members traded the stocks set aside for bad price data (data/state/bad_prices.json)."""
import sys, json
sys.path.insert(0, ".")
import pandas as pd
bad = json.load(open("data/state/bad_prices.json"))
tx = pd.read_pickle("data/cache/all_tx.pkl")
print("trades on file", len(tx), "| set aside", len(bad))
t = tx[tx["ticker"].isin(bad)].copy()
t["filed_date"] = pd.to_datetime(t["filed_date"], errors="coerce")
who = "who" if "who" in t else "member"
print("trades in set-aside stocks", len(t), "| members", t[who].nunique(),
      "| since 2025:", int((t["filed_date"] >= "2025-01-01").sum()), "| last 90 days:",
      int((t["filed_date"] >= pd.Timestamp.today() - pd.Timedelta(days=90)).sum()))
rows = []
for tk, g in t.groupby("ticker"):
    rows.append({"ticker": tk, "why": bad[tk], "trades": len(g), "members": g[who].nunique(),
                 "who": ", ".join(g[who].value_counts().index[:3].astype(str)),
                 "buys": int((g["tx_type"] == "buy").sum()), "sells": int((g["tx_type"] == "sell").sum()),
                 "first": str(g["filed_date"].min())[:10], "last": str(g["filed_date"].max())[:10]})
for r in sorted(rows, key=lambda r: -r["trades"]):
    print("ROW", json.dumps(r))
