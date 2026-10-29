"""Bills at the moment of a trade: where each bill stood on the day a member traded, which committees it sat in,
who backed it (and since when) and how each member voted on it.

Sources (all free, no key):
- GovInfo BILLSTATUS bulk zips: every dated action, committee activity (referred, hearing, markup, reported),
  sponsors and cosponsors with join/withdraw dates, policy area and subject terms.
- Voteview roll calls: each member's yea/nay on every House and Senate roll call, tied to the bill number.

Nothing here peeks: a bill's stage on a date only uses actions dated on or before it, a cosponsor counts only from the
day they signed on (until they withdrew), a committee link only from the day the bill was referred there.
Policy areas and subject terms are added by CRS later, so they are used only to decide which industry a bill touches,
never as a signal. A download that fails is never silently skipped: it is listed in `problems` and the caller must
report it."""
import os, re, time, zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-notebook"}
BILL_TYPES = ("hr", "s", "hjres", "sjres")       # the ones that can become law (simple/concurrent resolutions cannot)
FIRST_CONGRESS = 114                             # 2015 on; earlier Congresses run as a separate section

# Stage ladder (higher = further along). A bill's stage on a date is the highest step reached by then.
STAGES = {0: "no linked bill", 1: "introduced/referred", 2: "hearing", 3: "markup", 4: "reported out of committee",
          5: "passed one chamber", 6: "passed both chambers", 7: "sent to the President", 8: "became law"}
COMMITTEE_ACTIVITY = {"referred to": 1, "hearings by": 2, "markup by": 3, "reported by": 4,
                      "reported original measure": 4, "discharged from": 4}
LOC_REPORTED = {"5000", "5500", "14000", "14500", "14900"}       # reported / discharged (House, Senate)
LOC_RESOLVED = {"19500", "20500", "21000", "23000"}              # concurred / conference report agreed
LOC_FAILED = {"9000", "18000", "31000", "33000", "35000"}        # failed passage, vetoed, override failed


def congress_of(day):
    d = pd.Timestamp(day)
    c = (d.year - 1789) // 2 + 1
    if d.year % 2 == 1 and (d.month, d.day) < (1, 3):           # Jan 1-2 of an odd year still belongs to the old one
        c -= 1
    return c


def congress_span(c):
    return pd.Timestamp(1787 + 2 * c, 1, 3), pd.Timestamp(1789 + 2 * c, 1, 3)


def current_congress():
    return congress_of(pd.Timestamp.today())


