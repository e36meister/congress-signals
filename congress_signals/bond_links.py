"""What a member's bond trade is tied to (owner, Oct 29).

  - Municipal bond: which state and what kind of project (roads/transit, hospitals, schools, utilities, housing,
    general government). Flagged when it's the member's own state AND their committee deals with that kind of
    project, or they sit on Appropriations (the committees that write the earmarks). Earmark matching (the member
    requested money for that very place) comes once the GAO earmark file is on hand.
  - Corporate bond: the company, and its stock ticker when the name matches a listed company.

Nothing here feeds the picks yet; each flag has to pass the usual past-data test first.
"""
import re
import pandas as pd

STATES = {
    "AL": ["alabama", "ala"], "AK": ["alaska"], "AZ": ["arizona", "ariz"], "AR": ["arkansas", "ark"],
    "CA": ["california", "calif", "cal"], "CO": ["colorado", "colo"], "CT": ["connecticut", "conn"],
    "DE": ["delaware", "del"], "DC": ["district of columbia", "d c"], "FL": ["florida", "fla"], "GA": ["georgia"],
    "HI": ["hawaii"], "ID": ["idaho"], "IL": ["illinois", "ill"], "IN": ["indiana", "ind"], "IA": ["iowa"],
    "KS": ["kansas", "kans"], "KY": ["kentucky"], "LA": ["louisiana"], "ME": ["maine"], "MD": ["maryland"],
    "MA": ["massachusetts", "mass"], "MI": ["michigan", "mich"], "MN": ["minnesota", "minn"],
    "MS": ["mississippi", "miss"], "MO": ["missouri"], "MT": ["montana", "mont"], "NE": ["nebraska", "nebr"],
    "NV": ["nevada", "nev"], "NH": ["new hampshire", "n h"], "NJ": ["new jersey", "n j"], "NM": ["new mexico", "n m"],
    "NY": ["new york", "n y"], "NC": ["north carolina", "n c"], "ND": ["north dakota", "n d"], "OH": ["ohio"],
    "OK": ["oklahoma", "okla"], "OR": ["oregon", "ore"], "PA": ["pennsylvania", "penn"], "RI": ["rhode island", "r i"],
    "SC": ["south carolina", "s c"], "SD": ["south dakota", "s d"], "TN": ["tennessee", "tenn"], "TX": ["texas", "tex"],
    "UT": ["utah"], "VT": ["vermont"], "VA": ["virginia"], "WA": ["washington", "wash"],
    "WV": ["west virginia", "w va"], "WI": ["wisconsin", "wis"], "WY": ["wyoming", "wyo"], "PR": ["puerto rico"],
}
# big cities and authorities that don't name their state
PLACES = {"NY": ["new york city", "nyc", "long is", "long island", "metropolitan transn auth", "triborough", "port auth of ny"],
          "CA": ["los angeles", "san francisco", "san diego", "san joaquin", "sacramento", "irvine", "petaluma",
                 "palmdale", "bay area"],
          "IL": ["chicago"], "TX": ["houston", "dallas", "san antonio", "austin"], "PA": ["philadelphia", "pittsburgh"],
          "MA": ["boston"], "WA": ["seattle", "energy northwest"], "GA": ["atlanta"], "AZ": ["phoenix"],
          "OK": ["tulsa"], "CO": ["denver"], "FL": ["miami", "orlando", "tampa", "jacksonville"]}

