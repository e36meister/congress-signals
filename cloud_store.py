"""Copies the dashboard's data files to Cloudflare R2, where the Capitol Capital phone app reads them.
Google Drive keeps its copy too. Does nothing until CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are set.
Never raises: a failed upload only logs a line (no balances or positions are ever logged)."""
import os
import requests

BUCKET = "capitol-capital-data"


def put(name, data, log=print, content_type="application/json"):
    tok, acc = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not (tok and acc):
        return False
    try:
        if isinstance(data, str) and os.path.exists(data):
            data = open(data, "rb").read()
        elif isinstance(data, str):
            data = data.encode()
        url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/r2/buckets/{BUCKET}/objects/{name}"
        for attempt in range(3):
            r = requests.put(url, data=data, timeout=120,
                             headers={"Authorization": f"Bearer {tok}", "Content-Type": content_type})
            if r.status_code < 300:
                log(f"Cloudflare: updated {name}")
                return True
            if r.status_code < 500:
                break
        log(f"Cloudflare: couldn't update {name} (HTTP {r.status_code})")
    except Exception as e:
        log(f"Cloudflare: couldn't update {name} ({type(e).__name__})")
    return False


def get(name, log=print):
    """Read one object from R2 (bytes), or None when it isn't there or Cloudflare isn't set up."""
    tok, acc = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not (tok and acc):
        return None
    try:
        r = requests.get(f"https://api.cloudflare.com/client/v4/accounts/{acc}/r2/buckets/{BUCKET}/objects/{name}",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        log(f"Cloudflare: couldn't read {name} ({type(e).__name__})")
        return None



def list_keys(prefix, log=print):
    """Names of the R2 objects starting with `prefix` ([] when Cloudflare isn't set up or the listing fails)."""
    tok, acc = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not (tok and acc):
        return []
    keys, cursor = [], None
    try:
        for _ in range(50):
            p = {"prefix": prefix, "per_page": 1000}
            if cursor:
                p["cursor"] = cursor
            r = requests.get(f"https://api.cloudflare.com/client/v4/accounts/{acc}/r2/buckets/{BUCKET}/objects",
                             headers={"Authorization": f"Bearer {tok}"}, params=p, timeout=60)
            if r.status_code != 200:
                log(f"Cloudflare: couldn't list {prefix} (HTTP {r.status_code})")
                break
            js = r.json()
            keys += [o["key"] for o in js.get("result") or []]
            info = js.get("result_info") or {}
            cursor = info.get("cursor")
            if not (cursor and info.get("is_truncated")):
                break
    except Exception as e:
        log(f"Cloudflare: couldn't list {prefix} ({type(e).__name__})")
    return keys

def notify(title, body="", tag="", url="/", log=print, items=None):
    """Queue a phone alert; the Cloudflare Worker sends it within a minute to every phone that turned alerts on and
    keeps it in the app's Alerts tab. `url` is where tapping it goes in the app (e.g. "/#worth/NVDA", "/#post/<id>");
    `items` ([{"l": label, "u": app link, "x": outside link}]) lists each thing an alert covers. With several items
    and no url, tapping the alert opens it in the Alerts tab."""
    import json, time, random
    key = f"push/outbox/{int(time.time() * 1000)}-{random.randint(0, 99999):05d}.json"
    msg = {"title": title[:120], "body": body[:600], "tag": tag, "url": url or ""}
    if items:
        msg["items"] = [{k: str(v)[:500] for k, v in it.items() if v} for it in items[:20]]
    return put(key, json.dumps(msg), log=lambda m: log(m.replace(key, "a phone alert")))


def post_id(url):
    """Short stable id for a post (its link), the same one the app computes (FNV-1a, base 36)."""
    h = 0x811C9DC5
    for b in str(url or "").encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    n, out = h, ""
    while True:
        n, r = divmod(n, 36)
        out = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + out
        if not n:
            return out


def save_reasons(actions, log=print):
    """Why the tool bought or sold each stock this run, so trade alerts can say it (e.g. '15% stop')."""
    import json, datetime as dt
    why = {f"{a['symbol'].replace('.', '-')}|{a['side']}": a["why"] for a in actions or []
           if a.get("symbol") and a.get("side") and a.get("why") and a.get("ok", True)}
    if why:
        put(f"push/why/{dt.datetime.utcnow():%Y%m%d%H%M}.json", json.dumps(why), log=lambda m: log("Cloudflare: saved trade reasons for alerts" if "updated" in m else m))


_phones = None


def has_phones():
    """True when at least one phone has alerts turned on, so the matching emails can be skipped."""
    global _phones
    if _phones is None:
        _phones = False
        tok, acc = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        if tok and acc:
            try:
                r = requests.get(f"https://api.cloudflare.com/client/v4/accounts/{acc}/r2/buckets/{BUCKET}/objects/push/subs.json",
                                 headers={"Authorization": f"Bearer {tok}"}, timeout=30)
                _phones = r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0
            except Exception:
                _phones = False
    return _phones
