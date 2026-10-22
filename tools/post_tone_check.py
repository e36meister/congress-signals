"""Read-only: find feed posts naming Musk (default) and show their tone tags before/after the Oct 22 wording rule."""
import sys, os, json
sys.path.insert(0, ".")
import cloud_store
import congress_signals.post_sort as PS
q = os.environ.get("Q", "Musk").lower()
feed = json.loads(cloud_store.get("posts/feed.json", log=lambda m: None) or b"{}")
for x in feed.get("items") or []:
    if q in (x.get("text") or "").lower():
        sents = PS._sentences(x.get("text") or "")
        print(f"\n{x.get('d')} {x.get('who')} lvl={x.get('lvl')} tv={x.get('tv')}\n  text: {x.get('text')}")
        for g in x.get("tg") or []:
            w = str(g[0]).split()[-1] if g[1] != "co" else str(g[0])
            print("  tag", g, "->", PS.cue_tone([s for s in sents if w.lower() in s.lower()], g[2]) if len(g) >= 3 and g[1] in ("person", "lobbyist", "co") else "")
