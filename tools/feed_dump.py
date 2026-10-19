"""Read-only: print the app's post feed, one item per line (for local rendering tests)."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
d = json.loads(cloud_store.get("posts/feed.json", print) or b"{}")
print("FEEDMETA " + json.dumps({k: v for k, v in d.items() if k != "items"}))
for x in d.get("items", []):
    print("FEEDITEM " + json.dumps(x))