SECTORS = [
    ("roads and transit", r"\b(tpk|turnpike|toll|hwy|highway|bridge|tunnel|transit\b|transn|transp\w*|arpt|airport|port\b|"
                          r"parking|rail|metro\w*)"),
    ("hospitals and health", r"\b(hosp\w*|health|medical|med ctr|nursing|senior living)"),
    ("schools and universities", r"\b(sch|school|schs|isd|univ\w*|college|edl|educ\w*|dorm\w*|student)"),
    ("utilities and energy", r"\b(util\w*|pwr|power|energy|elec\w*|water|wtr|swr|sewer|waste|wastewater|gas)"),
    ("housing", r"\b(hsg|housing|mtg|mortgage)"),
    ("sports and venues", r"\b(sports|stadium|arena|exhib\w*|convention)"),
]
SECTOR_COMMITTEES = {
    "roads and transit": ["transportation", "commerce, science", "environment and public works", "banking, housing"],
    "hospitals and health": ["energy and commerce", "health, education", "veterans", "ways and means", "finance"],
    "schools and universities": ["education", "health, education"],
    "utilities and energy": ["energy", "natural resources", "environment and public works", "agriculture"],
    "housing": ["financial services", "banking, housing"],
    "sports and venues": [],
}


def _plain(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip() + " "


AMBIG = {"IN", "OR", "ME", "OK", "HI", "ID", "DE", "LA", "CO", "MA", "AL", "AR"}   # also ordinary words / abbreviations


def muni_state(asset):
    """Two-letter state of a municipal bond from its name, or None."""
    p = _plain(asset)
    for st, names in PLACES.items():
        if any(f" {n} " in p for n in names):
            return st
    best = None
    for st, names in STATES.items():
        for n in names:
            i = p.find(f" {n} ")
            if i >= 0 and (best is None or (i, -len(n)) < best[:2]):
                best = (i, -len(n), st)
    toks = p.split()
    for i, t in enumerate(toks):
        code = t.upper()
        if code in STATES and len(t) == 2:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if code in AMBIG and nxt not in ("area", "sch", "cnty", "st", "city", "go", "rev", "auth", "twp", "hosp", "univ"):
                continue
            pos = p.find(f" {t} ")
            if best is None or pos < best[0]:
                best = (pos, -2, code)
            break
    return best[2] if best else None


def muni_sector(asset):
    p = _plain(asset)
    for name, rx in SECTORS:
        if re.search(rx, p):
            return name
    return "general government"


def flag(row, committees, member_state):
    """(level, reason) for one bond trade, or (None, '')."""
    if row.get("kind") != "muni":
        return None, ""
    st, sec = muni_state(row.get("asset")), muni_sector(row.get("asset"))
    if not st or not member_state or st != member_state:
        return None, ""
    c = str(committees or "").lower()
    if "appropriations" in c:
        return "strong", f"a {sec} bond from their own state ({st}); they sit on Appropriations, which writes earmarks"
    hit = [k for k in SECTOR_COMMITTEES.get(sec, []) if k in c]
    if hit:
        return "strong", f"a {sec} bond from their own state ({st}); their committee covers {sec}"
    return "home", f"a {sec} bond from their own state ({st})"


def corp_ticker(asset, names):
    """Ticker of a corporate bond's company: names is {company_key: ticker} (engine.company_key of each company)."""
    import congress_signals.engine as E
    a = re.sub(r"\b\d+(\.\d+)?\s*%|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\bdue\b.*$|\b(sr|senior|notes?|bonds?|bds?|debentures?|"
               r"medium term|mtn|fixed|rate|var\w*)\b", " ", str(asset or ""), flags=re.I)
    k = E.company_key(a)
    if not k:
        return None
    if k in names:
        return names[k]
    k2 = " ".join(k.split()[:2])
    hits = {t for kk, t in names.items() if kk == k2 or kk.startswith(k2 + " ")}
    return next(iter(hits)) if len(hits) == 1 else None


def link_all(other, scored, names=None):
    """Bond trades with state, sector, flag and company ticker. Member state and committees come from the same
    member's stock trades nearest before the bond trade (committee seats in force then)."""
    if other is None or not len(other):
        return pd.DataFrame()
    import congress_signals.gauge as G
    o = other[other["kind"].isin(["muni", "corp_bond", "treasury"])].copy()
    o["trade_date"] = pd.to_datetime(o["trade_date"], errors="coerce")
    ref = scored.dropna(subset=["trade_date"]).sort_values("trade_date")
    ref = ref.assign(_k=ref["chamber"].astype(str) + "|" + ref["member"].map(G._last))
    by = {k: g for k, g in ref.groupby("_k")}
    out = []
    for _, r in o.iterrows():
        g = by.get(f"{r['chamber']}|{G._last(r['member'])}")
        comm, st, who = "", r.get("state"), None
        if g is not None and pd.notna(r["trade_date"]):
            j = max(0, int(g["trade_date"].searchsorted(r["trade_date"], side="right")) - 1)
            x = g.iloc[j]
            comm, who = x.get("committees") or "", x.get("who")
            st = st if isinstance(st, str) and len(st) == 2 else x.get("state")
        lvl, why = flag(r, comm, st if isinstance(st, str) else None)
        own, oth = [], []
        if r["kind"] == "muni":
            ms = muni_state(r["asset"])
            own, oth = earmark_match(r["asset"], ms, r["chamber"], G._last(r["member"]), st if isinstance(st, str) else ms)
            if own and own[0]["same_kind"]:
                e = own[0]
                lvl = "earmark"
                why = (f"they requested ${e['amount']:,.0f} in federal money (FY{e['fy']}) for {e['recipient']}: "
                       f"the same place ({e['place']}) and kind of project as this bond" + (f"; {why}" if why else ""))
            elif own:
                e = own[0]
                lvl = lvl or "home"
                why = ((why + "; ") if why else "") + (f"they also requested ${e['amount']:,.0f} (FY{e['fy']}) for another "
                                                       f"project in {e['place']}: {e['recipient']}")
        out.append({**r.to_dict(), "who": who, "member_state": st, "muni_state": muni_state(r["asset"]) if r["kind"] == "muni" else None,
                    "sector": muni_sector(r["asset"]) if r["kind"] == "muni" else None, "flag": lvl, "why": why, "earmarks": own, "others_earmarks": oth,
                    "ticker": corp_ticker(r["asset"], names or {}) if r["kind"] == "corp_bond" else None})
    return pd.DataFrame(out)


# ---------- earmarks (owner, Oct 30): GAO's list of projects members requested money for, FY2022-2024 ----------
# reference/earmarks_gao_fy2022_2024.csv.gz, from GAO "Tracking the Funds" (GAO-26-107944), one row per
# project and requesting member. A bond matches when it names the same place (city, county, district,
# authority) as a project the member asked money for, in the same state.
import os
EARMARK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reference",
                            "earmarks_gao_fy2022_2024.csv.gz")
