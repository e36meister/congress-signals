"""Read-only (owner, Nov 8): everything behind today's Buy candidates and every "Post" alert in the Alerts tab, for
a review page in chat. Writes out/review.json: the Buy rows as the app shows them, each Post alert with the posts it
listed (full feed record: tags, tone, why), the poster's trades of the tagged stocks, where each tagged stock sits
in the app's scoring, company/sector info, recent headlines (Alpaca) and the price move since the post vs SPY.
Nothing about balances, positions or orders is read or written."""
import sys, os, json, glob, datetime as dt
sys.path.insert(0, ".")
import requests
import cloud_store

os.makedirs("out", exist_ok=True)


def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


d = r2("dashboard_data.json") or {}
w = d.get("watchlist") or {}
buys_all = w.get("buys") or []
buys = [r for r in buys_all if r.get("action") == "BUY"]
print(f"Buy candidates: {len(buys)} (of {len(buys_all)} buy-side rows); updated {w.get('updated')}")

log = r2("push/log.json") or []
post_alerts = [a for a in log if a.get("kind") == "posts"]
print(f"alert log: {len(log)} entries, {len(post_alerts)} Post alerts")

feed = r2("posts/feed.json") or {"items": []}
watch = r2("posts/watch.json") or {"posts": []}
by_id = {x.get("id"): x for x in feed.get("items", []) if x.get("id")}
w_by_id = {x.get("id"): x for x in watch.get("posts", []) if x.get("id")}
by_url = {x.get("url"): x for x in feed.get("items", []) if x.get("url")}
w_by_url = {x.get("url"): x for x in watch.get("posts", []) if x.get("url")}
print(f"feed items {len(by_id)}, watched posts {len(w_by_id)}")

# the stored record for posts that have aged out of the feed (social cache, restored read-only)
need_urls = set()
for a in post_alerts:
    for it in a.get("items") or []:
        pid = str(it.get("u") or "").split("#post/")[-1] if "#post/" in str(it.get("u") or "") else None
        if pid and pid not in by_id and pid not in w_by_id and it.get("x"):
            need_urls.add(it["x"])
stored = {}
if need_urls:
    for f in glob.glob("social/**/*.jsonl", recursive=True):
        try:
            for ln in open(f):
                if '"http' not in ln:
                    continue
                for u in list(need_urls):
                    if u in ln:
                        r = json.loads(ln)
                        stored[u] = {"d": str(r[0])[:10], "b": r[1], "text": (r[3] or "")[:3000], "url": r[4],
                                     "tickers": r[5], "people": r[6], "sort": r[11] if len(r) > 11 else None}
                        need_urls.discard(u)
                        break
        except Exception:
            pass
print(f"posts found only in the social store: {len(stored)}; still missing: {len(need_urls)}")

alerts_out, tickers, posters = [], set(), set()
for a in post_alerts:
    items = []
    for it in a.get("items") or []:
        u = str(it.get("u") or "")
        pid = u.split("#post/")[-1] if "#post/" in u else None
        rec = by_id.get(pid) or w_by_id.get(pid) or stored.get(it.get("x")) if pid else None
        tk = None
        if u.startswith("/#t/"):
            tk = u.split("/#t/")[-1]
        xs_recs = [w_by_url.get(z.get("x")) or by_url.get(z.get("x")) for z in (it.get("xs") or [])]
        for xr in xs_recs:
            for t in (xr or {}).get("t") or []:
                if isinstance(t, str):
                    tickers.add(t)
            if xr and xr.get("b"):
                posters.add(xr["b"])
        items.append({"xs_recs": xs_recs, "l": it.get("l"), "u": u, "x": it.get("x"), "xs": it.get("xs"), "pid": pid, "tk": tk,
                      "rec": rec, "rec_src": ("feed" if pid in by_id else "watch" if pid in w_by_id else
                                              "social" if it.get("x") in stored else None)})
        for t in ((rec or {}).get("t") or (rec or {}).get("tickers") or []) + ([tk] if tk else []):
            if isinstance(t, str):
                tickers.add(t)
        if rec and rec.get("b"):
            posters.add(rec["b"])
    alerts_out.append({"id": a.get("id"), "at": a.get("at"), "title": a.get("title"), "body": a.get("body"),
                       "url": a.get("url"), "items": items})

