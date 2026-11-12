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
MONTHS_DAYS = set("jan january feb february mar march apr april may jun june jul july aug august sep sept september oct "
                  "october nov november dec december monday tuesday wednesday thursday friday saturday sunday".split())
TAGGER_VERSION = 12       # (not bumped for the Nov 11 spaCy name check: stored posts are re-tagged only on the owner's say)
# 9: executives by last name or distinctive first name alone, scored; 12: every person matched is kept (Nov 4)
# Nov 8 (officials, agency "lobbyists") changes tagging of new posts only: no version bump, so the stored history
# (still being sorted) isn't re-tagged from scratch
# How Bluesky posts are read (text, quotes, alt text, image text). Only a change here needs every account read again;
# a new TAGGER_VERSION doesn't: build_index re-tags every stored post. (Oct 22: the re-read was tied to TAGGER_VERSION,
# so after tagger 9 and 10 every run restarted a full re-read it never finished, and no new Bluesky posts were saved
# since; the files on hand were read under version 8.)
BSKY_READ_VERSION = 8

# 10: a member's own name is never a company person, nicknames included ("Chris Murphy" = Sen. Christopher Murphy)
# 11: a company person named with another organization ("X, director at the ACLU") isn't that company's person
_NICK_GROUPS = ["christopher chris kit", "robert bob rob bobby", "william bill will billy", "james jim jimmy jamie",
                "michael mike mick", "charles chuck charlie", "thomas tom tommy", "richard dick rick rich", "edward ed ted eddie",
                "theodore ted", "joseph joe joey", "john jack johnny", "daniel dan danny", "david dave", "steven steve stephen",
                "anthony tony", "elizabeth liz beth betsy", "katherine kathy kate katie", "catherine cathy kate", "patricia pat patty",
                "patrick pat", "margaret maggie peggy", "jennifer jen jenny", "kenneth ken", "gregory greg", "jeffrey jeff",
                "benjamin ben", "nicholas nick", "andrew andy drew", "matthew matt", "timothy tim", "donald don", "ronald ron",
                "gerald jerry", "lawrence larry", "raymond ray", "samuel sam", "alexander alex", "alexandria alex", "frederick fred",
                "douglas doug", "russell russ", "randall randy", "zachary zach", "jonathan jon", "joshua josh", "deborah debbie deb",
                "susan sue suzan", "suzanne sue", "rebecca becky", "victoria vicky", "kimberly kim", "cynthia cindy", "pamela pam",
                "christine chris", "christina chris", "abigail abby", "harold hal harry", "henry hank", "gabriel gabe", "jacob jake",
                "phillip phil", "philip phil", "leonard len", "lucille lucy", "terrence terry", "vernon vern", "mitchell mitch",
                "earl buddy", "jose pepe", "francisco frank", "francis frank", "eugene gene", "albert al", "alan al", "alfonso al"]
NICKNAMES = {}
for _g in _NICK_GROUPS:
    _w = _g.split()
    for _x in _w:
        NICKNAMES.setdefault(_x, set()).update(_w)


def _same_first(first, known):
    """True when `first` is one of a member's first names, a nickname of it, or the start of it (Chris/Christopher)."""
    if not known:
        return False
    if first in known or NICKNAMES.get(first, set()) & set(known):
        return True
    return len(first) >= 3 and any(k.startswith(first) or first.startswith(k) for k in known if len(k) >= 3)

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
# Nov 5 (owner, Salazar/PG alert): a sector is too broad to link a post to a company (farm-relief posts were matched to
# Procter & Gamble because both are "Consumer Defensive"). Each industry word points to the company industries it's
# about (Yahoo industry names, matched as parts); a word with no entry ("tech", "manufacturing", "energy", "health")
# is too broad to narrow down and links nothing by itself.
WORD_INDUSTRIES = {
    "oil": ["oil & gas"], "crude": ["oil & gas"], "petroleum": ["oil & gas"], "opec": ["oil & gas"], "drilling": ["oil & gas"],
    "fracking": ["oil & gas"], "gas": ["oil & gas", "utilities - regulated gas"], "pipeline": ["oil & gas midstream", "oil & gas integrated"],
    "pipelines": ["oil & gas midstream", "oil & gas integrated"], "keystone": ["oil & gas midstream"], "lng": ["oil & gas midstream", "oil & gas integrated"],
    "refinery": ["refining"], "refineries": ["refining"], "gasoline": ["refining", "oil & gas integrated"],
    "utility": ["utilities"], "utilities": ["utilities"], "electricity": ["utilities"], "grid": ["utilities"],
    "ratepayers": ["utilities"], "powerplant": ["utilities"], "nuclear": ["utilities", "uranium"],
    "drug": ["drug manufacturers", "biotechnology", "pharmaceutical retailers"], "drugs": ["drug manufacturers", "biotechnology", "pharmaceutical retailers"],
    "pharmaceutical": ["drug manufacturers", "biotechnology"], "pharma": ["drug manufacturers", "biotechnology"],
    "prescription": ["drug manufacturers", "pharmaceutical retailers", "healthcare plans"], "insulin": ["drug manufacturers"],
    "vaccine": ["drug manufacturers", "biotechnology"], "vaccines": ["drug manufacturers", "biotechnology"], "biotech": ["biotechnology"],
    "fda": ["drug manufacturers", "biotechnology", "medical devices", "medical instruments", "diagnostics"],
    "medicare": ["healthcare plans", "medical care facilities"], "medicaid": ["healthcare plans", "medical care facilities"],
    "insurers": ["healthcare plans", "insurance"], "hospital": ["medical care facilities"], "hospitals": ["medical care facilities"],
    "semiconductor": ["semiconductor"], "semiconductors": ["semiconductor"], "chips": ["semiconductor"], "chip": ["semiconductor"],
    "microchips": ["semiconductor"], "ai": ["semiconductor", "software", "information technology services", "internet content", "computer hardware"],
    "artificial": ["semiconductor", "software", "information technology services", "internet content", "computer hardware"],
    "software": ["software"], "cyber": ["software - infrastructure", "information technology services"],
    "cybersecurity": ["software - infrastructure", "information technology services"], "privacy": ["internet content", "software"],
    "tiktok": ["internet content", "entertainment"], "broadband": ["telecom services"], "telecom": ["telecom services"],
    "wireless": ["telecom services", "communication equipment"], "spectrum": ["telecom services"], "5g": ["telecom services", "communication equipment"],
    "media": ["internet content", "entertainment", "broadcasting", "publishing"], "streaming": ["entertainment"],
    "bank": ["banks"], "banks": ["banks"], "banking": ["banks"], "fdic": ["banks"], "lending": ["banks", "credit services", "mortgage finance"],
    "crypto": ["capital markets", "financial data"], "cryptocurrency": ["capital markets", "financial data"], "bitcoin": ["capital markets", "financial data"],
    "stablecoin": ["capital markets", "financial data", "credit services"], "sec": ["capital markets", "financial data", "asset management"], "mortgage": ["mortgage finance", "banks", "reit - mortgage"],
    "insurance": ["insurance"], "fintech": ["credit services", "financial data", "software - infrastructure"],
    "defense": ["aerospace & defense"], "pentagon": ["aerospace & defense"], "military": ["aerospace & defense"], "ndaa": ["aerospace & defense"],
    "missile": ["aerospace & defense"], "missiles": ["aerospace & defense"], "contractor": ["aerospace & defense"],
    "contractors": ["aerospace & defense"], "shipbuilding": ["aerospace & defense", "marine"], "aircraft": ["aerospace & defense"],
    "airline": ["airlines"], "airlines": ["airlines"], "railroad": ["railroads"], "rail": ["railroads"],
    "infrastructure": ["engineering & construction", "infrastructure operations", "building materials", "farm & heavy construction machinery"],
    "housing": ["residential construction", "reit - residential", "real estate"], "homebuyers": ["residential construction", "real estate", "mortgage finance"],
    "rent": ["reit - residential", "real estate services"], "landlords": ["reit - residential", "real estate services"],
    "food": ["packaged foods", "food distribution", "grocery stores", "farm products", "confectioners"],
    "grocery": ["grocery stores", "food distribution", "discount stores"], "groceries": ["grocery stores", "food distribution", "discount stores"],
    "farm": ["farm products", "agricultural inputs", "farm & heavy construction machinery"],
    "farmers": ["farm products", "agricultural inputs", "farm & heavy construction machinery"],
    "agriculture": ["farm products", "agricultural inputs", "farm & heavy construction machinery"], "tobacco": ["tobacco"],
    "retail": ["retail", "department stores", "discount stores"], "retailers": ["retail", "department stores", "discount stores"],
    "automakers": ["auto manufacturers", "auto parts"], "auto": ["auto manufacturers", "auto parts", "auto & truck dealerships"],
    "autos": ["auto manufacturers", "auto parts"], "cars": ["auto manufacturers", "auto parts", "auto & truck dealerships"],
    "ev": ["auto manufacturers", "auto parts"],
    "steel": ["steel"], "aluminum": ["aluminum"], "mining": ["metals & mining", "gold", "silver", "copper", "coking coal", "uranium"],
    "minerals": ["metals & mining", "gold", "silver", "copper", "uranium"], "lithium": ["other industrial metals", "specialty chemicals"],
    "copper": ["copper"], "chemicals": ["chemicals", "agricultural inputs"],
}
_IND_META = {}


def company_industry(t, data_dir="data"):
    """The company's industry (Yahoo's name, lower case) from the ticker cache; "" when it isn't known."""
    if "m" not in _IND_META:
        try:
            _IND_META["m"] = json.load(open(os.path.join(data_dir, "cache", "ticker_meta.json")))
        except Exception:
            _IND_META["m"] = {}
    m = _IND_META["m"].get(t) or {}
    return str(m.get("industry") or "").lower() if isinstance(m, dict) else ""


