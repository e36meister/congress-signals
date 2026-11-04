"""Read-only (Nov 4 2026): why the first buys for a family account placed 0 of 9 picks with no skip lines.
Prints yes/no facts from each account's saved snapshot only (no names, emails or amounts; the run log is public)."""
import json, sys
sys.path.insert(0, ".")
import cloud_store

idx = json.loads(cloud_store.get("accounts/index.json", log=lambda m: None) or b"[]")
for i, a in enumerate(idx, 1):
    raw = cloud_store.get(f"accounts/{a.get('id')}/portfolio.json", log=lambda m: None)
    doc = json.loads(raw) if raw else {}
    p = doc.get("portfolio") or {}
    L = p.get("limits") or {}
    eq, cash, pp = p.get("equity") or 0, p.get("cash") or 0, L.get("per_pick")
    print(f"account {i}: snapshot={'yes' if p else 'no'} updated={str(p.get('updated'))[:16]} auto={p.get('auto')}"
          f" equity>0={eq > 0} equity>=100={eq >= 100} cash>0={cash > 0} per_pick>=1={bool(pp and pp >= 1)}"
          f" positions={len(p.get('positions') or [])} orders={len(p.get('orders') or [])} this_run={len(p.get('this_run') or [])}"
          f" started_how={a.get('started_how')!r}")
