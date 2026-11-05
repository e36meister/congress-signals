"""The app's data files in Cloudflare R2, where the Capitol Capital app (phone and capitolcapital.cc) reads them.
Since Nov 5 2026 (owner: nothing uses Google Drive) R2 is the only home of dashboard_data.json, so the jobs that
change it read it with read_dashboard() (never mistakes an outage for an empty file) and save it with
save_dashboard(). The daily backup (backups/dashboard_data_<date>.json) is made by the Worker at 05:41 UTC,
inside Cloudflare. Does nothing until CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are set.
put/get never raise: a failed upload only logs a line (no balances or positions are ever logged)."""
import os, json, time, datetime as dt
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
        why = None
        for attempt in range(4):
            if attempt:
                time.sleep(5 * 2 ** (attempt - 1))         # 5, 10, 20 s
            try:
                r = requests.put(url, data=data, timeout=120,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": content_type})
            except requests.RequestException as e:
                why = type(e).__name__
                continue
            if r.status_code < 300:
                log(f"Cloudflare: updated {name}")
                return True
            why = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break
        log(f"Cloudflare: couldn't update {name} ({why})")
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



class ReadError(Exception):
    """R2 didn't answer (outage, timeout, 5xx): the file may well exist, so nothing may be written over it."""


def get_strict(name):
    """Bytes of one R2 object; None only when R2 says it doesn't exist (404). Any other answer is retried
    (5, 10, 20 s) and then raises ReadError, so a passing outage is never taken for an empty file."""
    tok, acc = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not (tok and acc):
        raise ReadError("Cloudflare keys not set")
    why = None
    for attempt in range(4):
        if attempt:
            time.sleep(5 * 2 ** (attempt - 1))
        try:
            r = requests.get(f"https://api.cloudflare.com/client/v4/accounts/{acc}/r2/buckets/{BUCKET}/objects/{name}",
                             headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        except requests.RequestException as e:
            why = type(e).__name__
            continue
        if r.status_code == 200:
            return r.content
        if r.status_code == 404:
            return None
        why = f"HTTP {r.status_code}"
        if r.status_code < 500 and r.status_code != 429:
            break
    raise ReadError(why)


DASH = "dashboard_data.json"


def read_dashboard():
    """The app's dashboard_data.json as a dict. Raises ReadError when R2 can't be read or the copy is missing or
    damaged, so a job stops instead of saving a near-empty file over the real one."""
    raw = get_strict(DASH)
    if not raw:
        raise ReadError("dashboard_data.json is missing in Cloudflare")
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise ReadError(f"dashboard_data.json doesn't read as JSON ({e})")
    if not isinstance(d, dict) or not d:
        raise ReadError("dashboard_data.json is empty")
    return d


def save_dashboard(d, log=print):
    """Save the app's dashboard_data.json (dict, bytes or a file path). True when saved."""
    if isinstance(d, dict):
        d = json.dumps(d).encode()
    return put(DASH, d, log)


def delete(name, log=print):
    """Remove one object from R2. True when it's gone."""
    tok, acc = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not (tok and acc):
        return False
    try:
        r = requests.delete(f"https://api.cloudflare.com/client/v4/accounts/{acc}/r2/buckets/{BUCKET}/objects/{name}",
                            headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        return r.status_code < 300 or r.status_code == 404
    except Exception as e:
        log(f"Cloudflare: couldn't delete {name} ({type(e).__name__})")
        return False


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

def notify(title, body="", tag="", url="/", log=print, items=None, account=None):
    """Queue a phone alert; the Cloudflare Worker sends it within a minute to every phone that turned alerts on and
    keeps it in the app's Alerts tab. `url` is where tapping it goes in the app (e.g. "/#worth/NVDA", "/#post/<id>");
    `items` ([{"l": label, "u": app link, "x": outside link}]) lists each thing an alert covers. With several items
    and no url, tapping the alert opens it in the Alerts tab."""
    import json, time, random
    key = f"push/outbox/{int(time.time() * 1000)}-{random.randint(0, 99999):05d}.json"
    msg = {"title": title[:120], "body": body[:600], "tag": tag, "url": url or ""}
    if account:                    # a family account's own phones and Alerts list instead of the owner's
        msg["acct"] = account
    if items:
        # xs: several outside links for one item ([{"l": label, "x": link}], e.g. each post before one trade)
        msg["items"] = [{k: ([{"l": str(z.get("l", ""))[:60], "x": str(z.get("x", ""))[:500]} for z in v[:6] if z.get("x")]
                             if k == "xs" else str(v)[:500]) for k, v in it.items() if v} for it in items[:20]]
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