ABBR = {"cnty": "county", "cntys": "county", "co": "county", "sch": "school", "schs": "school", "dist": "district",
        "arpt": "airport", "hosp": "hospital", "univ": "university", "mem": "memorial", "mun": "municipal",
        "util": "utility", "utils": "utility", "auth": "authority", "auh": "authority", "tpk": "turnpike",
        "pub": "public", "facs": "facilities", "impt": "improvement", "met": "metropolitan", "metro": "metropolitan",
        "edl": "educational", "educ": "educational", "transn": "transportation", "trans": "transportation",
        "dept": "department", "twp": "township", "boro": "borough", "wtr": "water", "swr": "sewer", "pwr": "power",
        "cmnty": "community", "cmty": "community", "redev": "redevelopment", "hsg": "housing", "vy": "valley",
        "mtn": "mountain", "ft": "fort", "pt": "port", "st": "saint", "lk": "lake", "spgs": "springs", "hts": "heights"}
GENERIC = set("""a an and the of for in at to on by with city town township borough village county parish state school
district schools unified independent isd authority authorities hospital hospitals university college community public
facilities facility improvement improvements metropolitan educational transportation department water sewer power
utility utilities energy gas electric airport airports turnpike port transit housing redevelopment financing finance fing
revenue rev bonds bond bds go general obligation gen oblig ref refunding ser series tax sales exempt project projects
proj lien jr sr capital center medical health system systems services service program programs construction new
repair repairs replacement upgrade upgrades road roads bridge street infrastructure plant treatment wastewater stormwater
drinking line lines main mains area regional region north south east west central upper lower greater memorial
saint valley mountain lake springs heights fort river creek island beach park development economic
due mat inc corp corporation association foundation council commission board trust nation tribe indian""".split())
STATE_WORDS = {w for names in STATES.values() for n in names for w in n.split()}


