"""Read-only: today's Buy list as the app shows it (every field per row), and everywhere one ticker appears
(default NVDA): the reasons, scores, member trades behind it and recent moves. For explaining new buys in chat."""
import sys, json
sys.path.insert(0, ".")
import cloud_store

T = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").upper()


def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


d = r2("dashboard_data.json") or {}
print("dashboard keys:", sorted(d)[:60])
print("as of:", d.get("generated") or d.get("asof") or d.get("updated") or d.get("date"))
w = d.get("watchlist") or {}
print("watchlist keys:", list(w))
print("watchlist new:", json.dumps(w.get("new"), default=str)[:3000])
print("close_report:", json.dumps(d.get("close_report"), default=str)[:6000])
ch = d.get("close_history")
print("close_history last:", json.dumps(ch[-3:] if isinstance(ch, list) else ch, default=str)[:6000])
if len(sys.argv) > 2 and sys.argv[2] == "short":
    sys.exit(0)
for k in ("buys", "sells"):
    rows = w.get(k) or []
    print(f"\n=== {k}: {len(rows)}; actions {sorted(set(str(r.get('action')) for r in rows))}")
    for r in rows:
        if k == "sells" and r.get("t") != T and r.get("ticker") != T:
            continue
        print(json.dumps(r, default=str)[:4000])
        print("---")


def walk(x, path):
    if isinstance(x, dict):
        if x.get("t") == T or x.get("ticker") == T or x.get("symbol") == T:
            print("FOUND", path, json.dumps(x, default=str)[:3000])
            return
        for k, v in x.items():
            walk(v, path + "." + str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x[:5000]):
            walk(v, f"{path}[{i}]")


print(f"\n=== everywhere {T} appears (outside the backtest)")
walk({k: v for k, v in d.items() if k not in ("backtest", "watchlist")}, "data")
for key in ("movers/latest.json",):
    m = r2(key)
    if m:
        print(f"\n=== {key}")
        walk(m, key)
# the last few trade-reason files (what the tool bought and why)
try:
    ks = sorted(cloud_store.list_keys("push/why/", log=lambda m: None) or [])[-6:]
    for k in ks:
        print("\n=== ", k, json.dumps(r2(k), default=str)[:3000])
except Exception as e:
    print("why files:", type(e).__name__, e)
