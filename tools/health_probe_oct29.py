"""Oct 29 (read-only): what Business Discovery answers for the Instagram handles listed as 'personal' in Data
health, next to a known Business account and a username that can't exist. Prints Meta's error code, subcode and
message for each (never the token), plus the profile's name/website when it can be read."""
import os, requests, time
tok = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
G = "https://graph.facebook.com/v23.0"
def get(path, **p):
    r = requests.get(G + path, params={**p, "access_token": tok}, timeout=30)
    try: return r.status_code, r.json()
    except Exception: return r.status_code, {"raw": r.text[:200]}
_, acc = get("/me/accounts", fields="instagram_business_account")
ig = next((pg["instagram_business_account"]["id"] for pg in acc.get("data") or [] if pg.get("instagram_business_account")), None)
print("own ig found:", bool(ig))
for h in ["nasa", "zzqx_no_such_user_88123", "senatorbaldwin", "senjerrymoran", "repstenyhoyer", "mcconnellpress",
          "houserepublicans", "senatorfischer", "repandybiggs", "repgarypalmer", "embed.js", "speakerpelosi",
          "aoc", "sensanders", "repmikerogersal", "reprileymoore"]:
    sc, js = get(f"/{ig}", fields=f"business_discovery.username({h}){{username,name,website,followers_count,media_count}}")
    e = js.get("error") or {}
    if e:
        print(f"{h:28s} HTTP {sc} code={e.get('code')} sub={e.get('error_subcode')} type={e.get('type')} "
              f"title={e.get('error_user_title')!r} msg={str(e.get('message'))[:160]!r}")
    else:
        bd = js.get("business_discovery") or {}
        print(f"{h:28s} HTTP {sc} OK name={bd.get('name')!r} site={bd.get('website')!r} media={bd.get('media_count')}")
    time.sleep(1)