def _tokens(s):
    w = re.sub(r"[^a-z ]", " ", str(s or "").lower()).split()
    return [ABBR.get(x, x) for x in w if len(x) > 1 or x in ABBR]


JOIN = {"san", "santa", "los", "las", "el", "la", "saint", "fort", "mount", "mt", "new", "east", "west", "north",
        "south", "lake", "port", "palm", "grand", "big", "little", "upper", "lower", "great", "sioux", "baton", "des",
        "st", "de", "del", "red", "white", "black", "glen", "long", "twin", "cedar", "bay", "eau", "le", "pine", "rock"}
GENERIC |= {"municipal", "unified", "unif", "uni", "usd", "imp", "infrastructure", "bay", "bays", "excise", "trans",
            "trust", "leasing", "lease", "building", "bldg", "governmental", "safety", "anticipation", "notes", "note",
            "cap", "commn", "commission", "comm", "funding", "fund", "variable", "var", "call", "make", "whole",
            "partners", "group", "office", "offices", "center", "centers", "station", "stations", "parks", "library"}


def _distinct(s):
    """Place words: single distinctive words ("arbuckle", "tulsa") and two-word names joined ("san_diego")."""
    t = _tokens(s)
    out, i = set(), 0
    while i < len(t):
        if t[i] in JOIN and i + 1 < len(t) and t[i + 1] not in GENERIC:
            out.add(t[i] + "_" + t[i + 1])
            i += 2
            continue
        if t[i] not in GENERIC and t[i] not in STATE_WORDS and t[i] not in JOIN and len(t[i]) > 3:
            out.add(t[i])
        i += 1
    return out


_EAR = None


def earmarks():
    """The earmark table with a matching key (chamber|last name|state) and each project's distinctive words."""
    global _EAR
    if _EAR is None:
        try:
            e = pd.read_csv(EARMARK_FILE)
            e["last"] = e["name"].map(lambda n: re.sub(r"[^a-z ]", "", str(n).lower().replace(", jr", "")
                                                       .replace(" jr.", "")).split()[-1])
            e["key"] = e["chamber"] + "|" + e["last"] + "|" + e["member_state"]
            e["words"] = e["recipient"].map(_distinct)
            _EAR = e
        except Exception:
            _EAR = pd.DataFrame()
    return _EAR


def earmark_match(asset, state, chamber, last, member_state):
    """(own, others): the member's own earmark projects naming the same place as this bond, and other members'."""
    E_ = earmarks()
    if not len(E_) or not state:
        return [], []
    bw = _distinct(asset)
    if not bw:
        return [], []
    cand = E_[E_["location"] == state]
    hit = cand[cand["words"].map(lambda w: len(w & bw) >= 1)]
    if not len(hit):
        return [], []
    key = f"{chamber}|{str(last or '').lower()}|{member_state}"
    sec = muni_sector(asset)
    pick = lambda d: [{"same_kind": muni_sector(r.recipient) == sec, "fy": int(r.fy), "amount": float(r.amount) if pd.notna(r.amount) else None,
                       "recipient": str(r.recipient)[:160], "by": f"{r['name']} ({r.member_state})",
                       "place": ", ".join(sorted(w.replace("_", " ") for w in (r.words & bw)))} for _, r in d.sort_values("amount", ascending=False).head(3).iterrows()]
    own = hit[hit["key"] == key]
    own = own.assign(_k=(own["recipient"].map(muni_sector) == sec).astype(int)).sort_values(["_k", "amount"], ascending=False)
    # other members' projects count only for the same kind of project
    oth = hit[(hit["key"] != key) & (hit["recipient"].map(muni_sector) == sec)].drop_duplicates("id")
    return pick(own), pick(oth)
