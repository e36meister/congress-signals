"""Nov 14 (read-only): Yassamin Ansari's Instagram (@repyassansari, from the congress-legislators list) became
unreadable on Nov 13 ~20:12 UTC. What does the stored status say, and can Meta read likely other usernames?
One Business Discovery call per handle. Never prints the token; writes nothing."""
import os, sys, json, requests
sys.path.insert(0, ".")
from congress_signals import social as S
tok = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
ig = S._meta_ig_user(tok)
igs = S._ig_status()
B = "A000381"
for k in ("bad", "ok", "found", "guessed"):
    v = igs.get(k) or {}
    hits = {h: x for h, x in v.items() if h.lower().startswith("repyass") or (isinstance(x, dict) and (x.get("b") == B or B in (x.get("for") or [])))} \
        if isinstance(v, dict) else {}
    if k == "guessed" and isinstance(v, dict):
        hits = {B: v.get(B)} if B in v else {}
    print(k, json.dumps(hits, default=str)[:600])
print("handles", {h: b for h, b in (igs.get("handles") or {}).items() if b == B})
for h in ["repyassansari", "yassaminansari", "yassansari", "repansari", "repyassaminansari", "yassforcongress",
          "yassamin.ansari", "yassamin_ansari", "congresswomanansari"]:
    r = requests.get(f"{S.META_GRAPH}/{ig}", params={"fields": f"business_discovery.username({h}){{username,name,website,media_count,media.limit(1){{timestamp}}}}",
                     "access_token": tok}, timeout=30)
    js = r.json()
    bd, err = js.get("business_discovery"), js.get("error") or {}
    last = (((bd or {}).get("media") or {}).get("data") or [{}])[0].get("timestamp")
    print(f"@{h}:", f"OK '{bd.get('name')}' {bd.get('website')} {bd.get('media_count')} posts, latest {last}" if bd else
          f"FAILED HTTP {r.status_code} code {err.get('code')}/{err.get('error_subcode')}: {str(err.get('message'))[:90]}")
