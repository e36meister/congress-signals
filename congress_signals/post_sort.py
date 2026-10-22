"""Reads each post the way a person would skim it, to sort it and to judge whether it matters for trading.

For every post (press release, Bluesky, Mastodon, Substack, YouTube, X archive):
  ty    what kind of post: funding (grants, jobs, money for the district), visit (tours, site visits), meeting
        (met with / sat down with), oversight (letters, investigations, demands), legislation (bills, votes),
        hearing (committee hearings, testimony), praise, criticism, personal/other. Several can apply.
  tg    who it's about: companies, company people (executives, lobbyists), agencies, industries; with a tone toward
        each (-1 against ... +1 for), read from the sentences that name them.
  ins   wording that hints at information not public yet ("stay tuned", "closed-door briefing", "I'm told", ...).
  meet  the post describes meeting a company, one of its people or a lobbyist (who: names).
Free version: rules plus an open sentiment model (VADER, a word-scoring model built for social media posts;
if the `transformers` library is installed, a Twitter-trained RoBERTa model is used for the sentences that name a
company, which reads tone better). Setting ANTHROPIC_API_KEY and POST_SORTER=haiku switches to Claude Haiku later.
Never raises; a post it can't read gets an empty result."""
import os, re

SORT_VERSION = 4       # 2: tone toward industries too; 3: 'met with' and 'strong' only when the company is what the sentence is about; 4: alert levels (Oct 19)

TYPE_RX = [
    ("meeting", re.compile(r"\b(met\b.{0,30}\bwith|meet(?:ing|s)?\b.{0,30}\bwith|sat down with|sit down with|hosted|"
                           r"spoke with|speaking with|conversation with|roundtable|discussed .{0,40} with|joined by|"
                           r"welcomed .{0,40} to (?:my|our) office)\b", re.I)),
    ("visit", re.compile(r"\b(toured|tour(?:ing)? (?:of|the)|visited|visiting|site visit|stopped by|ribbon[- ]cutting|"
                         r"groundbreaking|on the ground at)\b", re.I)),
    ("funding", re.compile(r"(\$\s?\d[\d,.]*\s*(?:million|billion|thousand|[mbk])\b|\bgrants?\b|\bfunding\b|\bawarded\b|"
                           r"\bsecured\b|\bappropriat\w+|\bfederal (?:dollars|investment)|\bnew jobs\b|\bjobs (?:to|in|for)\b|"
                           r"\binvest(?:ing|ment)s? in\b)", re.I)),
    ("oversight", re.compile(r"\b(letter to|wrote to|sent a letter|demand\w*|investigat\w+|probe|subpoena\w*|oversight|"
                             r"inquiry|calls? on|urg(?:e|es|ed|ing)|hold .{0,30} accountable|answers from)\b", re.I)),
    ("legislation", re.compile(r"\b(introduc\w+ (?:the |a |my |bipartisan )?(?:bill|legislation|act)|cosponsor\w*|"
                               r"\bbill\b|legislation|amendment|signed into law|passed the (?:house|senate)|"
                               r"voted (?:for|against|to)|my vote|\bact\b)", re.I)),
    ("hearing", re.compile(r"\b(hearing|testif\w+|testimony|markup|questioned|in committee|subcommittee)\b", re.I)),
    ("praise", re.compile(r"\b(congratulat\w+|thank(?:s| you)?|proud|honou?red|celebrat\w+|welcome[ds]?|great work|"
                          r"applaud\w*|grateful|excited)\b", re.I)),
    ("criticism", re.compile(r"\b(disgrace\w*|outrage\w*|greed\w*|price[- ]goug\w*|ripping off|shameful|unacceptable|"
                             r"slam\w*|scam|corrupt\w*|rip-?off|predatory|reckless|failed to|failing)\b", re.I)),
]

