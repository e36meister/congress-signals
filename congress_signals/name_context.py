"""Who is this name, really? A spaCy check for the posts tagger (owner, Nov 11 2026).

The tagger matches names in posts against company people (executives and lobbyists). A name alone is weak: "Bob
Brooks" (a new union-leader colleague) shares a name with lobbyists for 15 companies, and the attendee lists in
local-funding releases ("Police Captain James Rotundo Jr., Councilwoman Dr. Lisa Wisotsky, ...") matched people at
dozens of companies. spaCy (en_core_web_sm) reads the sentence the name sits in and says what kind of person the post
is talking about:

  "company"     the post ties them to a company: a business title (CEO, founder, lobbyist, ...) next to the name, or
                a company of theirs named in the same sentence.
  "other"       the post gives them a role that isn't a company job: a government or local title (Mayor, Police
                Chief, Councilwoman, Major), a relation (brother, constituent, colleague), "X of <town>", or a list
                of local officials. (kind "gov" when the role is a government title, so it can be learned as an
                official.)
  "not_person"  the words are part of a place, organization, law or event name ("Gallagher Park"), not a person.
  "unknown"     nothing either way; the tagger keeps its own rules for these.

It only runs on posts where a company person's name matched, so most posts never touch it. If spaCy or its model
can't load, every answer is "unknown" (the tagger works as before) and the problem is logged as a failure line, so
Data health shows it (the app's rule: nothing breaks silently).
"""
import re

_NLP = None
STATUS = {"loaded": None, "why": "", "calls": 0, "dropped": 0}
MAX_CHARS = 30000            # long pages: only the part around each name is read (see _window)

# role words. Checked in the few words right before the name, in an appositive ("X, the mayor of ..."), and in
# "X of <place>". Lowercase, one word each; multi-word titles are covered by their parts ("police chief").
GOV_ROLE = set("""
mayor deputy council councilman councilwoman councilmember alderman alderwoman freeholder commissioner selectman
selectwoman supervisor administrator manager clerk treasurer assessor comptroller auditor sheriff undersheriff police
chief officer detective trooper patrolman sergeant sgt lieutenant lt captain capt major maj colonel col general gen
admiral adm commander cmdr corporal cpl private pvt specialist airman seaman marine marines soldier veteran veterans
retired ret army navy force guard fire firefighter emt paramedic dispatcher warden judge justice magistrate prosecutor
attorney da ag governor gov lt. senator sen representative rep congressman congresswoman assemblyman assemblywoman
assemblymember legislator delegate speaker secretary undersecretary ambassador director superintendent principal
trustee board borough township county city village municipal state federal agency postmaster inspector ranger
""".split())
OTHER_ROLE = set("""
brother sister father mother son daughter wife husband grandson granddaughter grandfather grandmother uncle aunt
nephew niece cousin family widow widower constituent constituents resident residents neighbor student students teacher
professor coach pastor reverend rev rabbi imam bishop father priest nurse doctor dr volunteer volunteers honoree
colleague colleagues friend friends union laborers workers member members citizen citizens association coalition
league club chapter foundation nonprofit charity community activist advocate survivor victim
""".split())
COMPANY_ROLE = set("""
ceo cfo coo cto cio founder cofounder co-founder chairman chairwoman chair executive executives exec president
vice vp svp evp lobbyist lobbyists lobbying spokesperson spokesman spokeswoman employee employees engineer analyst
partner owner shareholder investor board director directors
""".split())
# words that look like titles but say nothing on their own
# titles that can be either (a company's president or a council president; a board director or an agency director)
AMBIGUOUS = {"president", "vice", "director", "directors", "chair", "chairman", "chairwoman", "board", "member",
             "members", "partner", "owner", "executive", "manager", "administrator", "officer", "secretary", "treasurer",
             "chief", "principal", "deputy"}
# titles that only a company has
STRONG_COMPANY = {"ceo", "cfo", "coo", "cto", "cio", "founder", "cofounder", "co-founder", "lobbyist", "lobbyists"}
NEUTRAL = {"mr", "ms", "mrs", "miss", "jr", "sr", "ii", "iii", "the", "a", "an", "and", "of", "for", "to", "dr."}
NOT_PERSON_LABELS = {"ORG", "GPE", "FAC", "LOC", "EVENT", "LAW", "WORK_OF_ART", "PRODUCT", "NORP"}


