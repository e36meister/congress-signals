"""Oct 28 (read-only): why Business Discovery answers (#10) "Application does not have permission". Prints the
token's type and granted scopes, what it can see, and tries Business Discovery on a few large public accounts.
Never prints the token."""
import os, requests
tok = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
G = "https://graph.facebook.com/v23.0"
hide = lambda s: str(s).replace(tok, "***") if tok else str(s)
def get(path, **p):
    r = requests.get(G + path, params={**p, "access_token": tok}, timeout=30)
    try: return r.json()
    except Exception: return {"raw": r.text[:200]}
d = get("/debug_token", input_token=tok).get("data") or {}
print("token:", {k: d.get(k) for k in ("type", "app_id", "application", "is_valid", "expires_at", "scopes", "granular_scopes")})
print("me:", hide(get("/me", fields="id,name")))
acc = get("/me/accounts", fields="name,instagram_business_account{id,username}")
print("me/accounts:", hide(acc)[:600])
ig = None
for pg in acc.get("data") or []:
    ig = (pg.get("instagram_business_account") or {}).get("id") or ig
print("own ig:", hide(get(f"/{ig}", fields="id,username,account_type,media_count")) if ig else None)
for h in ("nasa", "speakerjohnson", "aoc"):
    js = get(f"/{ig}", fields=f"business_discovery.username({h}){{username,media_count,media.limit(1){{timestamp}}}}") if ig else {}
    print("bd", h, hide(js)[:400])