buy_tk = {r.get("t") for r in buys if r.get("t")}
tickers |= buy_tk
tickers.discard(None)

# where each stock sits in the app's scoring today: every row of it on the buy and sell side
score_rows = {t: [] for t in tickers}
for side in ("buys", "sells"):
    for r in w.get(side) or []:
        if r.get("t") in score_rows:
            score_rows[r["t"]].append({"side": side, **{k: r.get(k) for k in ("action", "s", "pct", "m", "d", "td", "why")}})

# the poster's own trades of the tagged stocks (members' pages), since June
idx = r2("members/index.json") or {}
mem = idx.get("members") or idx.get("list") or idx
keymap = {}
if isinstance(mem, list):
    for m in mem:
        if isinstance(m, dict):
            keymap[m.get("b")] = m.get("k") or m.get("b")
poster_trades = {}
for b in posters:
    doc = r2(f"members/{keymap.get(b, b)}.json") or {}
    cols = doc.get("tcols") or []
    rows = []
    for row in doc.get("trades") or []:
        tr = dict(zip(cols, row)) if isinstance(row, list) else row
        if tr.get("t") in tickers and str(tr.get("td", "")) >= "2026-06":
            rows.append(tr)
    poster_trades[b] = {"n": doc.get("n"), "trades": rows}

meta = {}
try:
    m_all = json.load(open("data/cache/ticker_meta.json"))
    meta = {t: {k: (m_all.get(t) or {}).get(k) for k in ("name", "sector", "industry", "cap", "type")} for t in tickers}
except Exception as e:
    print("ticker meta not available:", type(e).__name__)

H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
news, closes = {}, {}
start_news = (dt.datetime.utcnow() - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
for t in sorted(tickers):
    try:
        r = requests.get("https://data.alpaca.markets/v1beta1/news", headers=H, timeout=30,
                         params={"symbols": t.replace("-", "."), "start": start_news, "limit": 12, "sort": "desc"})
        news[t] = [{"h": n.get("headline"), "s": n.get("summary", "")[:300], "src": n.get("source"), "u": n.get("url"),
                    "at": n.get("created_at"), "n_sym": len(n.get("symbols") or [])} for n in (r.json().get("news") or [])]
    except Exception as e:
        news[t] = {"error": type(e).__name__}
dates = [a.get("at", "")[:10] for a in post_alerts if a.get("at")]
start = (min(dates) if dates else "2026-10-01")
start = (dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat()
for t in sorted(tickers | {"SPY"}):
    try:
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{t.replace('-', '.')}/bars", headers=H, timeout=30,
                         params={"timeframe": "1Day", "start": start, "feed": "iex", "limit": 1000, "adjustment": "all"})
        closes[t] = {b["t"][:10]: float(b["c"]) for b in r.json().get("bars") or []}
    except Exception as e:
        closes[t] = {}

out = {"made": dt.datetime.utcnow().isoformat(timespec="seconds"),
       "watchlist": {"updated": w.get("updated"), "lookback": w.get("lookback"), "new": w.get("new"),
                     "n_watch_rows": len(buys_all) - len(buys)},
       "buys": buys, "post_alerts": alerts_out, "score_rows": score_rows, "poster_trades": poster_trades,
       "meta": meta, "news": news, "closes": closes, "missing_posts": sorted(need_urls),
       "dashboard_keys": sorted(d.keys())}
json.dump(out, open("out/review.json", "w"), default=str)
print(f"wrote out/review.json: {len(buys)} buys, {len(alerts_out)} post alerts, {len(tickers)} stocks")