def _log(msg):
    try:
        from congress_signals.social import _log as L
        L(msg)
    except Exception:
        print(msg, flush=True)


def nlp():
    """The spaCy pipeline, loaded once; None (logged as a failure) if it can't load."""
    global _NLP
    if STATUS["loaded"] is None:
        try:
            import spacy
            _NLP = spacy.load("en_core_web_sm", exclude=["lemmatizer"])
            STATUS["loaded"] = True
        except Exception as e:
            STATUS["loaded"], STATUS["why"] = False, f"{type(e).__name__}: {e}"[:200]
            _log(f"Name check (spaCy) unavailable, so matched names use the older rules only: {STATUS['why']}")
    return _NLP


def _window(text, start, end, pad=1500):
    """A slice around the name (whole sentences are what matter), with the name's offsets inside it."""
    if len(text) <= MAX_CHARS:
        return text, start, end
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return text[a:b], start - a, end - a


def _words(span):
    return [t.text.lower().strip(".,") for t in span]


def _before(doc, i, n=6):
    """The title words attached to the name: the run of words right before token i that read as a title
    ("Police Captain", "Retired Air Force Major", "Nvidia CEO", "his brother", "Secretary of the Interior"). It stops
    at another person's name, a verb, or punctuation that ends a phrase (not "Dr." / "Jr."), so a title that
    belongs to someone earlier in the sentence is never borrowed."""
    out = []
    j = i - 1
    roles = GOV_ROLE | OTHER_ROLE | COMPANY_ROLE
    while j >= 0 and len(out) < n:
        t = doc[j]
        w = t.text.lower().strip(".")
        if t.ent_type_ == "PERSON":
            break
        if t.text == "." and j > 0 and doc[j - 1].text.lower() in ("dr", "lt", "sgt", "gen", "col", "capt", "maj",
                                                                    "adm", "rev", "rep", "sen", "gov", "ret", "u.s"):
            j -= 1
            continue
        if t.is_punct and t.text not in ("-", "'s", "’s"):
            break
        if t.pos_ in ("VERB", "AUX") and w not in roles:
            break
        titleish = t.text[:1].isupper() or w in roles or w in NEUTRAL or w in ("his", "her", "their", "our", "my",
                                                                                "'s", "’s", "-", "u.s", "u.s.")
        if not titleish:
            break
        out.append(w)
        j -= 1
    return out


