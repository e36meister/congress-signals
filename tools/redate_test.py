"""Read-only test (Nov 12) of press_backfill.redate_misdated and the year-less date rule: works on a COPY of the
stored members'-site releases in a temporary folder; the real posts cache is not changed or saved."""
import sys, os, shutil, tempfile, time, collections
sys.path.insert(0, ".")
import congress_signals.social as S
import congress_signals.press_backfill as PB

tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "posts"))
shutil.copytree(os.path.join("social", "posts", "press_bf"), os.path.join(tmp, "posts", "press_bf"))
cfg = {"SOCIAL_DIR": tmp, "DATA_DIR": "data"}
before = {}
for f in ("T000250", "S001217"):
    p = os.path.join(tmp, "posts", "press_bf", f + ".jsonl.gz")
    rows = S.read_jsonl(p) if os.path.exists(p) else []
    before[f] = {r[4]: str(r[0])[:10] for r in rows}
    print(f, len(rows), "rows; by year before:", dict(sorted(collections.Counter(str(r[0])[:4] for r in rows).items())))
st = {}
t0 = time.time()
ok = PB.redate_misdated(cfg, st, time.time() + 600)
print("done:", ok, f"{time.time() - t0:.0f}s", {h: {"done": v["done"], "tries": len(v["tries"])} for h, v in st["press_redate"].items()})
for f in before:
    p = os.path.join(tmp, "posts", "press_bf", f + ".jsonl.gz")
    rows = S.read_jsonl(p) if os.path.exists(p) else []
    print(f, "by year after:", dict(sorted(collections.Counter(str(r[0])[:4] for r in rows).items())))
    for r in rows[:: max(1, len(rows) // 8)]:
        print("   ", before[f].get(r[4]), "->", str(r[0])[:10], r[4])
# the hourly reader for the two year-less sites, into the temporary copy
from python_statement import Scraper
for n in ("thune", "rickscott"):
    res = Scraper.run_scraper(n, 3) or []
    for r in res[:3]:
        d2, _, _ = PB._text(r["url"])
        print(n, "list", r.get("date"), "page", d2, "->", PB._pick_date(n, r.get("date"), d2), r["url"])
        time.sleep(1)