def _get(url, timeout=600, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                break
        except Exception as e:
            last = str(e)[:120]
        time.sleep(5 * (i + 1))
    raise RuntimeError(last or "no response")


# ----------------------------------------------------------------------------
# Industry of a bill: CRS subject terms first (narrow), policy area as the fallback (broad)
# ----------------------------------------------------------------------------
# Lower-case substrings of subject terms -> stock sectors (Yahoo's sector names, as used for trades).
SUBJECT_SECTORS = {
    # health
    "prescription drug": ["Healthcare"], "drug safety": ["Healthcare"], "pharmac": ["Healthcare"],
    "medical device": ["Healthcare"], "medical research": ["Healthcare"], "medical test": ["Healthcare"],
    "medicare": ["Healthcare"], "medicaid": ["Healthcare"], "health care cost": ["Healthcare"],
    "health insurance": ["Healthcare", "Financial Services"], "health care coverage": ["Healthcare"],
    "hospital": ["Healthcare"], "health technology": ["Healthcare", "Technology"], "vaccin": ["Healthcare"],
    "biotech": ["Healthcare"], "genetics": ["Healthcare"], "health information": ["Healthcare", "Technology"],
    "nursing": ["Healthcare"], "long-term": ["Healthcare"], "veterans' medical": ["Healthcare"],
    "medical tort": ["Healthcare"], "health facilities": ["Healthcare"], "mental health": ["Healthcare"],
    # energy & utilities
    "oil and gas": ["Energy"], "petroleum": ["Energy"], "pipeline": ["Energy"], "coal": ["Energy", "Basic Materials"],
    "natural gas": ["Energy", "Utilities"], "nuclear power": ["Utilities", "Energy"], "nuclear energy": ["Utilities", "Energy"],
    "electric power": ["Utilities"], "utilit": ["Utilities"], "renewable energy": ["Utilities", "Energy"],
    "solar": ["Technology", "Utilities"], "wind energy": ["Utilities", "Industrials"], "hydrogen": ["Energy", "Industrials"],
    "alternative and renewable": ["Energy", "Utilities"], "energy storage": ["Utilities", "Industrials"],
    "energy efficiency": ["Utilities", "Industrials"], "fuel": ["Energy"], "refin": ["Energy"],
    "hydraulic fracturing": ["Energy"], "offshore": ["Energy"], "outer continental shelf": ["Energy"],
    "climate change": ["Energy", "Utilities"], "air quality": ["Energy", "Utilities"],
    "water supply": ["Utilities"], "water quality": ["Utilities"], "dams": ["Utilities"],
    # materials
    "mining": ["Basic Materials"], "minerals": ["Basic Materials"], "metals": ["Basic Materials"],
    "steel": ["Basic Materials"], "aluminum": ["Basic Materials"], "chemical": ["Basic Materials"],
    "timber": ["Basic Materials"], "forest": ["Basic Materials"], "fertilizer": ["Basic Materials"],
    "critical mineral": ["Basic Materials"], "rare earth": ["Basic Materials"],
    # finance & real estate
    "banking": ["Financial Services"], "bank": ["Financial Services"], "financial services": ["Financial Services"],
    "securities": ["Financial Services"], "financial crises": ["Financial Services"], "credit": ["Financial Services"],
    "consumer credit": ["Financial Services"], "insurance": ["Financial Services"], "lending": ["Financial Services"],
    "mortgage": ["Financial Services", "Real Estate"], "housing finance": ["Financial Services", "Real Estate"],
    "home ownership": ["Real Estate"], "housing": ["Real Estate"], "real estate": ["Real Estate"],
    "digital currency": ["Financial Services", "Technology"], "payment": ["Financial Services"],
    "investment": ["Financial Services"], "pension": ["Financial Services"], "retirement": ["Financial Services"],
    "corporate finance": ["Financial Services"], "accounting": ["Financial Services"], "derivatives": ["Financial Services"],
    "income tax": ["Financial Services"], "corporate tax": ["Financial Services"], "tax treatment": ["Financial Services"],
    # technology & communications
    "computer": ["Technology"], "internet": ["Technology", "Communication Services"], "cybersecurity": ["Technology"],
    "computer security": ["Technology"], "semiconductor": ["Technology"], "artificial intelligence": ["Technology"],
    "information technology": ["Technology"], "data privacy": ["Technology", "Communication Services"],
    "technology transfer": ["Technology"], "telecommunication": ["Communication Services"],
    "broadband": ["Communication Services", "Technology"], "wireless": ["Communication Services", "Technology"],
    "radio": ["Communication Services"], "television": ["Communication Services"], "satellite": ["Communication Services", "Industrials"],
    "telephone": ["Communication Services"], "social media": ["Communication Services"], "media": ["Communication Services"],
    "intellectual property": ["Technology", "Healthcare"], "patent": ["Technology", "Healthcare"],
    "copyright": ["Communication Services"], "electronic commerce": ["Technology", "Consumer Cyclical"],
    # industrials, defense, transport
    "military procurement": ["Industrials"], "defense spending": ["Industrials"], "military research": ["Industrials", "Technology"],
    "aircraft": ["Industrials"], "aviation": ["Industrials"], "aerospace": ["Industrials"], "space": ["Industrials"],
    "missile": ["Industrials"], "nuclear weapons": ["Industrials"], "navy": ["Industrials"], "shipbuilding": ["Industrials"],
    "ships": ["Industrials"], "military readiness": ["Industrials"], "weapon": ["Industrials"],
    "railroad": ["Industrials"], "rail": ["Industrials"], "trucking": ["Industrials"], "motor carrier": ["Industrials"],
    "highway": ["Industrials", "Basic Materials"], "infrastructure": ["Industrials"], "ports": ["Industrials"],
    "maritime": ["Industrials"], "freight": ["Industrials"], "construction": ["Industrials", "Basic Materials"],
    "public contracts": ["Industrials", "Technology"], "manufacturing": ["Industrials"], "airport": ["Industrials"],
    "drones": ["Industrials", "Technology"], "unmanned": ["Industrials", "Technology"],
    # consumer
    "motor vehicle": ["Consumer Cyclical"], "automobile": ["Consumer Cyclical"], "electric vehicle": ["Consumer Cyclical"],
    "retail": ["Consumer Cyclical"], "consumer product": ["Consumer Cyclical"], "travel and tourism": ["Consumer Cyclical"],
    "gambling": ["Consumer Cyclical"], "hotel": ["Consumer Cyclical"], "restaurant": ["Consumer Cyclical"],
    "apparel": ["Consumer Cyclical"], "textile": ["Consumer Cyclical"], "airlines": ["Industrials"],
    "food industry": ["Consumer Defensive"], "food supply": ["Consumer Defensive"], "food safety": ["Consumer Defensive"],
    "nutrition": ["Consumer Defensive"], "beverages": ["Consumer Defensive"], "alcoholic beverage": ["Consumer Defensive"],
    "tobacco": ["Consumer Defensive"], "marijuana": ["Consumer Defensive", "Healthcare"], "agricultur": ["Consumer Defensive", "Basic Materials"],
    "crop": ["Consumer Defensive", "Basic Materials"], "livestock": ["Consumer Defensive"], "dairy": ["Consumer Defensive"],
    "meat": ["Consumer Defensive"], "sugar": ["Consumer Defensive"], "farm": ["Consumer Defensive", "Basic Materials"],
    "pesticide": ["Basic Materials"], "biofuel": ["Energy", "Consumer Defensive"], "ethanol": ["Energy", "Consumer Defensive"],
}
# Terms that contain a keyword above but are about something else
SUBJECT_IGNORE = re.compile(r"data banks|food assistance|fuel assistance|homelessness|tax credit|food banks|"
                            r"energy assistance for low-income|student aid|student loans")
_SUBJECT_RX = [(re.compile(r"\b" + re.escape(k)), v) for k, v in SUBJECT_SECTORS.items()]


def bill_sectors(policy, subjects, policy_map):
    """Sectors a bill touches: from its subject terms when any match, else its policy area."""
    out = set()
    for s in subjects or ():
        sl = s.lower()
        if SUBJECT_IGNORE.search(sl):
            continue
        for rx, v in _SUBJECT_RX:
            if rx.search(sl):
                out.update(v)
    if not out:
        out.update(policy_map.get(policy or "", []))
    return tuple(sorted(out))


def _cid(code):
    """congress.gov committee code (hsas00, hsas25) -> roster id (HSAS, HSAS25)."""
    c = str(code or "").upper()
    return c[:-2] if c.endswith("00") else c


def _d(s):
    s = (s or "").strip()[:10]
    return s if len(s) == 10 else None


# ----------------------------------------------------------------------------
# GovInfo BILLSTATUS
# ----------------------------------------------------------------------------
def download_billstatus(cache_dir, congresses, types=BILL_TYPES, log=print, problems=None):
    """Zip per Congress and bill type, kept in cache_dir; the current Congress is refreshed after 3 days."""
    os.makedirs(cache_dir, exist_ok=True)
    problems = problems if problems is not None else []
    cur = current_congress()
    paths = []
    for c in congresses:
        for t in types:
            p = os.path.join(cache_dir, f"BILLSTATUS-{c}-{t}.zip")
            fresh = os.path.exists(p) and (c < cur or time.time() - os.path.getmtime(p) < 3 * 86400)
            if not fresh:
                base = f"https://www.govinfo.gov/bulkdata/BILLSTATUS/{c}/{t}"
                err = None
                for url in (f"{base}/BILLSTATUS-{c}-{t}.zip", f"{base}/BILLSTATUS-{c}{t}.zip"):
                    try:
                        r = _get(url)
                        if r.content[:2] != b"PK":
                            raise RuntimeError("not a zip")
                        open(p + ".part", "wb").write(r.content)
                        os.replace(p + ".part", p)
                        log(f"Bills: downloaded {c}th Congress {t.upper()} ({len(r.content) / 1e6:.0f} MB)")
                        err = None
                        break
                    except Exception as e:
                        err = str(e)
                if err:
                    if os.path.exists(p):
                        problems.append(f"BILLSTATUS {c}-{t}: refresh failed ({err}); used the older copy")
                    else:
                        problems.append(f"BILLSTATUS {c}-{t}: download failed ({err}); these bills are MISSING")
                        continue
            paths.append((c, t, p))
    return paths


def _parse_bill(root, c, t):
    b = root.find("bill")
    if b is None:
        return None
    num = (b.findtext("number") or b.findtext("billNumber") or "").strip()
    if not num:
        return None
    key = f"{c}-{t}{num}"
    intro = _d(b.findtext("introducedDate"))
    policy = b.findtext("policyArea/name") or b.findtext("subjects/policyArea/name") or ""
    subjects = tuple(sorted({s.findtext("name") for s in b.findall("subjects/legislativeSubjects/item")
                             if s.findtext("name")}))
    title = (b.findtext("title") or "")[:160]
    backers = []
    for e in b.findall("sponsors/item"):
        if e.findtext("bioguideId"):
            backers.append((key, e.findtext("bioguideId"), "sponsor", intro, None))
    for e in b.findall("cosponsors/item"):
        if e.findtext("bioguideId"):
            backers.append((key, e.findtext("bioguideId"), "cosponsor", _d(e.findtext("sponsorshipDate")) or intro,
                            _d(e.findtext("sponsorshipWithdrawnDate"))))
    refs, events = [], []
    if intro:
        events.append((key, intro, 1, "introduced"))
    for com in b.findall("committees/item"):
        groups = [(com, _cid(com.findtext("systemCode")))]
        groups += [(sc, _cid(sc.findtext("systemCode"))) for sc in com.findall("subcommittees/item")]
        for node, cid in groups:
            for a in node.findall("activities/item"):
                nm, d = (a.findtext("name") or "").strip().lower(), _d(a.findtext("date"))
                if not d:
                    continue
                st = COMMITTEE_ACTIVITY.get(nm)
                if nm == "referred to" and cid:
                    refs.append((key, cid, d))
                if st:
                    events.append((key, d, st, f"{nm} {cid}"))
    passed, failed = set(), []
    loc = []
    for a in b.findall("actions/item"):
        if (a.findtext("sourceSystem/name") or "") != "Library of Congress":
            continue
        d, code = _d(a.findtext("actionDate")), (a.findtext("actionCode") or "").strip()
        if d and code:
            loc.append((d, code))
    for d, code in sorted(loc):
        if code in LOC_REPORTED:
            events.append((key, d, 4, "reported"))
        elif code in ("8000", "17000", "32000", "34000"):
            passed.add("H" if code in ("8000", "32000") else "S")
            events.append((key, d, 6 if len(passed) == 2 else 5,
                           ("passed House" if code == "8000" else "passed Senate" if code == "17000" else "veto overridden")))
        elif code in LOC_RESOLVED:
            events.append((key, d, 6, "chambers agreed"))
        elif code == "28000":
            events.append((key, d, 7, "sent to President"))
        elif code == "36000":
            events.append((key, d, 8, "became law"))
        elif code in LOC_FAILED:
            failed.append((key, d, code))
    return {"bill": (key, c, t, title, intro, policy, subjects), "backers": backers, "refs": refs,
            "events": events, "failed": failed}


def build_bills(cache_dir, congresses, policy_map, log=print, problems=None, version="v1"):
    """All bills of these Congresses as tables (cached as one pickle per Congress and type)."""
    problems = problems if problems is not None else []
    out = {k: [] for k in ("bills", "backers", "refs", "events", "failed")}
    for c, t, p in download_billstatus(cache_dir, congresses, log=log, problems=problems):
        pk = p.replace(".zip", f".{version}.pkl")
        if os.path.exists(pk) and os.path.getmtime(pk) >= os.path.getmtime(p):
            part = pd.read_pickle(pk)
        else:
            part = {k: [] for k in out}
            try:
                z = zipfile.ZipFile(p)
            except Exception as e:
                problems.append(f"BILLSTATUS {c}-{t}: zip unreadable ({e}); these bills are MISSING")
                continue
            bad = 0
            for n in z.namelist():
                if not n.lower().endswith(".xml"):
                    continue
                try:
                    r = _parse_bill(ET.fromstring(z.read(n)), c, t)
                except Exception:
                    r, bad = None, bad + 1
                if not r:
                    continue
                part["bills"].append(r["bill"])
                for k in ("backers", "refs", "events", "failed"):
                    part[k].extend(r[k])
            if bad:
                problems.append(f"BILLSTATUS {c}-{t}: {bad} bill files could not be read")
            pd.to_pickle(part, pk)
            log(f"Bills: {c}th Congress {t.upper()}: {len(part['bills']):,} bills, {len(part['events']):,} steps")
        for k in out:
            out[k].extend(part[k])
    bills = pd.DataFrame(out["bills"], columns=["bill", "congress", "type", "title", "introduced", "policy", "subjects"])
    bills["sectors"] = [bill_sectors(p, s, policy_map) for p, s in zip(bills["policy"], bills["subjects"])]
    backers = pd.DataFrame(out["backers"], columns=["bill", "bioguide", "role", "joined", "withdrawn"])
    refs = pd.DataFrame(out["refs"], columns=["bill", "cid", "date"]).drop_duplicates(["bill", "cid"])
    ev = pd.DataFrame(out["events"], columns=["bill", "date", "stage", "label"])
    failed = pd.DataFrame(out["failed"], columns=["bill", "date", "code"])
    for df, cols in ((bills, ["introduced"]), (backers, ["joined", "withdrawn"]), (refs, ["date"]),
                     (ev, ["date"]), (failed, ["date"])):
        for col in cols:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    ev = ev.dropna(subset=["date"]).sort_values(["bill", "date", "stage"]).reset_index(drop=True)
    # running highest step per bill; an "advance" is a step that raises it (a second hearing is not progress)
    ev["reached"] = ev.groupby("bill")["stage"].cummax()
    prev = ev.groupby("bill")["reached"].shift(1).fillna(0)
    ev["advance"] = ev["reached"] > prev
    return {"bills": bills, "backers": backers, "refs": refs, "events": ev, "failed": failed}


# ----------------------------------------------------------------------------
# Voteview: how each member voted, as support (+1) or opposition (-1) to the bill
# ----------------------------------------------------------------------------
_SKIP_Q = re.compile(r"amendment(?!s? (?:en gros|to the title))|previous question|ruling of the chair|instruct|"
                     r"table|attendance|nomination|adjourn|consideration of the resolution|refer\b|close meetings|"
                     r"request a conference|go to conference|point of order|quorum")
_OPPOSE_Q = re.compile(r"recommit|commit with instructions")
_SUPPORT_Q = re.compile(r"passage|suspend the rules and (?:pass|agree|concur)|conference report|concur|"
                        r"overrid|notwithstanding|cloture|motion to proceed|joint resolution|agreeing to the resolution|"
                        r"on the resolution|on the bill|recede")
_CONCUR = re.compile(r"concur")


def vote_side(question):
    """+1: a yea supports the bill, -1: a yea opposes it, 0: not a vote on the bill itself."""
    q = (question or "").lower()
    if _OPPOSE_Q.search(q):
        return -1
    if _SKIP_Q.search(q) and not _CONCUR.search(q):
        return 0
    return 1 if _SUPPORT_Q.search(q) else 0


def _vv_bill(raw, c):
    m = re.match(r"^(HR|S|HJRES|SJRES)(\d+)$", str(raw or "").upper().replace(" ", "").replace(".", ""))
    return f"{c}-{m.group(1).lower()}{int(m.group(2))}" if m else None


def build_votes(cache_dir, congresses, log=print, problems=None):
    """One row per member per roll call on a bill: date, bill, side (+1 for the bill, -1 against), contested."""
    os.makedirs(cache_dir, exist_ok=True)
    problems = problems if problems is not None else []
    cur = current_congress()
    frames = []
    for c in congresses:
        for ch in ("H", "S"):
            files = {}
            for kind in ("rollcalls", "votes", "members"):
                p = os.path.join(cache_dir, f"{ch}{c}_{kind}.csv")
                fresh = os.path.exists(p) and (c < cur or time.time() - os.path.getmtime(p) < 7 * 86400)
                if not fresh:
                    try:
                        r = _get(f"https://voteview.com/static/data/out/{kind}/{ch}{c}_{kind}.csv", timeout=300)
                        open(p + ".part", "wb").write(r.content)
                        os.replace(p + ".part", p)
                    except Exception as e:
                        if not os.path.exists(p):
                            problems.append(f"Voteview {ch}{c} {kind}: download failed ({e}); these votes are MISSING")
                            break
                        problems.append(f"Voteview {ch}{c} {kind}: refresh failed ({e}); used the older copy")
                files[kind] = p
            if len(files) < 3:
                continue
            rc = pd.read_csv(files["rollcalls"], usecols=["rollnumber", "date", "bill_number", "vote_question",
                                                          "yea_count", "nay_count"])
            rc["bill"] = [_vv_bill(b, c) for b in rc["bill_number"]]
            rc["side"] = rc["vote_question"].map(vote_side)
            rc = rc[rc["bill"].notna() & (rc["side"] != 0)]
            tot = (rc["yea_count"] + rc["nay_count"]).replace(0, np.nan)
            rc["contested"] = (rc["yea_count"] / tot).between(0.35, 0.65)
            mem = pd.read_csv(files["members"], usecols=["icpsr", "bioguide_id"]).dropna().drop_duplicates("icpsr")
            vt = pd.read_csv(files["votes"], usecols=["rollnumber", "icpsr", "cast_code"])
            vt = vt[vt["cast_code"].between(1, 6)]
            vt["yea"] = vt["cast_code"] <= 3
            vt = vt.merge(rc, on="rollnumber").merge(mem, on="icpsr")
            vt["side"] = np.where(vt["yea"], vt["side"], -vt["side"])     # nay on passage = against the bill
            vt["chamber"] = "House" if ch == "H" else "Senate"
            frames.append(vt[["bioguide_id", "bill", "date", "side", "contested", "chamber", "vote_question"]]
                          .rename(columns={"bioguide_id": "bioguide"}))
            log(f"Votes: {ch}{c}: {len(rc):,} roll calls on bills, {len(vt):,} member votes")
    if not frames:
        return pd.DataFrame(columns=["bioguide", "bill", "date", "side", "contested", "chamber", "vote_question"])
    v = pd.concat(frames, ignore_index=True)
    v["date"] = pd.to_datetime(v["date"], errors="coerce")
    return v.dropna(subset=["date"])


# ----------------------------------------------------------------------------
# Trades -> bills, as of the trade date
# ----------------------------------------------------------------------------
FOCUSED_MAX_SECTORS = 3         # a bill touching more sectors than this is an omnibus (links to nearly everything)
VOTE_WINDOW = 90                # days around the trade a vote counts
_K = 100000                     # day numbers fit below this (1970 + 273 years)


def _days(s):
    return ((pd.to_datetime(s) - pd.Timestamp("1970-01-01")).dt.days).astype("float")


class _Stages:
    """Highest step each bill had reached on any day, looked up for many (bill, day) pairs at once."""

    def __init__(self, ev, codes):
        e = ev.assign(code=ev["bill"].map(codes)).dropna(subset=["code"])
        e = e.assign(day=_days(e["date"]))
        e = e.sort_values(["code", "day", "reached"])
        self.key = (e["code"].values.astype(np.int64) * _K + e["day"].values.astype(np.int64))
        self.code = e["code"].values.astype(np.int64)
        self.reached = e["reached"].values.astype(np.int16)

    def at(self, code, day):
        code = np.asarray(code, dtype=np.int64)
        day = np.nan_to_num(np.asarray(day, dtype=float), nan=-1).astype(np.int64)
        pos = np.searchsorted(self.key, code * _K + day, side="right") - 1
        ok = (pos >= 0) & (self.code[np.clip(pos, 0, None)] == code)
        return np.where(ok, self.reached[np.clip(pos, 0, None)], 0)


def _windows(days_t, days_f):
    """Day boundaries used for every link: before the trade, at the trade, at filing, and after filing."""
    return {"pre30": days_t - 30, "t": days_t, "t30": days_t + 30, "f": days_f, "f30": days_f + 30, "f90": days_f + 90}


def _pair_features(pairs, st, prefix):
    """pairs: key_id, code, plus the window days. Returns one row per key_id."""
    if not len(pairs):
        return pd.DataFrame()
    s = {w: st.at(pairs["code"].values, pairs[w].values) for w in ("pre30", "t", "t30", "f", "f30", "f90")}
    df = pd.DataFrame({"key_id": pairs["key_id"].values, "focused": pairs["focused"].values,
                       **{f"s_{w}": v for w, v in s.items()}})
    # only bills still in play at the trade: not yet law, or signed within the 30 days before it
    df = df[(df["s_t"] < 8) | (df["s_pre30"] < 8)]
    if not len(df):
        return pd.DataFrame()
    df["adv_pre30"] = df["s_t"] > df["s_pre30"]          # bill moved in the 30 days before the trade
    df["adv_t2f"] = df["s_f"] > df["s_t"]                # moved between the trade and the filing (known at filing)
    df["adv_t30"] = df["s_t30"] > df["s_t"]              # moved within 30 days of the trade (partly after filing)
    df["adv_f30"] = df["s_f30"] > df["s_f"]              # moved in the 30 days after filing (hindsight only)
    df["adv_f90"] = df["s_f90"] > df["s_f"]              # 90 days after filing (hindsight only)
    df["top_t2f"] = np.where(df["adv_t2f"], df["s_f"], 0)
    df["top_f90"] = np.where(df["adv_f90"], df["s_f90"], 0)
    agg = {"s_t": "max", "s_f": "max", "adv_pre30": "any", "adv_t2f": "any", "adv_t30": "any",
           "adv_f30": "any", "adv_f90": "any", "top_t2f": "max", "top_f90": "max"}
    out = df.groupby("key_id").agg(agg)
    out["n_bills"] = df.groupby("key_id").size()
    foc = df[df["focused"]].groupby("key_id").agg({"s_t": "max", "s_f": "max", "adv_t2f": "any", "adv_f90": "any"})
    foc.columns = [f"focused_{c}" for c in foc.columns]
    out = out.join(foc)
    out.columns = [f"{prefix}_{c}" for c in out.columns]
    return out


def trade_bill_features(tx, tables, votes, log=print):
    """For each trade (needs bioguide, sector, tx_type, trade_date, filed_date, cids = committee ids held on the
    trade date), how its linked bills stood and moved, and how the member voted on bills in the trade's industry.

    Links (a bill must belong to the Congress in session on the trade date and touch the trade's sector):
      sponsor   - member sponsored it, or had cosponsored it by the trade date (and not withdrawn by then)
      committee - bill was referred, by the trade date, to a committee the member sat on that day
    Vote alignment: +1 for each vote for the bill, -1 against, times +1 for a buy / -1 for a sale; a net positive
    total is "aligned" (bought and voted for, sold and voted against), negative is "contrary"."""
    bills, backers, refs, ev = tables["bills"], tables["backers"], tables["refs"], tables["events"]
    codes = {b: i for i, b in enumerate(bills["bill"])}
    st = _Stages(ev, codes)
    t = tx[["bioguide", "sector", "tx_type", "trade_date", "filed_date"]].copy()
    t["cids"] = [tuple(sorted({str(c)[:4] for c in (x or [])})) for x in tx.get("cids", pd.Series([()] * len(tx), index=tx.index))]
    t["trade_date"], t["filed_date"] = pd.to_datetime(t["trade_date"]), pd.to_datetime(t["filed_date"])
    t["congress"] = [congress_of(d) if pd.notna(d) else -1 for d in t["trade_date"]]
    ok = t["bioguide"].notna() & t["sector"].notna() & t["trade_date"].notna() & t["filed_date"].notna()
    t = t[ok]
    # many trades share member, sector and dates (one filing, several tickers): work out each combination once
    kcols = ["bioguide", "sector", "trade_date", "filed_date", "cids", "congress"]
    keys = t[kcols].drop_duplicates().reset_index(drop=True)
    keys["key_id"] = np.arange(len(keys))
    dt_, df_ = _days(keys["trade_date"]).values, _days(keys["filed_date"]).values
    for w, v in _windows(dt_, df_).items():
        keys[w] = v
    log(f"Bill links: {len(t):,} trades, {len(keys):,} distinct member/sector/date combinations")

    bs = bills[["bill", "congress", "sectors"]].assign(focused=bills["sectors"].map(len) <= FOCUSED_MAX_SECTORS)
    bs = bs.explode("sectors").rename(columns={"sectors": "sector"}).dropna(subset=["sector"])
    bs["code"] = bs["bill"].map(codes)

    # sponsor / cosponsor links
    bk = backers.merge(bs, on="bill")
    sp = keys.merge(bk, on=["bioguide", "congress", "sector"])
    tday = sp["trade_date"]
    sp = sp[(sp["joined"] <= tday) & (sp["withdrawn"].isna() | (sp["withdrawn"] > tday))]
    sp = sp.drop_duplicates(["key_id", "code"])
    f_sp = _pair_features(sp, st, "sponsor")
    # who sponsored (not just cosponsored)
    if len(sp):
        f_sp = f_sp.join(sp[sp["role"] == "sponsor"].groupby("key_id").size().rename("sponsor_n_own"))

    # committee links: only bills that ever got past referral can change the results (a bill still sitting at
    # "referred" adds nothing), so the rest are left out to keep this fast; nothing about them is peeked at
    moved = set(ev.loc[ev["reached"] >= 2, "bill"])
    rf = refs[refs["bill"].isin(moved)].assign(cid=lambda x: x["cid"].str[:4]).drop_duplicates(["bill", "cid"])
    rf = rf.merge(bs, on="bill")
    kc = keys.explode("cids").rename(columns={"cids": "cid"}).dropna(subset=["cid"])
    parts = []
    for c, g in kc.groupby("congress"):
        m = g.merge(rf[rf["congress"] == c], on=["cid", "congress", "sector"])
        m = m[m["date"] <= m["trade_date"]].drop_duplicates(["key_id", "code"])
        parts.append(m)
    cm = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    f_cm = _pair_features(cm, st, "committee")

    # votes on bills in the trade's sector, from 90 days before the trade to 90 days after filing
    f_v = pd.DataFrame()
    if votes is not None and len(votes):
        vb = votes.merge(bills[["bill", "sectors"]], on="bill").explode("sectors").rename(columns={"sectors": "sector"})
        vb = vb.dropna(subset=["sector"])
        linked = set(zip(sp["key_id"], sp["bill"])) | set(zip(cm["key_id"], cm["bill"])) if len(cm) else set(zip(sp["key_id"], sp["bill"]))
        parts = []
        for c, g in keys.groupby("congress"):
            lo, hi = congress_span(c)
            vv = vb[(vb["date"] >= lo - pd.Timedelta(days=VOTE_WINDOW)) & (vb["date"] < hi + pd.Timedelta(days=200))]
            m = g[["key_id", "bioguide", "sector", "trade_date", "filed_date"]].merge(vv, on=["bioguide", "sector"])
            m = m[(m["date"] > m["trade_date"] - pd.Timedelta(days=VOTE_WINDOW)) &
                  (m["date"] <= m["filed_date"] + pd.Timedelta(days=VOTE_WINDOW))]
            parts.append(m)
        vm = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if len(vm):
            vm = vm.drop_duplicates(["key_id", "bill", "date", "vote_question"])
            vm["when"] = np.where(vm["date"] <= vm["trade_date"], "pre",
                                  np.where(vm["date"] <= vm["filed_date"], "t2f", "post"))
            vm["linked"] = [(k, b) in linked for k, b in zip(vm["key_id"], vm["bill"])]
            rows = {}
            for (w, lk), g in vm.groupby(["when", "linked"]):
                tag = f"vote_{w}" + ("_linked" if lk else "_other")
                rows[f"{tag}_net"] = g.groupby("key_id")["side"].sum()
                rows[f"{tag}_n"] = g.groupby("key_id").size()
                rows[f"{tag}_contested_net"] = g[g["contested"]].groupby("key_id")["side"].sum()
            f_v = pd.DataFrame(rows)
            # any-bill totals regardless of the linked split
            for w, g in vm.groupby("when"):
                f_v[f"vote_{w}_all_net"] = g.groupby("key_id")["side"].sum()
                f_v[f"vote_{w}_all_n"] = g.groupby("key_id").size()

    feat = keys[["key_id"]].set_index("key_id")
    for f in (f_sp, f_cm, f_v):
        if len(f):
            feat = feat.join(f)
    out = t.reset_index().merge(keys[kcols + ["key_id"]], on=kcols, how="left").set_index("index")
    out = out[["key_id", "tx_type"]].join(feat, on="key_id")
    # vote alignment with the trade's direction
    sign = np.where(out["tx_type"].eq("sell"), -1, 1)
    for c in [c for c in out.columns if c.startswith("vote_") and c.endswith("_net")]:
        out[c.replace("_net", "_align")] = np.sign(out[c].fillna(0) * sign)
    return out.drop(columns=["key_id", "tx_type"]).reindex(tx.index)
