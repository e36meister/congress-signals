"""Read-only (owner, Nov 8): re-runs the Post alerts from the Alerts tab through the Nov 8 tagging fixes (public
officials, agency "lobbyists", direct-name rule for "met with", crowd counts and hints) and writes, per post, what
was tagged then and now and whether it would still push. Writes out/tag_fix_check.json; logs counts only."""
import sys, os, json, datetime as dt
sys.path.insert(0, ".")
import requests
import pandas as pd
import cloud_store
import congress_signals.social as S
import congress_signals.post_sort as PS

os.makedirs("out", exist_ok=True)
cfg = {"DATA_DIR": "data", "SOCIAL_DIR": "social"}


def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


log = r2("push/log.json") or []
cleared = r2("push/cleared.json") or {}
ids = set(cleared.get("ids") or [])
alerts = [a for a in log if a.get("kind") == "posts" and a.get("id") not in ids]
items = [(a, it) for a in alerts for it in a.get("items") or []]
feed = r2("posts/feed.json") or {"items": []}
watch = r2("posts/watch.json") or {"posts": []}
stored = {x.get("url"): x for x in watch.get("posts", []) + feed.get("items", []) if x.get("url")}
urls = {it.get("x") for _, it in items if it.get("x")}
press = {}
for month in ("2026-10", "2026-11"):
    try:
        r = requests.get(S.PRESS + f"{month}.jsonl", timeout=300, headers=S.UA)
        for ln in r.text.splitlines():
            if '"url"' in ln and any(u in ln for u in urls):
                x = json.loads(ln)
                if x.get("url") in urls:
                    press[x["url"]] = (x.get("title") or "") + "\n" + (x.get("text") or "")
    except Exception as e:
        print(f"Congress Press {month}: {type(e).__name__}")

# officials as the hourly job would start them: seeded from the stored feed and watch list
seed = {}
for x in feed.get("items", []) + watch.get("posts", []):
    for nm in S.official_names(x.get("text") or ""):
        seed.setdefault("|".join(nm), str(x.get("d") or "")[:10])
json.dump({"names": seed}, open(S._dir(cfg, "officials.json"), "w"))
tg = S.Tagger(cfg)
print(f"officials seeded: {len(seed)}")

traded_by = {}
try:
    sc = pd.read_pickle(os.path.join("data", "cache", "scored.pkl"))
    sc = sc[pd.to_datetime(sc["trade_date"], errors="coerce") >= pd.Timestamp.today() - pd.Timedelta(days=365)]
    for t, b in zip(sc["ticker"], sc["bioguide"]):
        if isinstance(b, str):
            traded_by.setdefault(t, set()).add(b)
except Exception as e:
    print(f"trades not available ({type(e).__name__})")

rows = []
for a, it in sorted(items, key=lambda z: z[0].get("at", "")):
    u = it.get("x") or ""
    rec = stored.get(u) or {}
    text = press.get(u) or rec.get("text") or ""
    if not text:
        rows.append({"u": u, "at": a.get("at"), "l": it.get("l"), "skip": "no text"})
        continue
    tk, pp, secs, tone = tg.tag(text)
    r = [rec.get("d") or a.get("at", "")[:10], rec.get("b") or "", "", text, u, tk,
         [f"{x}|{y}|{z}" for x, y, z in pp], tone, secs, "", tg.version]
    srt = PS.sort_record(r, tg.names)
    rows.append({"u": u, "at": a.get("at"), "d": r[0], "b": r[1], "l": it.get("l"), "then": rec.get("t"), "now": tk,
                 "people": [list(x) for x in pp][:10], "srt": srt, "direct": sorted(PS.direct_of(srt)), "r": r[:2]})

# crowd counts in date order, as the hourly job would build them (3-day window, direct names only)
crowd = {}
for x in sorted([x for x in rows if "srt" in x], key=lambda x: x["at"]):
    day = x["d"]
    for t in x["now"]:
        if t in x["direct"]:
            crowd.setdefault(t, []).append([day, x["b"], PS.text_sig(press.get(x["u"]) or (stored.get(x["u"]) or {}).get("text") or "")])
    frm = (dt.date.fromisoformat(day) - dt.timedelta(days=3)).isoformat()
    cn = {t: PS.crowd_count([e for e in v if frm <= e[0] <= day]) for t, v in crowd.items()}
    lvl, why = PS.significance([x["d"], x["b"]], x["srt"], traded_by, cn, {})
    x["lvl"], x["why"] = lvl, why
for x in rows:
    x.pop("r", None)
json.dump({"made": dt.datetime.utcnow().isoformat(timespec="seconds"), "officials_seeded": len(seed),
           "officials_learned": len(tg.officials_new), "rows": rows}, open("out/tag_fix_check.json", "w"), default=str)
n = len(rows)
push = sum(1 for x in rows if x.get("lvl", 0) >= 3)
btf = sum(1 for x in rows if "BTF" in (x.get("now") or []))
bce = sum(1 for x in rows if "BCE" in (x.get("now") or []))
print(f"{n} alerted posts re-run: {push} would still push; still tagged BTF {btf}, BCE {bce}; "
      f"officials learned while re-running {len(tg.officials_new)}")