def industry_fits(t, words, data_dir="data"):
    """True when the post's industry words are about this company's own industry (farm relief is about farm products,
    not household products). A company whose industry isn't known falls back to the sector match (True) so nothing is
    lost to a missing field; broad words alone ("tech", "manufacturing") don't fit anything."""
    ci = company_industry(t, data_dir)
    if not ci:
        return True
    parts = {p for w in (words or []) for p in WORD_INDUSTRIES.get(str(w).lower(), [])}
    return any(p in ci for p in parts)


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
                        if _org_name(n):
                            continue        # owner, Nov 8: "Justice Department" was a BCE "lobbyist" (16 posts)
                        people.setdefault(t, {}).setdefault(n, "lobbyist")
        except Exception:
            pass
        np_ = 0
        self.last_idx, self.first_idx = {}, {}     # name -> {ticker: (full name, role)}
        self.sector_of = {t: (m.get("sector") or "") for t, m in meta.items() if isinstance(m, dict)}
        self.last_cands = []
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
                if role != "lobbyist":       # first or last name alone: candidates, scored in tag()
                    full = " ".join(x.capitalize() for x in toks)
                    self.last_idx.setdefault(toks[1], {}).setdefault(t, (full, role))
                    self.first_idx.setdefault(toks[0], {}).setdefault(t, (full, role))
        self.idx = idx
        self.surnames, self.firsts = set(), {}
        try:      # members' own names ("Bill Hagerty", "Huntsman") aren't companies or their executives
            for m in json.load(open(_dir(cfg, "legislators.json"))).values():
                last = re.findall(r"[a-z]+", E._strip_accents(m.get("last") or "").lower())
                if last:
                    self.surnames.add(last[-1])
                    fs = set(re.findall(r"[a-z]+", E._strip_accents(f"{m.get('first') or ''} {m.get('nick') or ''}").lower()))
                    for f in list(fs):          # Chris Murphy is Christopher Murphy, Bob Casey is Robert Casey
                        fs |= NICKNAMES.get(f, set())
                    self.firsts.setdefault(last[-1], set()).update(fs)
        except Exception:
            pass
        # owner's verdicts on possible company people ("elon|tsla": yes / no), saved by the app
        try:
            self.people_rev = {k: (v or {}).get("verdict") for k, v in json.load(open(_dir(cfg, "people_reviews.json"))).items()}
        except Exception:
            self.people_rev = {}
        # public officials (owner, Nov 8): names seen in posts with an official title right next to them ("White
        # House Chief of Staff X", "Postmaster General X", "X, the attorney general"), learned as posts come in and
        # kept in officials.json (synced with the app's storage by the hourly posts job)
        try:
            self.officials = {tuple(k.split("|")) for k in (json.load(open(_dir(cfg, "officials.json"))).get("names") or {})}
        except Exception:
            self.officials = set()
        self.officials_new = {}
        from congress_signals.name_context import Checker
        self.names_check = Checker()         # spaCy: what role the post gives a matched name (owner, Nov 11)
        self.name_drops = []
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
        self.name_drops = []                 # names the spaCy check set aside in this post: (name, ticker, why)
        for nm in official_names(raw):
            if nm not in self.officials:
                self.officials.add(nm)
                self.officials_new[nm] = dt.date.today().isoformat()
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
                    end_ = spans[i + n - 1][1] + len(spans[i + n - 1][0])
                    if (raw[end_:end_ + 1] == "-" and raw[end_ + 1:end_ + 2].isupper()) or \
                            (pos >= 2 and raw[pos - 1] == "-" and raw[pos - 2].isalpha()):
                        continue                                # Nov 8: "Magna-Power Electronics" isn't Magna (MGA)
                    if n == 1 and i > 0 and spans[i - 1][0][:1].isupper() and spans[i - 1][0].isalpha() \
                            and raw[max(0, spans[i - 1][1] - 2):spans[i - 1][1]].strip() not in (".", "!", "?", ":") \
                            and toks[i - 1] not in ("the", "at", "with", "from", "of", "and", "ceo", "inc"):
                        continue                                # "Brooke Rollins", "Elon Musk": a person, not the company
                    acronym = n == 1 and words[0].isupper() and len(words[0]) <= 5
                    weak = acronym or _generic(ct) or ct[0] in self.surnames
                    if n == 1 and not weak:       # one-word name: a well-known company, or a company word nearby
                        weak = self.caps.get(t, 0) < 10e9 or _zipf(ct[0]) >= 3.9
                    if weak and not near_cue:
                        continue
                    tick.add(t)
                else:
                    if ct[-1] in self.surnames and _same_first(ct[0], self.firsts.get(ct[-1], ())):
                        continue                                # it's a member of Congress, not the executive
                    end = spans[i + n - 1][1] + len(spans[i + n - 1][0])
                    if _other_affiliation(raw, pos, end, self.names.get(t, ()), t):
                        continue                                # same name, but the post says they're with another group
                    if tuple(ct) in self.officials and not _co_named(raw, self.names.get(t, ())):
                        continue                                # a public official who shares a company person's name
                    # owner, Nov 11: spaCy reads the sentence the name is in. A name the post gives a non-company
                    # role (Mayor, Police Captain, "his brother", "Tom Gallagher of Mahwah", a list of local
                    # officials, "Bob Brooks, a longtime union leader") or that is part of a place or event name
                    # ("Gallagher Park") isn't this company's person, unless the post names the company itself.
                    who, wkind, why = self.names_check.check(raw, pos, end, self.names.get(t, ()))
                    # Nov 12 review of 30 days of set-asides: two lost real intel. Toby Rice, EQT's CEO, in a post on
                    # the EQT-Range merger ("congratulating his friends Toby Rice, EQT's President and CEO") and Elon
                    # Musk dropped by the list rule (the owner's rule: a company's CEO named directly counts). So an
                    # executive is set aside only on strong evidence (a government title, "X of <town>", part of a
                    # place/event name), and any match stays when the company itself is named anywhere in the post.
                    role_ = kind[3:].lower()
                    is_exec = role_ != "lobbyist"
                    top_exec = is_exec and bool(re.search(r"\b(ceo|chief executive|(co-?)?founder|chair(man|woman|person)?|cob)\b", role_))
                    friend_only = wkind == "rel" and bool(re.search(r"\b(friends?|colleagues?)\b", why or "")) and not \
                        re.search(r"\b(pastor|resident|dr|reverend|student|teacher|coach|nurse|doctor|neighbor)\b",
                                  why or "", re.I)
                    if who == "other" and is_exec and (friend_only or (wkind == "list" and top_exec)):
                        who = "unknown"                 # "my friends Toby Rice", a CEO in a list of names
                    if who in ("other", "not_person") and not _co_mentioned(raw, self.names.get(t, ()), ct):
                        self.name_drops.append((" ".join(x.capitalize() for x in ct), t, why))
                        from congress_signals.name_context import STATUS as _NCS
                        _NCS["dropped"] += 1
                        if wkind == "gov":
                            nm = tuple(ct)
                            if nm not in self.officials:          # learned as a public official, like official_names
                                self.officials.add(nm)
                                self.officials_new[nm] = dt.date.today().isoformat()
                        continue
                    ppl.append((" ".join(x.capitalize() for x in ct), t, kind[3:]))
                    tick.add(t)
        secs = sorted({s for w in tset for s in self.sectors.get(w, ())})
        # an executive named by last name alone ("Huang said...") or a distinctive first name alone ("Elon"):
        # scored on how rare the name is, a title next to it, the company or its industry in the same post, and
        # how many companies have an executive by that name. High = counts as a mention; medium = for review.
        self.last_cands = []
        got = {p_[0].split()[-1].lower() for p_ in ppl}
        exec_title = re.compile(r"\b(ceo|chief executive|founder|co-founder|chair(?:man|woman)?|mr|ms|mrs|executive)\b", re.I)
        other_title = {"mayor", "judge", "justice", "sheriff", "governor", "gov", "secretary", "sec", "senator", "sen",
                       "rep", "representative", "congressman", "congresswoman", "director", "dr", "admiral", "general",
                       "gen", "commissioner", "nominee", "officer", "deputy", "chief", "speaker", "leader", "ambassador",
                       "president", "pres", "colonel", "col", "captain", "sgt", "sergeant", "councilman", "councilwoman",
                       "councilmember", "superintendent", "principal", "professor", "prof", "coach", "pastor", "rev",
                       "bishop", "father", "minister", "attorney", "ag", "chairwoman", "chairman"}
        for i, w in enumerate(toks):
            if w in got or w in self.surnames or len(w) < 3 or not spans[i][0][:1].isupper():
                continue
            prev = toks[i - 1] if i > 0 else ""
            prev_cap = i > 0 and spans[i - 1][0][:1].isupper() and raw[spans[i - 1][1] - 2:spans[i - 1][1]].strip() not in (".", "!", "?")
            nxt_cap = i + 1 < len(toks) and spans[i + 1][0][:1].isupper()
            for kind, table, weight in (("last", self.last_idx, 1.0), ("first", self.first_idx, 0.6)):
                cands = table.get(w)
                if not cands:
                    continue
                if kind == "last":
                    # part of someone else's full name ("Emil Bove", "Mayor Busse", "Rhode Island", "Proffitt Act")
                    if prev in other_title or (prev_cap and prev not in ("mr", "ms", "mrs", "ceo", "founder", "chairman", "chairwoman")):
                        continue
                    if nxt_cap:
                        continue
                else:
                    # only checks that the word is used as someone's first name here (not a month, an acronym, a
                    # place like "St. Louis" or "San Jose", or the last name in "Hillary Clinton")
                    if nxt_cap or prev in other_title or prev_cap or spans[i][0].isupper() or w in MONTHS_DAYS \
                            or prev in ("st", "san", "santa", "la", "los", "las", "el", "fort", "ft", "port", "lake",
                                        "mount", "mt", "new", "saint", "de", "del", "le"):
                        continue
                z = _zipf(w)
                rarity = max(0.0, min(1.0, (5.0 - z) / 3.0))
                near = raw[max(0, spans[i][1] - 40):spans[i][1] + 60]
                title = bool(exec_title.search(near))
                for t, (full, role) in list(cands.items())[:12]:
                    if not re.search(r"ceo|chief executive|founder|chair", str(role), re.I):
                        continue             # only the people whose name stands for the company
                    co = t in tick and w not in self.names.get(t, ())     # the company named by other words
                    sec = bool(self.sector_of.get(t)) and self.sector_of.get(t) in secs
                    if kind == "first":
                        # owner, Oct 16: first names always count and go to review, when the company or its
                        # industry is in the same post (they never count on their own)
                        if not (co or sec):
                            continue
                        self.last_cands.append({"n": full, "t": t, "role": role, "w": spans[i][0], "by": kind,
                                                "c": 0.5, "title": title, "co": co, "sec": sec})
                        continue
                    if not (co or (sec and title)):
                        continue             # no sign of the company in the post: not a candidate at all
                    conf = (0.35 * rarity + 0.25 * title + 0.45 * co + 0.15 * sec) * weight / (len(cands) ** 0.5)
                    if conf >= 0.3:
                        self.last_cands.append({"n": full, "t": t, "role": role, "w": spans[i][0], "by": kind,
                                                "c": round(conf, 2), "title": title, "co": co, "sec": sec})
        rev = getattr(self, "people_rev", {}) or {}
        kept = []
        for c in self.last_cands:
            v = rev.get(f"{str(c['w']).lower()}|{str(c['t']).lower()}")
            if v == "no":
                continue                         # owner said it isn't them
            if v == "yes":
                c["c"] = max(c["c"], 0.75)       # owner said it is: counts like a full-name mention
            kept.append(c)
        self.last_cands = kept
        for c in self.last_cands:
            if c["c"] >= 0.75 and all(x[0] != c["n"] for x in ppl):
                ppl.append((c["n"], c["t"], c["role"]))
                tick.add(c["t"])
        p, n = len(tset & POS), len(tset & NEG)
        tone = 0.0 if p + n == 0 else round((p - n) / (p + n), 2)
        # Nov 4 (owner, post audit): this kept only the first 6 people but every company they brought in, so a post
        # naming someone who shares a lobbyist's name (57 companies) showed 51 companies with no reason on file and
        # read as "posted about" each one. Every person is kept now, so each company has its reason.
        return sorted(tick), ppl[:120], secs, tone


_GEN = {}


def _zipf(w):
    try:
        from wordfreq import zipf_frequency
        return zipf_frequency(w, "en")
    except Exception:
        return 0.0


# "X, deputy director at the ACLU", "X of the Sierra Club", "the ACLU's X", "X (ACLU)": the sentence gives the person
# another organization. (Nov 3: an ACLU lawyer quoted in a 65-member letter shares a name with someone on a company's
# list, so the release, and every member who reposted it, was tagged with that company and pushed as an insider hint.)
_AFTER_ORG = re.compile(r"^\s*(?:,\s*[^,.;!?\"“”]{0,80}?\b(?:at|of|for|with)\s+|\s+(?:of|at|from)\s+|\s*\(\s*)"
                        r"(?:the\s+)?([A-Z#][\w&.#'\-]*(?:\s+(?:of\s+|for\s+|and\s+|&\s+)?[A-Z][\w&.'\-]*){0,5})")
_BEFORE_ORG = re.compile(r"(?:the\s+)?([A-Z#][\w&.#\-]*(?:\s+(?:of\s+|for\s+|and\s+|&\s+)?[A-Z][\w&.\-]*){0,5})(?:'s|’s)"
                         r"\s+(?:[a-z][a-z\-]*\s+){0,6}$")
_EXEC_TITLE = re.compile(r"\b(ceo|chief executive|founder|co-founder|chair(?:man|woman)?|president)\b", re.I)
_ORG_SKIP = {"the", "a", "an", "u", "s", "us", "of", "for", "and", "inc", "corp", "co", "company", "group", "llc"}


_SIGS = {}


def _sig_of(text):
    from congress_signals.post_sort import text_sig
    k = hash(text or "")
    if k not in _SIGS:
        if len(_SIGS) > 200000:
            _SIGS.clear()
        _SIGS[k] = text_sig(text)
    return _SIGS[k]


