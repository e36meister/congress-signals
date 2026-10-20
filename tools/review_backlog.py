"""One-time (owner, Oct 20): put every possible company person found in saved posts into the app's Needs review
list: one item per name + company (its newest post, and how many posts it appears in)."""
import sys, os, json, glob, gzip, collections
sys.path.insert(0, ".")
import cloud_store
import congress_signals.social as S
leg = {}
try:
    leg = json.load(open("social/legislators.json"))
except Exception:
    pass
name_of = {b: (m.get("name") or f"{m.get('first', '')} {m.get('last', '')}".strip()) for b, m in leg.items()} if isinstance(leg, dict) else {}
verd = json.loads(cloud_store.get("inputs/people_reviews.json", print) or b"{}")
queue = json.loads(cloud_store.get("posts/review.json", print) or b"[]")
best, cnt = {}, collections.Counter()
files = glob.glob("social/posts/**/*.jsonl*", recursive=True)
n_rec = 0
for f in files:
    try:
        op = gzip.open if f.endswith(".gz") else open
        for ln in op(f, "rt", encoding="utf-8"):
            r = json.loads(ln)
            if len(r) < 12 or not isinstance(r[11], dict) or not r[11].get("cand"):
                continue
            n_rec += 1
            for c in r[11]["cand"]:
                key = f"{str(c['w']).lower()}|{str(c['t']).lower()}"
                if key in verd:
                    continue
                cnt[key] += 1
                if key not in best or str(r[0]) > best[key]["d"]:
                    best[key] = {"key": key, "id": cloud_store.post_id(r[4]), "w": c["w"], "n": c["n"], "t": c["t"],
                                 "role": c.get("role"), "by": c.get("by"), "c": c.get("c"), "b": r[1],
                                 "who": name_of.get(r[1], r[1]), "d": str(r[0])[:10], "url": r[4], "text": (r[3] or "")[:600]}
    except Exception as e:
        print("skip", f, type(e).__name__)
print(f"{len(files)} files, {n_rec} posts with candidates, {len(best)} distinct name+company pairs")
for k, v in best.items():
    v["cnt"] = cnt[k]
old = {q["key"]: q for q in queue if q["key"] not in verd}
for k, v in best.items():
    if k in old:
        v["cnt"] = max(v["cnt"], old[k].get("cnt", 1))
    old[k] = v if k not in old or v["d"] >= old[k]["d"] else old[k]
items = sorted(old.values(), key=lambda q: (-q.get("cnt", 1), q["d"]), reverse=False)
items = sorted(items, key=lambda q: q["d"], reverse=True)
print("by first/last:", collections.Counter(q.get("by") for q in items))
print("top:", [(q["w"], q["t"], q.get("cnt")) for q in sorted(items, key=lambda q: -q.get("cnt", 1))[:20]])
cloud_store.put("posts/review.json", json.dumps(items[:1500], default=str), print)
print("queue size:", min(len(items), 1500))