INSIDER_RX = re.compile(
    r"\b(stay tuned|coming soon|more (?:to come|soon)|in the coming (?:days|weeks)|(?:big|major|exciting) news (?:coming|soon)|"
    r"about to (?:announce|be announced)|soon (?:to )?be announced|will be announc\w+|can(?:'|no)?t (?:say|share) (?:more|much)|"
    r"behind closed doors|closed[- ]door|classified briefing|private (?:meeting|briefing|conversation)s?|"
    r"(?:i was|we were|i've been|i have been) briefed|briefed (?:on|by)|i(?:'m| am) told|heads[- ]up|"
    r"expect(?:ing)? (?:an? )?(?:announcement|decision|deal|ruling|approval)|final stages|close to (?:a |an )?(?:deal|agreement)|"
    r"(?:ongoing|active) negotiations?|before (?:it'?s|it is) (?:public|announced)|first to know|off the record|"
    r"(?:pending|upcoming) (?:approval|decision|contract|award|merger|deal))\b", re.I)

MEET_RX = TYPE_RX[0][1]
# wording that points to money or a policy push for an industry (for "post hinted at a boost, then a trade")
BOOST_TYPES = {"funding", "legislation", "visit", "meeting", "praise"}
HIT_TYPES = {"oversight", "criticism"}

AGENCIES = {
    "FDA": r"\bFDA\b|Food and Drug Administration", "Pentagon": r"\bPentagon\b|Department of Defense|\bDoD\b",
    "EPA": r"\bEPA\b|Environmental Protection Agency", "FTC": r"\bFTC\b|Federal Trade Commission",
    "SEC": r"\bthe SEC\b|Securities and Exchange Commission", "FCC": r"\bFCC\b|Federal Communications Commission",
    "Energy Dept": r"\bDOE\b|Department of Energy", "FAA": r"\bFAA\b|Federal Aviation Administration",
    "HHS": r"\bHHS\b|Health and Human Services|\bCMS\b|Medicare", "USDA": r"\bUSDA\b|Department of Agriculture",
    "Treasury": r"\bTreasury\b", "Commerce Dept": r"Department of Commerce|Commerce Department",
    "Justice Dept": r"\bDOJ\b|Department of Justice|Justice Department", "NASA": r"\bNASA\b",
    "FERC": r"\bFERC\b", "NHTSA": r"\bNHTSA\b", "CFPB": r"\bCFPB\b", "Fed": r"Federal Reserve|\bthe Fed\b",
    "DHS": r"\bDHS\b|Homeland Security", "VA": r"\bthe VA\b|Veterans Affairs", "DOT": r"Department of Transportation|\bDOT\b",
}
AGENCY_RX = {k: re.compile(v) for k, v in AGENCIES.items()}

_vader = None
_hf = None


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _tone_vader(s):
    global _vader
    if _vader is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
        except Exception:
            _vader = False
    if not _vader:
        return None
    return float(_vader.polarity_scores(s)["compound"])


def _tone_model(sents):
    """Twitter-trained RoBERTa sentiment (free, runs on the GitHub machine) when `transformers` is installed."""
    global _hf
    if os.environ.get("POST_MODEL", "1") == "0":
        return None
    if _hf is None:
        try:
            from transformers import pipeline
            _hf = pipeline("sentiment-analysis", model="cardiffnlp/twitter-roberta-base-sentiment-latest",
                           truncation=True, max_length=256, top_k=None)
        except Exception:
            _hf = False
    if not _hf:
        return None
    try:
        out = []
        for res in _hf(sents, batch_size=32):
            d = {r["label"].lower(): r["score"] for r in res}
            out.append(float(d.get("positive", 0) - d.get("negative", 0)))
        return out
    except Exception:
        return None


def model_name():
    if _hf:
        return "roberta"
    return "vader" if _vader else "words"


# Sentiment models read "We need serious oversight over Elon Musk." as neutral (no angry words), but calling for
# oversight of, investigating, or holding someone accountable is plainly against them (owner, Oct 22: Jayapal's post).
# When a sentence that names the target has this wording, the tone toward the target is at least that negative.
AGAINST_RX = re.compile(
    r"\b(oversight (?:over|of|on|into)|(?:need|needs|demand\w*|call(?:s|ing)? for|must have) (?:\w+ ){0,3}(?:oversight|accountability|answers|investigation)|"
    r"hold\w* .{0,40}accountable|(?:no|without|lack of|zero) (?:\w+ ){0,2}(?:accountability|oversight)|investigat\w+|probe|subpoena\w*|conflicts? of interest|rein (?:in|him|her|them)|"
    r"crack(?:ing)? down|must (?:stop|end)|out of control|unchecked|unaccountable|self-dealing|profiteer\w*|"
    r"price[- ]goug\w*|ripping off|rip-?off|corrupt\w*|disgrace\w*|shameful|unacceptable|predatory|reckless|greed\w*|"
    r"scam\w*|lied|lying|dangerous|threat to|attack(?:s|ing)? on|gutting|slash(?:es|ed|ing)? |illegal\w*|break(?:s|ing) the law|"
    r"stop .{0,30}from|block\w* .{0,30}(?:merger|deal)|fire[ds]? (?:\w+ ){0,2}workers|lay(?:ing)? off)\b", re.I)
