"""Read-only: print the app's post feed as one JSON line (for local rendering tests)."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
print("FEEDJSON " + (cloud_store.get("posts/feed.json", print) or "{}").replace("\n", " "))
