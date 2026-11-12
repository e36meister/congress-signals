"""Read-only: the rows from Nov 6-12 2026 the newsletter sample would use (no balances or dollar amounts of the account)."""
import sys, json
sys.path.insert(0, ".")
import cloud_store

LO, HI = "2026-11-06", "2026-11-12"


def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def inwin(v):
    return isinstance(v, str) and LO <= v[:10] <= HI


j = lambda x, n=900: json.dumps(x, default=str)[:n]
d = r2("dashboard_data.json") or {}
w = d.get("watchlist") or {}
print("BUY ROW FULL SAMPLE:", j((w.get("buys") or [{}])[0], 2500))
for k in ("buys", "sells"):
    rows = [r for r in w.get(k) or [] if inwin(r.get("d")) or inwin(r.get("td"))]
    print(f"\n=== watchlist {k} in window: {len(rows)}")
    for r in rows[:25]:
        print(j({x: r.get(x) for x in ("rank", "action", "t", "co", "pct", "m", "why", "d", "td", "move", "an", "sz", "sec", "news", "px", "ppl", "mt", "up")}, 700))
p = d.get("portfolio") or {}
print("\n=== orders in window")
for o in p.get("orders") or []:
    if inwin(o.get("at")):
        print(j({x: o.get(x) for x in ("t", "side", "status", "filled_px", "at", "by", "bot")}, 300))
print("\n=== positions bought in window")
for o in p.get("positions") or []:
    if inwin(o.get("bought")):
        print(j({x: o.get(x) for x in ("t", "px", "avg", "plpc", "day", "bought", "sleeve", "manual", "bot")}, 300))
print("\n=== close_history last 7:", j([{k: v for k, v in r.items() if k != "equity"} for r in (d.get("close_history") or [])[-7:]], 1500))
cr = d.get("close_report") or {}
print("versions:", j(cr.get("versions"), 1500))
print("sp100_mix:", j({k: v for k, v in (cr.get("sp100_mix") or {}).items() if k not in ("series", "account")}, 500))
print("\n=== unusual in window")
for u in d.get("unusual") or []:
    if inwin(u.get("asof")):
        print(j(u, 600))
print("\n=== worth (first 12)")
for u in (d.get("worth") or [])[:12]:
    print(j({k: u.get(k) for k in ("t", "co", "who", "why", "d")}, 700))
m = (r2("movers/latest.json") or {}).get("items") or {}
print("\n=== movers in window")
for t, it in m.items():
    if inwin(it.get("date")):
        print(j({k: it.get(k) for k in ("t", "co", "date", "kind", "move", "spy", "rel", "held", "lines", "news", "members")}, 1200))
print("\nadaptive history:", j((d.get("adaptive") or {}).get("history"), 2000))
print("app version:", d.get("version"))
