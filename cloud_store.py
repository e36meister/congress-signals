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


def notify(title, body="", tag="", url="/", log=print):
    """Queue a phone alert; the Cloudflare Worker sends it within a minute to every phone that turned alerts on."""
    import json, time, random
    key = f"push/outbox/{int(time.time() * 1000)}-{random.randint(0, 99999):05d}.json"
    return put(key, json.dumps({"title": title[:120], "body": body[:600], "tag": tag, "url": url}), log=lambda m: log(m.replace(key, "a phone alert")))


def save_reasons(actions, log=print):
    """Why the tool bought or sold each stock this run, so trade alerts can say it (e.g. '15% stop')."""
    import json, datetime as dt
    why = {f"{a['symbol'].replace('.', '-')}|{a['side']}": a["why"] for a in actions or []
           if a.get("symbol") and a.get("side") and a.get("why") and a.get("ok", True)}
    if why:
        put(f"push/why/{dt.datetime.utcnow():%Y%m%d%H%M}.json", json.dumps(why), log=lambda m: log("Cloudflare: saved trade reasons for alerts" if "updated" in m else m))
