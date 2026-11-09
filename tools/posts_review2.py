"""Read-only (owner, Nov 8), second half of the Buy/Post review: which Post alerts are still showing in the Alerts tab
(not cleared), the full text of every alerted post (Congress Press monthly files for press releases), what the
current tagger finds in each one today, the whole Posts feed and watch list (for related posts) and where the
post signals stand in the weekly check. Writes out/review2.json. Nothing about balances, positions or orders."""
import sys, os, json, datetime as dt
sys.path.insert(0, ".")
import requests
import cloud_store
import congress_signals.social as S

os.makedirs("out", exist_ok=True)


def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


log = r2("push/log.json") or []
cleared = r2("push/cleared.json") or {"ids": [], "before": None}
ids, before = set(cleared.get("ids") or []), cleared.get("before")
alerts = [a for a in log if a.get("kind") == "posts"]
shown = [a.get("id") for a in alerts if a.get("id") not in ids and not (before and a.get("at", "") <= before)]
print(f"Post alerts {len(alerts)}; still in the Alerts tab {len(shown)}; clear-all at {before}")

urls = set()
for a in alerts:
    for it in a.get("items") or []:
        if it.get("x"):
            urls.add(it["x"])
        for z in it.get("xs") or []:
            if z.get("x"):
                urls.add(z["x"])

press = {}
for month in ("2026-10", "2026-11"):
    try:
        r = requests.get(S.PRESS + f"{month}.jsonl", timeout=300, headers=S.UA)
        for ln in r.text.splitlines():
            if not any(u in ln for u in urls if "http" in u):
                continue
            item = json.loads(ln)
            u = item.get("url")
            if u in urls:
                press[u] = {"title": item.get("title") or "", "text": item.get("text") or "", "date": item.get("date")}
    except Exception as e:
        print(f"Congress Press {month}: {type(e).__name__}")
print(f"full text found for {len(press)} of {len(urls)} alerted post links")

feed = r2("posts/feed.json") or {"items": []}
watch = r2("posts/watch.json") or {"posts": []}
text_of = {x.get("url"): x.get("text") or "" for x in watch.get("posts", []) + feed.get("items", []) if x.get("url")}

tg = S.Tagger({"DATA_DIR": "data", "SOCIAL_DIR": "social"})
retag = {}
for u in urls:
    p = press.get(u)
    full = (p["title"] + "\n" + p["text"]) if p else text_of.get(u, "")
    if not full:
        continue
    try:
        tk, pp, secs, tone = tg.tag(full)
        retag[u] = {"tickers": sorted(tk), "people": [list(x) for x in pp][:12], "industries": secs, "tone": tone,
                    "chars": len(full), "src": "press" if p else "stored", "text": full[:2500]}
    except Exception as e:
        retag[u] = {"error": f"{type(e).__name__}: {e}"}
print(f"retagged {len(retag)}")

d = r2("dashboard_data.json") or {}


def grab(x, path, out):
    if isinstance(x, dict):
        for k, v in x.items():
            if "post" in str(k).lower():
                out[path + "." + str(k)] = json.dumps(v, default=str)[:800]
            else:
                grab(v, path + "." + str(k), out)
    elif isinstance(x, list):
        for i, v in enumerate(x[:200]):
            grab(v, f"{path}[{i}]", out)


sig = {}
for k in ("rare_test", "filter_test", "adaptive"):
    grab(d.get(k), k, sig)

slim = lambda x: {k: x.get(k) for k in ("id", "d", "b", "who", "url", "t", "why", "lvl", "src", "ty", "secs", "tg")} | {
    "text": (x.get("text") or "")[:300]}
out = {"made": dt.datetime.utcnow().isoformat(timespec="seconds"), "shown_ids": shown, "cleared": cleared,
       "press": {u: {"title": p["title"], "date": p["date"]} for u, p in press.items()}, "retag": retag,
       "feed": [slim(x) for x in feed.get("items", [])], "watch": [slim(x) for x in watch.get("posts", [])],
       "post_signals": sig}
json.dump(out, open("out/review2.json", "w"), default=str)
print(f"wrote out/review2.json; post-signal entries {len(sig)}")