FOR_RX = re.compile(r"\b(proud to (?:partner|work|support|welcome)|thank(?:s| you)? to|grateful (?:to|for)|congratulat\w+|"
                    r"applaud\w*|great (?:news|partner|work) (?:from|by)|welcome[ds]? .{0,30} investment|"
                    r"creat(?:e|es|ed|ing) .{0,20}jobs|investing in)\b", re.I)


def cue_tone(sents, s):
    """Adjust a tone toward a target from the wording of the sentences that name it."""
    if not sents:
        return s
    neg = any(AGAINST_RX.search(x) for x in sents)
    pos = any(FOR_RX.search(x) for x in sents)
    if neg and not pos:
        return round(min(s if s is not None else 0.0, -0.5), 2)
    if pos and not neg and (s is None or s > -0.3):
        return round(max(s if s is not None else 0.0, 0.4), 2)
    return s


def _target_tone(text, words, fallback):
    """Tone of the sentences that name the target (or the whole post when none do)."""
    sents = _sentences(text)
    named = [s for s in sents if any(w and w.lower() in s.lower() for w in words)]
    hit = named or sents[:3]
    if not hit:
        return fallback
    m = _tone_model(hit[:4])
    if m:
        return cue_tone(named, round(sum(m) / len(m), 2))
    v = [x for x in (_tone_vader(s) for s in hit[:4]) if x is not None]
    return cue_tone(named, round(sum(v) / len(v), 2) if v else fallback)


def sort_record(r, names=None):
    """r: a stored post record [date, key, source, text, url, tickers, people, tone, sectors, repost, tagger].
    names: {ticker: company words} for finding the sentences that name a company."""
    try:
        text = r[3] or ""
        tk, pp, tone, secs = list(r[5] or []), list(r[6] or []), float(r[7] or 0), list(r[8] or [])
        types = [k for k, rx in TYPE_RX if rx.search(text)] or ["other"]
        ins = INSIDER_RX.search(text)
        targets, people_t = [], set()
        for p in pp:
            nm, t, role = (p.split("|") + ["", "", ""])[:3]
            people_t.add(t)
            targets.append({"k": "lobbyist" if role == "lobbyist" else "person", "n": nm, "t": t, "role": role,
                            "s": _target_tone(text, [nm.split()[-1]], tone)})
        for t in tk:
            if t in people_t:
                continue
            words = [" ".join((names or {}).get(t, [])) or t, t]
            targets.append({"k": "co", "t": t, "s": _target_tone(text, words, tone)})
        for a, rx in AGENCY_RX.items():
            if rx.search(text):
                targets.append({"k": "agency", "n": a})
        try:
            from congress_signals.social import SECTOR_WORDS
        except Exception:
            SECTOR_WORDS = {}
        for s in secs[:4]:
            words = [w for w in (SECTOR_WORDS.get(s) or "").split() if len(w) > 2]
            targets.append({"k": "industry", "n": s, "s": _target_tone(text, words, tone) if words else None})
        # "met with": the company or person must be in the same sentence as the meeting or visit words (Oct 18:
        # a Memphis roundtable post that listed FedEx among the city's employers was flagged as meeting FedEx)
        sents = _sentences(text)
        visit_rx = re.compile(r"\b(toured|tour(?:ing)? (?:of|the)|visited|visiting|site visit|stopped by)\b", re.I)
        def words_of(x):
            if x["k"] in ("person", "lobbyist"):
                return [x["n"].split()[-1]]
            return [w for w in ((names or {}).get(x["t"]) or []) if len(w) > 2] or [x["t"]]
        def named_in(sent, x):
            low = sent.lower()
            ws = words_of(x)
            return all(w.lower() in low for w in ws[:2]) if x["k"] == "co" and len(ws) > 1 else any(w.lower() in low for w in ws)
        who = []
        for x in targets:
            if x["k"] not in ("person", "lobbyist", "co"):
                continue
            if any((MEET_RX.search(sn) or visit_rx.search(sn)) and named_in(sn, x) for sn in sents):
                who.append(x["n"] if x["k"] != "co" else x["t"])
        meet = who[:4] or None
        # strong for/against only counts for a company the post is about: in the headline, or named at least twice
        head = sents[0] if sents else ""
        def central(x):
            if x["k"] != "co":
                return True
            return named_in(head, x) or sum(1 for sn in sents if named_in(sn, x)) >= 2
        strong = [x for x in targets if x["k"] in ("co", "person", "lobbyist", "industry") and abs(x.get("s") or 0) >= 0.6 and central(x)]
        for x in targets:
            if x["k"] == "co" and not central(x):
                x["side"] = True         # mentioned in passing
        return {"v": SORT_VERSION, "ty": types[:4], "tg": targets[:12], "ins": ins.group(0) if ins else "",
                "meet": meet, "strong": bool(strong), "m": model_name()}
    except Exception:
        return {"v": SORT_VERSION, "ty": ["other"], "tg": [], "ins": "", "meet": None, "strong": False, "m": "error"}