def classify(text, start, end, co_words=(), doc=None):
    """-> (verdict, kind, why) for the name at text[start:end]. co_words: the company's own name words
    (lowercase), used to spot the company named next to the person. doc: a parsed doc of `text` to reuse."""
    STATUS["calls"] += 1
    nl = nlp()
    if nl is None:
        return "unknown", "", "name check unavailable"
    if doc is None:
        text, start, end = _window(text, start, end)
        doc = nl(text)
    span = doc.char_span(start, end, alignment_mode="expand")
    if span is None or not len(span):
        return "unknown", "", "name not found by the reader"
    ent = next((e for e in doc.ents if e.start_char < end and e.end_char > start), None)
    if ent is not None and ent.label_ in NOT_PERSON_LABELS:
        # "Gallagher Park", "the Brooks Act": part of a bigger name. A company of that name is the company
        # matcher's job, not a person match.
        if not (ent.label_ == "ORG" and set(_words(ent)) <= set(_words(span)) | NEUTRAL):
            return "not_person", "", f"part of the {ent.label_.lower()} name \"{ent.text}\""
    person = ent if (ent is not None and ent.label_ == "PERSON") else span
    sent = person.sent
    sent_words = set(_words(sent))
    # the company's own words, minus the person's name ("Gallagher" the man isn't Arthur J. Gallagher the company)
    co = {w for w in co_words if len(w) > 2} - set(_words(person))
    # 1) the person's own title: the words attached right before the name ("Police Captain James ...",
    #    "Nvidia CEO Jensen ...", "his brother ..."). Their own title beats anything else in the sentence.
    pre = _before(doc, person.start)
    pre_set = set(pre)
    title = " ".join(reversed(pre))
    co_in_sent = bool(co and (co & sent_words or any(e.label_ == "ORG" and set(_words(e)) & co for e in sent.ents)))
    if pre_set & STRONG_COMPANY:
        return "company", "", f"\"{title}\" before the name"
    if pre_set & (GOV_ROLE - AMBIGUOUS):
        return "other", "gov", f"\"{title}\" before the name"
    if pre_set & (OTHER_ROLE - AMBIGUOUS):
        return "other", "", f"\"{title}\" before the name"
    if pre_set & AMBIGUOUS and co_in_sent:
        return "company", "", f"\"{title}\" before the name, company named"
    # 2) "X of <place>" / "X from <place>" right after the name ("Tom Gallagher of Mahwah")
    k = person.end
    if k + 1 < len(doc) and doc[k].text.lower() in ("of", "from"):
        nxt = doc[k + 1]
        nxt_ent = next((e for e in sent.ents if e.start == k + 1), None)
        if nxt_ent is not None and nxt_ent.label_ in ("GPE", "LOC", "FAC"):
            return "other", "", f"\"{person.text} {doc[k].text} {nxt_ent.text}\" (a person from a place)"
        if nxt_ent is not None and nxt_ent.label_ == "ORG" and co and set(_words(nxt_ent)) & co:
            return "company", "", f"\"of {nxt_ent.text}\""
        if nxt.text[:1].isupper() and not co_in_sent and (nxt_ent is None or nxt_ent.label_ not in ("ORG",)):
            return "other", "", f"\"{person.text} {doc[k].text} {nxt.text}\" (a person from a place)"
    # 3) the company itself in the same sentence, as an organization the reader found
    for e in sent.ents:
        if e.label_ == "ORG" and co and set(_words(e)) & co:
            return "company", "", f"with \"{e.text}\" in the same sentence"
    # 4) an appositive after the name ("John Smith, the city's mayor", "Tom Gallagher of Mahwah")
    root = person.root
    for ch in root.children:
        if ch.dep_ == "appos" or (ch.dep_ == "prep" and ch.i >= person.end):
            ws = set(_words(ch.subtree))
            if ch.dep_ == "prep" and ch.text.lower() in ("of", "from"):
                place = [e for e in sent.ents if e.label_ in ("GPE", "LOC", "FAC") and e.start >= ch.i]
                if place and place[0].start <= ch.i + 3:
                    return "other", "", f"\"{person.text} {ch.text} {place[0].text}\" (a person from a place)"
                orgs = [e for e in sent.ents if e.label_ == "ORG" and e.start >= ch.i and e.start <= ch.i + 3]
                if orgs and co and set(_words(orgs[0])) & co:
                    return "company", "", f"\"of {orgs[0].text}\""
            if ws & GOV_ROLE and not (ws & co):
                return "other", "gov", f"described as \"{' '.join(t.text for t in ch.subtree)[:60]}\""
            if ws & OTHER_ROLE and not (ws & co):
                return "other", "", f"described as \"{' '.join(t.text for t in ch.subtree)[:60]}\""
            if ws & COMPANY_ROLE and ws & co:
                return "company", "", f"described as \"{' '.join(t.text for t in ch.subtree)[:60]}\""
    # 5) a list of local officials or attendees ("Joined by Mayor A, Police Chief B, Councilwoman C, ...")
    persons_in_sent = [e for e in sent.ents if e.label_ == "PERSON"]
    if len(persons_in_sent) >= 3:
        titled = sum(1 for e in persons_in_sent if set(_before(doc, e.start)) & (GOV_ROLE | OTHER_ROLE))
        if titled >= 2 and not (sent_words & co):
            return "other", "", f"in a list of {len(persons_in_sent)} people, {titled} with local or public titles"
    return "unknown", "", ""


class Checker:
    """Parses each post once and answers for every name matched in it (the tagger calls this per post)."""

    def __init__(self):
        self.text, self.doc, self.off = None, None, 0

    def check(self, text, start, end, co_words=()):
        nl = nlp()
        if nl is None:
            return "unknown", "", "name check unavailable"
        if len(text) > MAX_CHARS:
            return classify(text, start, end, co_words)
        if text is not self.text:
            self.text, self.doc = text, nl(text)
        return classify(text, start, end, co_words, doc=self.doc)
