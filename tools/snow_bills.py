"""Nov 1 (read-only, owner question): which exact bills are behind SNOW's "sponsored 1 Commerce bill(s)" and
"a Science, Technology, Communications bill they back passed a chamber after the trade" (Maria Salazar, S000168,
trade 2026-09-04, filed 2026-10-08). Prints the bills with their titles, and whether any title names Snowflake or
its business (cloud, data, AI)."""
import json, os, re, sys
import pandas as pd
sys.path.insert(0, ".")
B, TD, FD = "S000168", pd.Timestamp("2026-09-04"), pd.Timestamp("2026-10-08")
KW = re.compile(r"snowflake|cloud|data|artificial intelligence|\bAI\b|software|cyber|comput|privacy|digital", re.I)
bills = json.load(open("data/cache/bills.json")).get(B, {}).get("rows", [])
print(f"saved sponsored/cosponsored bills for {B}: {len(bills)}")
print("\nSPONSORED, 180 days before the filing (the 'sponsored ... bill(s)' signal):")
for d, pol, role, title in sorted(bills):
    if role == "sponsor" and FD - pd.Timedelta(days=180) < pd.Timestamp(d) <= FD:
        print(f"  {d} | {pol} | {title} | keyword: {bool(KW.search(title))}")
print("\nCOSPONSORED Commerce / Science-Tech bills, last 12 months:")
for d, pol, role, title in sorted(bills):
    if role == "cosponsor" and pol in ("Commerce", "Science, Technology, Communications") and pd.Timestamp(d) > FD - pd.Timedelta(days=365):
        print(f"  {d} | {pol} | {title} | keyword: {bool(KW.search(title))}")
ba = pd.read_pickle("data/cache/bill_actions.pkl")
ba["date"] = pd.to_datetime(ba["date"], errors="coerce")
m = ba["date"].between(TD, FD) & ba["policy"].isin(["Science, Technology, Communications", "Commerce"])
print("\nBILL ACTIONS between trade and filing that she backs (the 'passed a chamber after the trade' signal):")
for r in ba[m].sort_values("date").itertuples():
    bk = r.backers or {}
    if B in bk:
        j = pd.to_datetime(bk[B], errors="coerce") if isinstance(bk, dict) else None
        print(f"  {r.date.date()} | {r.bill} | {r.policy} | {r.kind} | joined {j.date() if j is not None and pd.notna(j) else '?'} | "
              f"{len(bk)} backers | {r.title} | keyword: {bool(KW.search(r.title or ''))}")
