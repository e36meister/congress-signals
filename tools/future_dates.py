"""Read-only (Nov 12): stored posts dated in the future (a misread date makes an old post look new). Lists, per post
kind, how many rows are dated after tomorrow, with samples (date, member, address). Nothing is saved or changed."""
import sys, os, glob, collections, datetime as dt
sys.path.insert(0, ".")
import congress_signals.social as S

limit = (dt.date.today() + dt.timedelta(days=1)).isoformat()
cnt, samples = collections.Counter(), collections.defaultdict(list)
for path in glob.glob(os.path.join("social", "posts", "*", "*.jsonl.gz")):
    kind = path.split(os.sep)[-2]
    for r in S.read_jsonl(path):
        d = str(r[0])[:10]
        if d > limit:
            cnt[kind] += 1
            if len(samples[kind]) < 40:
                samples[kind].append((d, r[1], r[4]))
print(f"rows dated after {limit}: {sum(cnt.values())}  {dict(cnt)}")
for k, xs in samples.items():
    for d, b, u in sorted(xs):
        print(f"  {k} {d} {b} {u}")
