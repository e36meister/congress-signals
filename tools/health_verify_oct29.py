"""Oct 29 (read-only): checks the Instagram list fixes against the saved data. Which website-linked Instagram
handles are shared by several members or aren't account names, which members they were filed under, and what
the Data health check list looks like with the new rules (from the saved Instagram status; nothing is written)."""
import json, os, sys
sys.path.insert(0, ".")
import congress_signals.social as S
acc = json.load(open("social/accounts.json")) if os.path.exists("social/accounts.json") else {}
leg = S.legislators({}, {})
print("members:", len(leg), "current:", sum(1 for m in leg.values() if m.get("current")), "accounts on file:", len(acc))
site = {}
for b, a in acc.items():
    for v in a.get("instagram") or []:
        site.setdefault(v.strip("@").lower(), set()).add(b)
shared = {h: sorted(leg.get(b, {}).get("name", b) for b in bs) for h, bs in site.items() if len(bs) > 1}
print("shared website handles (now ignored):", json.dumps(shared, indent=0)[:3000])
print("non-handles (now ignored):", {h: sorted(leg.get(b, {}).get("name", b) for b in bs) for h, bs in site.items() if S._not_a_handle(h)})
igs = S._ig_status()
print("status keys:", {k: len(v) if hasattr(v, "__len__") else v for k, v in igs.items()})
lines = S.ig_unreadable(leg)
print("check-list lines now (before the next run prunes dropped handles):", len(lines))
for l in lines[:6]:
    print("  ", l[:230])
