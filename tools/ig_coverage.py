"""Oct 28 (read-only): which current members have no Instagram account on file, and how many have more than one.
No Meta calls (Business Discovery allows ~200 an hour; the hourly job uses those)."""
import sys, time, collections
sys.path.insert(0, ".")
from congress_signals import social as S
cfg = {"SOCIAL_DIR": "social"}
st = S._state(cfg)
leg = S.legislators(cfg, st)
acc = S.discover_accounts(cfg, leg, st, time.time() + 30)
cur = {b: m for b, m in leg.items() if m.get("current")}
has = collections.defaultdict(set)
for b, m in cur.items():
    v = (m.get("social") or {}).get("instagram")
    if isinstance(v, str) and v:
        has[b].add(v.strip("@").lower())
for b, a in acc.items():
    if b in cur:
        for v in a.get("instagram") or []:
            has[b].add(v.strip("@").lower())
none = sorted(cur[b]["name"] + f" ({cur[b].get('state')})" for b in cur if not has.get(b))
print(f"current members: {len(cur)}; with an Instagram handle: {len(has)}; none on file: {len(none)}; more than one: {sum(1 for v in has.values() if len(v) > 1)}")
print("none:", "; ".join(none))
