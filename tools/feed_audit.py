"""Read-only: audit the app's post feed: bad text (scripts, code, markup), unknown members, tag quality."""
import sys, json, re, collections
sys.path.insert(0, ".")
import cloud_store
feed = json.loads(cloud_store.get("posts/feed.json", print) or "{}")
items = feed.get("items", [])
print("items:", len(items), "| updated", feed.get("updated"), "| sorter", feed.get("sorter"))
print("levels:", collections.Counter(x.get("lvl") for x in items))
print("sources:", collections.Counter(x.get("src") for x in items).most_common(12))
CODE = re.compile(r"function\s*\(|var \w+\s*=|window\.|document\.|\{\s*\"|=>|gtag|dataLayer|<script|</?\w+[^>]*>|&[a-z]+;|\\u00|@media|\.css|jQuery|\$\(", re.I)
bad = [x for x in items if CODE.search(x.get("text") or "")]
print(f"\n=== {len(bad)} items whose text looks like code/markup ===")
for x in bad[:15]:
    m = CODE.search(x["text"])
    print(f"- {x['who']} | {x['src']} | {x['url'][:90]}\n    ...{x['text'][max(0, m.start() - 80):m.start() + 160]!r}")
print("\n=== reasons (why) most common ===")
print(collections.Counter(w.split(',')[0][:50] for x in items for w in (x.get("why") or [])).most_common(25))
print("\n=== who values (sample of unusual) ===")
whos = collections.Counter(x.get("who") for x in items)
print([w for w in whos if not re.match(r"^[A-Z][a-z]+", str(w or ""))][:20])
print("bioguide missing:", sum(1 for x in items if not x.get("b")))
try:
    idx = json.loads(cloud_store.get("members/index.json", print) or "{}")
    ks = {m["k"] for m in idx.get("members", [])}
    miss = collections.Counter(x["who"] for x in items if x.get("b") and x["b"] not in ks)
    print(f"\nposts by members with no member page (no disclosed trades): {sum(miss.values())} posts, {len(miss)} members:", miss.most_common(15))
except Exception as e:
    print("members index unreadable", e)
print("\n=== 12 notable items, full ===")
for x in [x for x in items if x.get("lvl", 0) >= 2][:12]:
    print(json.dumps({k: x.get(k) for k in ("who", "src", "d", "t", "ty", "meet", "tg", "why", "lvl")})[:500])
    print("   TEXT:", (x.get("text") or "")[:240].replace("\n", " | "))
