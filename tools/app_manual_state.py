"""Read-only (owner, Nov 11): the manual-rules state for APP (the Buy-button buy of Nov 6 that Cisneros' sale
disclosure flags), the Keep list, and phone alerts mentioning APP. Tickers and rule reasons only."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try: return json.loads(raw) if raw else None
    except Exception: return None
st = r2("manual/state.json") or {}
print("state keys:", list(st)[:20])
def find(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "APP" or (isinstance(v, (str,)) and v == "APP"):
                print("STATE", path + "/" + str(k), json.dumps(x if v == "APP" else v, default=str)[:900])
            else:
                find(v, path + "/" + str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x): find(v, f"{path}[{i}]")
find(st)
print("KEEP", json.dumps(r2("manual/keep.json"), default=str)[:600])
for x in (r2("push/log.json") or []):
    s = json.dumps(x, default=str)
    if "APP" in s or "AppLovin" in s:
        print("ALERT", s[:400])
