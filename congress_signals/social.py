"""What members of Congress and their offices say in public: press releases, X/Twitter (archive 2017-2023),
Bluesky, Mastodon, Substack and YouTube. Everything is matched to the member (Congress ID), tagged with the
companies and company people it names, its industries and its tone, and kept in ./social.

Sources (all free and allowed by their own terms):
  press   Congress Press (Derek Willis, UMD): every member's press releases, daily, 2001-now
  x       Tweets of Congress (Alex Litel): office, campaign, committee and party accounts, Jun 2017 - Jul 2023
  bsky    Bluesky public API (posts, reposts, quotes, image descriptions; text in images read with tesseract)
  masto   Mastodon public API (accounts from the congress-legislators project and members' websites)
  substack  public RSS feeds of members' Substacks (found on their websites)
  youtube   YouTube Data API (titles and descriptions; needs YOUTUBE_API_KEY)
Truth Social is left out: its terms forbid scripts and building a database from it.

Run by update_social.py: a resumable history build (social.yml) and a short daily refresh (daily.yml)."""
import os, re, io, json, gzip, time, zipfile, datetime as dt, hashlib
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "CapitolCapital research (congress trade research; contact via GitHub e36meister/congress-signals)"}
PRESS = "https://thescoop.org/congress-press/downloads/"
TWEETS = "https://raw.githubusercontent.com/alexlitel/congresstweets/master/data/{}.json"
TWEET_USERS = "https://raw.githubusercontent.com/alexlitel/congresstweets-automator/master/data/historical-users-filtered.json"
LEG_CUR = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
LEG_HIST = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"
LEG_SOC = "https://unitedstates.github.io/congress-legislators/legislators-social-media.json"
BSKY = "https://public.api.bsky.app/xrpc/"
TWEETS_FIRST, TWEETS_LAST = dt.date(2017, 6, 21), dt.date(2023, 7, 15)
START_YEAR = 2013
TAGGER_VERSION = 8

SECTOR_WORDS = {
    "Energy": "oil gas pipeline pipelines drilling fracking refinery refineries lng crude petroleum keystone opec gasoline energy",
    "Utilities": "utility utilities electricity grid nuclear ratepayers powerplant",
    "Healthcare": "drug drugs pharmaceutical pharma medicare medicaid hospital hospitals fda vaccine vaccines insulin biotech "
                  "health insurers prescription",
    "Technology": "semiconductor semiconductors chips chip microchips ai artificial intelligence software cyber cybersecurity "
                  "tech broadband data privacy tiktok",
    "Communication Services": "broadband telecom wireless spectrum 5g media streaming social media",
    "Financial Services": "bank banks banking crypto cryptocurrency bitcoin stablecoin wall street sec fdic lending mortgage "
                          "insurance fintech",
    "Industrials": "defense pentagon military ndaa shipbuilding aircraft missile missiles contractor contractors airline "
                   "airlines railroad rail infrastructure manufacturing",
    "Real Estate": "housing rent landlords real estate mortgage homebuyers",
    "Consumer Defensive": "food grocery groceries farm farmers agriculture tobacco",
    "Consumer Cyclical": "retail retailers automakers auto autos electric vehicles ev cars",
    "Basic Materials": "steel aluminum mining minerals lithium copper chemicals",
}
COMMON_NAMES = set("frontline compass alamo allot deluxe plexus prudential apple target visa meta intel oracle amazon delta southern general block square ford gap ball crown "
                   "united american progressive alliance dominion carrier fortune shell select global national first "
                   "equity public service health capital energy power river valley summit pioneer frontier liberty "
                   "patriot eagle heritage independence premier republic union security guardian discovery sprint "
                   "match zoom snap lumen evergy entergy ameren exelon realty prologis welltower dollar general "
                   "chewy wayfair etsy uber lyft paramount spirit hess murphy marathon devon apache halliburton".split())
STRONG_CUES = set("company companies ceo ceos shares stock stocks inc corp corporation headquarters executives executive merger "
                  "acquisition acquire earnings shareholders contract contracts subsidiary brand".split())
COMPANY_CUES = set("company companies ceo ceos shares stock stocks inc corp corporation workers employees plant factory "
                   "facility contract contracts merger acquisition announced invest investment headquarters executives "
                   "jobs layoffs earnings".split())
POS = set("great proud thank thanks congratulations congrats grateful excited honored glad welcome welcomed "
          "historic win wins success successful strong growth jobs investment invest innovative leader leading "
          "partnership celebrate celebrating support supporting good best amazing incredible fantastic".split())
NEG = set("greed greedy price gouging gouge outrageous unacceptable fraud scam corrupt corruption abuse abusive "
          "investigate investigation investigating accountable accountability ripoff ripping layoffs fail failed failure "
          "dangerous reckless lies lying misleading monopoly exploit exploiting harm harmful shameful disgraceful "
          "violation violations penalty fine fined lawsuit sue sued ban banned crisis threat".split())


def _log(*a):
    print(dt.datetime.now().strftime("%H:%M:%S"), *a, flush=True)
    try:
        from congress_signals import runlog
        runlog.capture(" ".join(map(str, a)))
    except Exception:
        pass


def _dir(cfg, *parts):
    """A path under ./social; the last part is a file when it contains a dot or is a known marker file."""
    p = os.path.join(cfg.get("SOCIAL_DIR") or "social", *parts)
    is_file = parts and ("." in parts[-1] or parts[-1] in ("complete", "needs_more_runs"))
    os.makedirs(os.path.dirname(p) if is_file else p, exist_ok=True)
    return p


def _state(cfg):
    p = _dir(cfg, "state.json")
    try:
        return json.load(open(p))
    except Exception:
        return {}


def _save_state(cfg, st):
    st = {k: v for k, v in st.items()}
    json.dump(st, open(_dir(cfg, "state.json"), "w"))
    open(_dir(cfg, ".changed"), "w").write(dt.datetime.utcnow().isoformat())


