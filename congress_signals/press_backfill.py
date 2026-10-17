"""Fills the press-release gap from 2021 on (Congress Press only scraped members' sites since 2021, so many releases
are missing).

1. Current members: page back through each member's own press-release list (python-statement's 538 scrapers,
   the same ones Congress Press uses, but much deeper than its 50-page backfill) to BACKFILL_FROM, and read the
   text of every release Congress Press doesn't have.
2. Members who left Congress since 2021: their sites are gone, so the Internet Archive's saved copies are used
   (Wayback Machine CDX index -> saved release pages). Partial by nature.

Saved as social/posts/press_bf/<bioguide>.jsonl.gz and social/posts/press_wb/<bioguide>.jsonl.gz (same record
format as the other posts); build_index skips any URL already in Congress Press. Resumable; runs in social.yml."""
import os, re, json, time, gzip, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import requests

BACKFILL_FROM = "2021-01-01"
UA = {"User-Agent": "Mozilla/5.0 (compatible; CapitolCapital research; congress press releases)"}
WB = "https://web.archive.org"
RELEASE_PATH = re.compile(r"/(press|news|media|newsroom|press-releases?|news-releases?|newsletters?)[/-]", re.I)
SKIP_PATH = re.compile(r"(\?|/page/\d|/tag/|/category/|/feed|\.(pdf|jpg|png|gif|css|js)$|/print/|/rss)", re.I)


def norm_url(u):
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.rstrip("/")


def _log(*a):
    print(dt.datetime.now().strftime("%H:%M:%S"), *a, flush=True)
    try:
        from congress_signals import runlog
        runlog.capture(" ".join(map(str, a)))
    except Exception:
        pass


def _text(url, html=None, timeout=40):
    """(date, title, text) of one release page via trafilatura; Nones when unreadable."""
    try:
        import trafilatura
        if html is None:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code != 200:
                return None, None, None
            html = r.text
        d = trafilatura.bare_extraction(html, url=url, with_metadata=True)
        if d is None:
            return None, None, None
        d = d if isinstance(d, dict) else d.as_dict()
        return d.get("date"), d.get("title"), _clean(d.get("text"))
    except Exception:
        return None, None, None


BOILER = re.compile(r"Pursuant to Senate Policy.*?(?:unsubscribe may do so here\.?|$)|"
                    r"(?:Share|Print|Email|Tweet|Facebook|Twitter)\s*(?:\||\n)|Skip to (?:main )?content", re.I | re.S)


