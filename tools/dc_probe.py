"""Oct 27: does Disclosed Capitol have Senate filings newer than Oct 22 (the Senate site blocks bots since Oct 23)?
Two or three calls only (free account: 750 credits)."""
import os, json, collections, requests
key = os.environ.get("DISCLOSED_CAPITOL_API_KEY") or ""
print("key present:", bool(key), "length", len(key))
hide = lambda s: str(s).replace(key, "***") if key else str(s)
B = "https://api.disclosedcapitol.com"
def get(path, **p):
    r = requests.get(B + path, params=p, headers={"DC-API-Key": key, "Accept": "application/json"}, timeout=60)
    try: js = r.json()
    except Exception: js = None
    print(f"GET {path} {p}: HTTP {r.status_code}", "" if js is not None else hide(r.text[:300]))
    print("  credit headers:", {k: v for k, v in r.headers.items() if "credit" in k.lower() or "ratelimit" in k.lower()})
    return js
def rows(js):
    if isinstance(js, list): return js
    if isinstance(js, dict):
        for k in ("data", "trades", "results", "items"):
            if isinstance(js.get(k), list): return js[k]
        print("  top keys:", list(js)[:15], hide(json.dumps(js)[:400]))
    return []
def show(js):
    rs = rows(js)
    if not rs: return
    print("  fields:", sorted(rs[0].keys()))
    ch = lambda d: str(d.get("chamber") or d.get("Chamber") or "?")
    dd = lambda d: str(d.get("disclosure_date") or d.get("disclosureDate") or d.get("filed_date") or d.get("filing_date") or d.get("reported_date") or "")[:10]
    print("  rows:", len(rs), "by chamber:", dict(collections.Counter(ch(d) for d in rs)))
    sen = [d for d in rs if "sen" in ch(d).lower()]
    print("  newest Senate filing dates:", sorted({dd(d) for d in sen}, reverse=True)[:8])
    print("  newest overall:", sorted({dd(d) for d in rs}, reverse=True)[:5])
    for d in sen[:6]: print("   ", hide(json.dumps(d)[:300]))
show(get("/trades/recent"))
show(get("/trades/recent", chamber="senate"))
