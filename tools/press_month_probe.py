"""Read-only probe (Nov 1 2026): does Congress Press have this month's file yet, and would the posts jobs now log a
missing new-month file as 'not posted yet' (first few days, last month fine) instead of a failure? Writes nothing."""
import datetime as dt, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from congress_signals import social as S, runlog

today = dt.date.today()
cur = today.strftime("%Y-%m")
prev = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
ok = set()
for k in (prev, cur):
    r = S._get(S.PRESS + f"{k}.jsonl", as_json=False, timeout=180)
    if r is not None:
        ok.add(k)
        dates = []
        for l in r.text.splitlines():
            try:
                dates.append(str(json.loads(l).get("date") or "")[:10])
            except Exception:
                pass
        dates = sorted(d for d in dates if d)
        print(f"{k}: read fine, {len(dates):,} releases, newest {dates[-1] if dates else '-'}")
    else:
        quiet = S._press_month_not_posted_yet(k, today, ok)
        line = (f"Press releases: {k}: Congress Press hasn't posted this month's file yet" if quiet
                else f"Press releases: {k}: Congress Press download failed")
        print(f"{k}: not available -> would log: {line!r} (counted as failure: {bool(runlog.FAIL.search(line))})")
