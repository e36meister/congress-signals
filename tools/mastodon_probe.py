"""Oct 31 (read-only): Data health says "Mastodon: nothing new since these hourly checks started" (14+ days).
For each Mastodon account the app follows: does the lookup work, when was the newest post on the server, and the newest
post we have saved. Tells a quiet source apart from a broken reader. No writes."""
import os
import re
import sys
import time
sys.path.insert(0, ".")
from congress_signals import social as S

cfg = {"SOCIAL_DIR": "social"}
st = S._state(cfg)
leg = S.legislators(cfg, st)
acc = S.discover_accounts(cfg, leg, st, time.time() + 60)
handles = {}
for b, m in leg.items():
    v = (m.get("social") or {}).get("mastodon")
    if v and "@" in v.strip("@"):
        handles[v.strip("@").lower()] = (b, "legislators")
for b, a in acc.items():
    for v in a.get("masto") or []:
        handles.setdefault(v.lower(), (b, "website"))
print(f"{len(handles)} Mastodon handles")
out_dir = S._dir(cfg, "posts", "masto")
for h, (b, src) in sorted(handles.items()):
    user, host = h.split("@", 1)
    m = leg.get(b) or {}
    who = f"{m.get('name')} ({'current' if m.get('current') else 'former'})"
    path = os.path.join(out_dir, re.sub(r"[^a-z0-9.@-]", "_", h) + ".jsonl.gz")
    saved = S.read_jsonl(path) if os.path.exists(path) else []
    newest_saved = max((str(r[0]) for r in saved), default="-")
    a = S._get(f"https://{host}/api/v1/accounts/lookup", params={"acct": user}, tries=2)
    if not a or not a.get("id"):
        print(f"  {h} | {who} | {src} | LOOKUP FAILED | saved {len(saved)}, newest {newest_saved}")
        continue
    js = S._get(f"https://{host}/api/v1/accounts/{a['id']}/statuses", params={"limit": 5}, tries=2) or []
    newest = js[0].get("created_at", "")[:10] if js else "-"
    print(f"  {h} | {who} | {src} | last_status_at {a.get('last_status_at')} | statuses {a.get('statuses_count')} | "
          f"newest via API {newest} ({len(js)} read) | saved {len(saved)}, newest {newest_saved}")
    time.sleep(1)
