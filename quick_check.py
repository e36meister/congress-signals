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
    main()
