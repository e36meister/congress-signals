"""Read-only: one member's trade records for one ticker from the app's member pages (for a company report)."""
import sys, os, json
sys.path.insert(0, ".")
import cloud_store
os.makedirs("out", exist_ok=True)
T, B = "EIX", "C001123"
def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None
idx = r2("members/index.json") or {}
mem = idx.get("members") or idx.get("list") or idx
key = B
if isinstance(mem, list):
    for m in mem:
        if isinstance(m, dict) and m.get("b") == B:
            key = m.get("k") or B
doc = r2(f"members/{key}.json") or {}
cols = doc.get("tcols") or []
rows = [dict(zip(cols, r)) if isinstance(r, list) else r for r in doc.get("trades") or []]
out = {"n": doc.get("n"), "cols": cols, "trades": [r for r in rows if r.get("t") == T],
       "doc_keys": [k for k in doc if k != "trades"]}
json.dump(out, open("out/trade_look.json", "w"), indent=1, default=str)
print(json.dumps(out, default=str)[:3000])
