"""Hourly: members' newest posts, read and sorted; the significant ones go to your phone; the app's post feed.
Usage: python posts_live.py [MINUTES]

Reads the saved posts (read-only, from the posts cache), collects the newest from every free source (Congress Press
releases, Bluesky, Mastodon, Substack; YouTube every third hour to stay within Google's free daily limit), keeps the
posts it hasn't seen before (state kept on Cloudflare, posts/live_state.json), sorts each one (post_sort.py), scores
why it matters, sends one phone alert for the notable ones and updates posts/feed.json for the app.
Nothing is saved to the GitHub cache: the twice-daily posts job stores everything for good."""
import os, re, sys, json, time, datetime as dt
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
import threading
new_rows = []
_tag, _cur = {}, {}        # which source each captured post came from (two collectors run at once)
_orig_write = S.write_jsonl


def _capture(path, rows):
    try:
        old = {r[4] for r in S.read_jsonl(path)} if os.path.exists(path) else set()
        src = os.path.basename(os.path.dirname(path))
        for r in rows:
            if len(r) > 4 and r[4] and r[4] not in old and str(r[0]) >= recent_from:
                new_rows.append((src, r))
                _tag[id(r)] = _cur.get(threading.get_ident(), "?")
    except Exception as e:
        E.log(f"Live posts: couldn't compare {os.path.basename(path)} ({type(e).__name__})")
    _orig_write(path, rows)


S.write_jsonl = _capture
st = S._state(cfg)
try:      # the owner's verdicts on possible company people, saved by the app; the tagger reads them
    rv = cloud_store.get("inputs/people_reviews.json", E.log)
    if rv:
        open(S._dir(cfg, "people_reviews.json"), "wb").write(rv if isinstance(rv, bytes) else rv.encode())
except Exception as e:
    E.log(f"Live posts: people reviews not loaded ({type(e).__name__})")
