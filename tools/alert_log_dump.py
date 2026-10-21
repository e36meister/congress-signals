"""Read-only: print today's phone alerts from the Worker's alert log (push/log.json), newest first."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
raw = cloud_store.get("push/log.json", log=print)
log = json.loads(raw) if raw else []
print("entries", len(log))
for x in log[:150]:
    print("ALERT", json.dumps({k: (str(v)[:160]) for k, v in x.items() if k in ("t", "at", "title", "body", "tag")}))
