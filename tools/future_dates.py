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
            if len(samples[kind]) < 5:
                samples[kind].append((d, r[1], r[4]))
print(f"rows dated after {limit}: {sum(cnt.values())}  {dict(cnt)}")
for k, xs in samples.items():
    for d, b, u in sorted(xs):
        print(f"  {k} {d} {b} {u}")

by = collections.Counter()
for path in glob.glob(os.path.join("social", "posts", "press_bf", "*.jsonl.gz")):
    rows = S.read_jsonl(path)
    fut = [r for r in rows if str(r[0])[:10] > limit]
    if fut:
        hosts = collections.Counter(str(r[4]).split("/")[2] for r in fut)
        print(f"member {os.path.basename(path)}: {len(fut)} of {len(rows)} rows in the future; {dict(hosts)}")

# what the site readers say now for those sites (dates as given by the library)
try:
    from python_statement import Scraper
    from python_statement.config import SCRAPER_CONFIG
    for name in [n for n in SCRAPER_CONFIG if any(k in n.lower() for k in ("thune", "rounds"))]:
        try:
            res = Scraper.run_scraper(name, 1) or []
        except Exception as e:
            print(name, "error", type(e).__name__, e); continue
        print(name, SCRAPER_CONFIG[name].get("method"), len(res))
        for r in res[:6]:
            print("   ", r.get("date"), r.get("url"))
except Exception as e:
    print("library:", type(e).__name__, e)
