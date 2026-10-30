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
        out.append({**r.to_dict(), "who": who, "member_state": st, "muni_state": muni_state(r["asset"]) if r["kind"] == "muni" else None,
                    "sector": muni_sector(r["asset"]) if r["kind"] == "muni" else None, "flag": lvl, "why": why,
                    "ticker": corp_ticker(r["asset"], names or {}) if r["kind"] == "corp_bond" else None})
    return pd.DataFrame(out)
