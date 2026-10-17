"""Hourly: members' newest posts, read and sorted; the significant ones go to your phone; the app's post feed.
Usage: python posts_live.py [MINUTES]

Reads the saved posts (read-only, from the posts cache), collects the newest from every free source (Congress Press
releases, Bluesky, Mastodon, Substack; YouTube every third hour to stay within Google's free daily limit), keeps the
posts it hasn't seen before (state kept on Cloudflare, posts/live_state.json), sorts each one (post_sort.py), scores
why it matters, sends one phone alert for the notable ones and updates posts/feed.json for the app.
Nothing is saved to the GitHub cache: the twice-daily posts job stores everything for good."""
import os, sys, json, time, datetime as dt
import pandas as pd
import congress_signals.engine as E
import congress_signals.social as S
from congress_signals import post_sort as PS, runlog
import cloud_store

minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10
t0 = time.time()
deadline = t0 + minutes * 60
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social",
       "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", ""), "TIME_BUDGET_MIN": 0}
today = dt.date.today()
recent_from = (today - dt.timedelta(days=3)).isoformat()

raw = cloud_store.get("posts/live_state.json", E.log)
state = json.loads(raw) if raw else {}
seen = state.get("seen") or {}
first = not seen

# every post a collector writes that wasn't in that file before, and is recent
new_rows = []
_orig_write = S.write_jsonl


def _capture(path, rows):
    try:
        old = {r[4] for r in S.read_jsonl(path)} if os.path.exists(path) else set()
        src = os.path.basename(os.path.dirname(path))
        for r in rows:
            if len(r) > 4 and r[4] and r[4] not in old and str(r[0]) >= recent_from:
                new_rows.append((src, r))
    except Exception as e:
        E.log(f"Live posts: couldn't compare {os.path.basename(path)} ({type(e).__name__})")
    _orig_write(path, rows)


S.write_jsonl = _capture
st = S._state(cfg)
tg = S.Tagger(cfg)
leg = S.legislators(cfg, st)
acc = S.discover_accounts(cfg, leg, st, time.time() + 30)
hmap = S.bluesky_accounts(cfg, leg, acc, st, time.time() + 30)
counts = {}
import congress_signals.press_backfill as PB
for name, fn in (("Members' own sites", lambda: PB.latest_current(cfg, tg, leg, time.time() + 300)),
                 ("Press releases", lambda: S.collect_press(cfg, st, tg, deadline, years=False)),
                 ("Bluesky", lambda: S.collect_bluesky(cfg, st, tg, hmap, deadline, ocr_days=3, max_ocr=40)),
                 ("Mastodon", lambda: S.collect_mastodon(cfg, st, tg, leg, acc, deadline)),
                 ("Substack", lambda: S.collect_substack(cfg, st, tg, acc, deadline)),
                 ("YouTube", lambda: S.collect_youtube(cfg, st, tg, leg, acc, deadline) if dt.datetime.utcnow().hour % 3 == 0 else None)):
    n0 = len(new_rows)
    if time.time() > deadline:
        E.log(f"Live posts: {name} skipped (out of time)")
        continue
    try:
        fn()
    except Exception as e:
        E.log(f"Live posts: {name} failed ({type(e).__name__}: {e})")
    counts[name] = len(new_rows) - n0
S.write_jsonl = _orig_write

# keep posts not already handled by an earlier hour
fresh = []
for src, r in new_rows:
    if r[4] in seen:
        continue
    seen[r[4]] = str(r[0])[:10]
    if len(r) < 12 or not isinstance(r[11], dict):
        r[:] = (r + [""] * 11)[:11] + [PS.sort_record(r, tg.names)]
    fresh.append((src, r))
