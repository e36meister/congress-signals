"""Read-only: is the app's Cloudflare copy of dashboard_data.json complete enough to be the only copy?
Prints key names, list lengths and dates only (no balances or positions)."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cloud_store

raw = cloud_store.get("dashboard_data.json")
if not raw:
    print("R2 dashboard_data.json: MISSING or unreadable"); sys.exit(1)
d = json.loads(raw)
print("size", len(raw), "bytes; top-level keys:", len(d))
for k in sorted(d):
    v = d[k]
    n = len(v) if isinstance(v, (list, dict)) else type(v).__name__
    print(f"  {k}: {n}")
ch = d.get("close_history") or []
print("close_history days:", len(ch), "first", ch[0].get("date") if ch else None, "last", ch[-1].get("date") if ch else None)
print("close_report date:", (d.get("close_report") or {}).get("date"))
print("portfolio as_of:", (d.get("portfolio") or {}).get("as_of") or (d.get("portfolio") or {}).get("updated"))
print("stop_check updated:", (d.get("stop_check") or {}).get("updated"))
for k in ("generated", "updated", "as_of", "generated_at"):
    if k in d: print(k, d[k])
