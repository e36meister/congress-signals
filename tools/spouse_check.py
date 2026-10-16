"""Read-only: spouse/relative insider ties on file, and the trades they flag, by year."""
import sys, json, os, pandas as pd
sys.path.insert(0, ".")
sc = pd.read_pickle("data/cache/scored.pkl")
ties = json.load(open("data/state/spouse_ties.json")) if os.path.exists("data/state/spouse_ties.json") else []
print(f"Family ties table: {len(ties)} rows")
print(pd.Series([t.get('Confidence') for t in ties]).value_counts().to_string())
print(pd.Series([t.get('Source') for t in ties]).value_counts().to_string())
for t in ties[:60]:
    print(" ", t.get("Member"), "|", t.get("Spouse"), "|", t.get("Company"), "|", t.get("Role"), "|", t.get("Since"), "|", t.get("Confidence"))
for c in ("f_spouse_insider", "f_relative_tie", "f_spouse_employer"):
    if c not in sc:
        print(c, "missing"); continue
    f = sc[sc[c] > 0]
    yrs = pd.to_datetime(f["filed_date"]).dt.year.value_counts().sort_index()
    print(f"\n{c}: {len(f)} trades flagged; by filing year: " + ", ".join(f"{y}: {n}" for y, n in yrs.items()))
    last = f.sort_values("filed_date").tail(12)
    for _, r in last.iterrows():
        print("   ", pd.Timestamp(r["filed_date"]).date(), r["member"], r["tx_type"], r["ticker"])
ins = "data/cache/spouse_insiders.pkl"
if os.path.exists(ins):
    d = pd.read_pickle(ins)
    print(f"\nSEC filings by people named like a spouse: {len(d):,}; by year: " +
          ", ".join(f"{y}: {n}" for y, n in pd.to_datetime(d['filed']).dt.year.value_counts().sort_index().items()))
sp = json.load(open("data/cache/spouses.json")) if os.path.exists("data/cache/spouses.json") else {}
print(f"Spouse names on file (Wikidata): {len(sp.get('rows', {}))} members")