def _get(url, params=None, tries=3, timeout=60, as_json=True, headers=None):
    for a in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={**UA, **(headers or {})})
            if r.status_code == 200:
                return r.json() if as_json else r
            if r.status_code in (404, 400, 401, 403, 410):
                return None
            time.sleep(2 * (a + 1) + (int(r.headers.get("Retry-After", 0) or 0) if r.status_code == 429 else 0))
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def write_jsonl(path, rows):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_jsonl(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# tagger: companies (names, $TICKERS), company people (insiders, lobbyists), industries, tone
# ---------------------------------------------------------------------------
class Tagger:
    def __init__(self, cfg):
        import congress_signals.engine as E
        self.E = E
        data_dir = cfg["DATA_DIR"]
        try:
            meta = json.load(open(os.path.join(data_dir, "cache", "ticker_meta.json")))
        except Exception:
            meta = {}
        self.tickers = set()
        self.names = {}
        self.caps = {t: float(m.get("cap") or 0) for t, m in meta.items() if isinstance(m, dict)}
        idx = {}            # first token -> [(tokens, ticker, kind, single)]
        self.cash = {}
        for t, m in meta.items():
            if not isinstance(m, dict) or (m.get("type") or "").upper() in ("ETF", "MUTUALFUND", "INDEX"):
                continue
            self.tickers.add(t)
            self.cash[t.lower()] = t
            key = E.company_key(m.get("name") or "")
            toks = key.split()
            if not toks or len(" ".join(toks)) < 4:
                continue
            if len(toks) > 2 and len(toks[-1]) <= 3:   # "xyz bancorp of", keep the distinctive part
                toks = toks[:2]
            self.names[t] = toks
            if len(toks) == 1 and _generic(tuple(toks)) and float(m.get("cap") or 0) < 50e9:
                continue        # "City Holding", "American International", "Travelers": only by $TICKER / (TICKER)
            idx.setdefault(toks[0], []).append((tuple(toks), t, "co", len(toks) == 1))
        people = {}
        try:
            people = json.load(open(_dir(cfg, "people.json")))
        except Exception:
            pass
        try:
            lob = json.load(open(os.path.join(data_dir, "cache", "lobbying.json")))
            for t, v in lob.items():
                for r in v.get("rows", []):
                    for n in (r[4] if len(r) > 4 else []) or []:
                        people.setdefault(t, {}).setdefault(n, "lobbyist")
        except Exception:
            pass
        np_ = 0
        top = re.compile(r"\b(ceo|chief|president|chair|chairman|chairwoman|founder|cfo|coo|cto|lobbyist)\b", re.I)
        for t, ps in people.items():
            if t not in self.tickers:
                continue
            for n, role in ps.items():
                if not top.search(str(role)):
                    continue
                toks = re.findall(r"[a-z]+", E._strip_accents(n).lower())
                toks = [x for x in toks if len(x) > 1 and x not in ("jr", "sr", "ii", "iii", "iv", "mr", "ms", "dr")]
                if len(toks) < 2:
                    continue
                toks = (toks[0], toks[-1])
                idx.setdefault(toks[0], []).append((toks, t, "pp:" + role, False))
                np_ += 1
        self.idx = idx
        self.surnames, self.firsts = set(), {}
        try:      # members' own names ("Bill Hagerty", "Huntsman") aren't companies or their executives
            for m in json.load(open(_dir(cfg, "legislators.json"))).values():
                last = re.findall(r"[a-z]+", E._strip_accents(m.get("last") or "").lower())
                if last:
                    self.surnames.add(last[-1])
                    self.firsts.setdefault(last[-1], set()).update(
                        x for x in re.findall(r"[a-z]+", E._strip_accents(f"{m.get('first') or ''} {m.get('nick') or ''}").lower()))
        except Exception:
            pass
        self.sectors = {}
        for s, words in SECTOR_WORDS.items():
            for w in words.split():
                self.sectors.setdefault(w, set()).add(s)
        self.version = f"t{TAGGER_VERSION}"      # bump TAGGER_VERSION to re-tag every stored post
        _log(f"Tagger: {len(self.tickers):,} companies, {np_:,} company people")

    def tag(self, text):
        """-> (tickers, people [(name, ticker, role)], sectors, tone).
        A company name counts only when capitalized where it appears, outside links and other accounts' handles.
        A name made only of everyday words or common surnames ("Apple", "People", "Graham", "American International")
        also needs a company word (CEO, shares, company, ...) within 5 words. Short all-capital words (PACS, USPS)
        need that too. $TICKER and "Name (TICKER)" always count."""
        if not text:
            return [], [], [], 0.0
        raw = self.E._strip_accents(text)
        raw = re.sub(r"https?://\S+", " ", raw)
        spans = [(m.group(0), m.start()) for m in re.finditer(r"[A-Za-z0-9]+", raw)]
        toks = [w.lower() for w, _ in spans]
        low = raw.lower()
        tset = set(toks)
        cue_at = [i for i, w in enumerate(toks) if w in STRONG_CUES]
        tick, ppl = set(), []
        for m in re.finditer(r"\$([A-Z]{1,5})\b", raw):              # $XOM (capitals; "$s" isn't a ticker)
            if m.group(1).lower() in self.cash:
                tick.add(self.cash[m.group(1).lower()])
        for m in re.finditer(r"\((?:NYSE|NASDAQ|Nasdaq|NYSE American)?:?\s*([A-Z]{1,5})\)", raw):
            t_ = self.cash.get(m.group(1).lower())                     # "Exxon Mobil (XOM)", not "(SHIP) program"
            if t_ and (re.search(r"(NYSE|NASDAQ|Nasdaq)", m.group(0)) or
                       set(re.findall(r"[a-z]+", low[max(0, m.start() - 60):m.start()])) & set(self.names.get(t_, ()))):
                tick.add(t_)
        for i, w in enumerate(toks):
            for ct, t, kind, single in self.idx.get(w, ()):
                n = len(ct)
                if tuple(toks[i:i + n]) != ct:
                    continue
                words = [spans[i + j][0] for j in range(n)]
                if not all(x[:1].isupper() or x[:1].isdigit() for x in words):
                    continue                                    # "apple pie", "frontline workers"
                pos = spans[i][1]
                pre = raw[max(0, pos - 1):pos]
                if pre in ("@", "_", "#") and not (pre == "@" and len(ct) == 1):
                    continue                                    # someone else's handle or a hashtag
                near_cue = any(abs(k - i) <= 5 for k in cue_at)
                if kind == "co":
                    acronym = n == 1 and words[0].isupper() and len(words[0]) <= 5
                    weak = acronym or _generic(ct) or ct[0] in self.surnames
                    if n == 1 and not weak:       # one-word name: a well-known company, or a company word nearby
                        weak = self.caps.get(t, 0) < 10e9 or _zipf(ct[0]) >= 3.9
                    if weak and not near_cue:
                        continue
                    tick.add(t)
                else:
                    if ct[-1] in self.surnames and ct[0] in self.firsts.get(ct[-1], ()):
                        continue                                # it's a member of Congress, not the executive
                    ppl.append((" ".join(x.capitalize() for x in ct), t, kind[3:]))
                    tick.add(t)
        secs = sorted({s for w in tset for s in self.sectors.get(w, ())})
        p, n = len(tset & POS), len(tset & NEG)
        tone = 0.0 if p + n == 0 else round((p - n) / (p + n), 2)
        return sorted(tick), ppl[:6], secs, tone


_GEN = {}


def _zipf(w):
    try:
        from wordfreq import zipf_frequency
        return zipf_frequency(w, "en")
    except Exception:
        return 0.0


def _generic(ct):
    """True when every word of the name is an everyday English word or common name (zipf frequency >= 4.0, e.g.
    'people' 6.3, 'northern' 4.9, 'apple' 4.8, 'graham' 4.3, 'visa' 4.1), or a listed one ('frontline'). Brand words
    are rarer (boeing 3.8, tesla 3.8, lockheed 3.4, pfizer 3.1)."""
    k = " ".join(ct)
    if k not in _GEN:
        try:
            from wordfreq import zipf_frequency
            _GEN[k] = all(w in COMMON_NAMES or zipf_frequency(w, "en") >= 4.0 for w in ct)
        except Exception:
            _GEN[k] = all(w in COMMON_NAMES or len(w) < 6 for w in ct)
    return _GEN[k]


def rec(date, key, src, text, url, tg, rp="", full=None):
    tk, pp, secs, tone = tg.tag(full or text)
    return [date, key, src, (text or "")[:400], url or "", tk, [f"{a}|{b}|{c}" for a, b, c in pp], tone, secs, rp, tg.version]


# ---------------------------------------------------------------------------
# who is who: members (Congress IDs) and their accounts
# ---------------------------------------------------------------------------
def legislators(cfg, st):
    p = _dir(cfg, "legislators.json")
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 7 * 86400:
        return json.load(open(p))
    cur = _get(LEG_CUR) or []
    hist = _get(LEG_HIST) or []
    soc = _get(LEG_SOC) or []
    out = {}
    for m in cur + [h for h in hist if any((t.get("end") or "") >= f"{START_YEAR}" for t in h.get("terms", []))]:
        b = (m.get("id") or {}).get("bioguide")
        if not b:
            continue
        nm = m.get("name") or {}
        last = (m.get("terms") or [{}])[-1]
        out[b] = {"name": nm.get("official_full") or f"{nm.get('first', '')} {nm.get('last', '')}".strip(),
                  "first": nm.get("first"), "last": nm.get("last"), "nick": nm.get("nickname"), "state": last.get("state"),
                  "chamber": "Senate" if last.get("type") == "sen" else "House", "url": last.get("url"),
                  "current": m in cur, "social": {}}
    for s in soc:
        b = (s.get("id") or {}).get("bioguide")
        if b in out:
            out[b]["social"].update(s.get("social") or {})
    if out:
        json.dump(out, open(p, "w"))
    return out


SOCIAL_LINKS = {
    "bsky": re.compile(r"bsky\.app/profile/([A-Za-z0-9._:-]+)", re.I),
    "threads": re.compile(r"threads\.(?:net|com)/@([A-Za-z0-9._]+)", re.I),
    "substack": re.compile(r"https?://([a-z0-9-]+)\.substack\.com", re.I),
    "masto": re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})/@([A-Za-z0-9_]+)(?=[\"'/?#\s])", re.I),
    "youtube": re.compile(r"youtube\.com/(?:channel/(UC[\w-]{20,})|@([\w.-]+)|user/([\w.-]+)|c/([\w.-]+))", re.I),
    "truth": re.compile(r"truthsocial\.com/@([A-Za-z0-9_]+)", re.I),
    "instagram": re.compile(r"instagram\.com/([A-Za-z0-9._]+)", re.I),
    "facebook": re.compile(r"facebook\.com/([A-Za-z0-9.-]+)", re.I),
    "tiktok": re.compile(r"tiktok\.com/@([A-Za-z0-9._]+)", re.I),
    "rumble": re.compile(r"rumble\.com/(?:c/)?([A-Za-z0-9_-]+)", re.I),
}
NOT_MASTO = ("youtube", "tiktok", "threads", "medium", "instagram", "facebook", "twitter", "x.com", "substack", "truthsocial", "bsky")


