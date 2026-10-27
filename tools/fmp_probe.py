"""Oct 27: does the owner's FMP key reach the Senate feed? A few calls only (the free plan has a daily cap).
Prints HTTP status, any error message (key hidden), how many rows, the newest filing dates, and Senate filings since Oct 23."""
import os, requests, collections
key = os.environ.get("FMP_API_KEY") or ""
print("key present:", bool(key), "length", len(key))
hide = lambda s: str(s).replace(key, "***") if key else str(s)
for ep in ("senate-latest", "house-latest"):
    for limit in (250, 100, 25):
        r = requests.get(f"https://financialmodelingprep.com/stable/{ep}", params={"page": 0, "limit": limit, "apikey": key}, timeout=60)
        try:
            js = r.json()
        except Exception:
            js = None
        if isinstance(js, list):
            dates = sorted({str(d.get("disclosureDate") or d.get("dateRecieved") or "")[:10] for d in js}, reverse=True)
            recent = [d for d in js if str(d.get("disclosureDate") or "")[:10] >= "2026-10-23"]
            print(f"{ep} limit={limit}: HTTP {r.status_code}, {len(js)} rows, newest filing dates {dates[:6]}, oldest {dates[-1:] }")
            print(f"  filed since Oct 23: {len(recent)} rows from {len({(d.get('firstName'), d.get('lastName')) for d in recent})} members")
            for d in recent[:8]:
                print("   ", d.get("disclosureDate"), d.get("firstName"), d.get("lastName"), d.get("symbol"), d.get("type"), d.get("amount"), "traded", d.get("transactionDate"))
            if js:
                print("  fields:", sorted(js[0].keys()))
            break
        print(f"{ep} limit={limit}: HTTP {r.status_code}: {hide(r.text[:300])}")
