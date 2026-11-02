"""Read-only probe (Nov 2 2026): which recent posts of the owner's shared Instagram accounts (instagram_handles.json
"_shared", e.g. @houseveteransaffairsgop) would be filed under which members. Writes nothing; never prints the token."""
import os, sys, re, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from congress_signals import social as S
tok = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
ig = S._meta_ig_user(tok)
cfg = {"SOCIAL_DIR": "social"}
leg = S.legislators(cfg, S._state(cfg))
handles = {h: b for b, hs in S.ig_manual(leg).items() for h in hs}
handles.update((S._ig_status().get("handles") or {}))
for h in ['elicraneaz']:
    js = requests.get(f"{S.META_GRAPH}/{ig}", params={"fields": f"business_discovery.username({h}){{media_count}}", "access_token": tok}, timeout=30).json()
    print(f"@{h}:", js.get('business_discovery') or (js.get('error') or {}).get('message'))
print('Data health Instagram lines:'); [print('  ', l[:150]) for l in S.ig_unreadable(leg)]
for h, sx in S.ig_shared(leg).items():
    print(f"== @{h} ({sx['name']}), stands in for: {[leg[b]['name'] for b in sx['for']]}")
    js = requests.get(f"{S.META_GRAPH}/{ig}", params={"fields": f"business_discovery.username({h}){{media.limit(25){{caption,timestamp}}}}",
                      "access_token": tok}, timeout=30).json()
    if js.get("error"):
        print("  FAILED", js["error"].get("code"), str(js["error"].get("message"))[:90]); continue
    for mm in js["business_discovery"]["media"]["data"]:
        cap = re.sub(r"\s+", " ", mm.get("caption") or "")
        who = [leg[b]["name"] for b in S.ig_mentions(cap, leg, handles)]
        print(f"  {mm['timestamp'][:10]}  {who or '-'}  | {cap[:110]}")
