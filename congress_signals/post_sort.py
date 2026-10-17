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

SORT_VERSION = 1

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


def _target_tone(text, words, fallback):
    """Tone of the sentences that name the target (or the whole post when none do)."""
    sents = _sentences(text)
    hit = [s for s in sents if any(w and w.lower() in s.lower() for w in words)] or sents[:3]
    if not hit:
        return fallback
    m = _tone_model(hit[:4])
    if m:
        return round(sum(m) / len(m), 2)
    v = [x for x in (_tone_vader(s) for s in hit[:4]) if x is not None]
    return round(sum(v) / len(v), 2) if v else fallback


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
        for s in secs[:4]:
            targets.append({"k": "industry", "n": s})
        meet = None
        if MEET_RX.search(text) or re.search(r"\b(toured|visited)\b", text, re.I):
            who = [x["n"] for x in targets if x["k"] in ("person", "lobbyist")] + [x["t"] for x in targets if x["k"] == "co"]
            if who:
                meet = who[:4]
        strong = [x for x in targets if x["k"] in ("co", "person", "lobbyist", "industry") and abs(x.get("s") or 0) >= 0.6]
        return {"v": SORT_VERSION, "ty": types[:4], "tg": targets[:12], "ins": ins.group(0) if ins else "",
                "meet": meet, "strong": bool(strong), "m": model_name()}
    except Exception:
        return {"v": SORT_VERSION, "ty": ["other"], "tg": [], "ins": "", "meet": None, "strong": False, "m": "error"}


def significance(r, srt, traded_by=None, crowd=None):
    """Why this post matters (owner, Oct 16: insider hints, a company the member or a colleague trades, strong
    for/against, several members at once, any company mention, or company people / lobbyists meeting a member).
    traded_by: {ticker: {bioguides who traded it in the last year}}; crowd: {ticker: distinct members naming it in 3 days}.
    -> (level 0-3, [reasons])"""
    why, lvl = [], 0
    cos = {x["t"] for x in srt.get("tg", []) if x.get("t")}
    if srt.get("ins") and (cos or any(x["k"] in ("agency", "industry") for x in srt.get("tg", []))):
        why.append(f"hint of non-public information (\"{srt['ins']}\")")
        lvl = max(lvl, 3)
    if srt.get("meet"):
        who = ", ".join(srt["meet"][:3])
        why.append(f"met with or visited {who}")
        lvl = max(lvl, 3 if any(x["k"] in ("person", "lobbyist") for x in srt.get("tg", [])) else 2)
    for t in sorted(cos):
        tb = (traded_by or {}).get(t) or set()
        if r[1] in tb:
            why.append(f"names {t}, which this member traded in the last year")
            lvl = max(lvl, 3)
        elif tb:
            why.append(f"names {t}, traded by {len(tb)} other member(s) in the last year")
            lvl = max(lvl, 2)
    for x in srt.get("tg", []):
        s = x.get("s") or 0
        if x["k"] in ("co", "person", "lobbyist", "industry") and abs(s) >= 0.6:
            why.append(f"strongly {'for' if s > 0 else 'against'} {x.get('n') or x.get('t')}")
            lvl = max(lvl, 2)
    for t in sorted(cos):
        n = (crowd or {}).get(t, 0)
        if n >= 3:
            why.append(f"{n} members named {t} in the last 3 days")
            lvl = max(lvl, 2)
    if cos and not why:
        why.append("names " + ", ".join(sorted(cos)[:4]))
        lvl = max(lvl, 1)
    return lvl, why