def _clean(t):
    if not t:
        return t
    t = BOILER.sub(" ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def known_press_urls(social_dir):
    """URLs already collected (Congress Press and earlier backfills)."""
    from congress_signals.social import read_jsonl
    seen = set()
    root = os.path.join(social_dir, "posts")
    for src in ("press", "press_bf", "press_wb"):
        d = os.path.join(root, src)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            for r in read_jsonl(os.path.join(d, fn)):
                seen.add(norm_url(r[4]))
    return seen


def _member_by_domain(leg):
    out = {}
    for b, m in leg.items():
        u = m.get("url") or ""
        h = re.sub(r"^https?://(www\.)?", "", u.lower()).split("/")[0]
        if h:
            out[h] = b
    return out


# ---------------------------------------------------------------------------
# 1. current members' own sites
# ---------------------------------------------------------------------------
def backfill_current(cfg, st, tg, leg, deadline, workers=8, max_pages=400):
    from congress_signals.social import rec, read_jsonl, write_jsonl, _dir, _save_state
    try:
        from python_statement import Scraper
        from python_statement.config import SCRAPER_CONFIG
    except Exception as e:
        _log(f"Press backfill: scraper library missing ({type(e).__name__}); skipped")
        return True
    bydom = _member_by_domain(leg)
    prog = st.setdefault("press_bf", {})        # scraper -> {"done": bool, "page": n, "b": bioguide}
    seen = known_press_urls(cfg.get("SOCIAL_DIR") or "social")
    todo = [n for n in sorted(SCRAPER_CONFIG) if not (prog.get(n) or {}).get("done")]
    _log(f"Press backfill: {len(todo)} of {len(SCRAPER_CONFIG)} member sites left; {len(seen):,} releases already known")

    def one(name):
        p = prog.get(name) or {}
        page, found, b = int(p.get("page", 0)) + 1, [], p.get("b")
        last_urls = set()
        while page <= max_pages and time.time() < deadline:
            try:
                res = Scraper.run_scraper(name, page) or []
            except Exception:
                break
            urls = {r.get("url") for r in res if r.get("url")}
            if not res or (urls and urls <= last_urls):
                page = max_pages + 1
                break
            last_urls = urls
            oldest = None
            for r in res:
                u = r.get("url")
                if not b and u:
                    b = bydom.get(re.sub(r"^https?://(www\.)?", "", u.lower()).split("/")[0])
                d = str(r.get("date") or "")[:10]
                if d:
                    oldest = d if oldest is None else min(oldest, d)
                if u and norm_url(u) not in seen and (not d or d >= BACKFILL_FROM):
                    found.append((d, u, r.get("title") or ""))
            page += 1
            if oldest and oldest < BACKFILL_FROM:
                page = max_pages + 1
                break
            if (SCRAPER_CONFIG.get(name) or {}).get("method") in ("react", "joyce"):
                page = max_pages + 1
                break
        done = page > max_pages
        rows = []
        for d, u, title in found:
            if time.time() > deadline:
                return name, b, False, int(p.get("page", 0)), []     # redo this site next run
            d2, t2, text = _text(u)
            d = d or (d2 or "")[:10]
            if not d or d < BACKFILL_FROM or not b:
                continue
            full = (t2 or title or "") + "\n" + (text or "")
            rows.append(rec(d, b, "Press release", f"{title or t2 or ''} — {text or ''}".strip(" —"), u, tg, full=full))
        return name, b, done, page - 1, rows

    added = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for name, b, done, page, rows in ex.map(one, todo):
            for r in rows:
                seen.add(norm_url(r[4]))
            if rows and b:
                path = _dir(cfg, "posts", "press_bf", f"{b}.jsonl.gz")
                old = read_jsonl(path) if os.path.exists(path) else []
                write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
                added += len(rows)
            prog[name] = {"done": bool(done), "page": page if done else 0, "b": b}
            _save_state(cfg, st)
    left = sum(1 for n in SCRAPER_CONFIG if not (prog.get(n) or {}).get("done"))
    _log(f"Press backfill: +{added:,} releases from members' sites; {left} sites left")
    return left == 0


# ---------------------------------------------------------------------------
# 2. members who left: Internet Archive copies of their sites
# ---------------------------------------------------------------------------
def departed_since(start="2021-01-03"):
    """{bioguide: [site hosts]} for members whose service ended on/after `start` (from congress-legislators)."""
    from congress_signals.social import _get, LEG_HIST
    out = {}
    for m in _get(LEG_HIST, timeout=120) or []:
        b = (m.get("id") or {}).get("bioguide")
        terms = [t for t in m.get("terms", []) if (t.get("end") or "") >= start]
        hosts = {re.sub(r"^https?://(www\.)?", "", (t.get("url") or "").lower()).split("/")[0] for t in terms if t.get("url")}
        if b and hosts:
            out[b] = sorted(h for h in hosts if h)
    return out


def _cdx(host, frm="2021", to="2025"):
    q = {"url": host, "matchType": "domain", "from": frm, "to": to, "filter": ["statuscode:200", "mimetype:text/html"],
         "collapse": "urlkey", "fl": "timestamp,original", "output": "json", "limit": "60000"}
    for a in range(3):
        try:
            r = requests.get(f"{WB}/cdx/search/cdx", params=q, headers=UA, timeout=180)
            if r.status_code == 200:
                rows = r.json()
                return rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows
            time.sleep(10 * (a + 1))
        except Exception:
            time.sleep(10 * (a + 1))
    return None


def backfill_departed(cfg, st, tg, deadline, per_run=40000, workers=4):
    from congress_signals.social import rec, read_jsonl, write_jsonl, _dir, _save_state
    prog = st.setdefault("press_wb", {})       # bioguide -> {"done": bool, "total": n}; queues in social/wb_queue
    mpath = _dir(cfg, "wb_members.json")
    if not os.path.exists(mpath):
        json.dump(departed_since(), open(mpath, "w"))
    members = json.load(open(mpath))
    qdir = _dir(cfg, "wb_queue")
    seen = known_press_urls(cfg.get("SOCIAL_DIR") or "social")
    fetched, added = 0, 0
    for b, hosts in sorted(members.items()):
        p = prog.get(b) or {}
        if p.get("done"):
            continue
        if time.time() > deadline or fetched >= per_run:
            break
        qpath = os.path.join(qdir, f"{b}.json")
        if not os.path.exists(qpath):
            urls = []
            for h in hosts:
                rows = _cdx(h)
                if rows is None:
                    urls = None
                    break
                for ts, orig in rows:
                    yrs = [int(y) for y in re.findall(r"(?<!\d)(20[0-2]\d)(?!\d)", orig)]
                    if yrs and max(yrs) < int(BACKFILL_FROM[:4]):
                        continue                  # dated before the gap in its own address
                    if RELEASE_PATH.search(orig) and not SKIP_PATH.search(orig) and len(orig.rstrip("/").split("/")) >= 5:
                        if norm_url(orig) not in seen:
                            urls.append([ts, orig])
            if urls is None:
                continue                      # archive didn't answer; try next run
            json.dump(urls, open(qpath, "w"))
            p = {"total": len(urls)}
            prog[b] = p
            _save_state(cfg, st)
            time.sleep(1)
        queue = json.load(open(qpath))
        rows = []

        def get(item):
            ts, orig = item
            for a in range(3):
                try:
                    r = requests.get(f"{WB}/web/{ts}id_/{orig}", headers=UA, timeout=60)
                    if r.status_code == 429:
                        time.sleep(30 * (a + 1))      # the archive asked us to slow down
                        continue
                    html = r.text if r.status_code == 200 else None
                    break
                except Exception:
                    html = None
                    time.sleep(5)
            time.sleep(0.5)                           # be gentle with the Internet Archive
            if not html:
                return None
            d, title, text = _text(orig, html=html)
            if not d or not text or len(text) < 200 or d < BACKFILL_FROM:
                return None
            return rec(d[:10], b, "Press release (archived)", f"{title or ''} — {text}".strip(" —"),
                       f"{WB}/web/{ts}/{orig}", tg, full=(title or "") + "\n" + text)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            while queue and time.time() < deadline and fetched < per_run:
                chunk = [queue.pop() for _ in range(min(len(queue), workers * 10))]
                fetched += len(chunk)
                for r_ in ex.map(get, chunk):
                    if r_ is not None:
                        rows.append(r_)
                        seen.add(norm_url(r_[4].split("/", 5)[-1] if "/web/" in r_[4] else r_[4]))
        if rows:
            path = _dir(cfg, "posts", "press_wb", f"{b}.jsonl.gz")
            old = read_jsonl(path) if os.path.exists(path) else []
            write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
            added += len(rows)
        json.dump(queue, open(qpath, "w"))
        if not queue:
            p["done"] = True
        prog[b] = p
        _save_state(cfg, st)
    left = sum(1 for b in members if not (prog.get(b) or {}).get("done"))
    queued = sum(len(json.load(open(os.path.join(qdir, f)))) for f in os.listdir(qdir) if f.endswith(".json"))
    _log(f"Archived press releases: {queued:,} saved pages still queued; "
         f"{sum(1 for b in members if os.path.exists(os.path.join(qdir, f'{b}.json')))} members' archives listed")
    _log(f"Archived press releases: +{added:,} from {fetched:,} saved pages; {left} of {len(members)} departed members left")
    return left == 0