# Owner, Nov 8 (post review): the new White House chief of staff shares a name with BTF's listed president, so 29
# posts about him were tagged BTF and the crowd count reached 71. A name that appears in a post with an official title
# right before it ("Chief of Staff X", "Attorney General X", "Postmaster General X") or right after it ("X, the White
# House chief of staff", "X's appointment as chief of staff") is a public official; once seen, that name no longer
# counts as a company person anywhere unless the company itself is named in the same post.
_OFF_TITLE = (r"(?:white house |wh |deputy |acting |former |new |incoming |senior )*"
              r"(?:chief of staff|press secretary|attorney general|solicitor general|surgeon general|postmaster general|"
              r"secretary of [a-z]+(?: [a-z]+){0,2}|[a-z]+ secretary|national security advis[eo]r|homeland security advis[eo]r|"
              r"(?:fbi|cia|ice|omb|nih|cdc|fda|epa|irs|fema|dni|nasa|atf|dea|ssa|cms) (?:director|administrator|commissioner|chief)|"
              r"director of national intelligence|border czar|ambassador to [a-z]+(?: [a-z]+)?|un ambassador|"
              r"administrator of the [a-z]+(?: [a-z]+){0,3}|white house (?:counsel|adviser|advisor|aide|official)|"
              r"special envoy|deputy secretary|vice president|speaker of the house|senate majority leader|"
              r"senate minority leader|house majority leader|house minority leader|governor|judge|justice|"
              r"mayor|sheriff|police chief|chief of police|fire chief|county (?:executive|clerk|commissioner|prosecutor)|"
              r"(?:county|city|town|borough) (?:administrator|manager|council(?:man|woman|member)?)|"
              r"council(?:man|woman|member)|commissioner|superintendent|prosecutor|freeholder|"
              r"assembly(?:man|woman|member)|state senator|state representative|lieutenant governor|clerk)")
_NAME = r"([A-Z][a-z]+)(?:\s+[A-Z]\.)?\s+((?:Mc|Mac|O')?[A-Z][a-z]+(?:-[A-Z][a-z]+)?)"
_OFF_BEFORE = re.compile(r"\b" + _OFF_TITLE + r"\s+" + _NAME, re.I)
_OFF_AFTER = re.compile(_NAME + r"(?:'s|’s)?\s*(?:,\s*(?:the\s+)?(?:president's\s+|trump's\s+|our\s+)?|\s+(?:as|to\s+be|to\s+serve\s+as)\s+(?:the\s+)?|"
                        r"(?:'s|’s)?\s+(?:appointment|nomination|selection|promotion|elevation)\s+(?:to|as)\s+(?:the\s+)?(?:new\s+)?)" + _OFF_TITLE + r"\b", re.I)


def official_names(raw):
    """{(first, last)} lowercased: people the post gives an official government title (see _OFF_TITLE)."""
    out = set()
    for rx, flags in ((_OFF_BEFORE, 0), (_OFF_AFTER, 0)):
        for m in rx.finditer(raw or ""):
            f, l = m.group(1), m.group(2)
            if not (f[:1].isupper() and l[:1].isupper()):
                continue                 # the title words matched case-insensitively; the name itself must be capitalized
            out.add((E_strip(f).lower(), E_strip(l).lower()))
    return out


def E_strip(x):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", x) if not unicodedata.combining(c))


def _co_mentioned(raw, co_words, person=()):
    """Looser than _co_named (Nov 12): any distinctive word of the company's name, capitalized, anywhere in the post
    ("EQT Corporation" / "EQT's" for EQT's listed name "EQT Corp"). Words of the person's own name don't count
    ("Gallagher" the man isn't Arthur J. Gallagher), nor everyday words ("general", "american")."""
    if _co_named(raw, co_words):
        return True
    own = {w.lower() for w in person or ()}
    for w in co_words or ():
        if len(w) <= 2 or w in own or w in _ORG_WORDS or _zipf(w) >= 4.0:
            continue
        if re.search(r"(?<![\w@#])" + re.escape(w) + r"(?:['’]s)?\b", raw or "", re.I):
            m = re.search(r"(?<![\w@#])(" + re.escape(w) + r")", raw or "", re.I)
            if m and (m.group(1)[:1].isupper() or m.group(1).isupper()):
                return True
    return False


def _co_named(raw, co_words):
    """True when the company's own name words appear, capitalized, in the post."""
    ws = [w for w in co_words or () if len(w) > 2]
    if not ws:
        return False
    return bool(re.search(r"\b" + r"\W+".join(re.escape(w) for w in ws) + r"\b", raw or "", re.I)
                and re.search(r"\b" + re.escape(ws[0]) + r"\b", re.sub(r"\b[a-z]\w*", " ", raw or ""), re.I))


_ORG_WORDS = {"department", "dept", "agency", "administration", "office", "bureau", "commission", "committee", "service",
              "services", "council", "board", "house", "senate", "congress", "government", "federal", "county", "city",
              "university", "college", "institute", "association", "foundation", "coalition", "center", "centre",
              "inc", "llc", "llp", "lp", "pllc", "group", "company", "corp", "corporation", "partners", "strategies",
              "associates", "consulting", "consultants", "law", "firm", "pac", "fund", "trust", "authority",
              "ministry", "embassy", "court", "advisors", "advisers", "affairs", "relations", "solutions", "global",
              "international", "national", "american", "united", "states", "the", "of", "and"}


def _org_name(n):
    """True for a 'lobbyist' entry that is an organization or agency rather than a person ("Justice Department",
    "Smith Strategies LLC")."""
    toks = re.findall(r"[a-z]+", str(n or "").lower())
    return not toks or any(t in _ORG_WORDS for t in toks)


def _other_affiliation(raw, start, end, co_words, ticker):
    """True when the words right around a person's name tie them to an organization that isn't the company."""
    orgs = []
    m = _AFTER_ORG.match(raw[end:end + 140])
    if m and not _EXEC_TITLE.search(m.group(0)):      # "CEO of Google" for Alphabet: an exec title keeps the match
        orgs.append(m.group(1))
    m = _BEFORE_ORG.search(raw[max(0, start - 100):start])
    if m:
        orgs.append(m.group(1))
    own = {w.lower() for w in co_words or ()} | {str(ticker).lower()}
    for org in orgs:
        ws = {w for w in re.findall(r"[a-z0-9]+", org.lower()) if w not in _ORG_SKIP}
        if ws and not ws & own:
            return True
    return False


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
    r = [date, key, src, (text or "")[:400], url or "", tk, [f"{a}|{b}|{c}" for a, b, c in pp], tone, secs, rp, tg.version]
    from congress_signals.post_sort import sort_record
    srt = sort_record(r[:3] + [full or text] + r[4:], getattr(tg, "names", None))
    cand = [c for c in getattr(tg, "last_cands", []) if 0.45 <= c["c"] < 0.75]
    if cand:
        srt["cand"] = cand[:4]
    drops = getattr(tg, "name_drops", None)
    if drops:                                   # owner, Nov 12: names the spaCy check set aside, kept for review
        srt["drops"] = [[n, t, w] for n, t, w in drops[:8]]
    r.append(srt)
    return r


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


# instagram.com/facebook.com paths that are pages or scripts, not accounts (Oct 29: a site's embed script
# instagram.com/embed.js was read as Barrasso's account "@embed.js")
NOT_HANDLES = {"p", "sharer", "share", "sharer.php", "reel", "reels", "tr", "plugins", "explore", "accounts", "about",
               "developer", "legal", "embed", "stories", "tv", "watch", "pages", "groups", "events", "help", "privacy",
               "v", "o1"}
# party, caucus and chamber accounts that members link in their site footers (Oct 29: @houserepublicans was filed as
# Mark Messmer's account). They aren't any one member's.
PARTY_HANDLES = {"houserepublicans", "housegop", "housedemocrats", "housedems", "senategop", "senaterepublicans",
                 "senatedems", "senatedemocrats", "gop", "thedemocrats", "dnc", "rnc", "nrcc", "dccc", "nrsc", "dscc",
                 "whitehouse", "uscapitol", "ushouse", "ussenate", "housedemocraticcaucus"}


def _not_a_handle(v):
    v = (v or "").lower().strip(".")
    return v in NOT_HANDLES or v in PARTY_HANDLES or bool(re.search(r"\.(?:js|css|php|html?|png|jpe?g|svg|gif)$", v))


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
                    if k in ("instagram", "facebook") and _not_a_handle(v):
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


def _last_match(display, m):
    """The member's last name is in the profile name ("Congresswoman Smith", "Rep. Jane Smith")."""
    E = __import__("congress_signals.engine", fromlist=["x"])
    last = E._strip_accents(m.get("last") or "").lower()
    return bool(last) and bool(re.search(r"\b" + re.escape(last) + r"\b", E._strip_accents(display or "").lower()))


def bluesky_accounts(cfg, leg, acc, st, deadline):
    """handle -> bioguide: from members' websites, then a search by name (kept only when the profile's name matches
    the member: their last name for a .gov handle, first and last name plus a bio saying they serve in Congress
    otherwise)."""
    p = _dir(cfg, "bsky_accounts.json")
    ba = json.load(open(p)) if os.path.exists(p) else {"map": {}, "searched": {}}
    dead = ba.setdefault("dead", {})
    wrong = ba.setdefault("wrong", {})
    site = {h.lower() for a in acc.values() for h in (a.get("bsky") or [])}     # linked from the member's own website
    for h in [h for h, t in dead.items() if time.time() - t > BSKY_DEAD_RECHECK_DAYS * 86400]:
        dead.pop(h)                         # look again now and then: a deactivated account can come back
    for b, a in acc.items():
        for h in a.get("bsky") or []:
            if h.lower() not in dead:
                ba["map"].setdefault(h.lower(), b)
    for h in list(ba["map"]):
        if h in dead:
            ba["map"].pop(h)
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
            if h in dead or (wrong.get(h) or {}).get("b") == b or h == "handle.invalid":
                continue
            # Nov 4 (owner, HONA audit): a .gov handle used to be kept without a name check, so searching one
            # member's name kept another member's account (a senator who shares a first name) and filed that
            # account's posts and reposts under the wrong member. The profile's name must match now, .gov or not.
            if (gov and _last_match(a.get("displayName"), m)) or \
                    (_name_match(a.get("displayName"), m) and cue.search(a.get("description") or "")):
                ba["map"].setdefault(h, b)
        time.sleep(0.3)
    _bsky_recheck_map(cfg, ba, leg, site, deadline)
    json.dump(ba, open(p, "w"))
    return ba["map"]


BSKY_MAP_CHECK = 2      # bump to check every account found by search again


def _bsky_recheck_map(cfg, ba, leg, site, deadline):
    """Accounts found by name search earlier (not linked from the member's own website): the profile's name must
    match the member they're filed under. A mismatch is taken off the list for good, and its saved posts move out of
    the posts folder (to social/dropped/bsky) so they stop showing on that member's page and in trade-post links.
    A profile that can't be read now is checked again next run (never dropped on a failed read)."""
    done = ba.setdefault("checked", {})
    wrong = ba.setdefault("wrong", {})
    dropped = []
    for h, b in list(ba["map"].items()):
        if time.time() > deadline:
            break
        if h in site or done.get(h) == BSKY_MAP_CHECK or b not in leg:
            continue
        js = _get(BSKY + "app.bsky.actor.getProfile", params={"actor": h})
        if not js:
            continue
        if _last_match(js.get("displayName"), leg[b]):
            done[h] = BSKY_MAP_CHECK
            continue
        ba["map"].pop(h, None)
        wrong[h] = {"b": b, "name": (js.get("displayName") or "")[:80], "at": time.time()}
        src = os.path.join(_dir(cfg, "posts", "bsky"), re.sub(r"[^a-z0-9.-]", "_", h) + ".jsonl.gz")
        if os.path.exists(src):
            os.replace(src, os.path.join(_dir(cfg, "dropped", "bsky"), os.path.basename(src)))
        # the account is usually another member's own (first and last name match exactly one current member):
        # file it under them; with its saved posts moved out, it's read again in full under the right member
        right = [k for k, m in leg.items() if m.get("current") and k != b and _name_match(js.get("displayName"), m)]
        if len(right) == 1:
            ba["map"][h] = right[0]
            done[h] = BSKY_MAP_CHECK
        dropped.append(f"@{h} (filed under {leg[b].get('name') or b}, profile says {js.get('displayName') or 'no name'}"
                       + (f"; now filed under {leg[right[0]].get('name') or right[0]})" if len(right) == 1 else ")"))
        time.sleep(0.2)
    if dropped:
        _log(f"Bluesky: {len(dropped)} account(s) were filed under the wrong member and were moved off them: "
             + "; ".join(dropped[:8]) + ("…" if len(dropped) > 8 else ""))


