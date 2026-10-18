"""Read-only: why a post in the app's feed was tagged the way it was (full text, tags, people matched)."""
import sys, os, json, re, glob
sys.path.insert(0, ".")
import cloud_store
WHO, TK = os.environ.get("WHO", "Blackburn"), os.environ.get("TK", "FDX")
feed = json.loads(cloud_store.get("posts/feed.json", print) or "{}")
hits = [x for x in feed.get("items", []) if WHO.lower() in str(x.get("who", "")).lower() and (TK in (x.get("t") or []) or TK in json.dumps(x))]
print(f"{len(hits)} feed item(s) for {WHO} / {TK}")
for x in hits:
    print(json.dumps(x, indent=1)[:3000])
    # the stored record with the full text the tagger read
    for f in glob.glob("social/**/*.jsonl", recursive=True):
        try:
            for ln in open(f):
                if x["url"] and x["url"] in ln:
                    r = json.loads(ln)
                    print("STORED:", f, "\nTEXT:", (r[3] or "")[:2500], "\nTICKERS:", r[5], "\nPEOPLE:", r[6], "\nSORT:", json.dumps(r[11] if len(r) > 11 else None)[:1500])
                    break
        except Exception:
            pass
import congress_signals.social as S
tg = S.Tagger({"DATA_DIR": "data", "SOCIAL_DIR": "social"})
for x in hits[:2]:
    print("RETAG with current tagger:", tg.tag(x.get("text") or ""))
