"""Read-only test of 'Why it moved': runs the check without saving or alerting, explaining every mover plus ETN
(forced), and prints the result."""
import sys, json
sys.path.insert(0, ".")
import quick_check as Q
res = Q.why_it_moved(force=("ETN",), dry=True)
for t, r in ((res or {}).get("items") or {}).items():
    print(f"\n=== {t} ({r['kind']}, {'held' if r['held'] else ''}{' candidate' if r['cand'] else ''}) move {r['move']} vs S&P {r['spy']}")
    for l in r["lines"]: print("  ", l)
    print("   news:", [(n["src"], n["h"][:90]) for n in r["news"]])
    print("   peers:", r["peers"][:8], "etf:", r["etf"], "industry:", r["industry"])
    print("   analysts:", r["analysts"], "filings:", r["filings"])
