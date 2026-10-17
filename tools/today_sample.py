"""Read-only: a trimmed sample of what the Today tab shows (for layout work)."""
import json
d = json.load(open("data/dashboard_data.json"))
w = d.get("watchlist") or {}
print("watchlist keys:", list(w))
for k in ("buys", "sells"):
    rows = w.get(k) or []
    print(f"\n{k}: {len(rows)}; actions {sorted(set(r.get('action') for r in rows))}")
    for r in rows[:3]:
        print(json.dumps(r, default=str)[:1800])
print("\nworth:", len(d.get("worth") or []))
for r in (d.get("worth") or [])[:4]:
    print(json.dumps(r, default=str)[:1500])
print("\nunusual:", len(d.get("unusual") or []))
for r in (d.get("unusual") or [])[:2]:
    print(json.dumps(r, default=str)[:600])
lens = [len(" ".join(r.get("why") or [])) for r in d.get("worth") or []]
print("\nworth why lengths:", sorted(lens)[-10:])
