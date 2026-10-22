"""Fills the press-release gap from 2021 on (Congress Press only scraped members' sites since 2021, so many releases
are missing).

1. Current members: page back through each member's own press-release list (python-statement's 538 scrapers,
   the same ones Congress Press uses, but much deeper than its 50-page backfill) to BACKFILL_FROM, and read the
   text of every release Congress Press doesn't have.
2. Members who left Congress since 2021: their sites are gone, so the Internet Archive's saved copies are used
   (Wayback Machine CDX index -> saved release pages). Partial by nature.

Saved as social/posts/press_bf/<bioguide>.jsonl.gz and social/posts/press_wb/<bioguide>.jsonl.gz (same record
format as the other posts); build_index skips any URL already in Congress Press. Resumable; runs in social.yml."""
import threading
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


def latest_current(cfg, tg, leg, deadline, since=None, workers=12, only=None):
    """Newest page of every current member's press-release list (the same 538 site readers), so releases arrive
    within the hour instead of waiting for Congress Press. New ones go into posts/press_bf like the backfill."""
    from congress_signals.social import rec, read_jsonl, write_jsonl, _dir
    try:
        from python_statement import Scraper
        from python_statement.config import SCRAPER_CONFIG
    except Exception as e:
        _log(f"Latest press releases: scraper library missing ({type(e).__name__}); skipped")
        return 0
    since = since or (dt.date.today() - dt.timedelta(days=3)).isoformat()
    bydom = _member_by_domain(leg)
    seen = known_press_urls(cfg.get("SOCIAL_DIR") or "social")
    names = sorted(only or SCRAPER_CONFIG)
    fails = []

    from congress_signals import press_sites as PS

    def backup(name):
        """The backup reader (press_sites) for a site whose library reader is blind or failed."""
        cfg_url = (SCRAPER_CONFIG.get(name) or {}).get("url_base") or (SCRAPER_CONFIG.get(name) or {}).get("url") or ""
        if isinstance(cfg_url, (list, tuple)):
            cfg_url = cfg_url[0] if cfg_url else ""
        url = PS.BACKUP.get(name) or cfg_url
        if not url:
            return None
        try:
            r = PS.read_site(url)
        except Exception:
            return None
        if r.get("items"):
            return [{"url": x["url"], "date": x["date"], "title": x["title"], "text": x["text"]} for x in r["items"]]
        return [{"url": u} for u in r["links"]] or None

    def _is_house(name):
        c = SCRAPER_CONFIG.get(name) or {}
        u = PS.BACKUP.get(name) or c.get("url_base") or c.get("url") or ""
        u = u[0] if isinstance(u, (list, tuple)) and u else u
        return "house.gov" in str(u).lower()

    house_gate = threading.Semaphore(4)     # House sites sit behind one firewall that throttles bursts

    def fetch(name):
        res = None
        if name not in PS.BACKUP:
            try:
                res = Scraper.run_scraper(name, 1) or []
            except Exception:
                res = None
        if not res:
            res = backup(name)
        return res

    unchecked = []

    def one(name, slow=False):
        if not slow and time.time() > deadline:
            unchecked.append(name)
            return name, None, []
        if _is_house(name) and not slow:
            with house_gate:
                res = fetch(name)
        else:
            res = fetch(name)
        if res is None:
            return name, None, None
        b, out = None, []
        for r in res:
            u = r.get("url")
            if not u:
                continue
            b = b or bydom.get(re.sub(r"^https?://(www\.)?", "", u.lower()).split("/")[0])
            d = str(r.get("date") or "")[:10]
            if norm_url(u) in seen or (d and d < since):
                continue
            if r.get("text"):                       # backup reader's WordPress feed already has the text
                out.append((d, u, r.get("title") or "", r["text"]))
                continue
            d2, t2, text = _text(u)
            d = d or (d2 or "")[:10]
            if d and d >= since:
                out.append((d, u, r.get("title") or t2 or "", text))
        return name, b, out

    added = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, names))
    # Oct 22: 7 then 23 House sites failed every hour with 12 readers at once (timeouts from the House firewall).
    # Sites that failed get a second, slow pass one at a time before they count as failed.
    again = [n for n, b, out in results if out is None]
    if again:
        second = {}
        for n in again:
            if time.time() > deadline + 120:
                break                   # the rest stay counted as failed
            time.sleep(3)
            second[n] = one(n, slow=True)
        results = [second.get(n, (n, b, out)) if out is None else (n, b, out) for n, b, out in results]
        late = [n for n in again if n in second and second[n][2] is not None]
        if late:
            _log(f"Latest press releases: {len(late)} site(s) answered on a second, slower try")
    for name, b, out in results:
        if out is None:
            fails.append(name)
            continue
        if not b or not out:
            continue
        rows = [rec(d, b, "Press release", f"{title} — {text or ''}".strip(" —"), u, tg,
                    full=(title or "") + "\n" + (text or "")) for d, u, title, text in out]
        path = _dir(cfg, "posts", "press_bf", f"{b}.jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
        added += len(rows)
        for r in rows:
            seen.add(norm_url(r[4]))
    _log(f"Latest press releases: {len(names) - len(unchecked)} of {len(names)} member sites checked, +{added} new")
    if unchecked:
        _log(f"Latest press releases: {len(unchecked)} member site(s) skipped: the time limit ran out before them")
    if fails:
        _log(f"Latest press releases: {len(fails)} member site(s) failed with both the site reader and the backup: "
             + ", ".join(sorted(fails)))
    return added


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


# One pause for every worker: when the Internet Archive refuses connections or times out, hammering it with 4 workers
# (x 8 shards) keeps it refusing. Each failure in a row doubles the pause (10 s .. 5 min); an answer resets it.
_WB_LOCK = threading.Lock()
_WB = {"until": 0.0, "streak": 0}


def _wb_wait():
    while True:
        with _WB_LOCK:
            w = _WB["until"] - time.time()
        if w <= 0:
            return
        time.sleep(min(w, 30))


def _wb_trouble(base):
    with _WB_LOCK:
        _WB["streak"] += 1
        pause = min(300, base * 2 ** min(_WB["streak"] - 1, 5))
        _WB["until"] = max(_WB["until"], time.time() + pause)


def _wb_ok():
    with _WB_LOCK:
        _WB["streak"] = 0


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


def backfill_departed(cfg, st, tg, deadline, per_run=40000, workers=4, root=None, only=None, seed_from=None):
    """Archived press releases of members who left. root: where output, queues and progress go (default: the
    social folder, the old way). only: the members this call handles (one shard of the parallel archive job).
    seed_from: a social folder whose earlier output/queues/progress are copied in on the first run."""
    from congress_signals.social import rec, read_jsonl, write_jsonl, _dir, _save_state
    sdir = cfg.get("SOCIAL_DIR") or "social"
    if root is None:
        prog = st.setdefault("press_wb", {})       # bioguide -> {"done": bool, "total": n}
        out_dir = _dir(cfg, "posts", "press_wb")
        qdir = _dir(cfg, "wb_queue")
        mpath = _dir(cfg, "wb_members.json")
        save = lambda: _save_state(cfg, st)
    else:
        os.makedirs(os.path.join(root, "posts", "press_wb"), exist_ok=True)
        os.makedirs(os.path.join(root, "queue"), exist_ok=True)
        prog = st                                   # caller's progress dict, saved by `save` below
        out_dir = os.path.join(root, "posts", "press_wb")
        qdir = os.path.join(root, "queue")
        mpath = os.path.join(root, "members.json")
        save = lambda: json.dump(prog, open(os.path.join(root, "progress.json"), "w"))
    if not os.path.exists(mpath):
        old = os.path.join(seed_from, "wb_members.json") if seed_from else None
        json.dump(json.load(open(old)) if old and os.path.exists(old) else departed_since(), open(mpath, "w"))
    members = json.load(open(mpath))
    if only is not None:
        members = {b: h for b, h in members.items() if b in only}
    if seed_from:                                   # first parallel run: carry over what the old single job did
        try:
            ost = json.load(open(os.path.join(seed_from, "state.json"))).get("press_wb") or {}
        except Exception:
            ost = {}
        for b in members:
            if b not in prog and b in ost:
                prog[b] = ost[b]
            q_old, q_new = os.path.join(seed_from, "wb_queue", f"{b}.json"), os.path.join(qdir, f"{b}.json")
            if os.path.exists(q_old) and not os.path.exists(q_new):
                open(q_new, "w").write(open(q_old).read())
            p_old, p_new = os.path.join(seed_from, "posts", "press_wb", f"{b}.jsonl.gz"), os.path.join(out_dir, f"{b}.jsonl.gz")
            if os.path.exists(p_old) and not os.path.exists(p_new):
                open(p_new, "wb").write(open(p_old, "rb").read())
    seen = known_press_urls(sdir)
    fetched, added, cdx_fail, requeued = 0, 0, [], 0
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
                cdx_fail.append(b)
                continue                      # archive didn't answer; try next run
            json.dump(urls, open(qpath, "w"))
            p = {"total": len(urls)}
            prog[b] = p
            save()
            time.sleep(1)
        queue = json.load(open(qpath))
        rows = []

        def get(item):
            ts, orig = item[0], item[1]
            html, transient = None, False
            for a in range(3):
                _wb_wait()
                try:
                    r = requests.get(f"{WB}/web/{ts}id_/{orig}", headers=UA, timeout=60)
                    if r.status_code == 429 or r.status_code >= 500:
                        transient = True
                        _wb_trouble(30 * (a + 1) if r.status_code == 429 else 10 * (a + 1))   # asked to slow down / busy
                        continue
                    html = r.text if r.status_code == 200 else None
                    transient = False
                    _wb_ok()
                    break
                except Exception:
                    html, transient = None, True
                    _wb_trouble(10 * (a + 1))         # refused or timed out: everyone backs off together
            time.sleep(0.5)                           # be gentle with the Internet Archive
            if not html:
                # Oct 22: pages the archive didn't answer (connection refused, timeouts, 5xx) were dropped for good;
                # about 3 in 4 pages were lost that way. They go back in the queue (up to 6 tries over later runs).
                if transient and (len(item) < 3 or item[2] < 5):
                    retry.append([ts, orig, (item[2] if len(item) > 2 else 0) + 1])
                return None
            d, title, text = _text(orig, html=html)
            if not d or not text or len(text) < 200 or d < BACKFILL_FROM:
                return None
            return rec(d[:10], b, "Press release (archived)", f"{title or ''} — {text}".strip(" —"),
                       f"{WB}/web/{ts}/{orig}", tg, full=(title or "") + "\n" + text)
        retry = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            while queue and time.time() < deadline and fetched < per_run:
                chunk = [queue.pop() for _ in range(min(len(queue), workers * 10))]
                fetched += len(chunk)
                for r_ in ex.map(get, chunk):
                    if r_ is not None:
                        rows.append(r_)
                        seen.add(norm_url(r_[4].split("/", 5)[-1] if "/web/" in r_[4] else r_[4]))
        queue[:0] = retry                     # retried last (the queue is read from the end)
        requeued += len(retry)
        if rows:
            path = os.path.join(out_dir, f"{b}.jsonl.gz")
            old = read_jsonl(path) if os.path.exists(path) else []
            write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
            added += len(rows)
        json.dump(queue, open(qpath, "w"))
        if not queue:
            p["done"] = True
        prog[b] = p
        save()
    left = sum(1 for b in members if not (prog.get(b) or {}).get("done"))
    queued = sum(len(json.load(open(os.path.join(qdir, f"{b}.json")))) for b in members
                 if os.path.exists(os.path.join(qdir, f"{b}.json")))
    _log(f"Archived press releases: {queued:,} saved pages still queued for {len(members)} members")
    if cdx_fail:
        _log(f"Archived press releases: the archive's index didn't answer for {len(cdx_fail)} member(s): {', '.join(cdx_fail[:10])}")
    if requeued:
        _log(f"Archived press releases: {requeued:,} pages the archive didn't answer went back in the queue for a later try")
    _log(f"Archived press releases: +{added:,} from {fetched:,} saved pages; {left} of {len(members)} departed members left")
    return left == 0
