"""Oct 30 (read-only, owner): can Meta Business Discovery read the handles the owner sent today?
One call per handle (well under Meta's ~200/hour). Never prints the token."""
import os, sys, requests
sys.path.insert(0, ".")
from congress_signals import social as S
tok = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
ig = S._meta_ig_user(tok)
cfg = {"SOCIAL_DIR": "social"}
leg = S.legislators(cfg, S._state(cfg))
man = S.ig_manual(leg)
want = ["leadermcconnell", "mcconnellpress", "sen.jerrymoran", "rep.brianbabin", "repgarypalmer", "palmerforalabama",
        "repwarrendavidson", "davidsonforcongress", "senatorfischer", "repannwagner"]
filed = {h: leg[b]["name"] for b, hs in man.items() for h in hs}
for h in want:
    r = requests.get(f"{S.META_GRAPH}/{ig}", params={"fields": f"business_discovery.username({h}){{username,media_count,media.limit(1){{timestamp}}}}",
                     "access_token": tok}, timeout=30)
    js = r.json()
    bd, err = js.get("business_discovery"), js.get("error") or {}
    last = (((bd or {}).get("media") or {}).get("data") or [{}])[0].get("timestamp")
    print(f"@{h} [{filed.get(h, 'NOT MATCHED to a member')}]:",
          f"OK, {bd.get('media_count')} posts, latest {last}" if bd else
          f"FAILED HTTP {r.status_code} code {err.get('code')}/{err.get('error_subcode')}: {str(err.get('message'))[:90]}")