# Nov 3: 5 handles failed every run with HTTP 400 and were reported as "couldn't be read": Bluesky answers "Profile not
# found" (normajtorrres = a typo of Norma Torres, repgoodlander, reppeteaguilar) or "Account has been suspended"
# (thomtillis, amyjeanklobuchar: impostor accounts). The members' real accounts (torres/goodlander/aguilar.house.gov,
# klobuchar.senate.gov) are on file and read fine. A handle Bluesky says doesn't exist (or was taken down/deactivated) is
# now dropped from the list, the member is searched by name again if it was their only account, and the handle is
# looked at again after BSKY_DEAD_RECHECK_DAYS. Any other failure (timeouts, 5xx, rate limits) is still reported.
BSKY_DEAD_RECHECK_DAYS = 30
BSKY_GONE = ("Profile not found", "AccountTakedown", "AccountDeactivated", "Unable to resolve handle", "AccountNotFound")


def _bsky_gone(h):
    """The reason Bluesky gives when the account doesn't exist / was taken down / deactivated; None otherwise."""
    try:
        r = requests.get(BSKY + "app.bsky.actor.getProfile", params={"actor": h}, timeout=30, headers=UA)
        if r.status_code != 400:
            return None
        js = r.json()
        why = f"{js.get('error', '')}: {js.get('message', '')}"
        return why if any(g in why for g in BSKY_GONE) else None
    except Exception:
        return None


def _bsky_drop(cfg, hmap, h, why):
    """Takes a nonexistent handle off the list (bsky_accounts.json); re-searches the member if it was their only one."""
    b = hmap.pop(h, None)
    p = _dir(cfg, "bsky_accounts.json")
    try:
        ba = json.load(open(p)) if os.path.exists(p) else {"map": {}, "searched": {}}
        ba.setdefault("dead", {})[h] = time.time()
        ba["map"].pop(h, None)
        if b and b not in ba["map"].values():
            ba.setdefault("searched", {}).pop(b, None)
        json.dump(ba, open(p, "w"))
    except Exception:
        pass
    others = sorted(k for k, v in hmap.items() if v == b)
    print(dt.datetime.now().strftime("%H:%M:%S"), f"Bluesky: @{h} doesn't exist any more ({why}); taken off the list"
          + (f", {b} is still read from @{', @'.join(others)}" if others else f", {b} will be looked up by name"),
          flush=True)


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
    # months of the current year that aren't on disk yet (first run, or a missed month), or not yet fully tagged from
    # the full text under today's tagger (press_stale)
    stale = set(st.get("press_stale", []))
    for m in range(1, today.month + 1):
        k = f"{today.year}-{m:02d}"
        if (k in stale or not os.path.exists(os.path.join(out_dir, f"{k}.jsonl.gz"))) and ("m", k) not in jobs:
            jobs.append(("m", k))
    read_ok = set()
    for kind, k in jobs:
        if time.time() > deadline:
            break
        if kind == "m":
            r = _get(PRESS + f"{k}.jsonl", as_json=False, timeout=180)
            if r is None:
                if _press_month_not_posted_yet(k, today, read_ok):
                    # Congress Press posts a month's file only once it has releases from that month, a day or two
                    # after they come out (Nov 1 2026: 2026-11.jsonl was a 404 while 2026-10 read fine). A missing
                    # file later in the month, or any other kind of failure, is still logged as a failure.
                    _log(f"Press releases: {k}: Congress Press hasn't posted this month's file yet (normal in the "
                         f"first {PRESS_NEW_MONTH_GRACE_DAYS} days of a month; last month read fine this run)")
                else:
                    _log(f"Press releases: {k}: Congress Press download failed")
                continue
            read_ok.add(k)
            lines = r.content.decode("utf-8", "ignore").splitlines()
            # releases already on file keep their tags and sorting (re-tagging and re-sorting ~6,000 releases every
            # run took the daily posts update past its 30-minute limit, so it never saved, Oct 17-21)
            path = os.path.join(out_dir, f"{k}.jsonl.gz")
            old = {}
            for x in (read_jsonl(path) if os.path.exists(path) else []):
                if len(x) > 4 and x[4]:
                    old[(x[4], x[1], str(x[0])[:10])] = x
            # a month not marked stale was last written by this reader under today's tagger: its rows count as full-text
            rows, fresh, left = _press_rows(lines, tg, reuse=old, deadline=deadline, trust_plain=k not in stale)
            write_jsonl(path, rows)
            if left:
                stale.add(k)
                _log(f"Press releases: {k}: {len(rows):,} ({fresh:,} tagged this run; {left:,} kept with older tags "
                     f"until a later run re-tags them)")
            else:
                stale.discard(k)
                _log(f"Press releases: {k}: {len(rows):,} ({fresh:,} new)")
            st["press_stale"] = sorted(stale)
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
                    rows += _press_rows(z.read(nm).decode("utf-8", "ignore").splitlines(), tg)[0]
            write_jsonl(os.path.join(out_dir, f"{k}.jsonl.gz"), rows)
            done.add(k)
            st["press_years"] = sorted(done)
            _save_state(cfg, st)
            _log(f"Press releases: {k}: {len(rows):,}")
    return all(str(y) in done for y in range(START_YEAR, today.year))


PRESS_NEW_MONTH_GRACE_DAYS = 4


