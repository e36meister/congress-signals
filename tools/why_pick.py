"""Read-only: why a stock was (or is) a buy pick: every place it appears in the app's data, plus the member
trades of it with their score, signals and context (members' pages)."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
T = (sys.argv[1] if len(sys.argv) > 1 else "ACN").upper()
def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try: return json.loads(raw) if raw else None
    except Exception: return None
d = r2("dashboard_data.json") or {}
def walk(x, path):
    if isinstance(x, dict):
        if x.get("t") == T or x.get("ticker") == T:
            print("FOUND", path, json.dumps(x, default=str)[:2500])
            return
        for k, v in x.items(): walk(v, path + "." + str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x[:5000]): walk(v, f"{path}[{i}]")
walk(d, "data")
idx = r2("members/index.json") or {}
mem = idx.get("members") or idx.get("list") or idx
keys = []
if isinstance(mem, list):
    for m in mem:
        if isinstance(m, dict) and any(n in str(m.get("n", "")) for n in ("Franklin", "Morrison", "Newhouse", "Cisneros")):
            keys.append(m.get("k") or m.get("b"))
print("member keys", keys)
for k in keys:
    doc = r2(f"members/{k}.json") or {}
    for tr in doc.get("trades") or doc.get("tr") or []:
        if tr.get("t") == T and str(tr.get("td", "")) >= "2026-06":
            print("TRADE", doc.get("n") or k, json.dumps(tr, default=str)[:2000])