tg = S.Tagger(cfg)
leg = S.legislators(cfg, st)
acc = S.discover_accounts(cfg, leg, st, time.time() + 30)
hmap = S.bluesky_accounts(cfg, leg, acc, st, time.time() + 30)
counts = {}
import congress_signals.press_backfill as PB
from concurrent.futures import ThreadPoolExecutor
# Oct 19: Bluesky, Mastodon, Substack and YouTube were skipped as "out of time" every hour. Members' own sites
# (~2 min) now run on their own thread next to the others.
tg_sites = S.Tagger(cfg)          # its own copy: the tagger keeps per-post state
SITES = ("Members' own sites", lambda: PB.latest_current(cfg, tg_sites, leg, time.time() + 300))
# The Congress press-release index walks whole months and found nothing new hourly (members' own sites cover it), so it
# goes last with a 3-minute cap; it was the step using up the hour before.
# each source gets its own time cap so one slow source can't starve the rest (Bluesky alone took 12 minutes when
# it first caught up)
cap = lambda m: min(deadline, time.time() + m * 60)
REST = (("Bluesky", lambda: S.collect_bluesky(cfg, st, tg, hmap, cap(5), ocr_days=3, max_ocr=40)),
        ("Mastodon", lambda: S.collect_mastodon(cfg, st, tg, leg, acc, cap(2))),
        ("Substack", lambda: S.collect_substack(cfg, st, tg, acc, cap(1.5))),
        ("YouTube", lambda: S.collect_youtube(cfg, st, tg, leg, acc, cap(1.5)) if dt.datetime.utcnow().hour % 3 == 0 else None),
        ("Instagram", lambda: S.collect_instagram(cfg, st, tg, leg, acc, cap(2), per_run=40, rotate=int(time.time() // 3600))))
# The Congress press-release index is left to the twice-daily posts job: hourly it ignored its time cap (21 minutes
# on one month's index) and never found anything the members' own sites hadn't already.
def run_one(name, fn):
    if time.time() > deadline and name != SITES[0]:
        E.log(f"Live posts: {name} skipped (out of time)")
        return
    n0 = len([1 for _, r in new_rows if _tag.get(id(r)) == name])
    _cur[threading.get_ident()] = name
    try:
        fn()
    except Exception as e:
        E.log(f"Live posts: {name} failed ({type(e).__name__}: {e})")
    counts[name] = sum(1 for _, r in new_rows if _tag.get(id(r)) == name) - n0


with ThreadPoolExecutor(2) as ex:
    f_sites = ex.submit(run_one, *SITES)
    f_rest = ex.submit(lambda: [run_one(n, f) for n, f in REST])
    f_rest.result()
    f_sites.result()
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
traded_by, member_sectors, traders, sc_all = {}, {}, set(), None
try:
    sc = pd.read_pickle(os.path.join("data", "cache", "scored.pkl"))
    sc_all = sc
    traders = {b for b in sc["bioguide"] if isinstance(b, str)}       # members with any disclosed trade, ever
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
    items.append({"id": cloud_store.post_id(r[4]), "sv": PS.SORT_VERSION, "d": str(r[0])[:10], "b": r[1], "who": names.get(r[1], r[1]), "src": r[2], "text": (r[3] or "")[:300],
                  "url": r[4], "t": r[5] or [], "ty": srt.get("ty") or [], "ins": srt.get("ins") or "",
                  "meet": srt.get("meet") or [], "tg": [[x.get("n") if x["k"] in ("person", "lobbyist", "agency", "industry") else x.get("t"), x["k"], x.get("s")]
                         for x in srt.get("tg", [])][:8],
                  "lvl": lvl, "why": why, "at": dt.datetime.utcnow().isoformat(timespec="seconds")})

# owner, Oct 19: posts from members who have never disclosed a trade aren't shown (they still count for "3+ members
# on a company", and their strong posts are watched below in case other members then trade that company);
# a member's posts show from their first disclosed trade on
hidden = [x for x in items if traders and x["b"] not in traders]
items = [x for x in items if not traders or x["b"] in traders]

# watched posts: strong posts by anyone (a company or its people named, an insider-style hint, a meeting). When another member trades that company (or, after an insider-style hint, that industry)
# within 30 days after the post, it's a "post, then trade" alert.
raw_w = cloud_store.get("posts/watch.json", E.log)
watch = json.loads(raw_w) if raw_w else {"posts": [], "hit": []}
def strong(x):
    tgs = x.get("tg") or []
    named = [g[0] for g in tgs if g[1] in ("co", "person", "lobbyist")]
    return bool(named or x.get("ins") or x.get("meet"))
for x in items + hidden:
    if strong(x):
        watch["posts"].append({k: x.get(k) for k in ("id", "d", "b", "who", "url", "t", "ins", "meet", "ty", "text")} |
                              {"secs": [g[0] for g in x.get("tg") or [] if g[1] == "industry"]})
cut60 = (today - dt.timedelta(days=60)).isoformat()
watch["posts"] = [w for w in {w["id"]: w for w in watch["posts"]}.values() if w.get("d", "") >= cut60][-3000:]
seen_hits = set(watch.get("hit") or [])
ptt = []
if sc_all is not None and len(sc_all):
    st_ = sc_all[pd.to_datetime(sc_all["trade_date"], errors="coerce") >= pd.Timestamp(cut60)]
    tr_rows = list(zip(st_["bioguide"], st_["member"], st_["ticker"], st_.get("sector", pd.Series(index=st_.index, dtype=object)),
                       pd.to_datetime(st_["trade_date"], errors="coerce"), st_["tx_type"]))
    for w in watch["posts"]:
        d0 = pd.Timestamp(w["d"])
        for b, m, t, sec, td, ty in tr_rows:
            if not isinstance(b, str) or b == w["b"] or pd.isna(td) or not (d0 <= td <= d0 + pd.Timedelta(days=30)):
                continue
            by_co = t in (w.get("t") or [])
            by_sec = bool(w.get("ins")) and isinstance(sec, str) and sec in (w.get("secs") or [])
            if not (by_co or by_sec):
                continue
            key = f"{w['id']}|{b}|{t}|{td:%Y-%m-%d}"
            if key in seen_hits:
                continue
            seen_hits.add(key)
            days = (td - d0).days
            ptt.append({"w": w, "line": f"{str(m).split(',')[0]} {'bought' if ty == 'buy' else 'sold'} {t} {days} day(s) after "
                                         f"{w['who']}'s post" + ("" if by_co else f" hinting at {sec.lower()}")})
watch["hit"] = sorted(seen_hits)[-5000:]
cloud_store.put("posts/watch.json", json.dumps(watch, default=str), E.log)
for h in ptt:          # the post goes in the feed (even a non-trader's), with what happened after it
    w = h["w"]
    items.append({"id": w["id"], "sv": PS.SORT_VERSION, "d": w["d"], "b": w["b"], "who": w["who"], "src": "Post, then trade",
                  "text": w.get("text") or "", "url": w["url"], "t": w.get("t") or [], "ty": w.get("ty") or [], "ins": w.get("ins") or "",
                  "meet": w.get("meet") or [], "tg": [], "lvl": 3, "why": [h["line"]], "at": dt.datetime.utcnow().isoformat(timespec="seconds")})
if ptt:
    E.log(f"Live posts: {len(ptt)} trade(s) by other members after a watched post")
E.log(f"Live posts: {len(hidden)} new post(s) from members with no disclosed trades kept out of the feed; "
      f"{len(watch['posts'])} strong posts watched for trades")

# "Needs review": a company person named by first or last name alone, too unsure to count by itself. The owner says
# "it's them" (counts from then on) or "not them" (never again) in the app.
try:
    revq = json.loads(cloud_store.get("posts/review.json", E.log) or b"[]")
    verd = json.loads(cloud_store.get("inputs/people_reviews.json", E.log) or b"{}")
    have = {(q["key"], q["id"]) for q in revq}
    add = 0
    for src, r in fresh:
        for c in (r[11] or {}).get("cand") or []:
            key = f"{str(c['w']).lower()}|{str(c['t']).lower()}"
            if key in verd or (key, cloud_store.post_id(r[4])) in have:
                continue
            revq.append({"key": key, "id": cloud_store.post_id(r[4]), "w": c["w"], "n": c["n"], "t": c["t"], "role": c.get("role"),
                         "by": c.get("by"), "c": c.get("c"), "who": names.get(r[1], r[1]), "b": r[1], "d": str(r[0])[:10],
                         "url": r[4], "text": (r[3] or "")[:600]})
            add += 1
    revq = [q for q in revq if q["key"] not in verd][-500:]
    if add or not revq:
        cloud_store.put("posts/review.json", json.dumps(revq, default=str), E.log)
    E.log(f"Live posts: {add} possible company people added for review ({len(revq)} waiting)")
except Exception as e:
    E.log(f"Live posts: review list skipped ({type(e).__name__}: {e})")

# the app's feed: notable posts from the last 7 days plus the newest others
raw = cloud_store.get("posts/feed.json", E.log)
feed = json.loads(raw) if raw else {"items": []}
old_items = [x for x in feed.get("items", []) if x.get("d", "") >= (today - dt.timedelta(days=7)).isoformat()]
for x in old_items:      # sorted before Oct 18: "met with" / "strongly for" could come from a company named in passing
    if (x.get("sv") or 0) < 3 and x.get("meet") and not any(" " in str(m) for m in x["meet"]):
        x["why"] = [w for w in (x.get("why") or []) if not w.startswith(("met with or visited", "strongly "))]
        x["meet"] = []
        x["lvl"] = min(int(x.get("lvl") or 0), 1)
        x["sv"] = 3
for x in old_items:      # sorted before Oct 17: a member's own name could count as a company person they "met"
    last = str(x.get("who") or "").split(",")[0].split(" ")[-1].lower()
    if last and any(last == str(m).split(" ")[-1].lower() for m in (x.get("meet") or [])):
        x["meet"] = [m for m in x["meet"] if str(m).split(" ")[-1].lower() != last]
        x["why"] = [w for w in (x.get("why") or []) if "met with or visited" not in w]
        x["lvl"] = min(int(x.get("lvl") or 0), 1)
old_items = [x for x in old_items if not traders or x.get("b") in traders or x.get("src") == "Post, then trade"]
for x in old_items:      # ranked before Oct 19: industry tone and passing mentions no longer count
    if (x.get("sv") or 0) < 4:
        x["why"] = [w for w in (x.get("why") or []) if not re.match(r"strongly (for|against) (Real Estate|Technology|Energy|Healthcare|"
                    r"Financial Services|Communication Services|Consumer Cyclical|Consumer Defensive|Industrials|Basic Materials|Utilities)", w)]
        x["lvl"] = 0 if not x["why"] else min(int(x.get("lvl") or 0), 2)
        x["sv"] = 4
for x in old_items:      # tone read before Oct 22 missed "we need oversight over <name>"-style wording
    if (x.get("tv") or 0) < 1:
        sents = PS._sentences(x.get("text") or "")
        for g in x.get("tg") or []:
            if len(g) >= 3 and g[1] in ("person", "lobbyist", "co") and g[0]:
                w = str(g[0]).split()[-1] if g[1] != "co" else str(g[0])
                g[2] = PS.cue_tone([s for s in sents if w.lower() in s.lower()], g[2])
        x["tv"] = 1
for x in items:
    x["tv"] = 1
allx = items + old_items
keep = [x for x in allx if x.get("lvl", 0) >= 1][:600] + [x for x in allx if x.get("lvl", 0) < 1][:200]
keep.sort(key=lambda x: (x.get("d", ""), x.get("at", "")), reverse=True)
feed = {"updated": dt.datetime.utcnow().isoformat(timespec="seconds"), "sorter": PS.model_name(), "items": keep}
cloud_store.put("posts/feed.json", json.dumps(feed, default=str), E.log)

# one phone alert for this hour's notable posts (none on the very first run, which only learns what exists)
top = sorted([x for x in items if x["lvl"] >= 3], key=lambda x: -x["lvl"])     # push: the four alert kinds only
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
LIVE_MAX_DAYS = {"Members' own sites": 2, "Bluesky": 2, "Mastodon": 14, "Substack": 30, "YouTube": 7}
problems = []
for k in counts:
    mx = LIVE_MAX_DAYS.get(k, 7)
    if k == "Instagram" and not (os.environ.get("META_ACCESS_TOKEN") or "").strip():
        # not a quiet source: it can't run until the owner adds the token, so say exactly that (still reported)
        problems.append("Instagram: not set up yet; waiting for the owner to add the META_ACCESS_TOKEN GitHub secret")
        continue
    if k in last_new:
        days = (now - dt.datetime.fromisoformat(last_new[k])).total_seconds() / 86400
        if days > mx:
            problems.append(f"{k}: nothing new for {days:.0f} days (normally new within {mx})")
    elif runs > mx * 24:
        problems.append(f"{k}: nothing new since these hourly checks started")
if (os.environ.get("META_ACCESS_TOKEN") or "").strip():
    # owner, Oct 28: almost every member has an Instagram; list each one the app can't read, for a manual check
    try:
        problems += S.ig_unreadable(leg)
    except Exception as e:
        E.log(f"Live posts: Instagram check list not built ({type(e).__name__}: {e})")
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
from congress_signals import health
health.finish("live", problems, fails, E.log)
E.log(f"Live posts: done in {(time.time() - t0) / 60:.1f} min; {len(items)} new, {len(top)} notable; sorter {PS.model_name()}")
