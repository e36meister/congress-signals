"""Read-only: the shape of the app's data (each top-level field, its type and a trimmed sample), so the newsletter
can be filled from the right fields. Prints nothing private beyond what the app already shows (no balances)."""
import sys, json
sys.path.insert(0, ".")
import cloud_store

SKIP = ("backtest",)
HIDE = ("balance", "equity", "cash", "account", "email", "token", "key")


def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def shape(x, depth=0):
    if isinstance(x, dict):
        if depth > 1:
            return "{" + ", ".join(list(x)[:25]) + "}"
        return {k: ("[hidden]" if any(h in k.lower() for h in HIDE) else shape(v, depth + 1)) for k, v in list(x.items())[:40]}
    if isinstance(x, list):
        return [f"list[{len(x)}]", shape(x[-1], depth + 1) if x else None]
    s = str(x)
    return s[:120]


d = r2("dashboard_data.json") or {}
print("TOP KEYS:", sorted(d))
for k in sorted(d):
    if k in SKIP or any(h in k.lower() for h in HIDE):
        continue
    print(f"\n### {k}\n" + json.dumps(shape(d[k]), default=str)[:2500])
for key in ("movers/latest.json",):
    m = r2(key)
    print(f"\n### R2 {key}\n" + json.dumps(shape(m), default=str)[:2500])
print("\nR2 prefixes sample:", sorted(cloud_store.list_keys("", log=lambda m: None) or [])[:80])