def _press_month_not_posted_yet(k, today, read_ok):
    """True only when `k` is the current month, it's the first few days of it, last month's file was read fine in
    this same run, and the server answers a plain 404 for this month's file (not a timeout, block or server error)."""
    if k != today.strftime("%Y-%m") or today.day > PRESS_NEW_MONTH_GRACE_DAYS:
        return False
    if (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m") not in read_ok:
        return False
    try:
        return requests.head(PRESS + f"{k}.jsonl", timeout=30, headers=UA, allow_redirects=True).status_code == 404
    except Exception:
        return False


PRESS_FULL = "f"     # suffix on the tagger version of a Congress Press row tagged from its full text


def _press_rows(lines, tg, reuse=None, deadline=None, trust_plain=False):
    """(rows, number tagged fresh, number left with older tags). `reuse`: {(url, bioguide, date): row already on file},
    kept as it is when it was tagged from the full text under today's tagger. Past `deadline`, a release already on
    file keeps its older row (build_index re-tags it from the stored text meanwhile) and a new one gets a plain row with
    no tags yet ("stale"), so nothing goes missing; the month is finished in a later run."""
    rows, fresh, left = [], 0, 0
    reuse = reuse or {}
    cur = tg.version + PRESS_FULL
    ok_v = (cur, tg.version) if trust_plain else (cur,)
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
        hit = reuse.get((j.get("url"), b, d)) if j.get("url") else None
        if hit is not None and len(hit) > 10 and hit[10] in ok_v:
            rows.append(hit)
            continue
        title, text = j.get("title") or "", j.get("text") or ""
        shown = (title + " — " + text).strip(" —")
        if deadline is not None and time.time() > deadline:
            rows.append(hit if hit is not None else [d, b, "Press release", shown[:400], j.get("url") or "", [], [], 0.0, [],
                                                      "", "stale"])
            left += 1
            continue
        r = rec(d, b, "Press release", shown, j.get("url"), tg, full=title + "\n" + text)
        r[10] = cur
        rows.append(r)
        fresh += 1
    return rows, fresh, left


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
    # Oct 22: accounts went in A-Z order and a run stopped at its time cap, so the same first accounts were read every
    # run and the rest waited. Now the least recently read go first, and a full re-read (new BSKY_READ_VERSION) is kept
    # per account (it used to restart from scratch until one run finished every account, which a capped run never did).
    last = st.setdefault("bsky_last", {})
    acc_v = st.setdefault("bsky_acc_v", {})
    if st.get("bsky_v") == BSKY_READ_VERSION:
        for h in hmap:
            acc_v.setdefault(h, BSKY_READ_VERSION)
    order = sorted(hmap.items(), key=lambda kv: (last.get(kv[0], ""), kv[0]))
    done_n, failed = 0, []
    for h, b in order:
        if time.time() > deadline:
            if len(failed) >= 5:
                _log(f"Bluesky: {len(failed)} account(s) couldn't be read this run (e.g. {', '.join(failed[:5])})")
            _log(f"Bluesky: time cap reached after {done_n} of {len(order)} accounts; "
                 f"the other {len(order) - done_n} go first next run" + (f" (+{total:,} new posts so far)" if total else ""))
            return False
        path = os.path.join(out_dir, re.sub(r"[^a-z0-9.-]", "_", h) + ".jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        if acc_v.get(h) != BSKY_READ_VERSION and old:
            old = []                           # first read under this version: re-read the whole account
        seen = {r[4] for r in old}
        newest = max((r[0] for r in old), default="")
        rows, cursor, read_failed, js = [], None, False, {}
        for _ in range(60):
            if time.time() > deadline + 90:     # one account can't run far past the cap; it's read again next run
                read_failed = True
                break
            js = _get(BSKY + "app.bsky.feed.getAuthorFeed",
                      params={"actor": h, "limit": 100, "filter": "posts_no_replies", **({"cursor": cursor} if cursor else {})})
            if js is None:
                read_failed = True
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
        if read_failed:
            if js is None:
                why = _bsky_gone(h) if not rows else None
                if why:
                    _bsky_drop(cfg, hmap, h, why)
                else:
                    failed.append(h)
            continue        # an unfinished read keeps the file as it is (no gap behind the newest post); next run retries
        if rows or not os.path.exists(path) or acc_v.get(h) != BSKY_READ_VERSION:
            write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
            total += len(rows)
        acc_v[h] = BSKY_READ_VERSION
        last[h] = dt.datetime.utcnow().isoformat()
        done_n += 1
    st["bsky_v"] = BSKY_READ_VERSION
    if len(failed) >= 5 and len(failed) >= 0.1 * max(1, len(order)):
        _log(f"Bluesky: {len(failed)} of {len(order)} account(s) couldn't be read this run (e.g. {', '.join(failed[:5])})")
    if total:
        _log(f"Bluesky: {len(hmap)} accounts, {total:,} new posts ({n_ocr} images read)")
    return True


# Oct 31: the "nothing new for N days" checks flagged Mastodon although the reader worked: the 13 accounts members
# list all answer, and our saved copy matched each server (newest post of any of them: 2026-03-06). collect_mastodon
# now records, per run, whether every account was read and whether our copy already holds each account's newest post,
# so the checks can tell "members aren't posting there" from "the reader is broken" (failed accounts are reported).
MASTO_STATUS = {}


def masto_quiet_ok():
    """True when this run read every Mastodon account and each one's newest post is already saved: a quiet source,
    not a broken one."""
    s = MASTO_STATUS
    return bool(s) and s.get("complete") and not s.get("failed") and s.get("current") == s.get("accounts")


def collect_mastodon(cfg, st, tg, leg, acc, deadline):
    MASTO_STATUS.clear()
    out_dir = _dir(cfg, "posts", "masto")
    handles = {}
    for b, m in leg.items():
        v = (m.get("social") or {}).get("mastodon")
        if v and "@" in v.strip("@"):
            handles[v.strip("@").lower()] = b
    for b, a in acc.items():
        for v in a.get("masto") or []:
            handles.setdefault(v.lower(), b)
    added, dead, checked, current, newest = 0, [], 0, 0, ""
    for h, b in handles.items():
        if time.time() > deadline:
            break
        checked += 1
        user, host = h.split("@", 1)
        a = _get(f"https://{host}/api/v1/accounts/lookup", params={"acct": user}, tries=2)
        if not a or not a.get("id"):
            dead.append(h)
            continue
        path = os.path.join(out_dir, re.sub(r"[^a-z0-9.@-]", "_", h) + ".jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        seen = {r[4] for r in old}
        rows, max_id, page, done = [], None, 0, False
        for _ in range(30):
            js = _get(f"https://{host}/api/v1/accounts/{a['id']}/statuses",
                      params={"limit": 40, "exclude_replies": "true", **({"max_id": max_id} if max_id else {})}, tries=2)
            page += 1
            if js is None and page == 1 and (a.get("statuses_count") or 0) > 0:
                dead.append(h)              # the account answered but its posts didn't load
                break
            if js and page == 1:
                newest = max(newest, (js[0].get("created_at") or "")[:10])
            if not js:
                done = js is not None or page == 1    # an empty page is the end; a failed later page is not
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
                done = True                 # reached a post already saved, or the account's oldest post
                break
        if done and h not in dead:
            current += 1
        if rows:
            write_jsonl(path, sorted(old + rows, key=lambda r: r[0]))
            added += len(rows)
    MASTO_STATUS.update(accounts=len(handles), checked=checked, complete=checked == len(handles), failed=dead,
                        current=current, newest=newest)
    _log(f"Mastodon: {len(handles)} accounts, +{added:,} posts" + (f"; no answer from {len(dead)}: {', '.join(dead[:8])}" if dead else "")
         + (f"; out of time after {checked}" if checked < len(handles) else "") + (f"; newest post of any account {newest}" if newest else ""))


def collect_substack(cfg, st, tg, acc, deadline):
    import xml.etree.ElementTree as ET
    out_dir = _dir(cfg, "posts", "substack")
    added, dead, n, mirrored = 0, [], 0, 0
    subs = sorted({sub for a in acc.values() for sub in (a.get("substack") or [])})
    try:        # the list the Cloudflare Worker reads hourly (Substack refuses GitHub's servers; see _substack_mirror)
        import cloud_store
        if subs:
            cloud_store.put("feeds/substack/list.json", json.dumps(subs), log=lambda m: None)
    except Exception:
        pass
    for b, a in acc.items():
        for sub in a.get("substack") or []:
            if time.time() > deadline:
                break
            n += 1
            r = _get(f"https://{sub}.substack.com/feed", as_json=False, timeout=60, tries=2)
            content = r.content if r is not None else _substack_mirror(sub)
            if content is None:
                dead.append(sub)
                continue
            mirrored += r is None
            path = os.path.join(out_dir, f"{sub}.jsonl.gz")
            old = read_jsonl(path) if os.path.exists(path) else []
            seen = {x[4] for x in old}
            rows = []
            try:
                root = ET.fromstring(content)
            except Exception:
                dead.append(sub)
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
    _log(f"Substack: {n} newsletters, +{added:,} posts" + (f" ({mirrored} read from the Worker's copy)" if mirrored else "")
         + (f"; no answer from {len(dead)}: {', '.join(dead[:8])}" if dead else ""))


def _substack_mirror(sub, max_hours=30):
    """The feed as the Cloudflare Worker last read it (feeds/substack/<sub>.json), if it's recent."""
    try:
        import cloud_store
        raw = cloud_store.get(f"feeds/substack/{sub}.json", log=lambda m: None)
        if not raw:
            return None
        j = json.loads(raw)
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(j["at"].replace("Z", "+00:00"))).total_seconds()
        return j["xml"].encode("utf-8") if age < max_hours * 3600 and j.get("xml") else None
    except Exception:
        return None


META_GRAPH = "https://graph.facebook.com/" + (os.environ.get("META_GRAPH_VERSION") or "v23.0")


def _meta_ig_user(token):
    """The owner's Instagram professional account ID (Business Discovery asks through it): META_IG_USER_ID, or the
    first Instagram account linked to a Facebook Page the token can see."""
    if os.environ.get("META_IG_USER_ID"):
        return os.environ["META_IG_USER_ID"].strip()
    r = requests.get(META_GRAPH + "/me/accounts", params={"fields": "instagram_business_account", "access_token": token}, timeout=30)
    try:
        js = r.json()
    except Exception:
        js = {}
    if r.status_code != 200:
        # Oct 29: Meta answered "API access blocked." (code 200) to every call, /me included; that used to be logged as
        # "can't see an Instagram account", which Data health didn't count. Say what Meta said, as a failure.
        e = js.get("error") or {}
        raise RuntimeError(f"Meta refused the request (HTTP {r.status_code}, code {e.get('code')}: {str(e.get('message') or '')[:120]})")
    for pg in js.get("data") or []:
        if (pg.get("instagram_business_account") or {}).get("id"):
            return pg["instagram_business_account"]["id"]
    return None


IG_STATUS_KEY = "posts/ig_status.json"     # kept in Cloudflare R2: the hourly job doesn't keep its own state
# Owner, Oct 29: no more username guessing. Meta blocked the app's API access that afternoon (lifted after the owner
# verified his phone and email); hourly lookups of made-up usernames can look like scraping. Members with no
# account on file go straight to the Data health check list and the owner sends the handle.
IG_GUESS_PER_RUN = 0                       # Business Discovery calls spent per run guessing missing accounts (off)
IG_EXTRA_PER_RUN = 0                       # calls per run guessing members' second accounts (off)
IG_RECHECK_DAYS = 7                        # an unreadable account is tried again after this many days
IG_GUESS_AGAIN_DAYS = 30                   # a member with no account found is searched again after this many days


def _ig_status():
    try:
        import cloud_store
        raw = cloud_store.get(IG_STATUS_KEY, log=lambda m: None)
        js = json.loads(raw) if raw else {}
        return js if isinstance(js, dict) else {}
    except Exception:
        return {}


def _ig_status_save(igs):
    try:
        import cloud_store
        cloud_store.put(IG_STATUS_KEY, json.dumps(igs), log=lambda m: None)
    except Exception:
        pass


def _ig_plain(x):
    import unicodedata
    x = unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", x)


def _ig_candidates(m):
    """Likely Instagram usernames for a member with none on file (checked against the account's own bio)."""
    last = _ig_plain((m.get("last") or "").split(",")[0])
    firsts = [_ig_plain(m.get("first")), _ig_plain(m.get("nick"))]
    firsts = [f for f in dict.fromkeys(firsts) if f]
    out = []
    if m.get("chamber") == "Senate":
        out += [f"sen{last}", f"senator{last}"] + [f"sen{f}{last}" for f in firsts] + [f"senator{f}{last}" for f in firsts]
    else:
        out += [f"rep{last}"] + [f"rep{f}{last}" for f in firsts] + [f"congressman{last}", f"congresswoman{last}"]
    out += [f"{f}{last}" for f in firsts] + [f"{f}_{last}" for f in firsts] + [f"{f}.{last}" for f in firsts]
    return [c for c in dict.fromkeys(out) if last and 3 <= len(c) <= 30]


def _ig_is_member(info, m):
    """A guessed account counts only when it is plainly the member's: an official .gov website, or their last name
    plus a congressional title in the name or bio."""
    site = str(info.get("website") or "").lower()
    if ".house.gov" in site or ".senate.gov" in site:
        return True
    text = f"{info.get('name') or ''} {info.get('biography') or ''}".lower()
    last = _ig_plain((m.get("last") or "").split(",")[0])
    title = re.search(r"congress|representative|\brep\.?\b|senator|\bsen\.?\b|u\.?s\.? house|u\.?s\.? senate", text)
    if re.search(r"state (?:rep|representative|senator|senate|house)|assembly(?:man|woman|member)?|city council|county", text):
        return False         # a state or local official with the same name
    return bool(last and last in _ig_plain(text) and title)


def ig_unreadable(leg):
    """Current members whose Instagram the app can't read, for Data health (owner, Oct 28: almost every member has
    an Instagram, so each missing one is listed for a manual check). A member with at least one readable account
    is not listed."""
    igs = _ig_status()
    bad, found, ok, tried = igs.get("bad") or {}, igs.get("found") or {}, igs.get("ok") or {}, igs.get("guessed") or {}
    per, all_h = {}, {}
    manual, owner_none = ig_manual(leg, with_none=True)
    for h, b in (igs.get("handles") or {}).items():
        all_h.setdefault(b, set()).add(h)
    for h, x in bad.items():
        per.setdefault(x.get("b"), []).append((h, x.get("why")))
    lines = []
    for b, m in sorted(leg.items(), key=lambda kv: kv[1].get("name") or ""):
        if not m.get("current"):
            continue
        who = f"{m.get('name')} ({m.get('state')}, {m.get('chamber')})"
        if any(x.get("b") == b or b in (x.get("for") or []) for x in ok.values()):
            continue                # read through their own account, or a shared one the owner pointed them at
        if b in per and b in all_h and all_h[b] <= {h for h, _ in per[b]}:     # every account on file is unreadable
            # Oct 29 (probe): Meta answers the same "Cannot find User" (code 110) for a personal account and for a
            # username that doesn't exist, so the line can't say which it is
            why = "; ".join(f"@{h}: Meta can't find it (a personal account, which Meta doesn't let apps read, "
                            "or the username changed)" for h, w in per[b])
            if manual.get(b) and set(manual[b]) <= {h for h, _ in per[b]}:
                # Nov 2: every handle the owner gave is unreadable (an older guessed handle doesn't reopen the ask)
                # Oct 30: the owner already checked these handles by hand, so asking again doesn't help; still listed
                lines.append(f"Instagram: can't read {who}. You confirmed {', '.join('@' + h for h, _ in per[b])}, but Meta "
                             "won't share it with apps (most likely a personal account, not a Business or Creator one). "
                             "Nothing to fix on our side unless they switch it or you find another account of theirs")
                continue
            lines.append(f"Instagram: can't read {who}. {why}. Please check their Instagram and send me the right handle")
        elif b not in all_h and b not in owner_none:   # owner checked: no Instagram, so nothing to ask for
            lines.append(f"Instagram: no account on file for {who}. Please check their Instagram and send me the handle")
    return lines


IG_MANUAL_FILE = "instagram_handles.json"


def ig_manual(leg, with_none=False):
    """Owner-checked Instagram handles: {"Member name or bioguide ID": "handle" or ["handle", ...]} in
    instagram_handles.json at the repo root (owner, Oct 28: read both accounts when a member has an official and a
    personal one). They win over the lists and guesses, and the member leaves the Data health check list once read.
    with_none=True also returns the members the owner checked and found have no Instagram at all ([] in the file)."""
    try:
        raw = json.load(open(IG_MANUAL_FILE))
    except Exception:
        return {}
    by_name = {}
    for b, m in leg.items():
        if m.get("current"):
            for nm in (m.get("name"), f"{m.get('first') or ''} {m.get('last') or ''}", f"{m.get('nick') or ''} {m.get('last') or ''}"):
                if nm and nm.strip():
                    by_name[_ig_plain(nm)] = b
    out, none = {}, set()
    for k, hs in raw.items():
        hs = [hs] if isinstance(hs, str) else hs
        if k.startswith("_") or not isinstance(hs, list):
            continue
        b = k if k in leg else by_name.get(_ig_plain(k))
        if b and not hs:
            none.add(b)             # Nov 2 (owner): an empty list means "checked, this member has no Instagram"
        for h in hs:
            if b and isinstance(h, str) and h.strip("@ "):
                out.setdefault(b, []).append(h.strip("@ ").lower())
    return (out, none) if with_none else out


def ig_shared(leg):
    """Owner-listed shared accounts (instagram_handles.json "_shared", Nov 2): committee or caucus accounts that post
    about several members. {handle: {"name": str, "for": {bioguide, ...}}}; "for" are the members the owner pointed
    at this account (it stands in for their own on Data health while it reads)."""
    try:
        raw = json.load(open(IG_MANUAL_FILE)).get("_shared") or {}
    except Exception:
        return {}
    by_name = {_ig_plain(m.get("name")): b for b, m in leg.items() if m.get("current")}
    by_name.update({_ig_plain(f"{m.get('first') or ''} {m.get('last') or ''}"): b for b, m in leg.items() if m.get("current")})
    out = {}
    for h, x in raw.items():
        x = x if isinstance(x, dict) else {"name": str(x)}
        fb = {k if k in leg else by_name.get(_ig_plain(k)) for k in x.get("for") or []}
        out[h.strip("@ ").lower()] = {"name": x.get("name") or h, "for": {b for b in fb if b}}
    return out


_TITLES = r"(?:rep\.?|representative|congressman|congresswoman|chairman|chairwoman|chair|ranking member|sen\.?|senator|leader|whip|speaker)"


def ig_mentions(text, leg, handles):
    """Current members a shared account's post is about: their Instagram handle (@...), their full name, or a title
    with a last name no other current member has ("Chairman Bost"). A bare common last name is not enough."""
    t = " " + (text or "") + " "
    low = t.lower()
    out = set()
    for h, b in handles.items():
        # Nov 2 (probe): Business Discovery captions come back with the "@" stripped ("repvanorden's"), so the
        # handle counts as a whole word too; only handles long enough not to be an ordinary word
        if re.search(r"@" + re.escape(h) + r"(?![a-z0-9_])", low) or \
                (len(h) >= 7 and re.search(r"(?<![a-z0-9._@])" + re.escape(h) + r"(?![a-z0-9_])", low)):
            out.add(b)
    lasts = {}
    for b, m in leg.items():
        if m.get("current") and m.get("last"):
            lasts.setdefault(_ig_plain(m["last"]), []).append(b)
    for b, m in leg.items():
        if not m.get("current"):
            continue
        last = m.get("last") or ""
        names = {m.get("name") or ""} | {f"{f} {last}" for f in (m.get("first"), m.get("nick")) if f}
        if any(n.strip() and re.search(r"(?<![a-z])" + re.escape(n.lower()) + r"(?![a-z])", low) for n in names):
            out.add(b)
            continue
        if last and len(lasts.get(_ig_plain(last), [])) == 1 and \
                re.search(_TITLES + r"\s+" + re.escape(last.lower()) + r"(?![a-z])", low):
            out.add(b)
    return out


def collect_instagram(cfg, st, tg, leg, acc, deadline, per_run=40, rotate=None):
    """Members' public Instagram posts through Meta's Instagram Business Discovery (free; owner's Meta app + token,
    Oct 21). Works for members whose accounts are Business or Creator accounts (most official ones are). Meta allows
    about 200 calls an hour, so each run reads `per_run` accounts, the least recently read first.
    Oct 28 (owner): every member should have one, so each run also spends a few calls looking for members with no
    account on file (likely usernames, accepted only when the bio shows it's them), and the members it can't read
    are kept in R2 (posts/ig_status.json) for the Data health list."""
    token = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
    if not token:
        _log("Instagram: waiting for the Meta setup (META_ACCESS_TOKEN not added yet)")
        return
    igs = _ig_status()
    bad, found, ok = igs.setdefault("bad", {}), igs.setdefault("found", {}), igs.setdefault("ok", {})
    guessed, none = igs.setdefault("guessed", {}), igs.setdefault("none", {})
    handles = {}
    for b, m in leg.items():
        v = (m.get("social") or {}).get("instagram")
        if m.get("current") and isinstance(v, str) and v and not _not_a_handle(v.strip("@")):
            handles[v.strip("@").lower()] = b
    # Oct 29: a handle linked from several members' websites is a shared account (a party or caucus account in the
    # page footer, e.g. @houserepublicans on Mark Messmer's site), not any one member's: don't file it as theirs.
    site_ig = {}
    for b, a in acc.items():
        for v in a.get("instagram") or []:
            site_ig.setdefault(v.strip("@").lower(), set()).add(b)
    for b, a in acc.items():
        for v in a.get("instagram") or []:
            v = v.strip("@").lower()
            if not _not_a_handle(v) and len(site_ig[v]) == 1:
                handles.setdefault(v, b)
    for b, h in found.items():
        if leg.get(b, {}).get("current") and not _not_a_handle(h):
            handles.setdefault(h, b)
    extra = igs.setdefault("extra", {})          # second accounts found for members (owner: read both, Oct 28)
    for b, hs in extra.items():
        if leg.get(b, {}).get("current"):
            for h in hs:
                if not _not_a_handle(h):
                    handles.setdefault(h, b)
    for b, hs in ig_manual(leg).items():         # handles the owner checked by hand (instagram_handles.json)
        for h in hs:
            handles[h] = b
    try:
        ig = _meta_ig_user(token)
    except Exception as e:
        _log(f"Instagram: failed, nothing read this run. {e}. If it says 'API access blocked', Meta has restricted the "
             "Meta app: open developers.facebook.com, select the app and check its alerts / App Review status")
        return
    if not ig:
        _log("Instagram: failed, the Meta token can't see an Instagram professional account linked to a Facebook Page; "
             "set META_IG_USER_ID")
        return
    now = time.time()
    seen_at = st.setdefault("ig_read", {})
    skip = st.setdefault("ig_skip", {})          # not a Business/Creator account (or gone): look again later
    for h, x in bad.items():
        if now - x.get("at", 0) < IG_RECHECK_DAYS * 86400:
            skip[h] = max(skip.get(h, 0), x.get("at", 0))
    ok_h = sorted(h for h in handles if now - skip.get(h, 0) > IG_RECHECK_DAYS * 86400)
    if rotate is not None and ok_h:       # the hourly job doesn't keep its state: take the next slice each hour
        k = (rotate * per_run) % len(ok_h)
        todo = (ok_h[k:] + ok_h[:k])[:per_run]
    else:
        todo = sorted(ok_h, key=lambda h: seen_at.get(h, 0))[:per_run]
    out_dir = _dir(cfg, "posts", "ig")
    added, n, failed, stop = 0, 0, [], None

    def ask(h, fields):
        """One Business Discovery call: (data, None) / (None, 'personal'|'missing'|'limit'|'token'|'app'|'other:..')."""
        try:
            r = requests.get(f"{META_GRAPH}/{ig}", params={"fields": f"business_discovery.username({h}){{{fields}}}",
                                                           "access_token": token}, timeout=30)
            js = r.json()
        except Exception as e:
            return None, f"other:{type(e).__name__}"
        err = js.get("error") or {}
        if not err:
            return js.get("business_discovery") or {}, None
        code, msg = err.get("code"), str(err.get("message") or "")
        if code in (10, 200) and err.get("type") == "OAuthException":
            # Oct 28: the token itself was valid (it read the owner's own account); Meta refused Business
            # Discovery for the app. That needs a change in the Meta app (permissions / app mode), not a new token.
            return None, "app:" + msg[:120]
        if code in (190, 102):
            return None, "token:" + msg[:120]
        if code in (4, 17, 32, 613):
            return None, "limit"
        if code == 110 or "business" in msg.lower() or "creator" in msg.lower():
            return None, "personal"
        if code == 100 or err.get("error_subcode") in (2207013,):
            return None, "missing"           # "Invalid user id": no such username (renamed or deleted)
        return None, f"other:{code}"

    for h in todo:
        if time.time() > deadline:
            break
        js, why = ask(h, "media.limit(25){caption,timestamp,permalink,media_type}")
        if why and why.split(":")[0] in ("app", "token", "limit"):
            stop = why
            break
        if why in ("personal", "missing"):
            skip[h] = time.time()
            bad[h] = {"b": handles[h], "why": why, "at": time.time()}
            ok.pop(h, None)
            continue
        if why:
            failed.append(f"{h} ({why.split(':', 1)[-1]})")
            continue
        n += 1
        seen_at[h] = time.time()
        bad.pop(h, None)
        ok[h] = {"b": handles[h], "at": time.time()}
        media = (js.get("media") or {}).get("data") or []
        path = os.path.join(out_dir, re.sub(r"[^a-z0-9._-]", "_", h) + ".jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        have = {x[4] for x in old}
        rows = []
        for mm in media:
            url = mm.get("permalink") or ""
            d = pd.to_datetime(mm.get("timestamp"), errors="coerce", utc=True)
            if not url or url in have or pd.isna(d):
                continue
            cap = re.sub(r"\s+", " ", mm.get("caption") or "").strip()
            if not cap:
                continue
            rows.append(rec(d.strftime("%Y-%m-%d"), handles[h], "Instagram", cap, url, tg))
        if rows or not os.path.exists(path):
            write_jsonl(path, sorted(old + rows, key=lambda x: x[0]))
        added += len(rows)

    # Nov 2 (owner): shared accounts (a committee's) post about several members; each post is filed under every
    # member it names, not under one owner. One call per account per run.
    shared = ig_shared(leg)
    for h, sx in shared.items():
        if stop or time.time() > deadline:
            break
        js, why = ask(h, "media.limit(25){caption,timestamp,permalink,media_type}")
        if why and why.split(":")[0] in ("app", "token", "limit"):
            stop = why
            break
        if why:
            failed.append(f"{h} ({why.split(':', 1)[-1]})")
            bad[h] = {"b": None, "shared": True, "why": why, "at": time.time()}
            ok.pop(h, None)
            continue
        bad.pop(h, None)
        ok[h] = {"b": None, "shared": True, "for": sorted(sx["for"]), "at": time.time()}
        path = os.path.join(out_dir, "_shared_" + re.sub(r"[^a-z0-9._-]", "_", h) + ".jsonl.gz")
        old = read_jsonl(path) if os.path.exists(path) else []
        have = {(x[4], x[1]) for x in old}
        rows, tagged = [], 0
        for mm in (js.get("media") or {}).get("data") or []:
            url = mm.get("permalink") or ""
            d = pd.to_datetime(mm.get("timestamp"), errors="coerce", utc=True)
            cap = re.sub(r"\s+", " ", mm.get("caption") or "").strip()
            if not url or not cap or pd.isna(d):
                continue
            who = ig_mentions(cap, leg, handles)
            tagged += bool(who)
            for b in sorted(who):
                if (url, b) not in have:
                    rows.append(rec(d.strftime("%Y-%m-%d"), b, "Instagram", f"{sx['name']}: {cap}", url, tg))
        if rows or not os.path.exists(path):
            write_jsonl(path, sorted(old + rows, key=lambda x: x[0]))
        added += len(rows)
        _log(f"Instagram: shared account @{h} ({sx['name']}): {tagged} recent posts name a member, +{len(rows)} new")

    # members with no account on file: try likely usernames, a few calls a run, accept only a clear match
    newly, gone_none, calls = [], 0, 0
    have_b = set(handles.values())
    for b in [b for b in none if b in have_b]:      # an account turned up (a list, or the owner's file)
        none.pop(b)
    owner_none = ig_manual(leg, with_none=True)[1]      # the owner checked: no Instagram, so don't guess usernames
    missing_b = [b for b, m in leg.items() if m.get("current") and b not in have_b and b not in owner_none]
    for b in sorted(missing_b, key=lambda b: guessed.get(b, {}).get("at", 0)):
        if stop or calls >= IG_GUESS_PER_RUN or time.time() > deadline:
            break
        g = guessed.setdefault(b, {"c": [], "at": 0})
        if b in none and now - none[b] < IG_GUESS_AGAIN_DAYS * 86400:
            continue
        if b in none:                       # searched before and found nothing: start over after a month
            g["c"] = []
            none.pop(b)
        left = [c for c in _ig_candidates(leg[b]) if c not in g["c"]]
        for c in left:
            if calls >= IG_GUESS_PER_RUN:
                break
            calls += 1
            info, why = ask(c, "username,name,biography,website")
            if why and why.split(":")[0] in ("app", "token", "limit"):
                stop = why
                break
            g["c"].append(c)
            g["at"] = time.time()
            if info and _ig_is_member(info, leg[b]):
                found[b] = c
                none.pop(b, None)
                newly.append(f"{leg[b].get('name')} @{c}")
                break
        else:
            if b not in found and not [c for c in _ig_candidates(leg[b]) if c not in g["c"]]:
                none[b] = time.time()      # every likely username tried: list the member for a manual check
                gone_none += 1
    # owner, Oct 28: "always add both if they are more active on it". Members who already have an account: try their
    # other likely usernames once a month (official vs personal/campaign), same bio check; every match is read too.
    ecalls, newly_x = 0, []
    have_h = set(handles)
    for b in sorted(have_b, key=lambda b: guessed.get(b, {}).get("xat", 0)):
        if stop or ecalls >= IG_EXTRA_PER_RUN or time.time() > deadline:
            break
        if not leg.get(b, {}).get("current"):
            continue
        g = guessed.setdefault(b, {"c": [], "at": 0})
        if g.get("xdone") and now - g["xdone"] < IG_GUESS_AGAIN_DAYS * 86400:
            continue
        if g.get("xdone"):
            g["x"], g["xdone"] = [], 0
        left = [c for c in _ig_candidates(leg[b]) if c not in have_h and c not in g.setdefault("x", [])]
        for c in left:
            if ecalls >= IG_EXTRA_PER_RUN:
                break
            ecalls += 1
            info, why = ask(c, "username,name,biography,website")
            if why and why.split(":")[0] in ("app", "token", "limit"):
                stop = why
                break
            g["x"].append(c)
            g["xat"] = time.time()
            if info and _ig_is_member(info, leg[b]):
                extra.setdefault(b, []).append(c)
                handles[c] = b
                have_h.add(c)
                newly_x.append(f"{leg[b].get('name')} @{c}")
        else:
            g["xdone"] = time.time()
    if newly_x:
        newly += [x + " (second account)" for x in newly_x]
    for h in [h for h in bad if h not in handles]:     # dropped from the lists (e.g. not a real account handle)
        bad.pop(h)
    igs["handles"] = {h: b for h, b in handles.items() if leg.get(b, {}).get("current")}
    igs["at"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    _ig_status_save(igs)
    if stop and stop.startswith("app"):
        _log(f"Instagram: Meta refused Business Discovery for this app ({stop[4:]}); the token works, but the Meta app "
             "needs Business Discovery access (instagram_manage_insights and ads_read permissions, app published)")
        return
    if stop and stop.startswith("token"):
        _log(f"Instagram: the Meta access token was refused ({stop[6:]}); it may have expired")
        return
    if stop == "limit":
        _log("Instagram: Meta's hourly limit reached; the next run continues")
    cur_b = {b for b, m in leg.items() if m.get("current")}
    _log(f"Instagram: {n} of {len(handles)} member accounts read this run, +{added:,} posts"
         + (f"; found accounts for {', '.join(newly)}" if newly else "")
         + f"; {len(cur_b - set(handles.values()) - set(found))} members still have no account on file"
         + f" ({sum(1 for b in none if b in cur_b)} searched with no match)"
         + (f"; {sum(1 for x in bad.values() if x.get('b') in cur_b)} accounts can't be read" if bad else "")
         + (f"; failed for {len(failed)}: {', '.join(failed[:6])}" if failed else ""))


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
def build_index(cfg, tg=None, sort_until=None, hard_until=None):
    """social/index.pkl: per member, posts for the member page (every post naming a company or person, plus the
    latest 120 others) and, for the signals, dated company mentions with tone and dated industry mentions.
    Posts not yet sorted (post_sort: type, who it's about, tone toward each, insider hints, meetings) are sorted
    here until `sort_until` (a time; the rest wait for the next run); each member's post profile is summed up."""
    from congress_signals.post_sort import sort_record, SORT_VERSION
    root = _dir(cfg, "posts")
    pages, mentions, sectors, crowd = {}, {}, {}, {}
    profiles = {}
    recent = {}          # each member's posts of the last ~15 months that name a company, person, industry or agency
    recent_from = (dt.date.today() - dt.timedelta(days=450)).isoformat()
    names = getattr(tg, "names", None)
    n_sorted = n_unsorted = n_retag_left = 0
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
                cands_now = None
                stale_tag = tg is not None and (len(r) < 11 or r[10] not in (tg.version, tg.version + PRESS_FULL))
                if stale_tag and hard_until is not None and time.time() >= hard_until:
                    n_retag_left += 1           # past the hard stop: keeps its older tags; the next run re-tags it
                    stale_tag = False
                if stale_tag:  # re-tag, once
                    tk2, pp2, secs2, tone2 = tg.tag(r[3])
                    cands_now = [c for c in tg.last_cands if 0.45 <= c["c"] < 0.75]
                    r[:] = (r + [""] * 10)[:10]
                    r[5], r[6], r[7], r[8] = tk2, [f"{a}|{b}|{c}" for a, b, c in pp2], tone2, secs2
                    r.append(tg.version)
                    redone = True
                srt = r[11] if len(r) > 11 and isinstance(r[11], dict) and r[11].get("v") == SORT_VERSION else None
                # the last ~15 months are always sorted (trade-post links, alerts); older ones while time allows
                # hard_until: even recent ones stop there, so the run finishes and saves (the rest wait for the next run;
                # Oct 21: a 4-day backlog of unsorted posts kept the daily posts update past its 30-minute limit)
                if srt is None and (sort_until is None or time.time() < sort_until
                                    or (str(r[0])[:10] >= recent_from and (hard_until is None or time.time() < hard_until))):
                    srt = sort_record(r, names)
                    if cands_now:
                        srt["cand"] = cands_now[:4]
                    r[:] = (r + [""] * 11)[:11] + [srt]
                    redone = True
                    n_sorted += 1
                elif srt is None:
                    n_unsorted += 1
                d, key, s, text, url, tk, pp, tone, secs, rp = (r + [""] * 10)[:10]
                if not d or not isinstance(key, str):
                    continue
                n += 1
                lst = pages.setdefault(key, [[], []])
                item = [d, s + (f" (repost of {rp})" if rp else ""), (text or "")[:300], tk, tone, url, ";".join(pp)]
                if str(d) >= recent_from and isinstance(key, str):
                    srt = srt or {}
                    ag = [x["n"] for x in srt.get("tg", []) if x["k"] == "agency"]
                    if tk or pp or secs or ag:
                        recent.setdefault(key, []).append({
                            "d": str(d)[:10], "u": url, "x": (text or "")[:220], "src": s, "t": list(tk or []),
                            "p": [q.split("|")[0] for q in (pp or [])], "s": list(secs or []), "ag": ag,
                            "ty": srt.get("ty") or [], "ins": srt.get("ins") or "", "meet": srt.get("meet") or [],
                            "ins_on": srt.get("ins_on") or [], "insw": srt.get("insw") or "",
                            "hit": [x["n"] for x in srt.get("tg", []) if x["k"] == "industry" and x.get("hit")],
                            "cen": [x["n"] for x in srt.get("tg", []) if x["k"] == "industry" and x.get("cen")],
                            "iw": {x["n"]: x.get("w") or [] for x in srt.get("tg", []) if x["k"] == "industry"},
                            "lob": _lobbyist_only(pp, text, names),
                            "rp": bool(rp),
                            "st": {(x.get("t") if x["k"] == "co" else x.get("n")): x.get("s") for x in srt.get("tg", [])
                                   if x.get("s") is not None}})
                if srt:      # what kind of post, insider hint, meeting, and tone toward each company or person
                    item += [srt.get("ty") or [], srt.get("ins") or "", srt.get("meet") or [],
                             [[x.get("n") if x["k"] in ("person", "lobbyist") else x.get("t"), x.get("s")] for x in srt.get("tg", [])
                              if x["k"] in ("co", "person", "lobbyist") and x.get("s") is not None]]
                    pf = profiles.setdefault(key, {"n": 0, "types": {}, "for": {}, "against": {}, "ins": 0, "meet": {},
                                                   "agencies": {}, "industries": {}})
                    pf["n"] += 1
                    for ty in srt.get("ty") or []:
                        pf["types"][ty] = pf["types"].get(ty, 0) + 1
                    pf["ins"] += bool(srt.get("ins"))
                    for w in srt.get("meet") or []:
                        pf["meet"][w] = pf["meet"].get(w, 0) + 1
                    for x in srt.get("tg", []):
                        if x["k"] == "agency":
                            pf["agencies"][x["n"]] = pf["agencies"].get(x["n"], 0) + 1
                        elif x["k"] == "industry":
                            pf["industries"][x["n"]] = pf["industries"].get(x["n"], 0) + 1
                        elif (x.get("s") or 0) >= 0.3:
                            k_ = x.get("n") if x["k"] in ("person", "lobbyist") else x.get("t")
                            pf["for"][k_] = pf["for"].get(k_, 0) + 1
                        elif (x.get("s") or 0) <= -0.3:
                            k_ = x.get("n") if x["k"] in ("person", "lobbyist") else x.get("t")
                            pf["against"][k_] = pf["against"].get(k_, 0) + 1
                j = 0 if (tk or pp) else 1
                lst[j].append(item)
                if len(lst[j]) > (1200 if j == 0 else 400):        # keep memory flat: newest 600 / 120
                    lst[j] = sorted(lst[j], key=lambda x: x[0])[-(600 if j == 0 else 120):]
                for t in tk:
                    mentions.setdefault(key, {}).setdefault(t, []).append((d, tone, bool(rp)))
                    crowd.setdefault(t, []).append((d, key, _sig_of(text)))
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
    top = lambda d, k=8: dict(sorted(d.items(), key=lambda kv: -kv[1])[:k])
    profiles = {k: {"n": v["n"], "types": top(v["types"], 10), "for": top(v["for"]), "against": top(v["against"]),
                    "ins": v["ins"], "meet": top(v["meet"]), "agencies": top(v["agencies"], 6),
                    "industries": top(v["industries"], 6)} for k, v in profiles.items()}
    idx = {"pages": pages, "profiles": profiles, "recent_posts": {k: sorted(v, key=lambda x: x["d"]) for k, v in recent.items()},
           "mentions": {k: {t: sorted(v) for t, v in m.items()} for k, m in mentions.items()},
           "sectors": {k: {s: to_np(v) for s, v in m.items()} for k, m in sectors.items()},
           "crowd": {t: sorted(v) for t, v in crowd.items()}, "x_counts": xc,
           "built": dt.datetime.utcnow().isoformat(), "posts": n, "unsorted": n_unsorted + n_retag_left}
    pd.to_pickle(idx, _dir(cfg, "index.pkl"))
    _log(f"Posts index: {n:,} posts from {len(pages):,} members and offices; "
         f"{sum(len(v) for m in mentions.values() for v in m.values()):,} company mentions; "
         f"sorted {n_sorted:,} posts this run" + (f", {n_unsorted:,} still to sort (next run)" if n_unsorted else "")
         + (f", {n_retag_left:,} still to re-tag (next run)" if n_retag_left else ""))
    return idx


AGENCY_SECTORS = {"FDA": ["Healthcare"], "HHS": ["Healthcare"], "Pentagon": ["Industrials"], "NASA": ["Industrials"],
                  "Energy Dept": ["Energy", "Utilities"], "FERC": ["Energy", "Utilities"], "EPA": ["Energy", "Utilities", "Basic Materials"],
                  "FCC": ["Communication Services", "Technology"], "SEC": ["Financial Services"], "Fed": ["Financial Services"],
                  "CFPB": ["Financial Services"], "Treasury": ["Financial Services"], "USDA": ["Consumer Defensive"],
                  "FAA": ["Industrials"], "DOT": ["Industrials", "Consumer Cyclical"], "NHTSA": ["Consumer Cyclical"],
                  "FTC": ["Technology", "Communication Services", "Consumer Cyclical"], "Commerce Dept": ["Technology", "Industrials"],
                  "DHS": ["Industrials", "Technology"], "VA": ["Healthcare"], "Justice Dept": []}


def _lobbyist_only(pp, text, names):
    """Companies a post is tagged with only because it names someone with the same name as one of their lobbyists
    (owner, Nov 4: a release quoting an author named John Green was tagged with all 57 companies a lobbyist of that
    name works for). {ticker: [name, how many companies that name lobbies for in this post]}; the company itself
    named in the text, or the person tagged as an executive, keeps it a normal company mention."""
    lob, other = {}, set()
    for q in pp or []:
        nm, t, role = (str(q).split("|") + ["", "", ""])[:3]
        (lob.setdefault(nm, []).append(t) if role == "lobbyist" else other.add(t))
    low = " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "
    out = {}
    for nm, ts in lob.items():
        for t in ts:
            words = [w for w in ((names or {}).get(t) or []) if len(w) > 2]
            if t in other or (words and (" " + " ".join(words) + " ") in low):
                continue
            out[t] = [nm, len(set(ts))]
    return out


def trade_post_links(trades, idx, before_days=90):
    """Posts by the same member that line up with a trade (owner, Oct 16: "if Gottheimer posts hinting at a boost
    in an industry, then trades a company in that industry, flag it and tell me to check the post").
    trades: rows with bioguide, ticker, company, sector, tx_type, trade_date, filed_date.
    A post counts when it is dated from `before_days` before the trade up to the filing date and:
      1. names the company or one of its people, or
      2. names the trade's industry (or an agency over it) with a hint of non-public information, or
      3. names the industry the way a boost (buy) or a hit (sell) would read: money, a bill, a visit or meeting, or
         praise and a positive tone for a purchase; a letter, criticism or a negative tone for a sale (30 days before).
    -> {row position: [{"d", "u", "x", "src", "why"}...]} (up to 3 posts per trade, strongest first)."""
    from congress_signals.post_sort import BOOST_TYPES, HIT_TYPES
    rp = (idx or {}).get("recent_posts") or {}
    out = {}
    for i, r in enumerate(trades):
        posts = rp.get(r.get("bioguide")) if isinstance(r.get("bioguide"), str) else None
        if not posts:
            continue
        td = pd.Timestamp(r.get("trade_date") if pd.notna(r.get("trade_date")) else r.get("filed_date"))
        fd = pd.Timestamp(r.get("filed_date"))
        lo, lo30 = (td - pd.Timedelta(days=before_days)).strftime("%Y-%m-%d"), (td - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        hi = max(td, fd).strftime("%Y-%m-%d")
        t, sec, buy = r.get("ticker"), r.get("sector"), r.get("tx_type") == "buy"
        found = []
        for p in posts:
            if not (lo <= p["d"] <= hi):
                continue
            secs = set(p.get("s") or []) | {x for a in p.get("ag") or [] for x in AGENCY_SECTORS.get(a, [])}
            when = "before the trade" if p["d"] <= td.strftime("%Y-%m-%d") else "after the trade, before it was disclosed"
            if t in (p.get("lob") or {}):
                # only a lobbyist's name matched: the owner checks whether it's the same person (never flags alone)
                nm, n_ = p["lob"][t]
                found.append((2, p, f"named {nm}, who lobbies for {r.get('company') or t}, {when}: check it's the same person"
                              + (f" (that name matches lobbyists for {n_} companies in this post, probably a common name)" if n_ >= 5 else ""), "lob"))
            elif t in (p.get("t") or []):
                tone = p.get("st", {}).get(t)
                verb = f"met with {', '.join(p['meet'][:2])} of" if p.get("meet") else "posted about"
                found.append((3, p, f"{verb} {r.get('company') or t} {when}" +
                              (f" ({'positive' if tone >= 0.3 else 'negative' if tone <= -0.3 else 'neutral'} tone)" if tone is not None else ""), "co"))
            elif sec and sec in secs and p.get("ins") and (not p.get("ins_on") or sec in p["ins_on"] or t in p["ins_on"]
                                                         or any(sec in AGENCY_SECTORS.get(a, []) for a in p["ins_on"])) \
                    and (sec not in (p.get("iw") or {}) or industry_fits(t, p["iw"][sec])):
                # strong wording anywhere; timing wording ("in the coming weeks") only with this company, its industry
                # or an agency over it named within 2 sentences (owner, Nov 4)
                found.append((3, p, f"hinted at something not yet public about {sec.lower()} (\"{p['ins']}\") {when}", "hint"))
            elif sec and sec in secs and (p.get("insw") or p.get("ins")):
                # timing wording that isn't near the company or industry: kept as context, never flags alone
                found.append((2, p, f"used timing words (\"{p.get('insw') or p['ins']}\") in a post that mentions {sec.lower()}, "
                                    f"but not near them, {when}", "hintw"))
            elif sec and sec in secs and sec in (p.get("cen") or []) and p["d"] >= lo30 and not p.get("rp"):
                # Nov 4 (owner, HONA audit): a repost isn't the member's own words, and "against" needs both
                # oversight/criticism wording in a sentence that names the industry and a negative tone toward it
                # (a +0.67 hearing release and a funding release were read as against industrials)
                tone = p.get("st", {}).get(sec)
                ty = set(p.get("ty") or [])
                if buy and (ty & BOOST_TYPES) and (tone is None or tone >= 0.2):
                    found.append((2, p, f"posted a {sec.lower()} boost ({', '.join(sorted(ty & BOOST_TYPES))}) {when}", "ind"))
                elif not buy and sec in (p.get("hit") or []) and tone is not None and tone <= -0.2:
                    found.append((2, p, f"posted against {sec.lower()} {when}", "ind"))
        if found:
            found.sort(key=lambda z: (-z[0], z[1]["d"]), reverse=False)
            out[i] = [{"d": p["d"], "u": p.get("u"), "x": p.get("x"), "src": p.get("src"),
                       "why": w + (" (a repost)" if p.get("rp") else ""), "lvl": lv, "kind": kd}
                      for lv, p, w, kd in found[:3]]
    return out


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
    f_post_other_named: another member named the company in their own post (not a repost) in the 30 days before the
       trade (owner, Nov 5: the "trade after another member's post" alert, off until this passes the weekly check).
    Only posts dated before the trade (or the filing, for the crowd signal) count."""
    for c in ("f_post_named_before", "f_post_sector_burst", "f_post_tone_clash", "f_post_crowd", "f_post_other_named"):
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
    f1, f2, f3, f4, f5 = (np.zeros(len(tx)) for _ in range(5))
    by_t = {}                     # ticker -> (dates, members) of members' own posts naming it, by date
    for mb, m_ in men.items():
        if isinstance(mb, str) and re.fullmatch(r"[A-Z]\d{6}", mb):
            for t_, lst in m_.items():
                by_t.setdefault(t_, []).extend((str(x[0])[:10], mb) for x in lst if not x[2])
    by_t = {t_: (np.array([pd.Timestamp(x[0]) for x in sorted(v)], dtype="datetime64[ns]"), [x[1] for x in sorted(v)])
            for t_, v in by_t.items()}
    notes = [[] for _ in range(len(tx))]
    from congress_signals.post_sort import crowd_count
    crowd_np = {t: (np.array([pd.Timestamp(x[0]) for x in v], dtype="datetime64[ns]"), list(v)) for t, v in crowd.items()}
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
        O = by_t.get(t)
        if O is not None:
            x = np.datetime64(d)
            a, z = np.searchsorted(O[0], x - np.timedelta64(30, "D")), np.searchsorted(O[0], x)
            if any(w_ != b for w_ in O[1][a:z]):
                f5[i] = 1.0
        C = crowd_np.get(t)
        if C is not None and pd.notna(f):
            x = np.datetime64(f)
            a, z = np.searchsorted(C[0], x - np.timedelta64(14, "D")), np.searchsorted(C[0], x)
            n_who = crowd_count(C[1][a:z], skip=lambda k: k.startswith("c:"))     # copies of one release count once
            if n_who >= 3:
                f4[i] = 1.0
                notes[i].append(f"{n_who} members named the company in posts in the 2 weeks before the filing")
    tx["f_post_named_before"], tx["f_post_sector_burst"], tx["f_post_tone_clash"], tx["f_post_crowd"] = f1, f2, f3, f4
    tx["f_post_other_named"] = f5
    tx["post_note"] = ["; ".join(n) for n in notes]
    return tx


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
BIG_BACKLOG = 150_000       # unsorted posts above this need the history runs (a daily run sorts ~11k)


def update_all(cfg, minutes, history=True):
    t0 = time.time()
    try:
        os.remove(_dir(cfg, "needs_history.txt"))
    except OSError:
        pass
    deadline = t0 + minutes * 60
    st = _state(cfg)
    tg = Tagger(cfg)
    if st.get("tagger_v") != TAGGER_VERSION:      # tagging rules changed: re-tag this year's press releases from full text
        # Nov 3: this used to delete this year's files and download them again. The daily run then spent its whole
        # 12 minutes re-tagging one month (Oct, 2,535 releases), Mastodon, Substack, YouTube and Instagram were skipped,
        # and Jan-Sep were missing from the posts index until someone re-read them ("shrank from 24 to 14"). The files
        # now stay; each month is re-tagged in place from the full text as time allows (collect_press, press_stale).
        yr = str(dt.date.today().year) + "-"
        st["press_stale"] = sorted(set(st.get("press_stale", [])) |
                                   {f[:7] for f in os.listdir(_dir(cfg, "posts", "press")) if f.startswith(yr)})
        st["tagger_v"] = TAGGER_VERSION
    st["tagger"] = tg.version
    leg = legislators(cfg, st)
    acc = discover_accounts(cfg, leg, st, min(deadline, time.time() + minutes * 60 * 0.15))
    hmap = bluesky_accounts(cfg, leg, acc, st, min(deadline, time.time() + minutes * 60 * 0.1))
    # fresh posts first (small), then the history backfills
    # each source gets its turn; one that the time limit cut out is logged (it shows on Data health), never dropped quietly
    # Oct 22: Bluesky used the whole 12 minutes of every daily run, so Mastodon, Substack, YouTube and Instagram were
    # skipped every day (and the members' own sites got no time at all). Each source now has its own share of the time,
    # like the hourly job; Bluesky continues where it stopped (least recently read accounts first).
    srcs = (("Congress Press releases", 0.15, lambda c: collect_press(cfg, st, tg, c, years=False)),
            ("Bluesky", 0.30, lambda c: collect_bluesky(cfg, st, tg, hmap, c)),
            ("Mastodon", 0.15, lambda c: collect_mastodon(cfg, st, tg, leg, acc, c)),
            ("Substack", 0.10, lambda c: collect_substack(cfg, st, tg, acc, c)),
            ("YouTube", 0.15, lambda c: collect_youtube(cfg, st, tg, leg, acc, c)),
            ("Instagram", 0.15, lambda c: collect_instagram(cfg, st, tg, leg, acc, c)))
    left_share = sum(f for _, f, _ in srcs)
    for name, frac, fn in srcs:
        now = time.time()
        if now > deadline:
            _log(f"Social: {name} skipped: the {minutes:g}-minute time limit ran out before it")
            left_share -= frac
            continue
        # this source's share of the time still left (a source that finishes early leaves its time to the rest)
        cap = min(deadline, now + max(60.0, (deadline - now) * frac / max(left_share, 1e-6)))
        left_share -= frac
        fn(cap)
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
        collect_people(cfg, st, max(deadline, time.time() + 120), per_run=1)
        try:        # every current member's newest press releases, from their own sites
            import congress_signals.press_backfill as PB
            # its own 5 minutes (it used to be capped by the sources' deadline, so it checked nothing once they used it)
            PB.latest_current(cfg, tg, leg, time.time() + 300)
        except Exception as e:
            _log(f"Latest press releases: failed ({type(e).__name__}: {e})")
    _save_state(cfg, st)
    # sorting every stored post the first time takes a few long runs; a daily run sorts for at least 5 minutes
    # Nov 4: the history run had no hard stop here. Once its sort time ran out it kept sorting every post of the
    # last 15 months (3.6M were waiting after the sort rules changed), ran into the job's 350-minute limit, was
    # cancelled, never wrote the index or the "needs more runs" marker, and the chain of history runs stopped. Both
    # modes now stop sorting and re-tagging at a hard time; what's left waits for the next run.
    idx = build_index(cfg, tg, sort_until=(deadline - 120) if history else max(deadline, time.time() + 300),
                      hard_until=(deadline + 10 * 60) if history else max(deadline, time.time() + 300) + 8 * 60)
    # Nov 6: after sort rules 9 (Nov 5) 3.4M posts had to be sorted again. The history chain stopped at its 6-run cap
    # with them still waiting, and the daily runs sort only ~11k each (~300 runs), so the posts index stayed mostly
    # unsorted with nothing saying so. A big backlog now leaves a marker the daily job uses to start the history
    # runs again, and the count is kept for the chain's progress check.
    try:
        open(_dir(cfg, "unsorted_left.txt"), "w").write(str(int(idx.get("unsorted") or 0)))
    except Exception:
        pass
    if idx.get("unsorted") and not history:
        if idx["unsorted"] >= BIG_BACKLOG:
            open(_dir(cfg, "needs_history.txt"), "w").write(str(idx["unsorted"]))
            _log(f"Social: {idx['unsorted']:,} posts not sorted yet, too many for the daily runs; starting the "
                 f"history runs to sort them")
        else:
            _log(f"Social: {idx['unsorted']:,} posts not sorted yet (time limit); the next runs continue")
    if idx.get("unsorted"):
        ok = False
    if history and ok:
        open(_dir(cfg, "complete"), "w").write(dt.datetime.utcnow().isoformat())
        _log("Social: history complete")
    elif history:
        _log("Social: history not finished; the next run continues")
    _log(f"Social: done in {(time.time() - t0) / 60:.1f} min")
    return ok
