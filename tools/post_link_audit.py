"""Read-only audit (owner, Nov 4): every post the app would link to a recently disclosed trade under the current
post rules (sorter v6, same-sentence hits, tone agreement, no reposts for industry links, Worth a look gating), plus
the near misses (posts in the window that name the trade's company or industry but don't link), so each can be
checked against the real page. Bluesky accounts found by name search are checked against the member's name
(dry run: nothing is changed). Writes out/post_link_audit.json; prints a summary. Public data only."""
import sys, os, re, json, glob, time
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
import congress_signals.social as S
from congress_signals.post_sort import sort_record, SORT_VERSION, BOOST_TYPES, HIT_TYPES

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social"}
os.makedirs("out", exist_ok=True)
sc = pd.read_pickle("data/cache/scored.pkl")
days = cfg.get("WATCHLIST_LOOKBACK_DAYS", 30)
since = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
tr = sc[(sc["filed_date"] >= since) & sc["tx_type"].isin(["buy", "sell"])].copy().reset_index(drop=True)
print(f"recently disclosed trades (last {days} days): {len(tr)}")
want = {b for b in tr["bioguide"] if isinstance(b, str)}

# --- Bluesky accounts found by name search: does the profile name match the member they're filed under? ---
leg = json.load(open("social/legislators.json")) if os.path.exists("social/legislators.json") else {}
acc = json.load(open("social/accounts.json")) if os.path.exists("social/accounts.json") else {}
ba = json.load(open("social/bsky_accounts.json")) if os.path.exists("social/bsky_accounts.json") else {"map": {}}
site = {h.lower() for a in acc.values() for h in (a.get("bsky") or [])}
wrong_files, bsky_checked = set(), []
for h, b in (ba.get("map") or {}).items():
    if h in site or b not in leg:
        continue
    js = S._get(S.BSKY + "app.bsky.actor.getProfile", params={"actor": h})
    ok = bool(js) and S._last_match(js.get("displayName"), leg[b])
    bsky_checked.append({"h": h, "b": b, "member": leg[b].get("name"), "profile": (js or {}).get("displayName"), "ok": ok, "read": bool(js)})
    if js and not ok:
        wrong_files.add(re.sub(r"[^a-z0-9.-]", "_", h) + ".jsonl.gz")
    time.sleep(0.15)
bad = [x for x in bsky_checked if x["read"] and not x["ok"]]
print(f"Bluesky search-found accounts checked: {len(bsky_checked)}, filed under the wrong member: {len(bad)}")
for x in bad:
    print(f"   @{x['h']}: filed under {x['member']}, profile says {x['profile']}")

# --- re-sort the recent posts of these members with the current sorter ---
tg = None
try:
    tg = S.Tagger({"DATA_DIR": "data", "SOCIAL_DIR": "social"})
except Exception as e:
    print("tagger not loaded:", type(e).__name__)
