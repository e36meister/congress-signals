"""Hourly check for new House and Senate trade filings. Takes a minute or two.
When it finds filings it hasn't seen before, it starts the full update (daily.yml) right away,
which scores them, updates the dashboard and emails any new BUY or avoid signals."""
import os, json, re, datetime as dt
import requests
import congress_signals.engine as E

STATE = "quick_state/seen.json"
LOOKBACK_DAYS = 10


def house_ids():
    today = dt.date.today()
    years = {today.year, (today - dt.timedelta(days=LOOKBACK_DAYS)).year}
    ids = set()
    for y in sorted(years):
        try:
            df = E.house_index(y)
        except Exception as e:
            E.log(f"Quick check: House {y} index failed ({e})")
            continue
        df["FilingDate"] = E.pd.to_datetime(df["FilingDate"], errors="coerce")
        recent = df[df["FilingDate"] >= E.pd.Timestamp(today - dt.timedelta(days=LOOKBACK_DAYS))]
        ids |= {f"H{d}" for d in recent["DocID"].astype(str)}
    return ids


def senate_ids():
    try:
        s = E._senate_session()
        start = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%m/%d/%Y 00:00:00")
        r = s.post(f"{E.EFD}/search/report/data/", timeout=60,
                   data={"start": "0", "length": "100", "report_types": "[11]", "filer_types": "[]",
                         "submitted_start_date": start, "submitted_end_date": "", "candidate_state": "",
                         "senator_state": "", "office_id": "", "first_name": "", "last_name": ""},
                   headers={"Referer": f"{E.EFD}/search/", "X-CSRFToken": s.cookies.get("csrftoken", "")})
        rows = r.json().get("data", [])
    except Exception as e:
        E.log(f"Quick check: Senate listing failed ({e})")
        return set()
    out = set()
    for row in rows:
        m = re.search(r'href="([^"]+)"', row[3])
        if m:
            out.add("S" + m.group(1))
    return out


def start_full_update():
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        E.log("Quick check: no GitHub token; can't start the full update")
        return False
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/dispatches",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                      json={"ref": os.environ.get("GITHUB_REF_NAME", "main"), "inputs": {"chain": "0"}}, timeout=30)
    E.log(f"Quick check: started the full update (HTTP {r.status_code})")
    return r.status_code == 204


def _drive():
    sa, folder = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON"), os.environ.get("GDRIVE_FOLDER_ID")
    if not (sa and folder):
        return None, None
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(json.loads(sa), scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False), folder


TAP = re.compile(r"^order_request_([A-Z][A-Z0-9\-]{0,6})_(\d+(?:\.\d+)?)_(\d{10,14})\.json$")


def process_order_requests():
    """Orders you approved with the Buy button on the dashboard (saved as small files in your Drive folder)."""
    import broker
    drive, folder = _drive()
    if drive is None:
        return
    q = f"name contains 'order_request_' and '{folder}' in parents and trashed = false"
    files = drive.files().list(q=q, fields="files(id,name)", supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute().get("files", [])
    for f in files:
        m = TAP.match(f["name"])
        if not m:
            new_name, ok = f["name"].replace("order_request_", "order_failed_", 1).replace(".json", "_bad-request.json"), False
        else:
            t, dollars, ms = m.group(1), float(m.group(2)), int(m.group(3))
            age_h = (dt.datetime.now(dt.timezone.utc).timestamp() * 1000 - ms) / 3.6e6
            if age_h > 48:
                ok, why = False, "expired"
            else:
                ok, why = broker.place_tap_order(t, dollars, m.group(3), E.log)
            new_name = (f"order_done_{t}_{m.group(2)}_{m.group(3)}.json" if ok
                        else f"order_failed_{t}_{m.group(2)}_{m.group(3)}_{why}.json")
        drive.files().update(fileId=f["id"], body={"name": new_name}, supportsAllDrives=True).execute()
    if files:
        E.log(f"Quick check: handled {len(files)} order request(s) from the dashboard")


def refresh_portfolio():
    """Read-only: update the dashboard's My portfolio tab from Alpaca. Never trades."""
    import broker
    snap = broker.sync({"HOLD_DAYS": int(os.environ.get("HOLD_DAYS", "60"))}, None, None, {}, E.log, trade=False)
    drive, folder = _drive()
    if snap is None or drive is None:
        return
    from googleapiclient.http import MediaIoBaseUpload
    import io
    q = f"name = 'dashboard_data.json' and '{folder}' in parents and trashed = false"
    found = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    if not found:
        return
    fid = found[0]["id"]
    data = json.loads(drive.files().get_media(fileId=fid, supportsAllDrives=True).execute())
    try:                                        # undo a waiting cleanup sale that no longer applies
        w = data.get("watchlist") or {}
        caps = {broker.to_alpaca(r["t"]): r.get("cap") for r in (w.get("buys") or []) + (w.get("sells") or []) if r.get("t")}
        if caps:
            broker.recheck_queued_sells(caps, E.log)
            snap = broker.sync({"HOLD_DAYS": int(os.environ.get("HOLD_DAYS", "60"))}, None, None, {}, E.log, trade=False) or snap
    except Exception as e:
        E.log(f"Quick check: sell recheck skipped ({type(e).__name__})")
    old = data.get("portfolio") or {}
    if old.get("limits", {}).get("from_policy"):
        snap["limits"] = old["limits"]          # the full update knows the current holding periods; keep them
    data["portfolio"] = snap
    body = io.BytesIO(json.dumps(data).encode())
    drive.files().update(fileId=fid, media_body=MediaIoBaseUpload(body, mimetype="application/json"),
                         supportsAllDrives=True).execute()
    E.log("Quick check: portfolio refreshed on the dashboard")


def main():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    seen = set(json.load(open(STATE))) if os.path.exists(STATE) else None
    now = house_ids() | senate_ids()
    if not now:
        E.log("Quick check: couldn't read either filing list; trying again next hour")
        return
    if seen is None:
        E.log(f"Quick check: first run, noted {len(now)} recent filings")
        json.dump(sorted(now), open(STATE, "w"))
        return
    new = now - seen
    if new:
        E.log(f"Quick check: {len(new)} new filing(s): {', '.join(sorted(new)[:10])}")
        if start_full_update():
            seen |= new
    else:
        E.log("Quick check: nothing new")
    # keep only IDs still inside the lookback window so the file stays small
    # (a filing whose update couldn't be started stays unseen, so the next hour tries again)
    json.dump(sorted(seen & now), open(STATE, "w"))


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            process_order_requests()
        except Exception as e:
            E.log(f"Quick check: order requests skipped ({type(e).__name__})")
        try:
            refresh_portfolio()
        except Exception as e:
            E.log(f"Quick check: portfolio refresh skipped ({type(e).__name__})")
