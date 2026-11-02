"""Read-only (Nov 2 2026): how the bill reading and bill-to-trade links stand today. Writes nothing.
Prints the bill settings the weekly check chose, its latest verdicts on them, and recent trades with a bill link."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from congress_signals import engine as E
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
a = E.load_adaptive(cfg)
pol = a.get("policy", {})
print("signals_on:", pol.get("signals_on"))
print("policy:", {k: v for k, v in pol.items() if not isinstance(v, (list, dict))})
print("sale_discount checked at:", a.get("sale_discount_checked_at"), "| adaptive keys:", [k for k in a if "at" in k.lower()])
for r in a.get("evaluation") or []:
    s = str(r.get("Setting", ""))
    if any(w in s.lower() for w in ("bill", "sale", "sponsor", "vote", "hold", "committee")):
        print(" ", {k: r.get(k) for k in ("Setting", "Option", "Per year vs S&P", "Gain vs current", "Sureness", "Verdict")})
sc = pd.read_pickle("data/cache/scored.pkl")
cols = [c for c in ("filed_date", "member", "name", "ticker", "type", "side", "bill_note", "f_bills",
                    "f_bill_lead_sponsor", "f_bill_vote_against", "f_bill_committee_passed") if c in sc.columns]
fd = pd.to_datetime(sc.get("filed_date"), errors="coerce")
rec = sc[fd >= pd.Timestamp.today() - pd.Timedelta(days=30)]
print(f"\ntrades filed in the last 30 days: {len(rec)}; with a bill note: {int((rec['bill_note'].astype(str) != '').sum())}")
for c in ("f_bills", "f_bill_lead_sponsor", "f_bill_vote_against", "f_bill_committee_passed"):
    if c in rec:
        print(f"  {c} > 0: {int((rec[c] > 0).sum())}")
x = rec[rec["bill_note"].astype(str) != ""].sort_values("filed_date", ascending=False)
for _, r in x.head(15).iterrows():
    print("  ", " | ".join(str(r[c])[:110] for c in cols if c not in ("f_bill_lead_sponsor", "f_bill_vote_against", "f_bill_committee_passed")))