def discover_accounts(cfg, leg, st, deadline):
    """Accounts linked from members' official websites (bluesky, threads, substack, mastodon, youtube, ...).
    Re-checked every 30 days, a batch per run."""
    p = _dir(cfg, "accounts.json")
    acc = json.load(open(p)) if os.path.exists(p) else {}
    todo = [b for b, m in leg.items() if m.get("current") and m.get("url")
            and (time.time() - (acc.get(b) or {}).get("_checked", 0)) > 30 * 86400]
    n = 0
    for b in todo:
        if time.time() > deadline:
            break
        r = _get(leg[b]["url"], as_json=False, timeout=30, tries=2)
        a = acc.setdefault(b, {})
        a["_checked"] = time.time()
        if r is None:
            continue
        html = r.text[:600000]
        for k, rx in SOCIAL_LINKS.items():
            found = set()
            for m in rx.finditer(html):
                if k == "masto":
                    host, user = m.group(1).lower(), m.group(2)
                    if any(x in host for x in NOT_MASTO):
                        continue
                    found.add(f"{user}@{host}")
                elif k == "youtube":
                    found.add(next(g for g in m.groups() if g))
                else:
                    v = m.group(1).strip(".")
                    if k in ("instagram", "facebook") and v.lower() in ("p", "sharer", "share", "sharer.php", "reel", "tr", "plugins"):
                        continue
                    found.add(v)
            if found:
                a[k] = sorted(found)[:4]
        n += 1
    json.dump(acc, open(p, "w"))
    if n:
        _log(f"Accounts: checked {n} member websites ({sum(1 for v in acc.values() if v.get('bsky'))} with Bluesky, "
             f"{sum(1 for v in acc.values() if v.get('substack'))} Substack, {sum(1 for v in acc.values() if v.get('masto'))} Mastodon)")
    return acc


def _name_match(display, m):
    E = __import__("congress_signals.engine", fromlist=["x"])
    d = E._strip_accents(display or "").lower()
    last = E._strip_accents(m.get("last") or "").lower()
    firsts = {E._strip_accents(x).lower() for x in (m.get("first"), m.get("nick")) if x}
    return bool(last) and re.search(r"\b" + re.escape(last) + r"\b", d) and any(re.search(r"\b" + re.escape(f) + r"\b", d) for f in firsts)


