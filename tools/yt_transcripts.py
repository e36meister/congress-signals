"""Read-only: find a member's YouTube posts in the feed and saved posts, and try to get each video's transcript."""
import sys, os, json, glob, gzip, re, subprocess
sys.path.insert(0, ".")
import cloud_store
WHO = os.environ.get("WHO", "Fern")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "youtube-transcript-api"], check=False)
vids = {}
feed = json.loads(cloud_store.get("posts/feed.json", print) or b"{}")
for x in feed.get("items", []):
    if WHO.lower() in str(x.get("who", "")).lower() and "youtube" in str(x.get("url", "")):
        vids[x["url"]] = (x.get("who"), x.get("d"), x.get("text"))
for f in glob.glob("social/posts/youtube/*.jsonl*"):
    op = gzip.open if f.endswith(".gz") else open
    try:
        for ln in op(f, "rt"):
            r = json.loads(ln)
            if r[4] and "youtube" in r[4] and r[0] >= "2026-10-01":
                if WHO.lower() in f.lower() or r[4] in vids:
                    vids.setdefault(r[4], (os.path.basename(f), r[0], r[3]))
    except Exception:
        pass
print(f"{len(vids)} video(s)")
from youtube_transcript_api import YouTubeTranscriptApi
api = YouTubeTranscriptApi()
for u, (who, d, text) in vids.items():
    vid = re.search(r"v=([\w-]{11})", u).group(1)
    print(f"\n=== {who} {d} {u}\nTITLE/DESC: {(text or '')[:600]}")
    try:
        tr = api.fetch(vid, languages=["en", "es"])
        full = " ".join(s.text for s in tr)
        print(f"TRANSCRIPT ({len(full)} chars): {full[:6000]}")
    except Exception as e:
        print("TRANSCRIPT failed:", type(e).__name__, str(e)[:300])