E.log("Live posts: new this hour: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + f"; {len(fresh)} not seen before")

# who traded what in the last year (a company the member or a colleague trades)
traded_by, member_sectors = {}, {}
try:
    sc = pd.read_pickle(os.path.join("data", "cache", "scored.pkl"))
    sc = sc[pd.to_datetime(sc["trade_date"], errors="coerce") >= pd.Timestamp(today) - pd.Timedelta(days=365)]
    for t, b in zip(sc["ticker"], sc["bioguide"]):
        if isinstance(b, str):
            traded_by.setdefault(t, set()).add(b)
    s90 = sc[pd.to_datetime(sc["trade_date"], errors="coerce") >= pd.Timestamp(today) - pd.Timedelta(days=90)]
    for t, b, sec in zip(s90["ticker"], s90["bioguide"], s90.get("sector", pd.Series(index=s90.index, dtype=object))):
        if isinstance(b, str) and isinstance(sec, str):
            lst = member_sectors.setdefault(b, {}).setdefault(sec, [])
            if t not in lst:
                lst.append(t)
except Exception as e:
    E.log(f"Live posts: trades not available ({type(e).__name__})")

# several members naming the same company within 3 days
crowd = {t: [x for x in v if x[0] >= recent_from] for t, v in (state.get("crowd") or {}).items()}
for src, r in fresh:
    for t in r[5] or []:
        crowd.setdefault(t, []).append([str(r[0])[:10], r[1]])
crowd = {t: v for t, v in crowd.items() if v}
crowd_n = {t: len({k for _, k in v}) for t, v in crowd.items()}

names = {b: m.get("name") or b for b, m in leg.items()}
items = []
for src, r in fresh:
    srt = r[11]
    lvl, why = PS.significance(r, srt, traded_by, crowd_n, member_sectors)
    items.append({"id": cloud_store.post_id(r[4]), "d": str(r[0])[:10], "b": r[1], "who": names.get(r[1], r[1]), "src": r[2], "text": (r[3] or "")[:300],
                  "url": r[4], "t": r[5] or [], "ty": srt.get("ty") or [], "ins": srt.get("ins") or "",
                  "meet": srt.get("meet") or [], "tg": [[x.get("n") if x["k"] in ("person", "lobbyist", "agency", "industry") else x.get("t"), x["k"], x.get("s")]
                         for x in srt.get("tg", [])][:8],
                  "lvl": lvl, "why": why, "at": dt.datetime.utcnow().isoformat(timespec="seconds")})

# the app's feed: notable posts from the last 7 days plus the newest others
raw = cloud_store.get("posts/feed.json", E.log)
feed = json.loads(raw) if raw else {"items": []}
old_items = [x for x in feed.get("items", []) if x.get("d", "") >= (today - dt.timedelta(days=7)).isoformat()]
for x in old_items:      # sorted before Oct 17: a member's own name could count as a company person they "met"
    last = str(x.get("who") or "").split(",")[0].split(" ")[-1].lower()
    if last and any(last == str(m).split(" ")[-1].lower() for m in (x.get("meet") or [])):
        x["meet"] = [m for m in x["meet"] if str(m).split(" ")[-1].lower() != last]
        x["why"] = [w for w in (x.get("why") or []) if "met with or visited" not in w]
        x["lvl"] = min(int(x.get("lvl") or 0), 1)
allx = items + old_items
keep = [x for x in allx if x.get("lvl", 0) >= 1][:600] + [x for x in allx if x.get("lvl", 0) < 1][:200]
keep.sort(key=lambda x: (x.get("d", ""), x.get("at", "")), reverse=True)
feed = {"updated": dt.datetime.utcnow().isoformat(timespec="seconds"), "sorter": PS.model_name(), "items": keep}
cloud_store.put("posts/feed.json", json.dumps(feed, default=str), E.log)

# one phone alert for this hour's notable posts (none on the very first run, which only learns what exists)
top = sorted([x for x in items if x["lvl"] >= 2], key=lambda x: -x["lvl"])
if top and not first:
    lines = [f"{x['who']}: {x['why'][0]}" for x in top[:3]]
    title = (f"{len(top)} notable post(s)" if len(top) > 1 else f"Post: {top[0]['who']}")[:120]
    links = [{"l": f"{x['who']}: {x['why'][0]}", "u": f"/#post/{x['id']}", "x": x.get("url")} for x in top[:20]]
    # one post: tapping opens it in the app; several: the alert opens in the Alerts tab with each post listed
    cloud_store.notify(title, " · ".join(lines)[:600], "posts", links[0]["u"] if len(top) == 1 else "", E.log, items=links)
    E.log(f"Live posts: alert sent for {len(top)} notable post(s)")
elif first:
    E.log("Live posts: first run; learned what's already out there, no alert")

now = dt.datetime.utcnow()
last_new = state.get("last_new") or {}
for k, v in counts.items():
    if v > 0:
        last_new[k] = now.isoformat(timespec="seconds")
runs = int(state.get("runs") or 0) + 1
state = {"seen": {u: d for u, d in seen.items() if d >= (today - dt.timedelta(days=21)).isoformat()},
         "crowd": crowd, "last": now.isoformat(timespec="seconds"), "last_new": last_new, "runs": runs}
cloud_store.put("posts/live_state.json", json.dumps(state), E.log)
# collection check: a source is flagged only when it has brought nothing new for longer than it normally goes
LIVE_MAX_DAYS = {"Members' own sites": 2, "Press releases": 3, "Bluesky": 2, "Mastodon": 14, "Substack": 30, "YouTube": 7}
problems = []
for k in counts:
    mx = LIVE_MAX_DAYS.get(k, 7)
    if k in last_new:
        days = (now - dt.datetime.fromisoformat(last_new[k])).total_seconds() / 86400
        if days > mx:
            problems.append(f"{k}: nothing new for {days:.0f} days (normally new within {mx})")
    elif runs > mx * 24:
        problems.append(f"{k}: nothing new since these hourly checks started")
fails = sorted(set(runlog.LINES))
if problems or fails:
    E.log(f"COLLECTION CHECK (live): {len(problems)} source problem(s), {len(fails)} failure line(s)")
    for x in problems:
        E.log(f"   - {x}")
    for x in fails[:30]:
        E.log(f"   ! {x}")
else:
    E.log("COLLECTION CHECK (live): every source on time; no failures logged")
cloud_store.put("posts/live_check.json", json.dumps({"at": now.isoformat(timespec="seconds"), "problems": problems,
                                                     "failures": fails[:60]}), E.log)
E.log(f"Live posts: done in {(time.time() - t0) / 60:.1f} min; {len(items)} new, {len(top)} notable; sorter {PS.model_name()}")