names = getattr(tg, "names", None)
lo_all = (since - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
recent = {}
n_read = 0
for f in glob.glob("social/posts/**/*.jsonl.gz", recursive=True) + glob.glob("wb/posts/**/*.jsonl.gz", recursive=True):
    src_dir = f.split(os.sep)[-2]
    if os.path.basename(f) in wrong_files and src_dir == "bsky":
        continue
    for r in S.read_jsonl(f):
        if len(r) < 9 or not isinstance(r[1], str) or r[1] not in want or str(r[0])[:10] < lo_all:
            continue
        n_read += 1
        srt = sort_record(r, names)
        d, key, s, text, url, tk, pp, tone, secs, rp = (r + [""] * 10)[:10]
        ag = [x["n"] for x in srt.get("tg", []) if x["k"] == "agency"]
        recent.setdefault(key, []).append({
            "d": str(d)[:10], "u": url, "x": (text or "")[:400], "src": s, "t": list(tk or []), "s": list(secs or []), "ag": ag,
            "ty": srt.get("ty") or [], "ins": srt.get("ins") or "", "meet": srt.get("meet") or [],
            "ins_on": srt.get("ins_on") or [], "insw": srt.get("insw") or "",
            "hit": [x["n"] for x in srt.get("tg", []) if x["k"] == "industry" and x.get("hit")], "rp": bool(rp),
            "cen": [x["n"] for x in srt.get("tg", []) if x["k"] == "industry" and x.get("cen")],
            "lob": S._lobbyist_only(pp, text, names),
            "st": {(x.get("t") if x["k"] == "co" else x.get("n")): x.get("s") for x in srt.get("tg", []) if x.get("s") is not None},
            "old_ty": (r[11] or {}).get("ty") if len(r) > 11 and isinstance(r[11], dict) else None})
for k in recent:
    recent[k].sort(key=lambda x: x["d"])
print(f"posts re-sorted (sorter v{SORT_VERSION}): {n_read:,}")

idx = {"recent_posts": recent}
recs = tr.to_dict("records")
links = S.trade_post_links(recs, idx)

# flags other than posts (same as engine.worth_a_look)
col = lambda c: tr[c] if c in tr else pd.Series(0, index=tr.index)
other = ((col("f_spouse_insider") > 0) | (col("f_relative_tie") > 0) | (col("f_family_possible") > 0)
         | (col("f_spouse_employer") > 0) | (col("volume_ratio").fillna(0) >= E.VOL_FLAG)
         | (col("f_reg_action") > 0) | (col("f_witness_after") > 0) | (col("f_markup_after") > 0)
         | (col("f_major_8k_after") > 0) | (col("f_closed_briefing") > 0))

rows = []
for i, r in enumerate(recs):
    b = r.get("bioguide")
    posts = recent.get(b) if isinstance(b, str) else None
    if not posts:
        continue
    td = pd.Timestamp(r.get("trade_date") if pd.notna(r.get("trade_date")) else r.get("filed_date"))
    fd = pd.Timestamp(r.get("filed_date"))
    lo, lo30 = (td - pd.Timedelta(days=90)).strftime("%Y-%m-%d"), (td - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    hi = max(td, fd).strftime("%Y-%m-%d")
    t, sec, buy = r.get("ticker"), r.get("sector"), r.get("tx_type") == "buy"
    linked = {p["u"]: p for p in links.get(i, [])}
    for p in posts:
        if not (lo <= p["d"] <= hi):
            continue
        secs = set(p.get("s") or []) | {x for a in p.get("ag") or [] for x in S.AGENCY_SECTORS.get(a, [])}
        names_co = t in (p.get("t") or [])
        names_sec = bool(sec) and sec in secs
        if not (names_co or names_sec):
            continue
        L = linked.get(p["u"])
        why_not = None
        if not L:
            ty = set(p.get("ty") or [])
            tone = (p.get("st") or {}).get(sec)
            if names_co:
                why_not = "company named but not linked (outside window?)"
            elif p.get("rp"):
                why_not = "repost"
            elif sec not in (p.get("cen") or []):
                why_not = "industry isn't the post's subject (not in the headline or 2+ sentences)"
            elif p["d"] < lo30:
                why_not = "industry post older than 30 days before the trade"
            elif buy:
                why_not = "no boost type" if not (ty & BOOST_TYPES) else f"tone toward industry {tone}"
            else:
                why_not = "no hit wording in a sentence naming the industry" if sec not in (p.get("hit") or []) else f"tone toward industry {tone}"
        rows.append({"member": r.get("member"), "b": b, "t": t, "co": r.get("company"), "sector": sec,
                     "tx": r.get("tx_type"), "td": td.strftime("%Y-%m-%d"), "fd": fd.strftime("%Y-%m-%d"),
                     "other_flag": bool(other.iloc[i]), "u": p["u"], "src": p["src"], "d": p["d"], "x": p["x"],
                     "ty": p["ty"], "old_ty": p.get("old_ty"), "hit": p.get("hit"), "cen": p.get("cen"), "lob": p.get("lob"), "ins": p.get("ins"), "rp": p.get("rp"),
                     "tone": (p.get("st") or {}).get(t if names_co else sec), "names_co": names_co,
                     "link": (L or {}).get("why"), "lvl": (L or {}).get("lvl"), "kind": (L or {}).get("kind"), "insw": p.get("insw"), "why_not": why_not,
                     "in_worth": bool(L) and L.get("kind") != "ind" and ((L.get("lvl") or 0) >= 3 or bool(other.iloc[i]))})
json.dump({"bsky": bsky_checked, "rows": rows}, open("out/post_link_audit.json", "w"), default=str)
lk = [x for x in rows if x["link"]]
print(f"\nlinked post-trade pairs: {len(lk)} (level 3: {sum(1 for x in lk if x['lvl'] == 3)}, industry-only: {sum(1 for x in lk if x['lvl'] == 2)})")
print(f"stocks Worth a look would show for posts (level 3, or another flag): {len({x['t'] for x in lk if x['in_worth']})}")
print(f"near misses (in the window, name the company or industry, not linked): {len(rows) - len(lk)}")
from collections import Counter
print("near-miss reasons:", dict(Counter(x["why_not"] for x in rows if not x["link"]).most_common()))
