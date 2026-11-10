"""Read-only: print the Senate access watch state (health/senate_access.json) and recent Senate-related phone alerts."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
print("SENATE_STATE", cloud_store.get("health/senate_access.json", log=print))
raw = cloud_store.get("push/log.json", log=print)
log = json.loads(raw) if raw else []
for x in log[:300]:
    s = json.dumps(x)
    if "senate" in s.lower():
        print("ALERT", json.dumps({k: (str(v)[:200]) for k, v in x.items() if k in ("t", "at", "title", "body", "tag")}))