def significance(r, srt, traded_by=None, crowd=None, member_sectors=None):
    """Why this post matters. Levels (owner, Oct 19): 3 = push alert (3+ members naming the same company, an
    insider-style hint naming a company, a real meeting or visit with a company, its people or a lobbyist);
    2 = shown as notable (names a company this member traded in the last year); 1 = shown (names a company others
    trade, a strong stance on a company, a post about an industry the member trades); 0 = everything else.
    Tone toward whole industries isn't a reason: it was mostly noise (a post about student journalists read as
    "strongly for Real Estate"). traded_by: {ticker: {bioguides who traded it in the last year}};
    crowd: {ticker: distinct members naming it in 3 days}. -> (level 0-3, [reasons])"""
    why, lvl = [], 0
    tg = srt.get("tg", [])
    side = {x["t"] for x in tg if x.get("side") and x.get("t")}
    cos = {x["t"] for x in tg if x.get("t") and x["k"] == "co"} | {x["t"] for x in tg if x.get("t") and x["k"] in ("person", "lobbyist")}
    main = cos - side
    if srt.get("ins") and main:
        why.append(f"hint of non-public information (\"{srt['ins']}\") naming {', '.join(sorted(main)[:3])}")
        lvl = max(lvl, 3)
    if srt.get("meet"):
        why.append(f"met with or visited {', '.join(srt['meet'][:3])}")
        lvl = max(lvl, 3)
    for t in sorted(cos):
        n = (crowd or {}).get(t, 0)
        if n >= 3:
            why.append(f"{n} members named {t} in the last 3 days")
            lvl = max(lvl, 3)
    for t in sorted(cos):
        tb = (traded_by or {}).get(t) or set()
        if r[1] in tb:
            why.append(f"{'mentions' if t in side else 'names'} {t}, which this member traded in the last year")
            lvl = max(lvl, 2 if t not in side else 1)
        elif tb and t not in side:
            why.append(f"names {t}, traded by {len(tb)} other member(s) in the last year")
            lvl = max(lvl, 1)
    for x in tg:
        s_ = x.get("s") or 0
        if x["k"] in ("co", "person", "lobbyist") and abs(s_) >= 0.6 and not x.get("side"):
            why.append(f"strongly {'for' if s_ > 0 else 'against'} {x.get('n') or x.get('t')}")
            lvl = max(lvl, 1)
    ms = (member_sectors or {}).get(r[1]) or {}
    for x in tg:
        if x["k"] == "industry" and x.get("n") in ms and (srt.get("ins") or set(srt.get("ty") or []) & (BOOST_TYPES | HIT_TYPES)):
            why.append(f"about {x['n'].lower()}, where this member recently traded {', '.join(ms[x['n']][:3])}")
            lvl = max(lvl, 1)
    if main and not why:
        why.append("names " + ", ".join(sorted(main)[:4]))
        lvl = max(lvl, 1)
    return lvl, why