def bluesky_accounts(cfg, leg, acc, st, deadline):
    """handle -> bioguide: from members' websites, then a search by name (kept only when the profile's handle is a
    .gov domain, or the name matches and the bio says they serve in Congress)."""
    p = _dir(cfg, "bsky_accounts.json")
    ba = json.load(open(p)) if os.path.exists(p) else {"map": {}, "searched": {}}
    for b, a in acc.items():
        for h in a.get("bsky") or []:
            ba["map"].setdefault(h.lower(), b)
    cue = re.compile(r"\b(congress|congressman|congresswoman|representative|u\.?s\.? rep|senator|u\.?s\.? senate|"
                     r"house of representatives|serving)\b", re.I)
    have = set(ba["map"].values())
    for b, m in leg.items():
        if time.time() > deadline or not m.get("current") or b in have:
            continue
        if time.time() - ba["searched"].get(b, 0) < 30 * 86400:
            continue
        ba["searched"][b] = time.time()
        js = _get(BSKY + "app.bsky.actor.searchActors", params={"q": m["name"], "limit": 10})
        for a in (js or {}).get("actors", []):
            h = (a.get("handle") or "").lower()
            gov = h.endswith(".house.gov") or h.endswith(".senate.gov")
            if gov or (_name_match(a.get("displayName"), m) and cue.search(a.get("description") or "")):
                ba["map"].setdefault(h, b)
        time.sleep(0.3)
    json.dump(ba, open(p, "w"))
    return ba["map"]


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
def collect_press(cfg, st, tg, deadline, years=True):
    """Current and last month every run (rewritten), then yearly archives newest first, once each."""
    out_dir = _dir(cfg, "posts", "press")
    today = dt.date.today()
    months = sorted({today.strftime("%Y-%m"), (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")})
    done = set(st.get("press_years", []))
    jobs = [("m", m) for m in months] + ([("y", str(y)) for y in range(today.year - 1, START_YEAR - 1, -1) if str(y) not in done]
                                         if years else [])
    # months of the current year that aren't on disk yet (first run, or a missed month)
    for m in range(1, today.month + 1):
        k = f"{today.year}-{m:02d}"
        if not os.path.exists(os.path.join(out_dir, f"{k}.jsonl.gz")) and ("m", k) not in jobs:
            jobs.append(("m", k))
    for kind, k in jobs:
        if time.time() > deadline:
            break
        if kind == "m":
            r = _get(PRESS + f"{k}.jsonl", as_json=False, timeout=180)
            if r is None:
                continue
            lines = r.content.decode("utf-8", "ignore").splitlines()
            rows = _press_rows(lines, tg)
            write_jsonl(os.path.join(out_dir, f"{k}.jsonl.gz"), rows)
            _log(f"Press releases: {k}: {len(rows):,}")
        else:
            r = _get(PRESS + f"{k}.zip", as_json=False, timeout=600)
            if r is None:
                continue
            try:
                z = zipfile.ZipFile(io.BytesIO(r.content))
            except Exception:
                continue
            rows = []
            for nm in z.namelist():
                if nm.endswith(".jsonl") or nm.endswith(".json"):
                    rows += _press_rows(z.read(nm).decode("utf-8", "ignore").splitlines(), tg)
            write_jsonl(os.path.join(out_dir, f"{k}.jsonl.gz"), rows)
            done.add(k)
            st["press_years"] = sorted(done)
            _save_state(cfg, st)
            _log(f"Press releases: {k}: {len(rows):,}")
    return all(str(y) in done for y in range(START_YEAR, today.year))


def _press_rows(lines, tg):
    rows = []
    for ln in lines:
        try:
            j = json.loads(ln)
        except Exception:
            continue
        m = j.get("member") or {}
        b = m.get("bioguide_id")
        d = (j.get("date") or "")[:10]
        if not b or not d:
            continue
        title, text = j.get("title") or "", j.get("text") or ""
        rows.append(rec(d, b, "Press release", (title + " — " + text).strip(" —"), j.get("url"), tg, full=title + "\n" + text))
    return rows


def collect_tweets(cfg, st, tg, deadline):
    """Archive of congressional X/Twitter accounts, a month of daily files at a time (kept: posts that name a company,
    a company person or an industry; counts of everything per member per day for the industry signals)."""
    out_dir = _dir(cfg, "posts", "x")
    users = st.get("_tweet_users")
    if not users:
        js = _get(TWEET_USERS, timeout=120) or []
        users = {}
        for u in js:
            b = (u.get("id") or {}).get("bioguide") if isinstance(u.get("id"), dict) else None
            key = b or ("c:" + (u.get("name") or "")[:60])
            for a in u.get("accounts") or []:
                if a.get("screen_name"):
                    users[a["screen_name"].lower()] = [key, a.get("account_type") or ""]
        st["_tweet_users"] = users
    nxt = dt.date.fromisoformat(st.get("tweets_next", TWEETS_FIRST.isoformat()))
    counts_p = _dir(cfg, "x_counts.json")
    counts = json.load(open(counts_p)) if os.path.exists(counts_p) else {}
    while nxt <= TWEETS_LAST and time.time() < deadline:
        month = nxt.strftime("%Y-%m")
        rows, day = [], nxt
        while day <= TWEETS_LAST and day.strftime("%Y-%m") == month:
            js = _get(TWEETS.format(day.isoformat()), timeout=120)
            for t in js or []:
                u = users.get((t.get("screen_name") or "").lower())
                if not u:
                    continue
                key = u[0]
                text = t.get("text") or ""
                rp = ""
                m = re.match(r"RT @(\w+):\s*", text)
                if m:
                    rp, text = "@" + m.group(1), text[m.end():]
                r_ = rec((t.get("time") or day.isoformat())[:10], key, "X", text, t.get("link"), tg, rp=rp)
                c = counts.setdefault(key, {}).setdefault(r_[0][:7], [0, {}])
                c[0] += 1
                for s in r_[8]:
                    c[1][s] = c[1].get(s, 0) + 1
                if r_[5] or r_[6] or r_[8]:
                    rows.append(r_)
            day += dt.timedelta(days=1)
            if time.time() > deadline and day.strftime("%Y-%m") == month:
                break
        if day.strftime("%Y-%m") == month and day <= TWEETS_LAST:      # month not finished: redo it next run
            break
        write_jsonl(os.path.join(out_dir, f"{month}.jsonl.gz"), rows)
        st["tweets_next"] = day.isoformat()
        json.dump(counts, open(counts_p, "w"))
        _save_state(cfg, st)
        _log(f"X archive: {month}: kept {len(rows):,} posts that name a company, person or industry")
        nxt = day
    return nxt > TWEETS_LAST


def _bsky_text(post):
    rec_ = post.get("record") or {}
    text = rec_.get("text") or ""
    emb = post.get("embed") or {}
    alts, imgs = [], []
    for e in [emb] + [emb.get("media") or {}]:
        for im in e.get("images") or []:
            if im.get("alt"):
                alts.append(im["alt"])
            if im.get("fullsize") or im.get("thumb"):
                imgs.append(im.get("fullsize") or im.get("thumb"))
    quoted = ""
    q = (emb.get("record") or {})
    q = q.get("record") if isinstance(q.get("record"), dict) and "author" in q.get("record", {}) else q
    if isinstance(q, dict) and q.get("author"):
        quoted = (q.get("author") or {}).get("handle", "") + ": " + ((q.get("value") or {}).get("text") or "")
    ext = (emb.get("external") or {})
    link = (ext.get("title") or "") + " " + (ext.get("description") or "")
    return text, alts, imgs, quoted, link.strip()


def _ocr(url):
    """Text inside an image (tesseract); '' when tesseract isn't installed or the image has no text."""
    try:
        import shutil, subprocess, tempfile
        if not shutil.which("tesseract"):
            return ""
        r = requests.get(url, timeout=30, headers=UA)
        if r.status_code != 200 or len(r.content) > 4_000_000:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            f.write(r.content)
            f.flush()
            out = subprocess.run(["tesseract", f.name, "-", "--psm", "3"], capture_output=True, text=True, timeout=40).stdout
        out = re.sub(r"\s+", " ", out).strip()
        return out if len(re.findall(r"[A-Za-z]{3,}", out)) >= 3 else ""
    except Exception:
        return ""


def collect_bluesky(cfg, st, tg, hmap, deadline, ocr_days=120, max_ocr=300):
    out_dir = _dir(cfg, "posts", "bsky")
    since = f"{START_YEAR}-01-01"
    ocr_from = (dt.date.today() - dt.timedelta(days=ocr_days)).isoformat()
    n_ocr, total = 0, 0
    for h, b in sorted(hmap.items()):
        if time.time() > deadline:
            return False
        path = os.path.join(out_dir, re.sub(r"[^a-z0-9.-]", "_", h) + ".jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        if st.get("bsky_v") != TAGGER_VERSION and old:
            old = []                           # first run of this version: re-read the whole account
        seen = {r[4] for r in old}
        newest = max((r[0] for r in old), default="")
        rows, cursor = [], None
        for _ in range(60):
            js = _get(BSKY + "app.bsky.feed.getAuthorFeed",
                      params={"actor": h, "limit": 100, "filter": "posts_no_replies", **({"cursor": cursor} if cursor else {})})
            feed = (js or {}).get("feed", [])
            stop = False
            for f in feed:
                post = f.get("post") or {}
                reason = f.get("reason") or {}
                is_rp = (reason.get("$type") or "").endswith("reasonRepost")
                when = (reason.get("indexedAt") if is_rp else (post.get("record") or {}).get("createdAt")) or post.get("indexedAt") or ""
                d = when[:10]
                if d and (d < since or (newest and d < newest)):
                    if not is_rp:
                        stop = True
                        break
                    continue
                uri = post.get("uri") or ""
                url = "https://bsky.app/profile/" + (post.get("author") or {}).get("handle", h) + "/post/" + uri.rsplit("/", 1)[-1]
                if url in seen:
                    continue
                seen.add(url)
                text, alts, imgs, quoted, link = _bsky_text(post)
                ocr = ""
                if imgs and d >= ocr_from and n_ocr < max_ocr:
                    ocr = " ".join(filter(None, (_ocr(u) for u in imgs[:2])))
                    n_ocr += 1
                full = "\n".join(x for x in (text, quoted, link, " ".join(alts), ocr) if x)
                shown = text + (f" [quoting {quoted[:160]}]" if quoted else "") + (f" [image: {ocr[:120]}]" if ocr else "")
                rp = "@" + (post.get("author") or {}).get("handle", "") if is_rp else ""
                rows.append(rec(d, b, "Bluesky", shown, url, tg, rp=rp, full=full))
            cursor = (js or {}).get("cursor")
            if stop or not cursor or not feed:
                break
            time.sleep(0.15)
        if rows or not os.path.exists(path):
            write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
            total += len(rows)
    st["bsky_v"] = TAGGER_VERSION
    if total:
        _log(f"Bluesky: {len(hmap)} accounts, {total:,} new posts ({n_ocr} images read)")
    return True


def collect_mastodon(cfg, st, tg, leg, acc, deadline):
    out_dir = _dir(cfg, "posts", "masto")
    handles = {}
    for b, m in leg.items():
        v = (m.get("social") or {}).get("mastodon")
        if v and "@" in v.strip("@"):
            handles[v.strip("@").lower()] = b
    for b, a in acc.items():
        for v in a.get("masto") or []:
            handles.setdefault(v.lower(), b)
    added, dead = 0, []
    for h, b in handles.items():
        if time.time() > deadline:
            break
        user, host = h.split("@", 1)
        a = _get(f"https://{host}/api/v1/accounts/lookup", params={"acct": user}, tries=2)
        if not a or not a.get("id"):
            dead.append(h)
            continue
        path = os.path.join(out_dir, re.sub(r"[^a-z0-9.@-]", "_", h) + ".jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        seen = {r[4] for r in old}
        rows, max_id = [], None
        for _ in range(30):
            js = _get(f"https://{host}/api/v1/accounts/{a['id']}/statuses",
                      params={"limit": 40, "exclude_replies": "true", **({"max_id": max_id} if max_id else {})}, tries=2)
            if not js:
                break
            stop = False
            for s in js:
                rb = s.get("reblog")
                src = rb or s
                url = src.get("url") or src.get("uri")
                if url in seen:
                    stop = True
                    break
                txt = re.sub(r"<[^>]+>", " ", src.get("content") or "")
                txt = re.sub(r"\s+", " ", __import__("html").unescape(txt)).strip()
                alts = " ".join(m_.get("description") or "" for m_ in src.get("media_attachments") or [])
                rows.append(rec((s.get("created_at") or "")[:10], b, "Mastodon", txt, url, tg,
                                rp=("@" + (rb.get("account") or {}).get("acct", "")) if rb else "", full=txt + " " + alts))
            max_id = js[-1].get("id")
            if stop or len(js) < 40:
                break
        if rows:
            write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
            added += len(rows)
    _log(f"Mastodon: {len(handles)} accounts, +{added:,} posts" + (f"; no answer from {len(dead)}: {', '.join(dead[:8])}" if dead else ""))


def collect_substack(cfg, st, tg, acc, deadline):
    import xml.etree.ElementTree as ET
    out_dir = _dir(cfg, "posts", "substack")
    added, dead, n = 0, [], 0
    for b, a in acc.items():
        for sub in a.get("substack") or []:
            if time.time() > deadline:
                break
            n += 1
            r = _get(f"https://{sub}.substack.com/feed", as_json=False, timeout=60, tries=2)
            if r is None:
                dead.append(sub)
                continue
            path = os.path.join(out_dir, f"{sub}.jsonl.gz")
            old = read_jsonl(path) if os.path.exists(path) else []
            seen = {x[4] for x in old}
            rows = []
            try:
                root = ET.fromstring(r.content)
            except Exception:
                continue
            for it in root.iter("item"):
                url = it.findtext("link") or ""
                if url in seen:
                    continue
                d = pd.to_datetime(it.findtext("pubDate"), errors="coerce")
                if pd.isna(d):
                    continue
                body = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or it.findtext("description") or ""
                body = re.sub(r"\s+", " ", __import__("html").unescape(re.sub(r"<[^>]+>", " ", body))).strip()
                title = it.findtext("title") or ""
                rows.append(rec(d.strftime("%Y-%m-%d"), b, "Substack", f"{title} — {body}", url, tg, full=title + "\n" + body))
            if rows or not os.path.exists(path):
                write_jsonl(path, sorted(old + rows, key=lambda x: x[0]))
            added += len(rows)
    _log(f"Substack: {n} newsletters, +{added:,} posts" + (f"; no answer from {len(dead)}: {', '.join(dead[:8])}" if dead else ""))


def collect_youtube(cfg, st, tg, leg, acc, deadline):
    key = os.environ.get("YOUTUBE_API_KEY") or cfg.get("YOUTUBE_API_KEY")
    if not key:
        return
    out_dir = _dir(cfg, "posts", "youtube")
    API = "https://www.googleapis.com/youtube/v3/"
    try:            # one cheap call first, so a wrong key or a used-up day shows in the log
        r = requests.get(API + "channels", params={"part": "id", "forHandle": "@YouTube", "key": key}, timeout=30)
        if r.status_code != 200:
            why = ((r.json().get("error") or {}).get("errors") or [{}])[0].get("reason", "") if r.text.startswith("{") else ""
            _log(f"YouTube: key not accepted (HTTP {r.status_code} {why}); skipped")
            return
    except Exception as e:
        _log(f"YouTube: not reachable ({type(e).__name__})")
        return
    chans = {}
    for b, m in leg.items():
        s = m.get("social") or {}
        if s.get("youtube_id"):
            chans.setdefault(s["youtube_id"], b)
    ids = st.setdefault("yt_ids", {})          # @handle / user name -> channel id ("" = not found)
    units = 0
    for b, a in acc.items():
        for c in a.get("youtube") or []:
            if c.startswith("UC") and len(c) >= 22:
                chans.setdefault(c, b)
                continue
            if c not in ids and time.time() < deadline and units < 2000:
                js = _get(API + "channels", params={"part": "id", "forHandle": "@" + c.lstrip("@"), "key": key}, tries=2)
                units += 1
                if not (js or {}).get("items"):
                    js = _get(API + "channels", params={"part": "id", "forUsername": c, "key": key}, tries=2)
                    units += 1
                if js is None:
                    continue              # no answer (quota or network): try again next run
                ids[c] = ((js.get("items") or [{}])[0]).get("id") or ""
            if ids.get(c):
                chans.setdefault(ids[c], b)
    _log(f"YouTube: {len(chans)} member channels")
    added = 0
    for cid, b in chans.items():
        if time.time() > deadline or units > 8000:
            break
        path = os.path.join(out_dir, f"{cid}.jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        seen = {x[4] for x in old}
        rows, page = [], None
        for _ in range(20):
            js = _get(API + "playlistItems", params={"part": "snippet", "playlistId": "UU" + cid[2:], "maxResults": 50,
                                                    "key": key, **({"pageToken": page} if page else {})}, tries=2)
            units += 1
            if not js:
                break
            stop = False
            for it in js.get("items", []):
                sn = it.get("snippet") or {}
                vid = (sn.get("resourceId") or {}).get("videoId")
                url = f"https://www.youtube.com/watch?v={vid}"
                if url in seen:
                    stop = True
                    break
                text = (sn.get("title") or "") + " — " + (sn.get("description") or "")
                rows.append(rec((sn.get("publishedAt") or "")[:10], b, "YouTube", text, url, tg))
            page = js.get("nextPageToken")
            if stop or not page:
                break
        if rows:
            write_jsonl(path, sorted(old + rows, key=lambda x: x[0]))
            added += len(rows)
    _log(f"YouTube: +{added:,} videos ({units:,} of the 10,000 free daily units used)")


def collect_people(cfg, st, deadline, per_run=6):
    """Company officers and directors (people) by ticker, from the SEC's quarterly insider data sets, so posts that
    name a company's executive count as naming the company. A few quarters per run, newest first."""
    import congress_signals.engine as E
    hdr = {"User-Agent": os.environ.get("SEC_USER_AGENT") or cfg.get("SEC_USER_AGENT") or ""}
    if not hdr["User-Agent"]:
        return True
    p = _dir(cfg, "people.json")
    people = json.load(open(p)) if os.path.exists(p) else {}
    done = set(st.get("people_q", []))
    if not st.get("people_q_moved_fix"):       # 2026q2+ were marked done when the SEC moved its files; fetch again
        done = {k for k in done if k < "2026q2"}
        st["people_q_moved_fix"] = True
    today = dt.date.today()
    qs = [f"{y}q{q}" for y in range(today.year, START_YEAR - 1, -1) for q in (4, 3, 2, 1)
          if dt.date(y, 3 * q - 2, 1) <= today - dt.timedelta(days=100)]
    n = 0
    for k in qs:
        if k in done:
            continue
        if n >= per_run or time.time() > deadline:
            break
        r = E.sec_345_zip(k, hdr, timeout=300)
        n += 1
        if r is None:
            if k < "2025q1":
                done.add(k)        # the SEC doesn't have some old quarters; don't block on them
                st["people_q"] = sorted(done)
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            sub = E._read_tsv(z, "SUBMISSION.TSV", ["ACCESSION_NUMBER", "ISSUERTRADINGSYMBOL"])
            own = E._read_tsv(z, "REPORTINGOWNER.TSV", ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
                                                         "RPTOWNER_TITLE"])
        except Exception as e:
            _log(f"People: {k} unreadable ({type(e).__name__})")
            continue
        d = own.merge(sub, on="ACCESSION_NUMBER")
        for t, nm, rel, title in zip(d["ISSUERTRADINGSYMBOL"], d["RPTOWNERNAME"], d["RPTOWNER_RELATIONSHIP"], d["RPTOWNER_TITLE"]):
            t = E.clean_ticker(t)
            rel = str(rel or "")
            if not t or not nm or not re.search(r"Director|Officer", rel, re.I):
                continue
            if re.search(r"\b(LLC|L\.?P\.?|INC|CORP|FUND|TRUST|CAPITAL|PARTNERS|HOLDINGS|GROUP|LTD|MANAGEMENT|BANK)\b", str(nm), re.I):
                continue
            parts = str(nm).replace(",", " ").split()           # SEC writes LAST FIRST MIDDLE
            if len(parts) < 2:
                continue
            name = f"{parts[1].capitalize()} {parts[0].capitalize()}"
            role = (str(title) if isinstance(title, str) and title and title != "nan" else ("director" if "Director" in rel else "officer"))[:40]
            people.setdefault(t, {})[name] = role
        done.add(k)
        st["people_q"] = sorted(done)
        json.dump(people, open(p, "w"))
        _save_state(cfg, st)
        _log(f"People: {k}: {sum(len(v) for v in people.values()):,} company officers and directors so far")
    return all(q in done for q in qs)


# ---------------------------------------------------------------------------
# index for the member pages and the post signals
# ---------------------------------------------------------------------------
def build_index(cfg, tg=None):
    """social/index.pkl: per member, posts for the member page (every post naming a company or person, plus the
    latest 120 others) and, for the signals, dated company mentions with tone and dated industry mentions."""
    root = _dir(cfg, "posts")
    pages, mentions, sectors, crowd = {}, {}, {}, {}
    n = 0
    press_seen = set()
    from congress_signals.press_backfill import norm_url
    # archived releases of departed members are built by the parallel archive job in their own folder (./wb)
    wb_root = os.path.join(cfg.get("WB_DIR") or "wb", "posts")
    folders = [(root, src) for src in (sorted(os.listdir(root)) if os.path.exists(root) else [])]
    folders += [(wb_root, src) for src in (sorted(os.listdir(wb_root)) if os.path.isdir(wb_root) else [])]
    for base, src in folders:
        for fn in sorted(os.listdir(os.path.join(base, src))):
            fpath = os.path.join(base, src, fn)
            recs, redone = read_jsonl(fpath), False
            if src.startswith("press"):          # Congress Press first ("press" sorts first); backfills skip its URLs
                keep = []
                for r in recs:
                    u = norm_url(r[4] if len(r) > 4 else "")
                    if u and u in press_seen:
                        continue
                    press_seen.add(u)
                    keep.append(r)
                recs = keep
            for r in recs:
                if tg is not None and (len(r) < 11 or r[10] != tg.version):     # re-tag with today's rules, once
                    tk2, pp2, secs2, tone2 = tg.tag(r[3])
                    r[:] = (r + [""] * 10)[:10]
                    r[5], r[6], r[7], r[8] = tk2, [f"{a}|{b}|{c}" for a, b, c in pp2], tone2, secs2
                    r.append(tg.version)
                    redone = True
                d, key, s, text, url, tk, pp, tone, secs, rp = (r + [""] * 10)[:10]
                if not d or not isinstance(key, str):
                    continue
                n += 1
                lst = pages.setdefault(key, [[], []])
                item = [d, s + (f" (repost of {rp})" if rp else ""), (text or "")[:300], tk, tone, url, ";".join(pp)]
                j = 0 if (tk or pp) else 1
                lst[j].append(item)
                if len(lst[j]) > (1200 if j == 0 else 400):        # keep memory flat: newest 600 / 120
                    lst[j] = sorted(lst[j], key=lambda x: x[0])[-(600 if j == 0 else 120):]
                for t in tk:
                    mentions.setdefault(key, {}).setdefault(t, []).append((d, tone, bool(rp)))
                    crowd.setdefault(t, []).append((d, key))
                for sc in secs:
                    sectors.setdefault(key, {}).setdefault(sc, []).append(d)
            if redone:
                write_jsonl(fpath, recs)
    for key, (a, b) in pages.items():
        b.sort(key=lambda x: x[0])
        pages[key] = sorted(a, key=lambda x: x[0])[-600:] + b[-120:]
        pages[key].sort(key=lambda x: x[0])
    # tweet archive: industry counts per month (all tweets, not just kept ones)
    xc = {}
    try:
        xc = json.load(open(_dir(cfg, "x_counts.json")))
    except Exception:
        pass
    to_np = lambda xs: np.array(sorted(pd.to_datetime(xs, errors="coerce").dropna().values), dtype="datetime64[ns]")
    idx = {"pages": pages,
           "mentions": {k: {t: sorted(v) for t, v in m.items()} for k, m in mentions.items()},
           "sectors": {k: {s: to_np(v) for s, v in m.items()} for k, m in sectors.items()},
           "crowd": {t: sorted(v) for t, v in crowd.items()}, "x_counts": xc,
           "built": dt.datetime.utcnow().isoformat(), "posts": n}
    pd.to_pickle(idx, _dir(cfg, "index.pkl"))
    _log(f"Posts index: {n:,} posts from {len(pages):,} members and offices; "
         f"{sum(len(v) for m in mentions.values() for v in m.values()):,} company mentions")
    return idx


def load_index(cfg):
    p = os.path.join(cfg.get("SOCIAL_DIR") or "social", "index.pkl")
    try:
        return pd.read_pickle(p) if os.path.exists(p) else None
    except Exception:
        return None


def page_posts(idx):
    """{bioguide: [[date, platform, text, tickers, tone, url, people]]} for members.export_members."""
    return (idx or {}).get("pages") or {}


# ---------------------------------------------------------------------------
# trial signals (start at weight 0; switch on only if the backtest shows they help)
# ---------------------------------------------------------------------------
def post_features(tx, cfg, idx=None):
    """f_post_named_before: the member (or their office) named the company or one of its people in a post in the
       30 days before the trade.
    f_post_sector_burst: the member posted about the company's industry at least twice as often as usual in the 30
       days before the trade (at least 3 posts).
    f_post_tone_clash: bought within 60 days after posting critically about the company, or sold after praising it.
    f_post_crowd: 3+ different members named the company in posts in the 14 days before the filing date.
    Only posts dated before the trade (or the filing, for the crowd signal) count."""
    for c in ("f_post_named_before", "f_post_sector_burst", "f_post_tone_clash", "f_post_crowd"):
        tx[c] = 0.0
    tx["post_note"] = ""
    root = cfg.get("SOCIAL_DIR") or "social"
    if idx is None and not os.path.exists(os.path.join(root, "complete")):
        return tx           # until the history is collected, a partial test would mislead the weekly check
    idx = idx if idx is not None else load_index(cfg)
    if not idx:
        return tx
    men, secs, crowd = idx.get("mentions") or {}, idx.get("sectors") or {}, idx.get("crowd") or {}
    bio = tx["bioguide"] if "bioguide" in tx else pd.Series([None] * len(tx), index=tx.index)
    td = pd.to_datetime(tx["trade_date"].where(tx["trade_date"].notna(), tx["filed_date"]))
    fd = pd.to_datetime(tx["filed_date"])
    f1, f2, f3, f4 = (np.zeros(len(tx)) for _ in range(4))
    notes = [[] for _ in range(len(tx))]
    crowd_np = {t: (np.array([pd.Timestamp(d) for d, _ in v], dtype="datetime64[ns]"), [k for _, k in v]) for t, v in crowd.items()}
    for i, (b, t, d, f, typ, sec) in enumerate(zip(bio, tx["ticker"], td, fd, tx["tx_type"], tx.get("sector", [None] * len(tx)))):
        if pd.isna(d):
            continue
        ds = d.strftime("%Y-%m-%d")
        lo30 = (d - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        lo60 = (d - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        if isinstance(b, str):
            ms = (men.get(b) or {}).get(t) or []
            near = [m for m in ms if lo30 <= m[0] < ds and not m[2]]
            if near:
                f1[i] = 1.0
                notes[i].append(f"named the company in a post {(d - pd.Timestamp(near[-1][0])).days} days before")
            tone = [m[1] for m in ms if lo60 <= m[0] < ds and not m[2]]
            if tone and ((typ == "buy" and min(tone) <= -0.3) or (typ == "sell" and max(tone) >= 0.3)):
                f3[i] = 1.0
                notes[i].append("posted " + ("critically about" if typ == "buy" else "praise for") + " the company before " +
                                ("buying" if typ == "buy" else "selling"))
            arr = (secs.get(b) or {}).get(sec) if sec else None
            if arr is not None and len(arr):
                x = np.datetime64(d)
                n30 = np.searchsorted(arr, x) - np.searchsorted(arr, x - np.timedelta64(30, "D"))
                n365 = np.searchsorted(arr, x - np.timedelta64(30, "D")) - np.searchsorted(arr, x - np.timedelta64(395, "D"))
                if n30 >= 3 and n30 >= 2 * max(n365 / 12.2, 0.5):
                    f2[i] = 1.0
                    notes[i].append(f"{n30} posts about {sec} in the 30 days before (usually {n365 / 12.2:.1f})")
        C = crowd_np.get(t)
        if C is not None and pd.notna(f):
            x = np.datetime64(f)
            a, z = np.searchsorted(C[0], x - np.timedelta64(14, "D")), np.searchsorted(C[0], x)
            who = {k for k in C[1][a:z] if not k.startswith("c:")}
            if len(who) >= 3:
                f4[i] = 1.0
                notes[i].append(f"{len(who)} members named the company in posts in the 2 weeks before the filing")
    tx["f_post_named_before"], tx["f_post_sector_burst"], tx["f_post_tone_clash"], tx["f_post_crowd"] = f1, f2, f3, f4
    tx["post_note"] = ["; ".join(n) for n in notes]
    return tx


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def update_all(cfg, minutes, history=True):
    t0 = time.time()
    deadline = t0 + minutes * 60
    st = _state(cfg)
    tg = Tagger(cfg)
    if st.get("tagger_v") != TAGGER_VERSION:      # tagging rules changed: re-read this year's press releases
        for f in os.listdir(_dir(cfg, "posts", "press")):
            if f.startswith(str(dt.date.today().year) + "-"):
                os.remove(os.path.join(_dir(cfg, "posts", "press"), f))
        st["tagger_v"] = TAGGER_VERSION
    st["tagger"] = tg.version
    leg = legislators(cfg, st)
    acc = discover_accounts(cfg, leg, st, min(deadline, time.time() + minutes * 60 * 0.15))
    hmap = bluesky_accounts(cfg, leg, acc, st, min(deadline, time.time() + minutes * 60 * 0.1))
    # fresh posts first (small), then the history backfills
    collect_press(cfg, st, tg, deadline, years=False)
    collect_bluesky(cfg, st, tg, hmap, deadline)
    collect_mastodon(cfg, st, tg, leg, acc, deadline)
    collect_substack(cfg, st, tg, acc, deadline)
    collect_youtube(cfg, st, tg, leg, acc, deadline)
    ok = True
    if history:
        ok &= collect_people(cfg, st, deadline - 600, per_run=60)
        ok &= collect_press(cfg, st, tg, deadline - 300)
        ok &= collect_tweets(cfg, st, tg, deadline - 300)
        try:        # press releases missing from Congress Press since 2021 (members' sites, then the Internet Archive)
            import congress_signals.press_backfill as PB
            left = deadline - time.time() - 600
            if left > 300:
                ok &= PB.backfill_current(cfg, st, tg, leg, time.time() + left * 0.55)
                # departed members' archived releases: the parallel archive job (archive.yml) does these now
        except Exception as e:
            _log(f"Press backfill: stopped ({type(e).__name__}: {e})")
            ok = False
    else:
        collect_people(cfg, st, deadline, per_run=1)
    _save_state(cfg, st)
    build_index(cfg, tg)
    if history and ok:
        open(_dir(cfg, "complete"), "w").write(dt.datetime.utcnow().isoformat())
        _log("Social: history complete")
    elif history:
        _log("Social: history not finished; the next run continues")
    _log(f"Social: done in {(time.time() - t0) / 60:.1f} min")
    return ok
