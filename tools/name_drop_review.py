"""Read-only (owner, Nov 12): is the spaCy name check (Nov 11) throwing away good intel? Re-tags stored posts from the
last DAYS days (default 30) that had a company person matched when they were stored, with the current tagger, and
prints every name the check now sets aside: company, name, the reason, the words around the name, and the post's
address. Also lists the names it kept, so both sides can be judged. Nothing is saved or changed."""
import sys, os, re, json, glob, datetime as dt
sys.path.insert(0, ".")
import requests
import congress_signals.social as S
from congress_signals import name_context as NC

DAYS = int(os.environ.get("DAYS", "30"))
since = (dt.date.today() - dt.timedelta(days=DAYS)).isoformat()
cfg = {"DATA_DIR": "data", "SOCIAL_DIR": "social"}
tg = S.Tagger(cfg)

_press = {}


def full_text(url, month):
    """A press release's full text from the Congress Press monthly file (stored rows keep only 400 characters)."""
    if month not in _press:
        _press[month] = {}
        try:
            r = requests.get(S.PRESS + f"{month}.jsonl", timeout=180, headers=S.UA)
            for ln in r.text.splitlines() if r.status_code == 200 else []:
                try:
                    x = json.loads(ln)
                except Exception:
                    continue
                if x.get("url"):
                    _press[month][x["url"].rstrip("/")] = (x.get("title") or "") + "\n" + (x.get("text") or "")
        except Exception:
            pass
    return _press[month].get((url or "").rstrip("/"))


rows, seen = [], set()
for path in glob.glob(os.path.join("social", "posts", "*", "*.jsonl.gz")):
    kind = path.split(os.sep)[-2]
    for r in S.read_jsonl(path):
        if len(r) < 7 or str(r[0])[:10] < since or not r[6] or r[4] in seen:
            continue
        seen.add(r[4])
        rows.append((kind, r))
print(f"{len(rows)} stored post(s) since {since} had a company person matched when stored")

drops, kept, n_full = [], [], 0
for kind, r in sorted(rows, key=lambda x: str(x[1][0]), reverse=True):
    text = r[3] or ""
    if kind in ("press", "press_bf", "press_wb"):
        ft = full_text(r[4], str(r[0])[:7])
        if ft:
            text, n_full = ft, n_full + 1
    tk, pp, secs, tone = tg.tag(text)
    for n, t, why in tg.name_drops:
        m = re.search(r"\b" + re.escape(n) + r"\b", text, re.I)
        around = text[max(0, m.start() - 160):m.end() + 160].replace("\n", " ") if m else "(name not found as written)"
        drops.append((str(r[0])[:10], kind, t, n, why, around, r[4]))
    for n, t, role in pp:
        kept.append((str(r[0])[:10], kind, t, n, role, r[4]))

print(f"full text found for {n_full} press release(s); the rest use the stored first 400 characters")
print(f"name check: loaded={NC.STATUS['loaded']} {NC.STATUS['why']}")
print(f"\n=== SET ASIDE: {len(drops)} name match(es) ===")
for i, (d, k, t, n, why, around, u) in enumerate(drops, 1):
    print(f"\n[{i}] {d} {k} {t} | {n} | {why}\n    ...{around}...\n    {u}")
print(f"\n=== KEPT: {len(kept)} name match(es) ===")
for d, k, t, n, role, u in kept:
    print(f"{d} {k} {t} | {n} ({role}) | {u}")
