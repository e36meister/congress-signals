"""Read-only (Nov 12): for a few stored press releases dated in the future, what date does the release page itself
give (trafilatura, as the backfill reads it)? Nothing is saved or changed."""
import sys, os, glob, time, datetime as dt
sys.path.insert(0, ".")
import congress_signals.social as S
import congress_signals.press_backfill as PB

limit = (dt.date.today() + dt.timedelta(days=1)).isoformat()
rows = []
for path in glob.glob(os.path.join("social", "posts", "press_bf", "*.jsonl.gz")):
    rows += [r for r in S.read_jsonl(path) if str(r[0])[:10] > limit]
rows.sort(key=lambda r: r[4])
ok = 0
for r in rows[::40][:12]:
    d2, t2, text = PB._text(r[4])
    ok += bool(d2)
    print(f"stored {str(r[0])[:10]}  page {d2}  text {len(text or '') if text is not None else None}  {r[4]}")
    time.sleep(2)
print(f"page date found for {ok} of 12")
