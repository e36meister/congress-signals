"""Read-only: run the hourly members'-site reader on just the sites that use the backup reader, with a 14-day
window, and report what each one brings in (nothing is saved back from the research job)."""
import sys, time, datetime as dt, glob, os
sys.path.insert(0, ".")
import congress_signals.engine as E
import congress_signals.social as S
import congress_signals.press_backfill as PB
from congress_signals import press_sites as PS
from congress_signals.social import read_jsonl
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social", "TIME_BUDGET_MIN": 0}
st = S._state(cfg)
leg = S.legislators(cfg, st)
before = {f: len(read_jsonl(f)) for f in glob.glob("social/posts/press_bf/*.jsonl.gz")}
since = (dt.date.today() - dt.timedelta(days=14)).isoformat()
n = PB.latest_current(cfg, S.Tagger(cfg), leg, time.time() + 600, since=since, workers=4, only=sorted(PS.BACKUP))
print("added", n)
for f in sorted(glob.glob("social/posts/press_bf/*.jsonl.gz")):
    rows = read_jsonl(f)
    if len(rows) != before.get(f, 0):
        new = rows[-1] if rows else None
        b = os.path.basename(f).split(".")[0]
        print(f"  {leg.get(b, {}).get('name', b)}: +{len(rows) - before.get(f, 0)}  e.g. {[r for r in rows if str(r[0])[:10] >= since][-1][0]} {[r for r in rows if str(r[0])[:10] >= since][-1][3][:90]!r}")
