"""Read-only: print the Data health report (every job's open problems, recent fixes, the app server's problems,
and per-site request counts), for the daily repair session."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
from congress_signals import health as H

def get(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try: return json.loads(raw) if raw else None
    except Exception: return None

names = sorted(set(H.EXPECT_HOURS) | set(H.SCRIPT_JOBS.values()) | {"watchdog"} | {f"archive_{i}" for i in range(8)})
out = {"jobs": {}, "app": get("health/app.json") or {}, "sizes": get(H.SIZES_KEY)}
for n in names:
    j = get(H.JOB_KEY.format(n))
    if j: out["jobs"][n] = j
for n, j in out["jobs"].items():
    print(f"\n=== {n}  (last report {j.get('at')}, {j.get('minutes')} min)")
    for x in j.get("open") or []:
        print(f"  OPEN [{x.get('kind')}] since {x.get('first')} x{x.get('runs')}: {x.get('text')}")
    for x in (j.get("fixed") or [])[-10:]:
        print(f"  fixed {x.get('at')}: {x.get('text')}")
    for h, v in (j.get("http") or {}).items():
        print(f"  site {h}: {v}")
print("\n=== app server")
for k, v in out["app"].items():
    print(f"  {'OPEN' if v.get('open') else 'fixed'} {k}: {v.get('text')} (first {v.get('first')}, last {v.get('last')}, n={v.get('n')})")
print("\nJSON_HEALTH " + json.dumps(out, default=str))
