"""Read-only: for each member-site reader that came back empty or stale (tools/press_probe_last.json), try the
backup reader (congress_signals/press_sites.py) and read the first few releases' dates."""
import sys, json, re
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
from congress_signals import press_sites as PS
from congress_signals.press_backfill import _text

rows = json.load(open("tools/press_probe_last.json"))
leg = {}
for p in ("social/legislators.json", "social/posts/legislators.json"):
    try: leg = json.load(open(p)); break
    except Exception: pass
cur = {re.sub(r"^https?://(www\.)?", "", (m.get("url") or "").lower()).split("/")[0]: (m.get("name") or b, m.get("current", True))
       for b, m in leg.items()}

def one(x):
    r = PS.read_site(x["url"])
    dates = []
    for u in r["links"][:4]:
        d, t, _ = _text(u)
        dates.append((str(d or "")[:10], u))
    host = re.sub(r"^https?://(www\.)?", "", x["url"].lower()).split("/")[0]
    return x, r, dates, cur.get(host)

with ThreadPoolExecutor(max_workers=12) as ex:
    res = list(ex.map(one, rows))
out = []
for x, r, dates, who in res:
    newest = max([d for d, _ in dates if d] or [""]) or None
    print(f"- {x['name']} ({x['why']}, reader newest {x['newest']}) member={who} -> backup: {r['how']} list={r['list']} "
          f"links={len(r['links'])} newest={newest}")
    for d, u in dates[:3]: print(f"     {d} {u}")
    out.append({"name": x["name"], "why": x["why"], "reader_newest": x["newest"], "member": who, "how": r["how"],
                "list": r["list"], "n": len(r["links"]), "newest": newest})
print("JSON_SUMMARY " + json.dumps(out))
