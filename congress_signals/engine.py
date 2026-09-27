# =============================================================================
#  Congress Trade Signals - engine
#  Collects US politicians' disclosed stock trades from public sources, scores
#  each purchase, backtests the picks, and builds a ranked watchlist.
# =============================================================================
import os, re, io, json, time, math, zipfile, datetime as dt, unicodedata, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor, as_completed

import logging
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-notebook"}

DEFAULT_CONFIG = {
    "DATA_DIR": "./congress-trading",
    "START_DATE": "2021-01-01",      # how far back to collect / backtest
    "HOLD_DAYS": 60,                 # trading days each pick is held (~3 months)
    "PICKS_PER_WEEK": 5,             # max new long picks each week
    "SHORTS_PER_WEEK": 2,            # max new short picks each week
    "PICK_PERCENTILE": 80,           # a buy must score in the top 20% of all earlier buy signals
    "SHORT_PERCENTILE": 90,          # a short must score in the top 10% of earlier sell signals
    "AVOID_PERCENTILE": 70,          # sell signals above this (but below short) = "avoid"
    "SHORT_ALLOCATION": 0.25,        # share of the combined portfolio that is short (only if ENABLE_SHORTS)
    "ENABLE_SHORTS": False,          # backtest showed shorting lost money; strong sell signals become "avoid"
    "MAX_PICKS_PER_MEMBER_WEEK": 2,  # stop one very active member from filling every weekly slot
    "MAX_WATCHLIST_BUYS_PER_MEMBER": 3,
    "SKIP_LATE_FILINGS": True,       # trades disclosed after the 45-day deadline did worst; never pick them
    "WATCHLIST_LOOKBACK_DAYS": 30,
    "CLUSTER_WINDOW_DAYS": 30,
    "TRACK_PRIOR_TRADES": 5,
    "BUY_WEIGHTS": {
        "track_record": 0.5, "cluster": 0.8, "committee": 0.6, "subcommittee": 0.4, "committee_leader": 0.4,
        "size": 0.3, "freshness": 0.4, "fast_filer": 0.6, "sell_pressure": -0.4, "insider_buying": 0.6,
        "insider_selling": -0.2, "contracts": 0.4, "lobbying": 0.3, "donations": 0.4, "bills": 0.4, "momentum": 0.4,
        "employee_donations": 0.3, "already_owned": 0.1, "disclosure_tie": 0.4, "revolving_door": 0.4,
        "testified": 0.4, "closed_briefing": 0.5, "vote_sector": 0.3, "home_state": 0.2, "contract_in_state": 0.3,
        "speech": 0.3, "social_post": 0.2, "pre_event": 0.4, "committee_cluster": 0.5, "option": 0.4,
        "spouse": 0.1, "first_time": 0.2, "unusual_size": 0.4, "new_sector": 0.1, "late": -0.5,
    },
    "SHORT_WEIGHTS": {
        "sell_track_record": 1.0, "sell_cluster": 0.8, "buy_pressure": -0.4, "committee": 0.5,
        "subcommittee": 0.3, "committee_leader": 0.3, "size": 0.3, "freshness": 0.3, "fast_filer": 0.3,
        "full_sale": 0.3, "insider_selling": 0.4, "insider_buying": -0.4, "momentum": -0.3, "donations": 0.2,
        "option": 0.5, "testified": 0.3, "closed_briefing": 0.5, "vote_sector": 0.3, "pre_event": 0.4,
        "committee_cluster": 0.5, "first_time": 0.1, "unusual_size": 0.4, "spouse": 0.1, "late": 0.2,
    },
    "USE_TUNED_WEIGHTS": False,      # True = use weights saved by the tuning step
    "TRAIN_TEST_SPLIT": "2024-01-01",
    "ASSET_TYPES": ["ST", "EF", "OP"],
    "SEC_USER_AGENT": "",            # required by the SEC: "Your Name you@example.com"
    "CONGRESS_API_KEY": "",          # free key from api.congress.gov/sign-up (bills)
    "LDA_API_KEY": "",               # optional free key from lda.gov (faster lobbying downloads)
    "QUIVER_API_KEY": "",
    "FMP_API_KEY": "",
    "USE_HOUSE": True, "USE_SENATE": True, "USE_INSIDERS": True, "USE_CONTRACTS": True,
    "USE_LOBBYING": True, "USE_DONATIONS": True, "USE_BILLS": True, "USE_NEWS": True,
    "USE_EMPLOYEE_DONATIONS": True, "USE_ANNUAL_DISCLOSURES": True, "USE_COMPANY_INFO": True,
    "USE_HEARINGS": True, "USE_VOTES": True, "USE_SPEECHES": True, "USE_BLUESKY": True, "USE_EVENTS": True,
    "TIME_BUDGET_MIN": 0,            # 0 = no limit (Colab); the daily GitHub run sets this
    "MAX_ENRICH_TICKERS": 800,       # contracts/lobbying are looked up for the most-traded tickers
    "WORKERS": 8,
}

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _p(cfg, *parts):
    path = os.path.join(cfg["DATA_DIR"], *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))


def name_key(s):
    s = _strip_accents(s).lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|hon|dr|mr|mrs|ms)\b\.?", " ", s)
    s = re.sub(r"[^a-z\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


STD_RANGES = {1001: 15000, 15001: 50000, 50001: 100000, 100001: 250000, 250001: 500000,
              500001: 1000000, 1000001: 5000000, 5000001: 25000000, 25000001: 50000000}


def parse_amount(s):
    """'$1,001 - $15,000' -> (1001, 15000). 'Over $50,000,000' -> (5e7, 1e8)."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return (np.nan, np.nan)
    if isinstance(s, (int, float)):
        return (float(s), float(s))
    t = str(s).replace(",", "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return (np.nan, np.nan)
    if "over" in t.lower():
        return (nums[0], nums[0] * 2)
    if len(nums) == 1:
        lo = nums[0]
        return (lo, float(STD_RANGES.get(int(lo), lo)))
    return (nums[0], nums[1])


def norm_type(s):
    t = str(s or "").strip().lower()
    if t in ("p", "purchase", "buy") or t.startswith("purchase"):
        return "buy"
    if t.startswith("s") or "sale" in t or t == "sell":
        return "sell"
    return None  # exchanges etc.


# old symbols whose history now lives under a new ticker
RENAMES = {"FB": "META", "SQ": "XYZ", "ANTM": "ELV", "WLTW": "WTW", "UTX": "RTX", "RTN": "RTX", "TPX": "SGI",
           "ZI": "GTM", "ABC": "COR", "PKI": "RVTY", "FISV": "FI", "DISCA": "WBD", "DISCK": "WBD", "HFC": "DINO",
           "RE": "EG", "BRK": "BRK-B", "BRK-A": "BRK-B", "TWTR": None, "FLT": "CPAY", "PEAK": "DOC", "CDAY": "DAY",
           "COG": "CTRA", "XEC": "CTRA", "NLOK": "GEN", "SIVB": None}


def clean_ticker(t):
    if t is None or (isinstance(t, float) and np.isnan(t)):
        return None
    t = re.sub(r"<[^>]+>", "", str(t)).strip().upper()
    t = t.replace(".", "-").replace("/", "-")
    if t in ("", "--", "N/A", "NA", "NONE") or not re.fullmatch(r"[A-Z][A-Z0-9\-]{0,6}", t):
        return None
    return RENAMES.get(t, t) if t in RENAMES else t


def to_date(x):
    return pd.to_datetime(x, errors="coerce", format="mixed") if isinstance(x, pd.Series) else pd.to_datetime(x, errors="coerce")


TX_COLS = ["member", "first", "last", "chamber", "state", "ticker", "tx_type", "trade_date",
           "filed_date", "amt_lo", "amt_hi", "owner", "source", "doc_id", "tx_raw", "option"]


def finalize_tx(df):
    for c in TX_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[TX_COLS].copy()
    df["ticker"] = df["ticker"].map(clean_ticker)
    # options: buying calls is a bullish bet (buy); buying puts is a bearish bet (treated like a sale)
    opt = df["option"].fillna("").astype(str)
    puts_bought = (opt == "put") & (df["tx_type"] == "buy")
    df.loc[puts_bought, "tx_type"] = "sell"
    df.loc[puts_bought, "tx_raw"] = "put purchase"
    df = df[~((opt == "put") & (df["tx_type"] == "sell") & ~puts_bought)]   # selling puts: ambiguous, skip
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df["filed_date"] = pd.to_datetime(df["filed_date"], errors="coerce")
    df = df.dropna(subset=["ticker", "tx_type", "filed_date"])
    # a trade date after the filing date is a typo; fall back to filing date
    bad = df["trade_date"].isna() | (df["trade_date"] > df["filed_date"])
    df.loc[bad, "trade_date"] = df.loc[bad, "filed_date"]
    return df


# ----------------------------------------------------------------------------
# SOURCE 1: House Clerk periodic transaction reports (official, free)
# ----------------------------------------------------------------------------
HOUSE_CORE = re.compile(
    r"(?<![A-Za-z])(P|S \(partial\)|S|E)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(Over \$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)")
HOUSE_ASSET = re.compile(r"(?:\(([A-Za-z0-9.\-/]{1,8})\)\s*)?\[([A-Z]{2})\]")


PARSER_VERSION = 3


def parse_house_ptr_text(text):
    """Pair the k-th transaction line with the k-th asset tag in a House PTR.
    Also returns the owner code (SP spouse, JT joint, DC child; blank = the member)
    and, for options, whether it is a call or a put."""
    text = text.replace("\x00", "")
    cores = list(HOUSE_CORE.finditer(text))
    assets = list(HOUSE_ASSET.finditer(text))
    out = []
    if not cores:
        return out
    if len(assets) == len(cores):
        pairs = list(zip(cores, assets))
    else:  # fall back to nearest asset tag
        pairs = []
        for c in cores:
            if not assets:
                break
            a = min(assets, key=lambda a: min(abs(a.start() - c.end()), abs(c.start() - a.end())))
            pairs.append((c, a))
    prev_end = 0
    for k, (c, a) in enumerate(pairs):
        typ, tdate, ndate, amt = c.groups()
        lo, hi = parse_amount(amt)
        row_start = text.rfind("\n", 0, min(a.start(), c.start())) + 1
        if k + 1 < len(pairs):
            nc, na = pairs[k + 1]
            row_end = text.rfind("\n", 0, min(na.start(), nc.start()))
            row_end = row_end if row_end > max(a.end(), c.end()) else max(a.end(), c.end())
        else:
            row_end = len(text)
        seg = text[row_start:row_end]
        own = re.search(r"(?:^|\n)\s*(SP|JT|DC)\s", "\n" + text[max(prev_end, row_start - 200):min(a.start(), c.start())])
        opt = None
        if a.group(2) == "OP":
            low = seg.lower()
            opt = "put" if re.search(r"\bputs?\b", low) else ("call" if re.search(r"\bcalls?\b", low) else "option")
        out.append({"ticker": a.group(1), "asset_type": a.group(2), "tx_raw": typ,
                    "trade_date": tdate, "notif_date": ndate, "amt_lo": lo, "amt_hi": hi,
                    "owner": own.group(1) if own else "", "option": opt})
        prev_end = max(a.end(), c.end())
    return out


def house_index(year):
    url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_name = [n for n in z.namelist() if n.lower().endswith(".xml")][0]
    df = pd.read_xml(z.open(xml_name))
    df = df[df["FilingType"].astype(str).str.upper() == "P"].copy()
    df["year"] = year
    return df


def _fetch_house_pdf(year, doc_id):
    import pdfplumber
    url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def collect_house(cfg):
    start = pd.Timestamp(cfg["START_DATE"])
    cache_tx = _p(cfg, "cache", "house_tx.pkl")
    cache_done = _p(cfg, "cache", "house_done.json")
    tx = pd.read_pickle(cache_tx) if os.path.exists(cache_tx) else pd.DataFrame()
    done = set(json.load(open(cache_done))) if os.path.exists(cache_done) else set()
    ver_path = _p(cfg, "cache", "house_parser_version.txt")
    if not os.path.exists(ver_path) or open(ver_path).read().strip() != str(PARSER_VERSION):
        if done:
            log("House: parser upgraded (owners and options); re-reading all reports once")
        tx, done = pd.DataFrame(), set()
        open(ver_path, "w").write(str(PARSER_VERSION))

    idx = []
    for y in range(start.year, dt.date.today().year + 1):
        try:
            idx.append(house_index(y))
            log(f"House {y}: index loaded")
        except Exception as e:
            log(f"House {y}: index failed ({e})")
    if not idx:
        return tx
    idx = pd.concat(idx, ignore_index=True)
    idx["FilingDate"] = pd.to_datetime(idx["FilingDate"], errors="coerce")
    idx = idx[idx["FilingDate"] >= start]
    todo = idx[~idx["DocID"].astype(str).isin(done)]
    log(f"House: {len(idx)} reports since {start.date()}, {len(todo)} new to download")

    rows, n = [], 0

    def work(r):
        return r, _fetch_house_pdf(int(r["year"]), str(r["DocID"]))

    def flush():
        nonlocal tx, rows
        if rows:
            tx = pd.concat([tx, pd.DataFrame(rows)], ignore_index=True)
            rows = []
        tx.to_pickle(cache_tx)
        json.dump(sorted(done), open(cache_done, "w"))

    with ThreadPoolExecutor(cfg["WORKERS"]) as ex:
        futs = [ex.submit(work, r) for _, r in todo.iterrows()]
        for f in as_completed(futs):
            r, text = f.result()
            done.add(str(r["DocID"]))
            n += 1
            if text:
                first = str(r.get("First") or "").strip()
                last = str(r.get("Last") or "").strip()
                st = str(r.get("StateDst") or "")[:2]
                for t in parse_house_ptr_text(text):
                    if t["asset_type"] not in cfg["ASSET_TYPES"]:
                        continue
                    rows.append({"member": f"{first} {last}".strip(), "first": first, "last": last,
                                 "chamber": "House", "state": st, "ticker": t["ticker"],
                                 "tx_type": norm_type(t["tx_raw"]), "trade_date": t["trade_date"],
                                 "filed_date": r["FilingDate"], "amt_lo": t["amt_lo"], "amt_hi": t["amt_hi"],
                                 "owner": t["owner"], "source": "house_clerk", "doc_id": str(r["DocID"]),
                                 "tx_raw": t["tx_raw"], "option": t["option"]})
            if n % 250 == 0:
                log(f"House: {n}/{len(todo)} reports processed")
                flush()
    flush()
    log(f"House: {len(tx)} stock transactions total")
    return tx


# ----------------------------------------------------------------------------
# SOURCE 2: Senate eFD periodic transaction reports (official, free)
# ----------------------------------------------------------------------------
EFD = "https://efdsearch.senate.gov"


def _senate_session():
    s = requests.Session()
    s.headers.update(UA)
    r = s.get(f"{EFD}/search/home/", timeout=60)
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text).group(1)
    s.post(f"{EFD}/search/home/", data={"prohibition_agreement": "1", "csrfmiddlewaretoken": tok},
           headers={"Referer": f"{EFD}/search/home/"}, timeout=60)
    return s


def parse_senate_ptr_html(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    heads = [h.get_text(" ", strip=True).lower() for h in table.find_all("th")]

    def col(*names):
        for i, h in enumerate(heads):
            if any(n in h for n in names):
                return i
        return None

    ci = {"date": col("transaction date"), "owner": col("owner"), "ticker": col("ticker"),
          "atype": col("asset type"), "type": col("type"), "amount": col("amount")}
    # "type" also matches "asset type"; pick the exact 'type' column
    for i, h in enumerate(heads):
        if h == "type":
            ci["type"] = i
    out = []
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) < len(heads) or not tds:
            continue
        g = lambda k: tds[ci[k]] if ci[k] is not None and ci[k] < len(tds) else None
        atype = (g("atype") or "").lower()
        if atype and not any(k in atype for k in ("stock", "fund", "etf", "option")):
            continue
        opt = None
        if "option" in atype:
            row_txt = " ".join(tds).lower()
            opt = "put" if re.search(r"\bputs?\b", row_txt) else ("call" if re.search(r"\bcalls?\b", row_txt) else "option")
        lo, hi = parse_amount(g("amount"))
        out.append({"trade_date": g("date"), "owner": g("owner"), "ticker": g("ticker"),
                    "tx_raw": g("type"), "amt_lo": lo, "amt_hi": hi, "option": opt})
    return out


def collect_senate(cfg):
    start = pd.Timestamp(cfg["START_DATE"])
    cache_tx = _p(cfg, "cache", "senate_tx.pkl")
    cache_done = _p(cfg, "cache", "senate_done.json")
    tx = pd.read_pickle(cache_tx) if os.path.exists(cache_tx) else pd.DataFrame()
    done = set(json.load(open(cache_done))) if os.path.exists(cache_done) else set()
    ver_path = _p(cfg, "cache", "senate_parser_version.txt")
    if not os.path.exists(ver_path) or open(ver_path).read().strip() != str(PARSER_VERSION):
        tx, done = pd.DataFrame(), set()
        open(ver_path, "w").write(str(PARSER_VERSION))
    try:
        s = _senate_session()
    except Exception as e:
        log(f"Senate: could not open eFD site ({e}); skipping")
        return tx

    reports, offset = [], 0
    while True:
        data = {"start": str(offset), "length": "100", "report_types": "[11]", "filer_types": "[]",
                "submitted_start_date": start.strftime("%m/%d/%Y 00:00:00"), "submitted_end_date": "",
                "candidate_state": "", "senator_state": "", "office_id": "", "first_name": "", "last_name": ""}
        try:
            r = s.post(f"{EFD}/search/report/data/", data=data, timeout=60,
                       headers={"Referer": f"{EFD}/search/", "X-CSRFToken": s.cookies.get("csrftoken", "")})
            page = r.json().get("data", [])
        except Exception as e:
            log(f"Senate: listing failed at {offset} ({e})")
            break
        if not page:
            break
        for row in page:
            m = re.search(r'href="([^"]+)"', row[3])
            if m:
                reports.append({"first": row[0], "last": row[1], "link": m.group(1), "filed": row[4]})
        offset += 100
        time.sleep(0.3)
    todo = [r for r in reports if "/ptr/" in r["link"] and r["link"] not in done]
    log(f"Senate: {len(reports)} reports since {start.date()}, {len(todo)} new electronic ones")

    rows = []

    def work(rep):
        for attempt in range(3):
            try:
                r = s.get(EFD + rep["link"], timeout=60)
                if r.status_code == 200 and "<table" in r.text:
                    return rep, parse_senate_ptr_html(r.text)
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        return rep, None

    with ThreadPoolExecutor(min(4, cfg["WORKERS"])) as ex:
        for i, f in enumerate(as_completed([ex.submit(work, r) for r in todo]), 1):
            rep, items = f.result()
            if items is None:
                continue
            done.add(rep["link"])
            for t in items:
                rows.append({"member": f"{rep['first']} {rep['last']}", "first": rep["first"], "last": rep["last"],
                             "chamber": "Senate", "state": None, "ticker": t["ticker"],
                             "tx_type": norm_type(t["tx_raw"]), "trade_date": t["trade_date"],
                             "filed_date": rep["filed"], "amt_lo": t["amt_lo"], "amt_hi": t["amt_hi"],
                             "owner": t["owner"], "source": "senate_efd", "doc_id": rep["link"],
                             "tx_raw": t["tx_raw"], "option": t["option"]})
            if i % 200 == 0:
                log(f"Senate: {i}/{len(todo)} reports processed")
    if rows:
        tx = pd.concat([tx, pd.DataFrame(rows)], ignore_index=True)
    tx.to_pickle(cache_tx)
    json.dump(sorted(done), open(cache_done, "w"))
    log(f"Senate: {len(tx)} stock transactions total")
    return tx


# ----------------------------------------------------------------------------
# SOURCE 3/4: optional paid APIs (fill gaps, e.g. scanned paper filings)
# ----------------------------------------------------------------------------
def _pick(d, *keys):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] not in (None, ""):
            return low[k.lower()]
    return None


def _split_name(full):
    parts = str(full or "").replace(",", " ").split()
    parts = [p for p in parts if name_key(p)]
    return (parts[0] if parts else "", parts[-1] if parts else "")


def collect_quiver(cfg):
    key = cfg.get("QUIVER_API_KEY")
    if not key:
        return pd.DataFrame()
    try:
        r = requests.get("https://api.quiverquant.com/beta/bulk/congresstrading", timeout=180,
                         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        r.raise_for_status()
        rows = []
        for d in r.json():
            name = _pick(d, "Representative", "Name", "Politician")
            first, last = _split_name(name)
            ch = str(_pick(d, "House", "Chamber") or "")
            lo, hi = parse_amount(_pick(d, "Range", "Amount", "Trade_Size_USD"))
            rows.append({"member": name, "first": first, "last": last,
                         "chamber": "Senate" if "sen" in ch.lower() else "House", "state": None,
                         "ticker": _pick(d, "Ticker"), "tx_type": norm_type(_pick(d, "Transaction", "Type")),
                         "tx_raw": _pick(d, "Transaction", "Type"),
                         "trade_date": _pick(d, "TransactionDate", "Traded"),
                         "filed_date": _pick(d, "ReportDate", "Filed", "Disclosed"),
                         "amt_lo": lo, "amt_hi": hi, "source": "quiver"})
        log(f"Quiver: {len(rows)} rows")
        return pd.DataFrame(rows)
    except Exception as e:
        log(f"Quiver: failed ({e})")
        return pd.DataFrame()


def collect_fmp(cfg):
    key = cfg.get("FMP_API_KEY")
    if not key:
        return pd.DataFrame()
    start = pd.Timestamp(cfg["START_DATE"])
    rows = []
    for chamber, ep in (("Senate", "senate-latest"), ("House", "house-latest")):
        for page in range(0, 400):
            try:
                r = requests.get(f"https://financialmodelingprep.com/stable/{ep}",
                                 params={"page": page, "limit": 250, "apikey": key}, timeout=60)
                data = r.json()
            except Exception as e:
                log(f"FMP {chamber}: failed ({e})")
                break
            if not isinstance(data, list) or not data:
                break
            oldest = None
            for d in data:
                lo, hi = parse_amount(_pick(d, "amount"))
                filed = _pick(d, "disclosureDate", "dateRecieved", "dateReceived")
                oldest = filed
                rows.append({"member": f"{_pick(d, 'firstName') or ''} {_pick(d, 'lastName') or ''}".strip()
                                       or _pick(d, "office", "representative"),
                             "first": _pick(d, "firstName"), "last": _pick(d, "lastName"),
                             "chamber": chamber, "state": None, "ticker": _pick(d, "symbol", "ticker"),
                             "tx_type": norm_type(_pick(d, "type")), "tx_raw": _pick(d, "type"),
                             "trade_date": _pick(d, "transactionDate"),
                             "filed_date": filed, "amt_lo": lo, "amt_hi": hi,
                             "owner": _pick(d, "owner"), "source": "fmp"})
            if oldest and pd.to_datetime(oldest, errors="coerce") < start:
                break
    log(f"FMP: {len(rows)} rows")
    return pd.DataFrame(rows)


def collect_all(cfg):
    parts = []
    if cfg.get("USE_HOUSE", True):
        parts.append(("house_clerk", collect_house(cfg)))
    if cfg.get("USE_SENATE", True):
        parts.append(("senate_efd", collect_senate(cfg)))
    parts.append(("quiver", collect_quiver(cfg)))
    parts.append(("fmp", collect_fmp(cfg)))
    frames = [finalize_tx(d) for _, d in parts if d is not None and len(d)]
    if not frames:
        raise RuntimeError("No trade data collected from any source.")
    tx = pd.concat(frames, ignore_index=True)
    for c in ("first", "last"):
        tx[c] = tx[c].fillna("").astype(str)
    fix = tx["last"].str.strip() == ""
    tx.loc[fix, "last"] = tx.loc[fix, "member"].map(lambda m: _split_name(m)[1])
    tx["last_key"] = tx["last"].map(lambda s: name_key(s).split(" ")[-1] if name_key(s) else "")
    # de-duplicate across sources: official sources first
    prio = {"house_clerk": 0, "senate_efd": 0, "quiver": 1, "fmp": 2}
    tx["_p"] = tx["source"].map(prio).fillna(3)
    tx = tx.sort_values("_p")
    tx = tx.drop_duplicates(["chamber", "last_key", "ticker", "trade_date", "tx_type", "amt_lo"], keep="first")
    tx = tx.drop(columns="_p")
    tx = tx[tx["filed_date"] >= pd.Timestamp(cfg["START_DATE"])]
    tx = tx.sort_values("filed_date").reset_index(drop=True)
    tx.to_pickle(_p(cfg, "cache", "all_tx.pkl"))
    tx.to_csv(_p(cfg, "data", "all_transactions.csv"), index=False)
    log(f"All sources: {len(tx)} unique transactions, {tx['member'].nunique()} members, "
        f"{tx['ticker'].nunique()} tickers. By source: {tx['source'].value_counts().to_dict()}")
    return tx


# ----------------------------------------------------------------------------
# Committee assignments (current Congress) -> sectors each member oversees
# ----------------------------------------------------------------------------
LEG_BASE = "https://unitedstates.github.io/congress-legislators/"
COMMITTEE_SECTORS = {
    "armed services": ["Industrials", "Technology"],
    "defense": ["Industrials"],
    "intelligence": ["Technology", "Industrials", "Communication Services"],
    "homeland security": ["Industrials", "Technology"],
    "energy": ["Energy", "Utilities", "Basic Materials"],
    "natural resources": ["Energy", "Basic Materials", "Utilities"],
    "environment": ["Energy", "Utilities", "Basic Materials", "Industrials"],
    "financial services": ["Financial Services", "Real Estate"],
    "banking": ["Financial Services", "Real Estate"],
    "finance": ["Financial Services", "Healthcare"],
    "ways and means": ["Financial Services", "Healthcare"],
    "health": ["Healthcare"],
    "commerce": ["Technology", "Communication Services", "Consumer Cyclical", "Healthcare"],
    "science": ["Technology", "Industrials"],
    "transportation": ["Industrials"],
    "infrastructure": ["Industrials", "Basic Materials"],
    "agriculture": ["Consumer Defensive", "Basic Materials"],
    "veterans": ["Healthcare"],
    "judiciary": ["Technology", "Communication Services"],
    "aging": ["Healthcare"],
    "foreign": ["Industrials", "Energy"],
}


def load_committees(cfg):
    cache = _p(cfg, "cache", "committees.json")
    try:
        leg = requests.get(LEG_BASE + "legislators-current.json", timeout=60).json()
        coms = requests.get(LEG_BASE + "committees-current.json", timeout=60).json()
        mem = requests.get(LEG_BASE + "committee-membership-current.json", timeout=60).json()
    except Exception as e:
        log(f"Committees: download failed ({e})")
        return json.load(open(cache)) if os.path.exists(cache) else {}
    top = {c["thomas_id"]: c["name"] for c in coms if "thomas_id" in c}
    by_bio = {}
    for cid, members in mem.items():
        if cid not in top:
            continue
        for m in members:
            by_bio.setdefault(m.get("bioguide"), []).append(top[cid])
    out = {}
    for p in leg:
        term = p["terms"][-1]
        chamber = "Senate" if term["type"] == "sen" else "House"
        names = by_bio.get(p["id"]["bioguide"], [])
        sectors = sorted({s for n in names for k, v in COMMITTEE_SECTORS.items() if k in n.lower() for s in v})
        rec = {"name": p["name"].get("official_full") or f"{p['name']['first']} {p['name']['last']}",
               "first": p["name"].get("first", ""), "nick": p["name"].get("nickname", ""),
               "state": term.get("state"), "party": term.get("party"), "committees": names, "sectors": sectors,
               "bioguide": p["id"].get("bioguide"), "fec": p["id"].get("fec", []), "chamber": chamber}
        lk = name_key(p["name"]["last"]).split(" ")[-1]
        out.setdefault(f"{chamber}|{lk}", []).append(rec)
    json.dump(out, open(cache, "w"))
    log(f"Committees: {len(leg)} current members loaded")
    return out


def match_member(com, chamber, last_key, first, state):
    cands = com.get(f"{chamber}|{last_key}", [])
    if len(cands) <= 1:
        return cands[0] if cands else None
    if state:
        s = [c for c in cands if c["state"] == state]
        if len(s) == 1:
            return s[0]
        cands = s or cands
    f = name_key(first)[:3]
    s = [c for c in cands if name_key(c["first"]).startswith(f) or name_key(c["nick"]).startswith(f)]
    return s[0] if s else None


# ----------------------------------------------------------------------------
# Prices & sectors (Yahoo Finance via yfinance)
# ----------------------------------------------------------------------------
def _days_since(iso):
    try:
        return (dt.date.today() - dt.date.fromisoformat(iso)).days
    except Exception:
        return 999


def _yf_close(tickers, start, tries=3):
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    for a in range(tries):
        try:
            d = yf.download(tickers, start=start, auto_adjust=True, progress=False, threads=True)
            if d is None or d.empty or d.dropna(how="all", axis=1).empty:
                if len(tickers) <= 3 and a == tries - 1:
                    return pd.DataFrame()
                raise ValueError("empty result")
            c = d["Close"] if isinstance(d.columns, pd.MultiIndex) else d[["Close"]].rename(columns={"Close": tickers[0]})
            return c.dropna(how="all", axis=1)
        except Exception as e:
            log(f"Prices: Yahoo attempt {a+1} failed ({e}); retrying")
            time.sleep(5 * (a + 1))
    return pd.DataFrame()


def _stooq_close(t, start):
    try:
        r = requests.get(f"https://stooq.com/q/d/l/?s={t.lower()}.us&i=d", headers=UA, timeout=30)
        d = pd.read_csv(io.StringIO(r.text))
        if "Close" not in d.columns:
            return None
        s = d.set_index(pd.to_datetime(d["Date"]))["Close"].rename(t)
        return s[s.index >= start]
    except Exception:
        return None


def load_prices(cfg, tickers, max_age_hours=12):
    cache = _p(cfg, "cache", "prices.pkl")
    tickers = sorted(set(tickers) | {"SPY"})
    px = None
    if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) / 3600 < max_age_hours:
        px = pd.read_pickle(cache)
        if "SPY" not in px.columns:
            px = None                      # stale/broken cache: rebuild
    dead_path = _p(cfg, "cache", "no_price_tickers.json")
    dead = json.load(open(dead_path)) if os.path.exists(dead_path) else {}
    dead = {t: d for t, d in dead.items() if _days_since(d) < 30}      # retry dead tickers monthly
    missing = tickers if px is None else [t for t in tickers if t not in px.columns]
    missing = [t for t in missing if t not in dead or t == "SPY"]
    start = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    if missing:
        log(f"Prices: downloading {len(missing)} tickers (first run takes a few minutes)")
        frames = []
        spy = _yf_close(["SPY"], start)
        if "SPY" not in spy.columns:
            s = _stooq_close("SPY", pd.Timestamp(start))
            spy = s.to_frame() if s is not None else pd.DataFrame()
        if "SPY" not in spy.columns:
            raise RuntimeError("Could not download SPY prices from Yahoo or Stooq. Run "
                               "'!pip install -U yfinance', then Runtime > Restart session and try again.")
        frames.append(spy)
        rest = [t for t in missing if t != "SPY"]
        for i in range(0, len(rest), 50):
            frames.append(_yf_close(rest[i:i + 50], start, tries=2))
            time.sleep(2)
        got = set().union(*[set(f.columns) for f in frames])
        retry = [t for t in rest if t not in got]
        if retry:
            log(f"Prices: retrying {len(retry)} tickers Yahoo missed (throttling or delisted)")
            time.sleep(20)
            for i in range(0, len(retry), 25):
                frames.append(_yf_close(retry[i:i + 25], start, tries=2))
                time.sleep(3)
            got = set().union(*[set(f.columns) for f in frames])
            still = [t for t in retry if t not in got][:300]
            if still:
                log(f"Prices: trying Stooq for {len(still)} remaining tickers")
                with ThreadPoolExecutor(4) as ex:
                    for s_ in ex.map(lambda t: _stooq_close(t, pd.Timestamp(start)), still):
                        if s_ is not None and len(s_):
                            frames.append(s_.to_frame())
        new = pd.concat(frames, axis=1)
        new.index = pd.to_datetime(new.index)
        if getattr(new.index, "tz", None) is not None:
            new.index = new.index.tz_localize(None)
        got = set(new.columns)
        today = dt.date.today().isoformat()
        dead.update({t: today for t in missing if t not in got})
        json.dump(dead, open(dead_path, "w"))
        log(f"Prices: got {len(got)}/{len(missing)} tickers; {len(missing) - len(got)} have no price data "
            f"(delisted, acquired or renamed) and will be skipped for 30 days")
        px = new if px is None else px.join(new[[c for c in new.columns if c not in px.columns]], how="outer")
        px = px.loc[:, ~px.columns.duplicated()].sort_index().dropna(how="all", axis=1)
        px.to_pickle(cache)
    log(f"Prices: {px.shape[1]} tickers, {px.index.min().date()} to {px.index.max().date()}")
    return px



# ============================================================================
# Ticker metadata (sector, company name) from Yahoo
# ============================================================================
def load_meta(cfg, tickers):
    import yfinance as yf
    cache = _p(cfg, "cache", "ticker_meta.json")
    meta = json.load(open(cache)) if os.path.exists(cache) else {}
    todo = [t for t in set(tickers) if not isinstance(meta.get(t), dict)]
    if todo:
        log(f"Company info: looking up {len(todo)} tickers")

        def get(t):
            for a in range(2):
                try:
                    i = yf.Ticker(t).info or {}
                    return t, {"sector": i.get("sector") or i.get("quoteType"), "industry": i.get("industry"),
                               "name": i.get("longName") or i.get("shortName") or "", "type": i.get("quoteType")}
                except Exception:
                    time.sleep(2)
            return t, {"sector": None, "industry": None, "name": "", "type": None}

        with ThreadPoolExecutor(min(4, cfg["WORKERS"])) as ex:
            for i, f in enumerate(as_completed([ex.submit(get, t) for t in todo]), 1):
                t, m = f.result()
                meta[t] = m
                if i % 300 == 0:
                    json.dump(meta, open(cache, "w"))
                    log(f"Company info: {i}/{len(todo)}")
        json.dump(meta, open(cache, "w"))
    return meta


_SUFFIX = re.compile(r"\b(the|inc|incorporated|corp|corporation|co|company|companies|ltd|limited|plc|holdings?|group|"
                     r"llc|lp|l p|n v|nv|s a|sa|ag|se|class [a-c]|common stock|new|de|del|trust|international|intl)\b")


def company_key(name):
    s = _strip_accents(name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _SUFFIX.sub(" ", s)
    words = [w for w in s.split() if w not in ("and",)]
    return " ".join(words[:3])


def _cache_json(cfg, name, default):
    path = _p(cfg, "cache", name)
    return (json.load(open(path)) if os.path.exists(path) else default), path


def _fresh(ts, days):
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(ts)).days < days
    except Exception:
        return False


def _enrich_tickers(tx, cfg):
    counts = tx["ticker"].value_counts()
    return list(counts.index[: cfg["MAX_ENRICH_TICKERS"]])


# ============================================================================
# SEC insider trades (company officers/directors, reported within 2 business days)
# ============================================================================
def _sec_headers(cfg):
    ua = (cfg.get("SEC_USER_AGENT") or "").strip()
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"} if ua else None


def _read_tsv(z, suffix, cols):
    name = [n for n in z.namelist() if n.upper().endswith(suffix)]
    if not name:
        return pd.DataFrame(columns=cols)
    d = pd.read_csv(z.open(name[0]), sep="\t", dtype=str, quoting=3, on_bad_lines="skip", low_memory=False)
    d.columns = [c.strip().upper() for c in d.columns]
    return d[[c for c in cols if c in d.columns]]


def collect_insiders(cfg):
    hdr = _sec_headers(cfg)
    cache = _p(cfg, "cache", "insiders.pkl")
    done, done_path = _cache_json(cfg, "insider_quarters.json", [])
    ins = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame()
    if not hdr:
        log("Insiders: skipped - fill in SEC_USER_AGENT in Settings (the SEC requires a name and email)")
        return ins
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=120)
    today = pd.Timestamp.today()
    new_rows = []
    for y in range(start.year, today.year + 1):
        for q in range(1, 5):
            key = f"{y}q{q}"
            if key in done or pd.Timestamp(y, 3 * q, 1) < start - pd.Timedelta(days=90) or pd.Timestamp(y, 3 * q - 2, 1) > today:
                continue
            url = f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{key}_form345.zip"
            try:
                r = requests.get(url, headers=hdr, timeout=180)
                if r.status_code != 200:
                    continue
                z = zipfile.ZipFile(io.BytesIO(r.content))
                sub = _read_tsv(z, "SUBMISSION.TSV", ["ACCESSION_NUMBER", "FILING_DATE", "ISSUERTRADINGSYMBOL"])
                tr = _read_tsv(z, "NONDERIV_TRANS.TSV", ["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES",
                                                         "TRANS_PRICEPERSHARE"])
                own = _read_tsv(z, "REPORTINGOWNER.TSV", ["ACCESSION_NUMBER", "RPTOWNERCIK"]).drop_duplicates("ACCESSION_NUMBER")
                tr = tr[tr["TRANS_CODE"].isin(["P", "S"])]
                d = tr.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER", how="left")
                d["value"] = pd.to_numeric(d["TRANS_SHARES"], errors="coerce") * pd.to_numeric(d["TRANS_PRICEPERSHARE"], errors="coerce")
                d["filed"] = pd.to_datetime(d["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
                d.loc[d["filed"].isna(), "filed"] = pd.to_datetime(d.loc[d["filed"].isna(), "FILING_DATE"], errors="coerce")
                d = d.rename(columns={"ISSUERTRADINGSYMBOL": "ticker", "TRANS_CODE": "code", "RPTOWNERCIK": "owner"})
                d["ticker"] = d["ticker"].map(clean_ticker)
                d = d.dropna(subset=["ticker", "filed"]).groupby(["ticker", "filed", "owner", "code"], as_index=False)["value"].sum()
                new_rows.append(d)
                if pd.Timestamp(y, 3 * q, 1) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=45) < today:
                    done.append(key)  # quarter is final
                log(f"Insiders: {key} loaded ({len(d):,} trades)")
            except Exception as e:
                log(f"Insiders: {key} failed ({e})")
    if new_rows:
        ins = pd.concat([ins] + new_rows, ignore_index=True).drop_duplicates(["ticker", "filed", "owner", "code", "value"])
        ins.to_pickle(cache)
        json.dump(done, open(done_path, "w"))
    return ins


def recent_form4(cfg, tickers, days=120):
    """Live Form 4 filings for a few tickers (the quarterly data lags up to 3 months)."""
    import xml.etree.ElementTree as ET
    hdr = _sec_headers(cfg)
    if not hdr or not tickers:
        return pd.DataFrame()
    try:
        cmap = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in
                requests.get("https://www.sec.gov/files/company_tickers.json", headers=hdr, timeout=60).json().values()}
    except Exception as e:
        log(f"Insiders (live): ticker list failed ({e})")
        return pd.DataFrame()
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []
    log(f"Insiders (live): checking the latest SEC filings for {len(tickers)} recently traded stocks")
    for n_t, t in enumerate(tickers, 1):
        if n_t % 25 == 0:
            log(f"Insiders (live): {n_t}/{len(tickers)}")
        cik = cmap.get(t)
        if not cik:
            continue
        try:
            rec = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=hdr, timeout=30).json()["filings"]["recent"]
            time.sleep(0.12)
            for form, fdate, acc, doc in zip(rec["form"], rec["filingDate"], rec["accessionNumber"], rec["primaryDocument"]):
                if form != "4" or fdate < cutoff:
                    continue
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc.split('/')[-1]}"
                x = requests.get(url, headers=hdr, timeout=30)
                time.sleep(0.12)
                root = ET.fromstring(x.content)
                owner = root.findtext(".//reportingOwnerId/rptOwnerCik")
                for n in root.iter("nonDerivativeTransaction"):
                    code = n.findtext("transactionCoding/transactionCode")
                    if code not in ("P", "S"):
                        continue
                    sh = pd.to_numeric(n.findtext("transactionAmounts/transactionShares/value"), errors="coerce")
                    pr = pd.to_numeric(n.findtext("transactionAmounts/transactionPricePerShare/value"), errors="coerce")
                    rows.append({"ticker": t, "filed": pd.Timestamp(fdate), "owner": owner, "code": code,
                                 "value": (sh or 0) * (pr or 0)})
        except Exception:
            continue
    d = pd.DataFrame(rows)
    if len(d):
        d = d.groupby(["ticker", "filed", "owner", "code"], as_index=False)["value"].sum()
    log(f"Insiders (live): {len(d)} recent trades across {d['ticker'].nunique() if len(d) else 0} tickers")
    return d


# ============================================================================
# Federal contracts (USAspending.gov)
# ============================================================================
def collect_contracts(cfg, meta, tickers):
    cache, path = _cache_json(cfg, "contracts.json", {})
    start = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    today = dt.date.today().isoformat()
    todo = [t for t in tickers if (not _fresh(cache.get(t, {}).get("fetched", ""), 7) or cache.get(t, {}).get("v") != 2)
            and company_key((meta.get(t) or {}).get("name"))]
    if todo:
        log(f"Contracts: checking {len(todo)} companies on USAspending.gov")

    def work(t):
        key = company_key(meta[t]["name"])
        rows = []
        for page in (1, 2):
            body = {"filters": {"recipient_search_text": [key], "award_type_codes": ["A", "B", "C", "D"],
                                "time_period": [{"start_date": start, "end_date": today}]},
                    "fields": ["Action Date", "Transaction Amount", "Recipient Name", "Awarding Agency",
                               "Primary Place of Performance"],
                    "sort": "Transaction Amount", "order": "desc", "limit": 100, "page": page}
            try:
                r = requests.post("https://api.usaspending.gov/api/v2/search/spending_by_transaction/",
                                  json=body, timeout=60)
                res = r.json().get("results", [])
            except Exception:
                return t, None
            for x in res:
                rn = company_key(x.get("Recipient Name"))
                amt = x.get("Transaction Amount") or 0
                if (rn == key or rn.startswith(key + " ")) and amt > 0 and x.get("Action Date"):
                    pop = x.get("Primary Place of Performance")
                    if isinstance(pop, dict):
                        pop = pop.get("state_code") or pop.get("state") or ""
                    m_ = re.search(r"\b([A-Z]{2})\b", str(pop or ""))
                    rows.append([x["Action Date"], float(amt), x.get("Awarding Agency") or "", m_.group(1) if m_ else ""])
            if len(res) < 100:
                break
        return t, rows

    with ThreadPoolExecutor(4) as ex:
        for i, f in enumerate(as_completed([ex.submit(work, t) for t in todo]), 1):
            t, rows = f.result()
            if rows is not None:
                cache[t] = {"fetched": dt.datetime.now().isoformat(), "rows": rows, "v": 2}
            if i % 100 == 0:
                json.dump(cache, open(path, "w"))
                log(f"Contracts: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    out = [{"ticker": t, "action_date": r[0], "amount": r[1], "agency": r[2], "pop_state": (r[3] if len(r) > 3 else None) or None}
           for t, v in cache.items() for r in v.get("rows", [])]
    c = pd.DataFrame(out, columns=["ticker", "action_date", "amount", "agency", "pop_state"])
    c["action_date"] = pd.to_datetime(c["action_date"], errors="coerce")
    # USAspending publishes Defense awards with a 90-day delay; others within ~2 weeks
    c["known_date"] = c["action_date"] + pd.to_timedelta(
        np.where(c["agency"].str.contains("Defense", case=False, na=False), 90, 14), unit="D")
    log(f"Contracts: {c['ticker'].nunique()} companies with federal contracts")
    return c


# ============================================================================
# Lobbying (Senate/House Lobbying Disclosure Act filings)
# ============================================================================
COMMITTEE_ISSUES = {
    "armed services": ["DEF", "AER", "HOM"], "defense": ["DEF", "AER"], "intelligence": ["DEF", "INT", "HOM"],
    "homeland security": ["HOM", "IMM", "TRA"], "energy": ["ENG", "FUE", "NAT", "UTI", "ENV"],
    "natural resources": ["NAT", "ENG", "FUE", "RES"], "environment": ["ENV", "CAW", "WAS"],
    "financial services": ["FIN", "BAN", "HOU", "INS"], "banking": ["BAN", "FIN", "HOU"],
    "finance": ["TAX", "HCR", "MMM", "RET", "TRD"], "ways and means": ["TAX", "HCR", "MMM", "TRD", "RET"],
    "health": ["HCR", "PHA", "MED", "MMM"],
    "commerce": ["TEC", "COM", "CSP", "TEL", "HCR", "PHA", "ENG", "AUT", "MAN"],
    "science": ["SCI", "TEC", "AER", "CPT"], "transportation": ["TRA", "AVI", "RRR", "MAR", "TRU", "AUT"],
    "infrastructure": ["TRA", "AVI", "RRR", "WAS", "CAW"], "agriculture": ["AGR", "FOO", "TOB", "ANI"],
    "veterans": ["VET", "HCR"], "judiciary": ["CPT", "LAW", "IMM", "CIV", "TEC"], "aging": ["HCR", "MMM", "RET"],
    "foreign": ["FOR", "TRD", "DEF"], "appropriations": ["BUD", "DEF"], "budget": ["BUD"],
}


def collect_lobbying(cfg, meta, tickers):
    cache, path = _cache_json(cfg, "lobbying.json", {})
    key = cfg.get("LDA_API_KEY")
    hdr = dict(UA, **({"Authorization": f"Token {key}"} if key else {}))
    if not key:
        tickers = tickers[:150]
    max_pages = 20 if key else 4
    start_year = pd.Timestamp(cfg["START_DATE"]).year - 1
    todo = [t for t in tickers if (not _fresh(cache.get(t, {}).get("fetched", ""), 14) or cache.get(t, {}).get("v") != 2)
            and company_key((meta.get(t) or {}).get("name"))]
    if todo:
        log(f"Lobbying: checking {len(todo)} companies" + ("" if key else " (add LDA_API_KEY for more and faster)"))
    bases = ["https://lda.gov/api/v1/filings/", "https://lda.senate.gov/api/v1/filings/"]

    def fetch(url, params):
        for a in range(4):
            try:
                r = requests.get(url, params=params, headers=hdr, timeout=60)
                if r.status_code == 429:
                    time.sleep(int(r.headers.get("Retry-After", 10)))
                    continue
                if r.status_code == 200:
                    return r.json()
                return None
            except Exception:
                time.sleep(3)
        return None

    for i, t in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        ck = company_key(meta[t]["name"])
        rows, ok = [], False
        for base in bases:
            url, params, pages = base, {"client_name": ck, "filing_year__gte": start_year, "ordering": "-dt_posted"}, 0
            while url and pages < max_pages:
                js = fetch(url, params)
                if js is None:
                    break
                ok = True
                for f in js.get("results", []):
                    cn = company_key((f.get("client") or {}).get("name"))
                    if not (cn == ck or cn.startswith(ck + " ")):
                        continue
                    acts = f.get("lobbying_activities") or []
                    amt = f.get("income") or f.get("expenses") or 0
                    cov = " | ".join(sorted({(l.get("covered_position") or "").strip() for a in acts
                                             for l in (a.get("lobbyists") or []) if (l.get("covered_position") or "").strip()}))
                    rows.append([f.get("dt_posted"), float(amt or 0),
                                 sorted({a.get("general_issue_code") for a in acts if a.get("general_issue_code")}), cov[:2000]])
                url, params, pages = js.get("next"), None, pages + 1
                if not key:
                    time.sleep(4)
            if ok:
                break
        if ok:
            cache[t] = {"fetched": dt.datetime.now().isoformat(), "rows": rows, "v": 2}
        if i % 50 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Lobbying: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    out = [{"ticker": t, "posted": r[0], "amount": r[1], "issues": r[2], "covered": r[3] if len(r) > 3 else ""}
           for t, v in cache.items() for r in v.get("rows", [])]
    lb = pd.DataFrame(out, columns=["ticker", "posted", "amount", "issues", "covered"])
    lb["posted"] = pd.to_datetime(lb["posted"], errors="coerce", utc=True).dt.tz_localize(None)
    log(f"Lobbying: {lb['ticker'].nunique()} companies with lobbying filings")
    return lb


# ============================================================================
# Campaign donations: company PACs -> members (FEC bulk data)
# ============================================================================
CM_COLS = ["CMTE_ID", "CMTE_NM", "TRES_NM", "CMTE_ST1", "CMTE_ST2", "CMTE_CITY", "CMTE_ST", "CMTE_ZIP", "CMTE_DSGN",
           "CMTE_TP", "CMTE_PTY_AFFILIATION", "CMTE_FILING_FREQ", "ORG_TP", "CONNECTED_ORG_NM", "CAND_ID"]
PAS2_COLS = ["CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM", "TRANSACTION_TP", "ENTITY_TP", "NAME",
             "CITY", "STATE", "ZIP_CODE", "EMPLOYER", "OCCUPATION", "TRANSACTION_DT", "TRANSACTION_AMT", "OTHER_ID",
             "CAND_ID", "TRAN_ID", "FILE_NUM", "MEMO_CD", "MEMO_TEXT", "SUB_ID"]


def _fec_zip(url, cols):
    r = requests.get(url, headers=UA, timeout=300)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = [n for n in z.namelist() if n.lower().endswith(".txt")][0]
    return pd.read_csv(z.open(name), sep="|", header=None, names=cols, dtype=str, quoting=3,
                       on_bad_lines="skip", encoding="latin-1", index_col=False)


def collect_donations(cfg):
    cache = _p(cfg, "cache", "donations.pkl")
    state, spath = _cache_json(cfg, "donation_cycles.json", {})
    don = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame()
    y0 = pd.Timestamp(cfg["START_DATE"]).year - 4
    cycles = [c for c in range(y0 + (y0 % 2), dt.date.today().year + 2, 2)]
    frames = []
    for cyc in cycles:
        final = cyc < dt.date.today().year - 1
        if cyc and str(cyc) in state and (final or _fresh(state[str(cyc)], 7)):
            continue
        yy = str(cyc)[2:]
        try:
            cm = _fec_zip(f"https://www.fec.gov/files/bulk-downloads/{cyc}/cm{yy}.zip", CM_COLS)
            pas = _fec_zip(f"https://www.fec.gov/files/bulk-downloads/{cyc}/pas2{yy}.zip", PAS2_COLS)
        except Exception as e:
            log(f"Donations: {cyc} cycle failed ({e})")
            continue
        corp = cm[(cm["ORG_TP"] == "C") | (cm["CMTE_TP"].isin(["Q", "N"]) & cm["CONNECTED_ORG_NM"].notna())]
        corp = corp.assign(org=[company_key(o if isinstance(o, str) and o.strip() else n)
                                for o, n in zip(corp["CONNECTED_ORG_NM"], corp["CMTE_NM"])])
        pas = pas[pas["TRANSACTION_TP"] == "24K"]
        d = pas.merge(corp[["CMTE_ID", "org"]], on="CMTE_ID")
        d = pd.DataFrame({"org": d["org"], "cand_id": d["CAND_ID"],
                          "date": pd.to_datetime(d["TRANSACTION_DT"], format="%m%d%Y", errors="coerce"),
                          "amount": pd.to_numeric(d["TRANSACTION_AMT"], errors="coerce"), "cycle": cyc})
        frames.append(d.dropna(subset=["date", "amount"]))
        state[str(cyc)] = dt.datetime.now().isoformat()
        log(f"Donations: {cyc} cycle loaded ({len(d):,} corporate PAC contributions)")
    if frames:
        done = {f["cycle"].iloc[0] for f in frames if len(f)}
        if len(don):
            don = don[~don["cycle"].isin(done)]
        don = pd.concat([don] + frames, ignore_index=True)
        don.to_pickle(cache)
        json.dump(state, open(spath, "w"))
    return don


# ============================================================================
# Bills each member sponsored / cosponsored (congress.gov)
# ============================================================================
POLICY_SECTORS = {
    "Armed Forces and National Security": ["Industrials", "Technology"],
    "Energy": ["Energy", "Utilities"], "Health": ["Healthcare"],
    "Finance and Financial Sector": ["Financial Services"], "Taxation": ["Financial Services"],
    "Economics and Public Finance": ["Financial Services"],
    "Science, Technology, Communications": ["Technology", "Communication Services"],
    "Transportation and Public Works": ["Industrials"], "Agriculture and Food": ["Consumer Defensive", "Basic Materials"],
    "Environmental Protection": ["Energy", "Utilities", "Basic Materials"],
    "Commerce": ["Consumer Cyclical", "Technology", "Consumer Defensive"],
    "Housing and Community Development": ["Real Estate"],
    "Public Lands and Natural Resources": ["Energy", "Basic Materials"],
    "Foreign Trade and International Finance": ["Industrials", "Technology", "Basic Materials"],
    "Water Resources Development": ["Utilities"], "Emergency Management": ["Industrials"],
}


def collect_bills(cfg, bioguides):
    key = cfg.get("CONGRESS_API_KEY")
    cache, path = _cache_json(cfg, "bills.json", {})
    if not key:
        log("Bills: skipped - add a free CONGRESS_API_KEY in Settings (api.congress.gov/sign-up)")
    else:
        todo = [b for b in bioguides if b and not _fresh(cache.get(b, {}).get("fetched", ""), 7)]
        if todo:
            log(f"Bills: downloading sponsored/cosponsored bills for {len(todo)} members")
        cutoff = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
        for i, b in enumerate(todo, 1):
            rows, ok = [], True
            for kind, fld in (("sponsored-legislation", "sponsoredLegislation"),
                              ("cosponsored-legislation", "cosponsoredLegislation")):
                off = 0
                while off < 3000:
                    try:
                        js = requests.get(f"https://api.congress.gov/v3/member/{b}/{kind}",
                                          params={"api_key": key, "format": "json", "limit": 250, "offset": off},
                                          timeout=60).json()
                    except Exception:
                        ok = False
                        break
                    items = js.get(fld, [])
                    for it in items:
                        d, pa = it.get("introducedDate"), (it.get("policyArea") or {}).get("name")
                        if d and pa and d >= cutoff:
                            rows.append([d, pa, "sponsor" if kind.startswith("sp") else "cosponsor",
                                         (it.get("title") or "")[:140]])
                    if len(items) < 250 or (items and (items[-1].get("introducedDate") or "9") < cutoff):
                        break
                    off += 250
                    time.sleep(0.4)
            if ok:
                cache[b] = {"fetched": dt.datetime.now().isoformat(), "rows": rows}
            if i % 25 == 0:
                json.dump(cache, open(path, "w"))
                log(f"Bills: {i}/{len(todo)} members")
        json.dump(cache, open(path, "w"))
    out = [{"bioguide": b, "date": r[0], "policy": r[1], "role": r[2], "title": r[3]}
           for b, v in cache.items() for r in v.get("rows", [])]
    bl = pd.DataFrame(out, columns=["bioguide", "date", "policy", "role", "title"])
    bl["date"] = pd.to_datetime(bl["date"], errors="coerce")
    return bl


# ============================================================================
# Committee assignments over time (snapshots of the congress-legislators project)
# ============================================================================
SUBCOMMITTEE_SECTORS = {
    "communications": ["Communication Services", "Technology"], "technology": ["Technology"],
    "cyber": ["Technology"], "innovation": ["Technology"], "digital": ["Technology", "Financial Services"],
    "capital markets": ["Financial Services"], "financial institutions": ["Financial Services"],
    "securities": ["Financial Services"], "insurance": ["Financial Services"], "housing": ["Real Estate"],
    "health": ["Healthcare"], "energy": ["Energy", "Utilities"], "oil": ["Energy"], "mineral": ["Basic Materials"],
    "tactical": ["Industrials"], "seapower": ["Industrials"], "readiness": ["Industrials"], "strategic forces": ["Industrials"],
    "aviation": ["Industrials"], "railroad": ["Industrials"], "highways": ["Industrials"], "space": ["Industrials", "Technology"],
    "food": ["Consumer Defensive"], "commodity": ["Basic Materials", "Consumer Defensive"], "trade": ["Industrials"],
    "consumer protection": ["Consumer Cyclical", "Technology"], "manufacturing": ["Industrials"],
}


def _sectors_for(name, table):
    n = name.lower()
    return {s for k, v in table.items() if k in n for s in v}


def _gh_sha(until_iso, path):
    r = requests.get("https://api.github.com/repos/unitedstates/congress-legislators/commits",
                     params={"path": path, "until": until_iso, "per_page": 1}, headers=UA, timeout=60)
    r.raise_for_status()
    js = r.json()
    return js[0]["sha"] if js else None


def _gh_yaml(sha, path):
    import yaml
    r = requests.get(f"https://raw.githubusercontent.com/unitedstates/congress-legislators/{sha}/{path}",
                     headers=UA, timeout=120)
    r.raise_for_status()
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return yaml.load(r.text, Loader=loader)


def _snapshot(sha):
    mem = _gh_yaml(sha, "committee-membership-current.yaml")
    coms = _gh_yaml(sha, "committees-current.yaml")
    leg = _gh_yaml(sha, "legislators-current.yaml")
    top, sub = {}, {}
    for c in coms:
        tid = c.get("thomas_id")
        if not tid:
            continue
        top[tid] = c["name"]
        for sc in c.get("subcommittees") or []:
            sub[tid + str(sc.get("thomas_id"))] = f"{c['name']}: {sc.get('name')}"
    by_bio = {}
    for cid, members in (mem or {}).items():
        for m in members or []:
            b = m.get("bioguide")
            if not b:
                continue
            d = by_bio.setdefault(b, {"committees": [], "subs": [], "lead": [], "cids": []})
            d["cids"].append(cid)
            if cid in top:
                d["committees"].append(top[cid])
                t = (m.get("title") or "").lower()
                if "chair" in t or "ranking" in t:
                    d["lead"].append(top[cid])
            elif cid in sub:
                d["subs"].append(sub[cid])
    out = {}
    for p in leg:
        term = p["terms"][-1]
        chamber = "Senate" if term["type"] == "sen" else "House"
        b = p["id"]["bioguide"]
        d = by_bio.get(b, {"committees": [], "subs": [], "lead": [], "cids": []})
        rec = {"name": p["name"].get("official_full") or f"{p['name']['first']} {p['name']['last']}",
               "first": p["name"].get("first", ""), "nick": p["name"].get("nickname", ""),
               "state": term.get("state"), "party": term.get("party"), "chamber": chamber,
               "bioguide": b, "fec": p["id"].get("fec", []), "lis": p["id"].get("lis"), "cids": d["cids"],
               "committees": d["committees"], "subs": d["subs"], "lead": d["lead"],
               "sectors": sorted({s for n in d["committees"] for s in _sectors_for(n, COMMITTEE_SECTORS)}),
               "sub_sectors": sorted({s for n in d["subs"] for s in _sectors_for(n.split(":")[-1], SUBCOMMITTEE_SECTORS)}),
               "lead_sectors": sorted({s for n in d["lead"] for s in _sectors_for(n, COMMITTEE_SECTORS)})}
        lk = name_key(p["name"]["last"]).split(" ")[-1]
        out.setdefault(f"{chamber}|{lk}", []).append(rec)
    return out


def load_committee_history(cfg, current=None):
    """Quarterly snapshots of who sat on which committee, so past trades use the seats held at the time."""
    idx, idx_path = _cache_json(cfg, "committee_history_index.json", {})
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=120)
    dates = list(pd.date_range(start, pd.Timestamp.today(), freq="QS"))
    snaps = []
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        rec = idx.get(key)
        if not rec:
            try:
                sha = _gh_sha(d.strftime("%Y-%m-%dT00:00:00Z"), "committee-membership-current.yaml")
                rec = {"sha": sha}
                idx[key] = rec
                json.dump(idx, open(idx_path, "w"))
                time.sleep(1)
            except Exception as e:
                log(f"Committee history: lookup for {key} failed ({e})")
                continue
        sha = rec.get("sha")
        if not sha:
            continue
        spath = _p(cfg, "cache", "committee_snapshots", f"{sha}_v2.json")
        if os.path.exists(spath):
            snap = json.load(open(spath))
        else:
            try:
                snap = _snapshot(sha)
                json.dump(snap, open(spath, "w"))
                log(f"Committee history: loaded rosters as of {key}")
            except Exception as e:
                log(f"Committee history: snapshot {key} failed ({e})")
                continue
        snaps.append((d, snap))
    if not snaps and current:
        snaps = [(pd.Timestamp.today(), current)]
    log(f"Committee history: {len(snaps)} quarterly snapshots since {start.date()}")
    return snaps


def match_member_at(history, when, chamber, last_key, first, state):
    """Committee record for this member as of `when` (latest snapshot on or before it)."""
    if not history:
        return None
    pos = 0
    for i, (d, _) in enumerate(history):
        if d <= when:
            pos = i
    # search the matching snapshot first, then neighbours (members who joined mid-quarter)
    for j in [pos, pos + 1, pos - 1]:
        if 0 <= j < len(history):
            m = match_member(history[j][1], chamber, last_key, first, state)
            if m:
                return m
    return None


# ============================================================================
# Features. Every one uses only information public on the filing date.
# ============================================================================
def _entry_positions(px_index, dates):
    # first trading day strictly after the filing date
    return np.searchsorted(px_index.values, pd.to_datetime(dates).values, side="right")


def forward_returns(tx, px, hold):
    """Return after entering the day after disclosure and holding `hold` trading days."""
    idx = px.index
    n = len(idx)
    ent = _entry_positions(idx, tx["filed_date"])
    ext = ent + hold
    col = {c: i for i, c in enumerate(px.columns)}
    arr = px.values
    spy = px["SPY"].values
    ci = tx["ticker"].map(col)
    ok = ci.notna().values & (ent < n)
    res = pd.DataFrame(index=tx.index, columns=["entry_date", "exit_date", "entry_px", "exit_px",
                                                "ret", "spy_ret", "excess", "closed"], dtype=object)
    e_i = np.where(ok, ent, 0)
    x_i = np.minimum(np.where(ok, ext, 0), n - 1)
    c_i = np.where(ok, ci.fillna(0).astype(int).values, 0)
    p0, p1 = arr[e_i, c_i], arr[x_i, c_i]
    s0, s1 = spy[e_i], spy[x_i]
    res["entry_date"] = np.where(ok, idx.values[e_i], np.datetime64("NaT"))
    res["exit_date"] = np.where(ok, idx.values[x_i], np.datetime64("NaT"))
    res["entry_px"], res["exit_px"] = np.where(ok, p0, np.nan), np.where(ok, p1, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = p1 / p0 - 1
        sr = s1 / s0 - 1
    res["ret"] = np.where(ok, r, np.nan)
    res["spy_ret"] = np.where(ok, sr, np.nan)
    res["excess"] = res["ret"].astype(float) - res["spy_ret"].astype(float)
    res["closed"] = ok & (ext < n)
    for c in ("entry_date", "exit_date"):
        res[c] = pd.to_datetime(res[c])
    for c in ("entry_px", "exit_px", "ret", "spy_ret", "excess"):
        res[c] = res[c].astype(float)
    res["closed"] = res["closed"].astype(bool)
    return res


FACTOR_RANGE = {"track_record": (-1, 1), "sell_track_record": (-1, 1), "momentum": (-1, 1)}


def _window_lookup(events, key_col, date_col, keys, ends, days, fn, starts=None):
    """For each (key, end): apply fn to events of that key with date in (end-days, end]."""
    out = np.zeros(len(keys))
    if events is None or events.empty:
        return out
    grp = {k: g.sort_values(date_col) for k, g in events.groupby(key_col)}
    for i, (k, e) in enumerate(zip(keys, ends)):
        g = grp.get(k)
        if g is None or pd.isna(e):
            continue
        d = g[date_col].values
        lo = np.searchsorted(d, np.datetime64(e - pd.Timedelta(days=days)), side="right")
        hi = np.searchsorted(d, np.datetime64(e), side="right")
        if hi > lo:
            out[i] = fn(g.iloc[lo:hi])
    return out


def _member_track(tx, sign):
    """Shrunk mean of sign*excess over the member's earlier closed trades of that type."""
    typ = "buy" if sign > 0 else "sell"
    done = tx[(tx["tx_type"] == typ) & tx["closed"] & tx["excess"].notna()]
    val, n = np.zeros(len(tx)), np.zeros(len(tx), dtype=int)
    pos = {ix: i for i, ix in enumerate(tx.index)}
    for (ch, lk), g in tx.groupby(["chamber", "last_key"]):
        past = done[(done["chamber"] == ch) & (done["last_key"] == lk)].sort_values("exit_date")
        if past.empty:
            continue
        csum = np.concatenate([[0.0], np.cumsum((sign * past["excess"]).clip(-1, 3).values)])
        k = np.searchsorted(past["exit_date"].values, g["filed_date"].values, side="left")
        ii = [pos[x] for x in g.index]
        n[ii] = k
        val[ii] = csum[k]
    return val, n


def compute_features(tx, px, data, cfg):
    meta, committees = data["meta"], data["committees"]
    hold = cfg["HOLD_DAYS"]
    K = cfg["TRACK_PRIOR_TRADES"]
    tx = tx.copy().reset_index(drop=True)
    tx = tx.join(forward_returns(tx, px, hold))

    # member track records (buys, and sells for the short side)
    v, n = _member_track(tx, +1)
    tx["track_n"], tx["track_excess"] = n, v / (n + K)
    tx["f_track_record"] = np.tanh(tx["track_excess"] / 0.05)
    v, n = _member_track(tx, -1)
    tx["sell_track_n"], tx["sell_track_excess"] = n, v / (n + K)
    tx["f_sell_track_record"] = np.tanh(tx["sell_track_excess"] / 0.05)

    # clusters of members buying / selling the same ticker
    win = np.timedelta64(cfg["CLUSTER_WINDOW_DAYS"], "D")
    nb, ns = np.zeros(len(tx), dtype=int), np.zeros(len(tx), dtype=int)
    for t, g in tx.groupby("ticker"):
        g = g.sort_values("filed_date")
        d, who, typ = g["filed_date"].values, (g["chamber"] + "|" + g["last_key"]).values, g["tx_type"].values
        for j, ix in enumerate(g.index):
            lo = np.searchsorted(d, d[j] - win, side="right")
            hi = np.searchsorted(d, d[j], side="right")
            w, ty = who[lo:hi], typ[lo:hi]
            nb[ix], ns[ix] = len(set(w[ty == "buy"])), len(set(w[ty == "sell"]))
    tx["n_buyers"], tx["n_sellers"] = nb, ns
    tx["f_cluster"] = (tx["n_buyers"].clip(lower=1) - 1).clip(upper=4) / 4
    tx["f_sell_cluster"] = (tx["n_sellers"].clip(lower=1) - 1).clip(upper=4) / 4
    tx["f_sell_pressure"] = (tx["n_sellers"] - (tx["tx_type"] == "sell")).clip(0, 4) / 4
    tx["f_buy_pressure"] = (tx["n_buyers"] - (tx["tx_type"] == "buy")).clip(0, 4) / 4

    # member info: committees, sectors they oversee, lobbying issues, IDs
    tx["sector"] = tx["ticker"].map(lambda t: (meta.get(t) or {}).get("sector"))
    tx["company"] = tx["ticker"].map(lambda t: (meta.get(t) or {}).get("name") or "")
    history = data.get("committee_history")
    if history:
        mems = [match_member_at(history, r.trade_date, r.chamber, r.last_key, r.first, r.state)
                or match_member(committees, r.chamber, r.last_key, r.first, r.state)
                for r in tx[["chamber", "last_key", "first", "state", "trade_date"]].itertuples(index=False)]
    else:
        mems = [match_member(committees, r.chamber, r.last_key, r.first, r.state)
                for r in tx[["chamber", "last_key", "first", "state"]].itertuples(index=False)]
    tx["committees"] = ["; ".join(m["committees"]) if m else "" for m in mems]
    tx["bioguide"] = [m.get("bioguide") if m else None for m in mems]
    tx["party"] = [m.get("party") if m else None for m in mems]
    csect = [set(m["sectors"]) if m else set() for m in mems]
    cissue = [{c for n in m["committees"] for k, v in COMMITTEE_ISSUES.items() if k in n.lower() for c in v}
              if m else set() for m in mems]
    fec_ids = [set(m.get("fec") or []) if m else set() for m in mems]
    tx["f_committee"] = [1.0 if (s and s in cs) else 0.0 for s, cs in zip(tx["sector"], csect)]
    tx["f_subcommittee"] = [1.0 if (s and m and s in (m.get("sub_sectors") or [])) else 0.0
                            for s, m in zip(tx["sector"], mems)]
    tx["f_committee_leader"] = [1.0 if (s and m and s in (m.get("lead_sectors") or [])) else 0.0
                                for s, m in zip(tx["sector"], mems)]
    tx["subcommittees"] = ["; ".join(m.get("subs") or []) if m else "" for m in mems]

    # size, freshness, how fast this member usually files, full vs partial sale
    mid = ((tx["amt_lo"].fillna(1001) + tx["amt_hi"].fillna(tx["amt_lo"]).fillna(15000)) / 2).clip(lower=1)
    tx["f_size"] = ((np.log10(mid) - np.log10(8000)) / (np.log10(5e6) - np.log10(8000))).clip(0, 1)
    tx["lag_days"] = (tx["filed_date"] - tx["trade_date"]).dt.days.clip(lower=0)
    tx["f_freshness"] = (1 - tx["lag_days"] / 45).clip(0, 1)
    tx = tx.sort_values("filed_date")
    tx["member_median_lag"] = tx.groupby(["chamber", "last_key"])["lag_days"].transform(
        lambda s: s.expanding().median().shift(1))
    tx["f_fast_filer"] = (1 - tx["member_median_lag"] / 45).clip(0, 1).fillna(0.5)
    tx = tx.sort_index()
    raw = tx["tx_raw"].astype(str).str.lower() if "tx_raw" in tx else pd.Series("", index=tx.index)
    tx["f_full_sale"] = np.where(raw.str.contains("partial"), 0.0,
                                 np.where(raw.isin(["s", "sale (full)", "sale_full", "sale"]), 1.0, 0.5))

    # price momentum vs SPY over the ~3 months before filing
    idx = px.index
    pos = np.searchsorted(idx.values, tx["filed_date"].values, side="right") - 1
    colmap = {c: i for i, c in enumerate(px.columns)}
    ci = tx["ticker"].map(colmap)
    arr, spy = px.values, px["SPY"].values
    ok = ci.notna().values & (pos >= 63)
    p_i, c_i = np.where(ok, pos, 63), np.where(ok, ci.fillna(0).astype(int), 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mom = arr[p_i, c_i] / arr[p_i - 63, c_i] - spy[p_i] / spy[p_i - 63]
    tx["momentum_3m"] = np.where(ok, mom, np.nan)
    tx["f_momentum"] = np.tanh(np.nan_to_num(tx["momentum_3m"]) / 0.15)

    # how much the stock moved between the trade and the disclosure (the part we can't capture)
    tpos = np.searchsorted(idx.values, tx["trade_date"].values, side="right") - 1
    okp = ci.notna().values & (tpos >= 0) & (pos >= 0)
    t_i = np.where(okp, tpos, 0)
    p_i2 = np.where(okp, pos, 0)
    c_i2 = np.where(okp, ci.fillna(0).astype(int), 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        pre = arr[p_i2, c_i2] / arr[t_i, c_i2] - spy[p_i2] / spy[t_i]
    tx["pre_disclosure_excess"] = np.where(okp, pre, np.nan)

    ends = tx["filed_date"]
    # insiders: distinct company insiders buying / selling in the 90 days before filing
    ins = data.get("insiders")
    if ins is not None and len(ins):
        b = ins[ins["code"] == "P"]
        s = ins[ins["code"] == "S"]
        tx["insider_buyers"] = _window_lookup(b, "ticker", "filed", tx["ticker"], ends, 90, lambda g: g["owner"].nunique())
        tx["insider_sellers"] = _window_lookup(s, "ticker", "filed", tx["ticker"], ends, 90, lambda g: g["owner"].nunique())
        tx["insider_buy_value"] = _window_lookup(b, "ticker", "filed", tx["ticker"], ends, 90, lambda g: g["value"].sum())
    else:
        tx["insider_buyers"] = tx["insider_sellers"] = tx["insider_buy_value"] = 0.0
    tx["f_insider_buying"] = tx["insider_buyers"].clip(upper=3) / 3
    tx["f_insider_selling"] = tx["insider_sellers"].clip(upper=5) / 5

    # federal contracts publicly known in the 180 days before filing
    con = data.get("contracts")
    if con is not None and len(con):
        tx["contracts_180d"] = _window_lookup(con, "ticker", "known_date", tx["ticker"], ends, 180, lambda g: g["amount"].sum())
        # a contract awarded after the member traded, and public by the time the trade was disclosed
        after = np.zeros(len(tx), dtype=bool)
        cg = {k: (g["action_date"].values, g["known_date"].values) for k, g in con.dropna(subset=["action_date"]).groupby("ticker")}
        for i, (t, td, fd) in enumerate(zip(tx["ticker"], tx["trade_date"].values, tx["filed_date"].values)):
            if t in cg:
                A, K = cg[t]
                after[i] = bool(((A > td) & (A <= fd) & (K <= fd)).any())
        tx["contract_after_trade"] = after
    else:
        tx["contracts_180d"], tx["contract_after_trade"] = 0.0, False
    tx["f_contracts"] = ((np.log10(tx["contracts_180d"] + 1) - 6) / 3).clip(0, 1)

    # lobbying in the prior year on issues this member's committees handle
    lb = data.get("lobbying")
    if lb is not None and len(lb):
        lbg = {k: g.sort_values("posted") for k, g in lb.dropna(subset=["posted"]).groupby("ticker")}
        f, spend = np.zeros(len(tx)), np.zeros(len(tx))
        for i, (t, e, iss) in enumerate(zip(tx["ticker"], ends, cissue)):
            g = lbg.get(t)
            if g is None:
                continue
            w = g[(g["posted"] > e - pd.Timedelta(days=365)) & (g["posted"] <= e)]
            if len(w):
                spend[i] = w["amount"].sum()
                codes = {c for cs in w["issues"] for c in cs}
                f[i] = 1.0 if codes & iss else 0.3
        tx["f_lobbying"], tx["lobbying_12m"] = f, spend
    else:
        tx["f_lobbying"], tx["lobbying_12m"] = 0.0, 0.0

    # donations from the company's PAC to this member (4 years before, known 60+ days before filing)
    don = data.get("donations")
    tx["donations_4y"] = 0.0
    if don is not None and len(don):
        orgs = don["org"].dropna().unique()
        tk_orgs = {}
        for t in tx["ticker"].unique():
            k = company_key((meta.get(t) or {}).get("name"))
            if len(k) >= 3:
                tk_orgs[t] = {o for o in orgs if o == k or o.startswith(k + " ")}
        dsub = don[don["org"].isin(set().union(*tk_orgs.values()) if tk_orgs else set())]
        by = {k: g for k, g in dsub.groupby(["org", "cand_id"])}
        vals = np.zeros(len(tx))
        for i, (t, e, fids) in enumerate(zip(tx["ticker"], ends, fec_ids)):
            if not fids or t not in tk_orgs:
                continue
            tot = 0.0
            for o in tk_orgs[t]:
                for c in fids:
                    g = by.get((o, c))
                    if g is not None:
                        m = (g["date"] <= e - pd.Timedelta(days=60)) & (g["date"] > e - pd.Timedelta(days=1461))
                        tot += g.loc[m, "amount"].sum()
            vals[i] = tot
        tx["donations_4y"] = vals
    tx["f_donations"] = (tx["donations_4y"] / 10000).clip(0, 1)

    # bills the member sponsored/cosponsored in the ticker's sector (180 days before filing)
    bl = data.get("bills")
    tx["f_bills"], tx["bill_note"] = 0.0, ""
    if bl is not None and len(bl):
        bl = bl.assign(sectors=bl["policy"].map(lambda p: set(POLICY_SECTORS.get(p, []))))
        blg = {k: g for k, g in bl.groupby("bioguide")}
        f, note = np.zeros(len(tx)), [""] * len(tx)
        for i, (b, s, e) in enumerate(zip(tx["bioguide"], tx["sector"], ends)):
            g = blg.get(b)
            if g is None or not s:
                continue
            w = g[(g["date"] > e - pd.Timedelta(days=180)) & (g["date"] <= e) & g["sectors"].map(lambda x: s in x)]
            if len(w):
                sp = (w["role"] == "sponsor").any()
                f[i] = 1.0 if sp else 0.5
                note[i] = f"{'sponsored' if sp else 'cosponsored'} {len(w)} {w['policy'].iloc[0]} bill(s)"
        tx["f_bills"], tx["bill_note"] = f, note
    tx = compute_connections(tx, data, cfg, mems)
    return tx


def _score(tx, weights):
    raw = sum(w * tx[f"f_{k}"] for k, w in weights.items() if f"f_{k}" in tx)
    lo = sum(w * (FACTOR_RANGE.get(k, (0, 1))[0 if w > 0 else 1]) for k, w in weights.items())
    hi = sum(w * (FACTOR_RANGE.get(k, (0, 1))[1 if w > 0 else 0]) for k, w in weights.items())
    return ((raw - lo) / max(hi - lo, 1e-9) * 100).clip(0, 100).round(1)


def active_weights(cfg):
    bw, sw = cfg["BUY_WEIGHTS"], cfg["SHORT_WEIGHTS"]
    path = os.path.join(cfg["DATA_DIR"], "state", "tuned_weights.json")
    if cfg.get("USE_TUNED_WEIGHTS") and os.path.exists(path):
        t = json.load(open(path))
        bw, sw = t.get("buy", bw), t.get("short", sw)
        log("Using tuned weights from the last tuning run")
    return bw, sw


def apply_scores(tx, cfg, buy_w=None, short_w=None):
    bw, sw = active_weights(cfg)
    tx = tx.copy()
    tx["score"] = _score(tx, buy_w or bw)
    tx["short_score"] = _score(tx, short_w or sw)
    return tx


# ============================================================================
# Explanations
# ============================================================================
def _why_buy(r):
    b = []
    if r["track_n"] >= 3:
        b.append(f"member's past buys {r['track_excess']*100:+.1f}% vs SPY avg ({int(r['track_n'])})")
    if r["n_buyers"] >= 2:
        b.append(f"{int(r['n_buyers'])} members bought within 30d")
    if r["f_committee_leader"] > 0:
        b.append(f"chairs or leads a committee over {r['sector']}")
    elif r["f_committee"] > 0:
        b.append(f"committee oversees {r['sector']}")
    if r["f_subcommittee"] > 0:
        b.append("sits on the subcommittee for this industry")
    if r["insider_buyers"] > 0:
        b.append(f"{int(r['insider_buyers'])} company insider(s) bought")
    if r["f_contracts"] > 0:
        b.append(f"${r['contracts_180d']/1e6:,.0f}M federal contracts")
    if r.get("contract_after_trade"):
        b.append("contract awarded after the trade")
    if r["f_lobbying"] >= 1:
        b.append("company lobbies this member's committees")
    if r["donations_4y"] > 0:
        b.append(f"company PAC gave member ${r['donations_4y']:,.0f}")
    if r.get("bill_note"):
        b.append(r["bill_note"])
    for f, txt in (("f_employee_donations", lambda: f"company employees gave ${r['employee_donations']:,.0f} to the campaign"),
                   ("f_already_owned", lambda: "already held it per yearly disclosure"),
                   ("f_disclosure_tie", lambda: "company named in their yearly disclosure (job, income, travel or gift)"),
                   ("f_revolving_door", lambda: "company's lobbyists used to work for this member"),
                   ("f_testified", lambda: "company testified before their committee"),
                   ("f_closed_briefing", lambda: "closed committee briefing shortly before the trade"),
                   ("f_vote_sector", lambda: "voted on a bill in this industry near the trade"),
                   ("f_home_state", lambda: "company is based in their state"),
                   ("f_contract_in_state", lambda: "company does federal contract work in their state"),
                   ("f_speech", lambda: "named the company in a floor speech"),
                   ("f_social_post", lambda: "posted about the company"),
                   ("f_pre_event", lambda: "traded shortly before a company event"),
                   ("f_committee_cluster", lambda: "a committee colleague made the same trade"),
                   ("f_option", lambda: "used options"),
                   ("f_spouse", lambda: "spouse or family account"),
                   ("f_first_time", lambda: "first time trading this stock"),
                   ("f_unusual_size", lambda: "3x their usual trade size"),
                   ("f_late", lambda: f"filed late ({int(r['lag_days'])}d)")):
        if r.get(f, 0) and r.get(f, 0) >= 0.5:
            b.append(txt())
    if r["f_size"] >= 0.4:
        b.append(f"large (${r['amt_lo']:,.0f}+)")
    if r["f_freshness"] >= 0.7:
        b.append(f"disclosed in {int(r['lag_days'])}d")
    if r["n_sellers"] >= 2:
        b.append(f"but {int(r['n_sellers'])} members sold")
    return "; ".join(b) or "baseline signal"


def _why_short(r):
    b = []
    if r["sell_track_n"] >= 3:
        b.append(f"stocks this member sold lagged SPY by {r['sell_track_excess']*100:.1f}% avg ({int(r['sell_track_n'])})")
    if r["n_sellers"] >= 2:
        b.append(f"{int(r['n_sellers'])} members sold within 30d")
    if r["f_full_sale"] >= 1:
        b.append("sold the entire position")
    if r["f_committee"] > 0:
        b.append(f"committee oversees {r['sector']}")
    if r["insider_sellers"] > 0:
        b.append(f"{int(r['insider_sellers'])} company insider(s) sold")
    if r["f_size"] >= 0.4:
        b.append(f"large (${r['amt_lo']:,.0f}+)")
    if r["momentum_3m"] < -0.05:
        b.append(f"already lagging SPY {r['momentum_3m']*100:.0f}% over 3m")
    for f, txt in (("f_option", "bought puts" if str(r.get("tx_raw")) == "put purchase" else "options trade"),
                   ("f_testified", "company testified before their committee"),
                   ("f_closed_briefing", "closed committee briefing shortly before the sale"),
                   ("f_vote_sector", "voted on a bill in this industry near the sale"),
                   ("f_pre_event", "sold shortly before a company event"),
                   ("f_committee_cluster", "a committee colleague also sold"),
                   ("f_first_time", "first sale of this stock"), ("f_unusual_size", "3x their usual size"),
                   ("f_spouse", "spouse or family account"), ("f_late", "filed late")):
        if r.get(f, 0) and r.get(f, 0) >= 0.5:
            b.append(txt)
    return "; ".join(b) or "baseline signal"


def ticker_level(rows, score_col="score", short=False):
    """Collapse multiple disclosures of one ticker into one candidate (best score)."""
    if rows.empty:
        return rows
    rows = rows.sort_values(score_col, ascending=False)
    best = rows.drop_duplicates("ticker").copy()
    mem = rows.groupby("ticker")["member"].agg(lambda s: ", ".join(sorted(set(s))))
    best["members"] = best["ticker"].map(mem)
    best["why"] = best.apply(_why_short if short else _why_buy, axis=1)
    return best.reset_index(drop=True)


# ============================================================================
# Backtest
# ============================================================================
def _perf(daily):
    daily = daily.fillna(0)
    eq = (1 + daily).cumprod()
    yrs = max(len(daily) / 252, 1e-9)
    sd = daily.std()
    return {"Total return": eq.iloc[-1] - 1, "Annual return (CAGR)": eq.iloc[-1] ** (1 / yrs) - 1,
            "Volatility": sd * np.sqrt(252), "Sharpe": daily.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
            "Worst drawdown": (eq / eq.cummax() - 1).min()}, eq


def _portfolio_slots(picks, px, idx, slots, idle="spy", sign=1):
    """Each pick gets 1/slots of the money; money not in a pick sits in SPY (or cash)."""
    rets = px.pct_change(fill_method=None).reindex(idx)
    spy = rets["SPY"].fillna(0).values
    col = {c: i for i, c in enumerate(px.columns)}
    R = rets.values
    acc, cnt = np.zeros(len(idx)), np.zeros(len(idx))
    for p in picks.itertuples():
        if p.ticker not in col or pd.isna(p.entry_date):
            continue
        a = idx.searchsorted(p.entry_date, side="right")
        b = idx.searchsorted(p.exit_date, side="right")
        acc[a:b] += sign * np.nan_to_num(R[a:b, col[p.ticker]])
        cnt[a:b] += 1
    over = cnt > slots                      # more open picks than slots: scale them down
    w = np.where(over, 1 / np.maximum(cnt, 1), 1 / slots)
    invested = np.minimum(cnt, slots) / slots
    fill = spy if idle == "spy" else 0.0
    return pd.Series(acc * w + (1 - invested) * fill, index=idx), pd.Series(cnt, index=idx)


def _portfolio(picks, px, idx, sign=1):
    rets = px.pct_change(fill_method=None).reindex(idx)
    col = {c: i for i, c in enumerate(px.columns)}
    R = rets.values
    acc, cnt = np.zeros(len(idx)), np.zeros(len(idx))
    for p in picks.itertuples():
        if p.ticker not in col or pd.isna(p.entry_date):
            continue
        a = idx.searchsorted(p.entry_date, side="right")
        b = idx.searchsorted(p.exit_date, side="right")
        acc[a:b] += sign * np.nan_to_num(R[a:b, col[p.ticker]])
        cnt[a:b] += 1
    return pd.Series(np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.0), index=idx)


def _select(rows, score_col, pct, per_week, blocked=None, short=False, per_member=None, skip_late=False):
    """Weekly picks: clears the percentile of all EARLIER scores, best first, no re-buying an open ticker,
    at most `per_member` picks per member each week, and (optionally) no trades filed past the 45-day deadline."""
    rows = rows.sort_values("filed_date")
    rows = rows.assign(week=rows["filed_date"].dt.to_period("W-FRI"))
    hist = np.array([])
    picks, held = [], {}
    blocked = blocked or {}
    for wk, g in rows.groupby("week"):
        bar = np.percentile(hist, pct) if len(hist) >= 100 else np.inf
        hist = np.concatenate([hist, g[score_col].values])
        cand = ticker_level(g, score_col, short)
        cand = cand[cand[score_col] >= bar]
        if skip_late and "lag_days" in cand:
            cand = cand[cand["lag_days"] <= 45]
        taken, by_member = 0, {}
        for _, r in cand.iterrows():
            if taken >= per_week:
                break
            if per_member and by_member.get(r["member"], 0) >= per_member:
                continue
            t = r["ticker"]
            if (held.get(t) is not None and r["entry_date"] <= held[t]) or \
               (blocked.get(t) is not None and r["entry_date"] <= blocked[t]):
                continue
            r = r.copy()
            r["bar"] = bar
            picks.append(r)
            held[t] = r["exit_date"]
            taken += 1
            by_member[r["member"]] = by_member.get(r["member"], 0) + 1
    return pd.DataFrame(picks).reset_index(drop=True), held


def _attribution(rows, factors, sign):
    out = []
    y = sign * rows["excess"]
    for f, label in factors:
        s = rows[f]
        if s.nunique() <= 1:
            continue
        if set(s.unique()) <= {0.0, 1.0}:
            grp = np.where(s > 0, "yes", "no")
        elif (s == 0).mean() > 0.6:
            grp = np.where(s > 0, "yes", "no")
        else:
            q = s.rank(pct=True)
            grp = np.where(q > 0.8, "high", np.where(q <= 0.2, "low", "mid"))
        for gname in ("no", "yes", "low", "mid", "high"):
            m = grp == gname
            if m.sum():
                out.append({"Signal": label, "Group": gname, "Trades": int(m.sum()),
                            "Avg result vs SPY": y[m].mean(), "Worked (%)": (y[m] > 0).mean() * 100})
    return pd.DataFrame(out)


BUY_FACTORS = [("f_track_record", "Member track record"), ("f_cluster", "Cluster buying"),
               ("f_committee", "Committee relevance"), ("f_subcommittee", "Subcommittee relevance"),
               ("f_committee_leader", "Chairs or leads that committee"), ("f_insider_buying", "Company insiders buying"),
               ("f_insider_selling", "Company insiders selling"), ("f_contracts", "Federal contracts"),
               ("f_lobbying", "Lobbying link"), ("f_donations", "PAC donations to member"),
               ("f_bills", "Member's bills in sector"), ("f_size", "Trade size"), ("f_freshness", "Freshness"),
               ("f_fast_filer", "Member files fast"), ("f_sell_pressure", "Others selling"),
               ("f_momentum", "Prior 3m momentum"), ("f_employee_donations", "Employee donations to member"), ("f_already_owned", "Already held it"),
               ("f_disclosure_tie", "Company in yearly disclosure"), ("f_revolving_door", "Lobbyists are ex-staff"),
               ("f_testified", "Company testified to their committee"), ("f_closed_briefing", "Closed briefing before trade"),
               ("f_vote_sector", "Voted on industry bill near trade"), ("f_home_state", "Company based in their state"),
               ("f_contract_in_state", "Contracts in their state"), ("f_speech", "Named company in floor speech"),
               ("f_social_post", "Posted about company"), ("f_pre_event", "Traded before company event"),
               ("f_committee_cluster", "Committee colleague same trade"), ("f_option", "Options trade"),
               ("f_spouse", "Spouse or family account"), ("f_first_time", "First trade in this stock"),
               ("f_unusual_size", "Unusually large for them"), ("f_new_sector", "New industry for them"),
               ("f_late", "Filed late"), ("score", "Overall buy score")]
SHORT_FACTORS = [("f_sell_track_record", "Member sell track record"), ("f_sell_cluster", "Cluster selling"),
                 ("f_full_sale", "Sold entire position"), ("f_committee", "Committee relevance"),
                 ("f_subcommittee", "Subcommittee relevance"), ("f_committee_leader", "Chairs or leads that committee"),
                 ("f_insider_selling", "Company insiders selling"), ("f_insider_buying", "Company insiders buying"),
                 ("f_size", "Trade size"), ("f_freshness", "Freshness"), ("f_buy_pressure", "Others buying"),
                 ("f_momentum", "Prior 3m momentum"), ("f_option", "Options trade (incl. puts)"),
                 ("f_testified", "Company testified to their committee"), ("f_closed_briefing", "Closed briefing before sale"),
                 ("f_vote_sector", "Voted on industry bill near sale"), ("f_pre_event", "Sold before company event"),
                 ("f_committee_cluster", "Committee colleague same trade"), ("f_first_time", "First sale of this stock"),
                 ("f_unusual_size", "Unusually large for them"), ("f_spouse", "Spouse or family account"),
                 ("f_late", "Filed late"), ("short_score", "Overall short score")]


def _trade_stats(rows, sign):
    c = rows[rows["closed"]]
    y = sign * c["excess"]
    return {"Trades": len(rows), "Closed trades": len(c),
            "Made money (%)": ((sign * c["ret"]) > 0).mean() * 100 if len(c) else np.nan,
            "Beat SPY (%)": (y > 0).mean() * 100 if len(c) else np.nan,
            "Avg return per trade (%)": (sign * c["ret"]).mean() * 100 if len(c) else np.nan,
            "Avg vs SPY per trade (%)": y.mean() * 100 if len(c) else np.nan,
            "Median vs SPY (%)": y.median() * 100 if len(c) else np.nan}


def run_backtest(scored, px, cfg, since=None):
    all_buys = scored[(scored["tx_type"] == "buy") & scored["entry_px"].notna()]
    all_sells = scored[(scored["tx_type"] == "sell") & scored["entry_px"].notna()]
    longs, held = _select(all_buys, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"],
                          per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), skip_late=cfg.get("SKIP_LATE_FILINGS"))
    # shorts are still tested (so the report shows whether they would have worked) even when turned off
    shorts, _ = _select(all_sells, "short_score", cfg["SHORT_PERCENTILE"], cfg["SHORTS_PER_WEEK"] or 2, blocked=held,
                        short=True, per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), skip_late=cfg.get("SKIP_LATE_FILINGS"))
    buys, sells = all_buys, all_sells
    if since is not None:
        since = pd.Timestamp(since)
        longs = longs[longs["filed_date"] >= since] if len(longs) else longs
        shorts = shorts[shorts["filed_date"] >= since] if len(shorts) else shorts
        buys, sells = all_buys[all_buys["filed_date"] >= since], all_sells[all_sells["filed_date"] >= since]
    start = buys["entry_date"].min()
    idx = px.index[px.index >= start]
    slots_l = max(1, int(cfg["PICKS_PER_WEEK"] * cfg["HOLD_DAYS"] / 5))
    slots_s = max(1, int(cfg["SHORTS_PER_WEEK"] * cfg["HOLD_DAYS"] / 5))
    empty = pd.Series(0.0, index=idx)
    ld, n_long = _portfolio_slots(longs, px, idx, slots_l, "spy") if len(longs) else (px["SPY"].pct_change(fill_method=None).reindex(idx), empty)
    sd, _ = _portfolio_slots(shorts, px, idx, slots_s, "cash", sign=-1) if len(shorts) else (empty, empty)
    a = cfg["SHORT_ALLOCATION"]
    series = {"Long picks": ld, "Short picks": sd,
              **({f"Long {100-a*100:.0f}% / short {a*100:.0f}%": (1 - a) * ld + a * sd} if cfg.get("ENABLE_SHORTS") else {}),
              "Copy every purchase": _portfolio(buys.drop_duplicates(["ticker", "filed_date"]), px, idx),
              "S&P 500 (SPY)": px["SPY"].pct_change(fill_method=None).reindex(idx)}
    perf, curves = {}, {}
    for k, d in series.items():
        perf[k], curves[k] = _perf(d)

    trade_stats = pd.DataFrame({"Long picks": _trade_stats(longs, 1) if len(longs) else {},
                                "All disclosed purchases": _trade_stats(buys, 1),
                                "Short picks": _trade_stats(shorts, -1) if len(shorts) else {},
                                "All disclosed sales (as shorts)": _trade_stats(sells, -1)})
    cb, cs = buys[buys["closed"]], sells[sells["closed"]]

    # the disclosure delay: how much of the move happens before we can see the trade
    lag = []
    for lo, hi, lab in ((0, 7, "0-7 days"), (8, 20, "8-20 days"), (21, 45, "21-45 days"), (46, 10**6, "over 45 days")):
        m = (cb["lag_days"] >= lo) & (cb["lag_days"] <= hi)
        if m.sum():
            lag.append({"Disclosed after": lab, "Purchases": int(m.sum()),
                        "Move before disclosure (vs SPY)": cb.loc[m, "pre_disclosure_excess"].mean(),
                        f"Move after disclosure, {cfg['HOLD_DAYS']}d (vs SPY)": cb.loc[m, "excess"].mean(),
                        "Beat SPY after disclosure (%)": (cb.loc[m, "excess"] > 0).mean() * 100})
    closed_l = longs[longs["closed"]] if len(longs) else longs
    if len(closed_l) >= 20:
        mid = closed_l["entry_date"].median()
        halves = {"First half": closed_l[closed_l["entry_date"] <= mid], "Second half": closed_l[closed_l["entry_date"] > mid]}
        stab = pd.DataFrame({k: {"From": v["entry_date"].min().date(), "To": v["entry_date"].max().date(),
                                 "Trades": len(v), "Avg vs SPY (%)": v["excess"].mean() * 100,
                                 "Beat SPY (%)": (v["excess"] > 0).mean() * 100} for k, v in halves.items()})
    else:
        stab = pd.DataFrame()

    def fmt_trades(p, short):
        if p.empty:
            return p
        cols = ["filed_date", "ticker", "company", "members", "sector", "short_score" if short else "score", "why",
                "entry_date", "entry_px", "exit_date", "exit_px", "ret", "spy_ret", "excess", "closed"]
        t = p[cols].rename(columns={"short_score": "score"}).copy()
        if short:
            t["result"] = np.where(~t["closed"], "open", np.where(t["excess"] < 0, "worked (lagged SPY)",
                                   np.where(t["ret"] < 0, "fell but beat SPY", "went up")))
        else:
            t["result"] = np.where(~t["closed"], "open", np.where(t["excess"] > 0, "beat SPY",
                                   np.where(t["ret"] > 0, "gained, lagged SPY", "lost money")))
        return t

    coverage = {"Transactions": len(scored), "Members": scored["member"].nunique(), "Tickers": scored["ticker"].nunique(),
                "With committee data": int((scored["committees"] != "").sum()),
                "With insider activity": int(((scored["insider_buyers"] + scored["insider_sellers"]) > 0).sum()),
                "With federal contracts": int((scored["contracts_180d"] > 0).sum()),
                "With lobbying": int((scored["lobbying_12m"] > 0).sum()),
                "With PAC donations to member": int((scored["donations_4y"] > 0).sum()),
                "With related bills": int((scored["f_bills"] > 0).sum()),
                **{lab: int((scored[f] > 0).sum()) for f, lab in (
                    ("f_employee_donations", "With employee donations"), ("f_already_owned", "Already held"),
                    ("f_disclosure_tie", "Named in yearly disclosure"), ("f_revolving_door", "With revolving-door lobbyists"),
                    ("f_testified", "Company testified"), ("f_closed_briefing", "After a closed briefing"),
                    ("f_vote_sector", "Near an industry vote"), ("f_home_state", "Home-state company"),
                    ("f_contract_in_state", "Contracts in member's state"), ("f_speech", "Floor speech mention"),
                    ("f_social_post", "Social post mention"), ("f_pre_event", "Before a company event"),
                    ("f_option", "Options trades"), ("f_spouse", "Spouse or family")) if f in scored}}
    return {"longs": fmt_trades(longs, False), "shorts": fmt_trades(shorts, True), "perf": pd.DataFrame(perf),
            "curves": pd.DataFrame(curves), "trade_stats": trade_stats,
            "buy_factors": _attribution(cb, BUY_FACTORS, 1), "short_factors": _attribution(cs, SHORT_FACTORS, -1),
            "lag": pd.DataFrame(lag), "stability": stab, "coverage": coverage, "since": since, "cfg": cfg,
            "avg_open": float(n_long[n_long > 0].mean()) if len(n_long) and (n_long > 0).any() else 0.0,
            "slots": slots_l}


# ============================================================================
# Weight tuning: learn on early years, test on later years
# ============================================================================
def _fit(rows, names, y, defaults):
    """Ridge regression of the trade's result on the signals. Signals with too little data keep their default."""
    X = rows[[f"f_{n}" for n in names]].values.astype(float)
    mode = np.array([pd.Series(X[:, j]).mode().iloc[0] for j in range(X.shape[1])])
    usable = ((X != mode).sum(0) >= 30)
    out = {}
    if usable.sum():
        Xu = X[:, usable]
        mu, sd = Xu.mean(0), Xu.std(0)
        sd[sd == 0] = 1
        Xs = (Xu - mu) / sd
        lam = 0.5 * len(Xs)
        w = np.linalg.solve(Xs.T @ Xs + lam * np.eye(Xs.shape[1]), Xs.T @ (y - y.mean())) / sd
        w = w / max(np.abs(w).max(), 1e-12)
        out = dict(zip([n for n, u in zip(names, usable) if u], w))
    return {n: round(float(out[n]), 3) if n in out else defaults[n] for n in names}


def tune_weights(scored, px, cfg):
    split = pd.Timestamp(cfg["TRAIN_TEST_SPLIT"])
    tr = scored[scored["closed"] & scored["excess"].notna() & (scored["exit_date"] < split)]
    trb, trs = tr[tr["tx_type"] == "buy"], tr[tr["tx_type"] == "sell"]
    if len(trb) < 200:
        raise RuntimeError(f"Only {len(trb)} completed purchases before {split.date()}; move TRAIN_TEST_SPLIT later.")
    bw = _fit(trb, list(cfg["BUY_WEIGHTS"]), trb["excess"].clip(-0.5, 1).values, cfg["BUY_WEIGHTS"])
    sw = (_fit(trs, list(cfg["SHORT_WEIGHTS"]), (-trs["excess"]).clip(-1, 0.5).values, cfg["SHORT_WEIGHTS"])
          if len(trs) >= 200 else dict(cfg["SHORT_WEIGHTS"]))
    log(f"Tuned on {len(trb):,} purchases and {len(trs):,} sales that finished before {split.date()}")
    default = apply_scores(scored, dict(cfg, USE_TUNED_WEIGHTS=False), cfg["BUY_WEIGHTS"], cfg["SHORT_WEIGHTS"])
    tuned = apply_scores(scored, cfg, bw, sw)
    bd, bt = run_backtest(default, px, cfg, since=split), run_backtest(tuned, px, cfg, since=split)
    rows = {}
    for name, b in (("Default weights", bd), ("Tuned weights", bt)):
        ts = b["trade_stats"]
        rows[name] = {"Long annual return": b["perf"].loc["Annual return (CAGR)", "Long picks"],
                      "Long picks": ts.loc["Trades", "Long picks"] if "Long picks" in ts else 0,
                      "Long avg vs SPY": ts.loc["Avg vs SPY per trade (%)", "Long picks"] / 100 if "Long picks" in ts else np.nan,
                      "Long beat SPY": ts.loc["Beat SPY (%)", "Long picks"] / 100 if "Long picks" in ts else np.nan,
                      "Short picks": ts.loc["Trades", "Short picks"] if "Short picks" in ts else 0,
                      "Short avg result vs SPY": ts.loc["Avg vs SPY per trade (%)", "Short picks"] / 100 if "Short picks" in ts else np.nan}
    rows["S&P 500 (SPY)"] = {"Long annual return": bd["perf"].loc["Annual return (CAGR)", "S&P 500 (SPY)"]}
    comp = pd.DataFrame(rows).T
    def wtab(d, t, rows):
        n = {k: int((rows[f"f_{k}"] != rows[f"f_{k}"].mode().iloc[0]).sum()) for k in d}
        return pd.DataFrame({"Default": pd.Series(d), "Tuned": pd.Series(t), "Training trades with this signal": pd.Series(n)})
    wt, swt = wtab(cfg["BUY_WEIGHTS"], bw, trb), wtab(cfg["SHORT_WEIGHTS"], sw, trs)
    json.dump({"buy": bw, "short": sw, "split": str(split.date()), "made": dt.datetime.now().isoformat()},
              open(_p(cfg, "state", "tuned_weights.json"), "w"), indent=1)
    better = (comp.loc["Tuned weights", "Long avg vs SPY"] > comp.loc["Default weights", "Long avg vs SPY"]
              and comp.loc["Tuned weights", "Long picks"] >= 30)
    return {"comparison": comp, "buy_weights": wt, "short_weights": swt, "split": split, "tuned_better": bool(better),
            "tuned_bt": bt, "default_bt": bd}


def tuning_report(res):
    comp = res["comparison"].reset_index().rename(columns={"index": ""})
    verdict = ("Tuned weights did better on the years they never saw. Set USE_TUNED_WEIGHTS = True in Settings to use them."
               if res["tuned_better"] else
               "Tuned weights did not beat the defaults on the unseen years, so keep the defaults. "
               "The pattern in the training years didn't hold up.")
    wt = res["buy_weights"].reset_index().rename(columns={"index": "Buy signal"})
    swt = res["short_weights"].reset_index().rename(columns={"index": "Short signal"})
    for d in (wt, swt):
        d["Note"] = ["kept default: too few trades" if n < 30 else "" for n in d["Training trades with this signal"]]
    return f"""<div class='ct'>{CSS}<h1>Weight tuning</h1>
<p class='sub'>Weights learned from trades that finished before {res['split'].date()}, then tested only on trades
disclosed after it.</p><p class='note'><b>{verdict}</b></p>
<h2>Results on the unseen years</h2>{_table(comp, pct_cols=('Long annual return', 'Long avg vs SPY', 'Short avg result vs SPY'), plain_pct=('Long beat SPY',), int_cols=('Long picks', 'Short picks'))}
<h2>Buy weights</h2>{_table(wt, num_cols=('Default', 'Tuned'), int_cols=('Training trades with this signal',))}
<h2>Short weights</h2>{_table(swt, num_cols=('Default', 'Tuned'), int_cols=('Training trades with this signal',))}
<p class='note'>Tuned weights are only worth using if they win on the unseen years above. Signals that stay the same for a
member (like how fast they file) can end up standing in for one successful member, so re-run tuning after each big data update.</p></div>"""


# ============================================================================
# Watchlist (buy / short / avoid) with live context
# ============================================================================
def _news(name, days=30):
    try:
        q = f'"{name}"'
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", timeout=30,
                         params={"query": q, "mode": "artlist", "maxrecords": 100, "sort": "datedesc",
                                 "timespan": f"{days}d", "format": "json"})
        arts = r.json().get("articles", [])
    except Exception:
        return None
    dates = pd.to_datetime([a.get("seendate") for a in arts], format="%Y%m%dT%H%M%SZ", errors="coerce")
    wk = int((dates >= pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=7)).sum())
    heads = [f"<a href='{a.get('url')}' target='_blank'>{(a.get('title') or '')[:90]}</a>" for a in arts[:2]]
    return {"news_7d": wk, "news_30d": len(arts), "headlines": "<br>".join(heads)}


def _market_context(t):
    import yfinance as yf
    try:
        tk = yf.Ticker(t)
        i = tk.info or {}
        nxt = None
        try:
            cal = tk.calendar
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            nxt = str(ed[0]) if ed else None
        except Exception:
            pass
        return {"analysts": i.get("recommendationKey"), "target_upside_%":
                (i.get("targetMeanPrice") / i.get("currentPrice") - 1) * 100 if i.get("targetMeanPrice") and i.get("currentPrice") else np.nan,
                "fwd_PE": i.get("forwardPE"), "next_earnings": nxt}
    except Exception:
        return {}


def build_watchlist(scored, px, cfg, enrich_top=20):
    since = pd.Timestamp.today().normalize() - pd.Timedelta(days=cfg["WATCHLIST_LOOKBACK_DAYS"])
    recent = scored[scored["filed_date"] >= since]
    hist_b = scored.loc[scored["tx_type"] == "buy", "score"].values
    hist_s = scored.loc[scored["tx_type"] == "sell", "short_score"].values
    last = px.ffill().iloc[-1]

    def pct(v, hist):
        return float((hist < v).mean() * 100) if len(hist) else np.nan

    def prices(df):
        df["price_at_filing"] = [px[t].asof(d) if t in px.columns else np.nan for t, d in zip(df["ticker"], df["filed_date"])]
        df["price_now"] = [last.get(t, np.nan) for t in df["ticker"]]
        df["move_since_filing"] = df["price_now"] / df["price_at_filing"] - 1
        return df

    buys = ticker_level(recent[recent["tx_type"] == "buy"], "score")
    if len(buys):
        buys = prices(buys.sort_values("score", ascending=False).reset_index(drop=True))
        buys["percentile"] = [pct(v, hist_b) for v in buys["score"]]
        buys["action"] = np.where(buys["percentile"] >= cfg["PICK_PERCENTILE"], "BUY", "watch")
        if cfg.get("SKIP_LATE_FILINGS"):
            late = buys["lag_days"] > 45
            buys.loc[late & (buys["action"] == "BUY"), "action"] = "watch"
            buys.loc[late, "why"] = buys.loc[late, "why"] + "; filed after the 45-day deadline, so not a buy"
        cap = cfg.get("MAX_WATCHLIST_BUYS_PER_MEMBER")
        if cap:
            n = buys[buys["action"] == "BUY"].groupby("member").cumcount()
            over = n[n >= cap].index
            buys.loc[over, "action"] = "watch"
            buys.loc[over, "why"] = buys.loc[over, "why"] + f"; this member already has {cap} buy picks"
        buys.loc[(buys["n_sellers"] >= 2) & (buys["action"] == "watch"), "action"] = "mixed"
    sells = ticker_level(recent[recent["tx_type"] == "sell"], "short_score", short=True)
    if len(sells):
        sells = prices(sells.sort_values("short_score", ascending=False).reset_index(drop=True))
        sells["percentile"] = [pct(v, hist_s) for v in sells["short_score"]]
        strong = "SHORT" if cfg.get("ENABLE_SHORTS") else "avoid"
        sells["action"] = np.where(sells["percentile"] >= cfg["SHORT_PERCENTILE"], strong,
                                   np.where(sells["percentile"] >= cfg["AVOID_PERCENTILE"], "avoid", ""))
        sells = sells[sells["action"] != ""].reset_index(drop=True)
        if len(buys):
            sells = sells[~sells["ticker"].isin(buys.loc[buys["action"] == "BUY", "ticker"])].reset_index(drop=True)

    # live context for the top of each list (not part of the score: it isn't available historically)
    top = list(buys["ticker"].head(enrich_top)) if len(buys) else []
    top += list(sells["ticker"].head(max(5, enrich_top // 2))) if len(sells) else []
    ctx = {}
    if top:
        log(f"Watchlist: fetching analyst, earnings{' and news' if cfg.get('USE_NEWS') else ''} context for {len(top)} tickers")
    names = {}
    for df in (buys, sells):
        if len(df):
            names.update(dict(zip(df["ticker"], df["company"])))
    firsts = {}
    for df in (buys, sells):
        if len(df):
            firsts.update({t: (m.split(",")[0].split(" ")[-1] if m else "") for t, m in zip(df["ticker"], df["members"])})
    for t in top:
        c = _market_context(t)
        if cfg.get("USE_NEWS") and names.get(t) and firsts.get(t):
            c["news_with_member"] = news_together(firsts[t], company_key(names[t]) or t)
            time.sleep(3)
        if cfg.get("USE_NEWS") and names.get(t):
            n = _news(company_key(names[t]) or t)
            if n:
                c.update(n)
            time.sleep(3)
        ctx[t] = c
    for df in (buys, sells):
        if len(df):
            for k in ("analysts", "target_upside_%", "fwd_PE", "next_earnings", "news_7d", "news_30d", "headlines",
                      "news_with_member"):
                df[k] = [ctx.get(t, {}).get(k) for t in df["ticker"]]
            df.insert(0, "rank", range(1, len(df) + 1))
    return buys, sells


def diff_alerts(buys, sells, cfg):
    state_path = _p(cfg, "state", "last_watchlist.json")
    prev = json.load(open(state_path)) if os.path.exists(state_path) else {}
    prev = prev if isinstance(prev, dict) else {"BUY": prev}
    now = {"BUY": list(buys.loc[buys["action"] == "BUY", "ticker"]) if len(buys) else [],
           "SHORT": list(sells.loc[sells["action"] == "SHORT", "ticker"]) if len(sells) else []}
    new = []
    for k, df in (("BUY", buys), ("SHORT", sells)):
        for t in now[k]:
            if t not in prev.get(k, []):
                r = df[df["ticker"] == t].iloc[0]
                new.append({"action": k, "ticker": t, "score": r.get("score") if k == "BUY" else r.get("short_score"),
                            "members": r["members"], "why": r["why"]})
    json.dump(now, open(state_path, "w"))
    return pd.DataFrame(new, columns=["action", "ticker", "score", "members", "why"])


def send_email_alert(new, to_addr, from_addr, app_password):
    if new is None or new.empty:
        return False
    rows = "".join(f"<tr><td>{r.action}</td><td><b>{r.ticker}</b></td><td>{r.score:.0f}</td><td>{r.members}</td>"
                   f"<td>{r.why}</td></tr>" for r in new.itertuples())
    html = (f"<h3>{len(new)} new congressional trade signal(s)</h3><table border=1 cellpadding=4 style='border-collapse:"
            f"collapse'><tr><th>Action</th><th>Ticker</th><th>Score</th><th>Members</th><th>Why</th></tr>{rows}</table>")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Congress trade signals: " + ", ".join(f"{a} {t}" for a, t in zip(new["action"], new["ticker"]))[:120]
    msg["From"], msg["To"] = from_addr, to_addr
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(from_addr, app_password)
        s.send_message(msg)
    return True


# ============================================================================
# Reports
# ============================================================================
CSS = """<style>
.ct{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#1f1f1e;background:#fcfcfb;max-width:1280px;margin:0 auto;padding:16px}
.ct h1{font-size:24px;margin:0 0 4px}.ct h2{font-size:18px;margin:28px 0 8px}
.ct .sub{color:#6b6a63;margin:0 0 16px}.ct table{border-collapse:collapse;font-size:13px;width:100%}
.ct th,.ct td{border-bottom:1px solid #e5e4de;padding:6px 8px;text-align:left;vertical-align:top}
.ct th{background:#f3f2ee;position:sticky;top:0}.ct td.n{text-align:right;font-variant-numeric:tabular-nums}
.ct .tiles{display:flex;flex-wrap:wrap;gap:12px}.ct .tile{flex:1 1 170px;border:1px solid #e5e4de;border-radius:8px;padding:12px}
.ct .tile .v{font-size:26px;font-weight:600}.ct .tile .l{color:#6b6a63;font-size:12px}
.ct .good{color:#006300}.ct .bad{color:#b3261e}.ct .scroll{max-height:640px;overflow:auto}
.ct .note{background:#f3f2ee;border-radius:8px;padding:10px 12px;font-size:13px}
.ct a{color:#2a78d6}
</style>"""


def _fmt_pct(x, signed=True):
    if x is None or pd.isna(x):
        return "–"
    return f"{x*100:+.1f}%" if signed else f"{x*100:.1f}%"


def _table(df, pct_cols=(), num_cols=(), plain_pct=(), int_cols=()):
    h = "<table><tr>" + "".join(f"<th>{c}</th>" for c in df.columns) + "</tr>"
    for _, r in df.iterrows():
        h += "<tr>"
        for c in df.columns:
            v = r[c]
            if c in plain_pct:
                h += f"<td class='n'>{_fmt_pct(v, signed=False)}</td>"
            elif c in pct_cols:
                k = "good" if (pd.notna(v) and v > 0) else ("bad" if pd.notna(v) and v < 0 else "")
                h += f"<td class='n {k}'>{_fmt_pct(v)}</td>"
            elif c in int_cols:
                h += f"<td class='n'>{'' if pd.isna(v) else f'{v:,.0f}'}</td>"
            elif c in num_cols:
                h += f"<td class='n'>{'' if pd.isna(v) else f'{v:,.2f}'}</td>"
            elif isinstance(v, (pd.Timestamp, dt.date)):
                h += f"<td style='white-space:nowrap'>{pd.Timestamp(v).date()}</td>"
            elif isinstance(v, (float, np.floating)):
                h += f"<td class='n'>{'' if np.isnan(v) else f'{v:,.1f}'}</td>"
            else:
                h += f"<td>{'' if v is None else v}</td>"
        h += "</tr>"
    return h + "</table>"


SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]


def _equity_chart_html(curves):
    try:
        import plotly.graph_objects as go
    except ImportError:
        return ""
    fig = go.Figure()
    k = 0
    for c in curves.columns:
        spy = "SPY" in c
        color = "#8a8980" if spy else SERIES_COLORS[k % 4]
        k += 0 if spy else 1
        fig.add_trace(go.Scatter(x=curves.index, y=(curves[c] - 1) * 100, name=c, mode="lines",
                                 line=dict(width=2, color=color, dash="dot" if spy else "solid"),
                                 hovertemplate="%{y:+.1f}%<extra>" + c + "</extra>"))
    fig.update_layout(template="plotly_white", height=400, margin=dict(l=40, r=20, t=10, b=30),
                      yaxis_title="Growth of $1 (%)", hovermode="x unified",
                      legend=dict(orientation="h", y=1.1, x=0), paper_bgcolor="#fcfcfb", plot_bgcolor="#fcfcfb")
    fig.update_yaxes(gridcolor="#ecebe6", ticksuffix="%")
    fig.update_xaxes(showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def _trades_table(t):
    if t is None or t.empty:
        return "<p>No picks.</p>"
    tr = t.sort_values("filed_date", ascending=False).rename(columns={
        "filed_date": "Disclosed", "ticker": "Ticker", "company": "Company", "members": "Members", "sector": "Sector",
        "score": "Score", "why": "Why", "entry_date": "Entered", "entry_px": "Entry $", "exit_date": "Exited",
        "exit_px": "Exit $", "ret": "Stock return", "spy_ret": "SPY same period", "excess": "Stock vs SPY",
        "result": "Result"}).drop(columns=["closed"])
    return f"<div class='scroll'>{_table(tr, pct_cols=('Stock return', 'SPY same period', 'Stock vs SPY'), num_cols=('Entry $', 'Exit $'))}</div>"


def backtest_report(bt, cfg, title="Congress trade signals - backtest"):
    perf, ts = bt["perf"], bt["trade_stats"]
    L, S = bt["longs"], bt["shorts"]
    lc = L[L["closed"]] if len(L) else L
    sc = S[S["closed"]] if len(S) else S
    lp, spy = perf["Long picks"], perf["S&P 500 (SPY)"]
    tiles = [("Long picks, annual return", _fmt_pct(lp["Annual return (CAGR)"]),
              "good" if lp["Annual return (CAGR)"] > spy["Annual return (CAGR)"] else "bad"),
             ("S&P 500, annual return", _fmt_pct(spy["Annual return (CAGR)"]), ""),
             ("Long picks that beat SPY", f"{(lc['excess'] > 0).mean()*100:.0f}%" if len(lc) else "–", ""),
             ("Short picks that worked", f"{(sc['excess'] < 0).mean()*100:.0f}%" if len(sc) else "–", ""),
             ("Long worst drawdown", _fmt_pct(lp["Worst drawdown"]), "")]
    tiles_html = "<div class='tiles'>" + "".join(
        f"<div class='tile'><div class='v {k}'>{v}</div><div class='l'>{l}</div></div>" for l, v, k in tiles) + "</div>"
    perf_t = perf.T.reset_index().rename(columns={"index": "Portfolio"})
    tst = ts.T.reset_index().rename(columns={"index": ""})
    for c in tst.columns[1:]:
        tst[c] = [("" if pd.isna(v) else (f"{v:,.0f}" if "rades" in c else f"{v:,.1f}")) for v in tst[c]]

    def fac(df):
        if df.empty:
            return "<p>Not enough data.</p>"
        d = df.copy()
        d["Worked (%)"] = d["Worked (%)"].map(lambda v: f"{v:.0f}%")
        return _table(d, pct_cols=("Avg result vs SPY",))
    lag = bt["lag"]
    lag_cols = [c for c in lag.columns if "vs SPY" in c] if len(lag) else []
    lag_html = _table(lag, pct_cols=tuple(lag_cols), plain_pct=()) if len(lag) else ""
    if len(lag):
        lag = lag.copy()
        lag["Beat SPY after disclosure (%)"] = lag["Beat SPY after disclosure (%)"].map(lambda v: f"{v:.0f}%")
        lag_html = _table(lag, pct_cols=tuple(lag_cols))
    cov = pd.DataFrame([bt["coverage"]]).T.reset_index()
    cov.columns = ["Data", "Transactions"]
    cov["Transactions"] = cov["Transactions"].map(lambda v: f"{v:,}")
    c = cfg
    span = f"{L['filed_date'].min().date()} to {L['filed_date'].max().date()}" if len(L) else ""
    stab = bt["stability"]
    html = f"""<div class='ct'>{CSS}
<h1>{title}</h1>
<p class='sub'>{span} · enter the trading day after a trade is publicly disclosed · hold {c['HOLD_DAYS']} trading days ·
each long pick gets 1/{bt['slots']} of the money and the rest sits in the S&P 500 ·
up to {c['PICKS_PER_WEEK']} longs and {c['SHORTS_PER_WEEK']} shorts per week · a pick must score in the top
{100-c['PICK_PERCENTILE']}% (longs) / {100-c['SHORT_PERCENTILE']}% (shorts) of all earlier signals</p>
{tiles_html}
<h2>Growth vs the market</h2>{_equity_chart_html(bt['curves'])}
{_table(perf_t, pct_cols=('Total return', 'Annual return (CAGR)', 'Worst drawdown'), plain_pct=('Volatility',))}
<h2>Per-trade results</h2><p class='sub'>For shorts, "Beat SPY" means the stock did worse than SPY, which is what a short wants.</p>{_table(tst)}
<h2>The disclosure delay</h2>
<p class='sub'>How much stocks moved between the politician's trade and its disclosure (the part nobody can capture),
versus after disclosure (the part this strategy captures), grouped by how long the member took to file.</p>
{lag_html}
<h2>Which buy signals predicted returns</h2>
<p class='sub'>All disclosed purchases, grouped by signal strength (low = bottom 20%, high = top 20%; yes/no for on/off signals).
A signal is useful when the high or yes group clearly beats the low or no group.</p>{fac(bt['buy_factors'])}
<h2>Which sell signals predicted drops</h2>
<p class='sub'>All disclosed sales. "Avg result vs SPY" is the return of shorting the stock, compared with SPY.</p>{fac(bt['short_factors'])}
{"<h2>Consistency: first half vs second half (long picks)</h2>" + _table(stab.T.reset_index().rename(columns={'index': ''})) if len(stab) else ""}
<h2>Data feeding the model</h2>{_table(cov)}
<h2>Every long pick ({len(L)})</h2>{_trades_table(L)}
<h2>Every short pick ({len(S)})</h2>{_trades_table(S)}
<p class='note'>Limits: committee assignments are today's, applied to past trades. Paper filings aren't parsed unless a paid
API key is added. Prices are Yahoo adjusted closes with no trading costs, taxes or short-borrow fees, and delisted stocks
may be missing. Company-name matching for contracts, lobbying and donations is automatic and can miss or mismatch
some companies.</p>
</div>"""
    stamp = dt.date.today().isoformat()
    path = _p(cfg, "reports", f"backtest_{stamp}.html")
    open(path, "w", encoding="utf-8").write("<!doctype html><meta charset='utf-8'><title>Backtest</title>" + html)
    pd.concat([L.assign(side="long"), S.assign(side="short")]).to_csv(_p(cfg, "reports", f"backtest_trades_{stamp}.csv"), index=False)
    return html, path


def watchlist_report(buys, sells, new, cfg):
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    newset = set(zip(new["action"], new["ticker"])) if len(new) else set()

    def view(df, short):
        if df is None or df.empty:
            return "<p>None in the lookback window.</p>"
        sc = "short_score" if short else "score"
        v = df.copy()
        v["NEW"] = ["NEW" if (("SHORT" if short else "BUY"), t) in newset else "" for t in v["ticker"]]
        v["top %"] = v["percentile"].map(lambda p: f"{100-p:.0f}%")
        cols = ["rank", "NEW", "action", "ticker", "company", sc, "top %", "members", "why", "filed_date",
                "move_since_filing", "analysts", "target_upside_%", "next_earnings", "news_7d", "headlines"]
        v = v[[c for c in cols if c in v.columns]].rename(columns={
            "action": "Action", "ticker": "Ticker", "company": "Company", sc: "Score", "members": "Members",
            "why": "Why", "filed_date": "Disclosed", "move_since_filing": "Move since disclosure",
            "analysts": "Analysts", "target_upside_%": "Analyst target upside", "next_earnings": "Next earnings",
            "news_7d": "News (7d)", "headlines": "Latest headlines"})
        if "Analyst target upside" in v:
            v["Analyst target upside"] = v["Analyst target upside"].astype(float) / 100
        return _table(v, pct_cols=("Move since disclosure", "Analyst target upside"), int_cols=("News (7d)",))

    html = f"""<div class='ct'>{CSS}<h1>Congress trade watchlist</h1>
<p class='sub'>Updated {stamp} · trades disclosed in the last {cfg['WATCHLIST_LOOKBACK_DAYS']} days ·
BUY = top {100-cfg['PICK_PERCENTILE']}% of buy signals · SHORT = top {100-cfg['SHORT_PERCENTILE']}% of sell signals ·
avoid = top {100-cfg['AVOID_PERCENTILE']}% · {len(new)} new since last run</p>
<h2>Buy candidates</h2>{view(buys, False)}
<h2>Short and avoid</h2>{view(sells, True)}
<p class='note'>Analyst, earnings and news columns are live context only. They aren't in the score because
they can't be backtested.</p></div>"""
    path = _p(cfg, "reports", "watchlist_latest.html")
    open(path, "w", encoding="utf-8").write("<!doctype html><meta charset='utf-8'><title>Watchlist</title>" + html)
    pd.concat([buys.assign(list="buy") if len(buys) else buys, sells.assign(list="short/avoid") if len(sells) else sells]
              ).to_csv(_p(cfg, "reports", "watchlist_latest.csv"), index=False)
    return html, path


# ============================================================================
# Entry point
# ============================================================================
def prepare(cfg):
    global _RUN_START
    tx = collect_all(cfg)
    px = load_prices(cfg, tx["ticker"].unique())
    tx = tx[tx["ticker"].isin(px.columns)].reset_index(drop=True)
    meta = load_meta(cfg, tx["ticker"].unique())
    committees = load_committees(cfg)
    history = load_committee_history(cfg, committees)
    data = {"meta": meta, "committees": committees, "committee_history": history}
    enrich = _enrich_tickers(tx, cfg)
    step = lambda flag: cfg.get(flag, True) and not out_of_time(cfg)
    if step("USE_INSIDERS"):
        ins = collect_insiders(cfg)
        recent = tx.loc[tx["filed_date"] >= pd.Timestamp.today() - pd.Timedelta(days=60), "ticker"].value_counts()
        live = recent_form4(cfg, list(recent.index[:150]))
        data["insiders"] = pd.concat([ins, live], ignore_index=True).drop_duplicates(
            ["ticker", "filed", "owner", "code"]) if len(live) else ins
    if step("USE_CONTRACTS"):
        data["contracts"] = collect_contracts(cfg, meta, enrich)
    if step("USE_LOBBYING"):
        data["lobbying"] = collect_lobbying(cfg, meta, enrich)
    if step("USE_DONATIONS"):
        data["donations"] = collect_donations(cfg)
    bios, fecs, lis_map = set(), set(), {}
    traded = set(tx["chamber"] + "|" + tx["last_key"])
    for _, snap in history or [(None, committees)]:
        for k, ms in snap.items():
            for m in ms:
                if m.get("lis") and m.get("bioguide"):
                    lis_map[m["lis"]] = m["bioguide"]
                if k in traded:
                    bios.add(m.get("bioguide"))
                    fecs |= set(m.get("fec") or [])
    bios.discard(None)
    if step("USE_BILLS"):
        data["bills"] = collect_bills(cfg, sorted(bios))
    if step("USE_EMPLOYEE_DONATIONS"):
        data["employee_donations"] = collect_employee_donations(cfg, fecs)
    if step("USE_ANNUAL_DISCLOSURES"):
        data["annual_fd"] = collect_annual_disclosures(cfg)
    if step("USE_COMPANY_INFO"):
        data["sec_companies"] = load_sec_companies(cfg, enrich)
    if step("USE_HEARINGS"):
        data["meetings"] = meetings_frame(collect_meetings(cfg))
    if step("USE_VOTES"):
        votes = collect_votes(cfg, lis_map)
        data["votes"] = votes
        data["bill_policy"] = collect_bill_policy(cfg, [v.get("bill") for v in votes.values()])
    if step("USE_SPEECHES"):
        data["speeches"] = collect_speeches(cfg, meta, enrich)
    if step("USE_BLUESKY"):
        data["bluesky"] = collect_bluesky(cfg)
    if step("USE_EVENTS"):
        data["events"] = collect_events(cfg, meta, enrich)
    if out_of_time(cfg):
        log("Time limit reached: saved progress; the next run continues where this one stopped")
    log("Computing signals for every transaction...")
    feats = compute_features(tx, px, data, cfg)
    scored = apply_scores(feats, cfg)
    scored.to_pickle(_p(cfg, "cache", "scored.pkl"))
    log(f"Done: {len(scored):,} transactions scored")
    return scored, px


# ============================================================================
# Dashboard data file (read by the dashboard link through Google Drive)
# ============================================================================
def _j(v):
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, dt.date, dt.datetime)):
        return None if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), 4)
    return v


def _rows(df, cols):
    if df is None or len(df) == 0:
        return []
    return [{k: _j(d.get(src)) for k, src in cols.items()} for d in df.to_dict("records")]


_TRADE_COLS = {"d": "filed_date", "t": "ticker", "co": "company", "m": "members", "sec": "sector", "s": "score",
               "why": "why", "in": "entry_date", "inpx": "entry_px", "out": "exit_date", "outpx": "exit_px",
               "r": "ret", "spy": "spy_ret", "ex": "excess", "res": "result"}


def export_dashboard(cfg, bt=None, buys=None, sells=None, new=None, tuned=None):
    path = os.path.join(cfg["DATA_DIR"], "dashboard_data.json")
    try:
        data = json.load(open(path)) if os.path.exists(path) else {}
    except Exception:
        data = {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    data.update({"version": 2, "updated": now})
    if bt is not None:
        cur = bt["curves"]
        wk = cur.resample("W-FRI").last().dropna(how="all")
        perf = bt["perf"]
        ts = bt["trade_stats"]
        L = bt["longs"]
        data["backtest"] = {
            "updated": now,
            "span": [_j(L["filed_date"].min()) if len(L) else None, _j(L["filed_date"].max()) if len(L) else None],
            "settings": {k: cfg[k] for k in ("HOLD_DAYS", "PICKS_PER_WEEK", "SHORTS_PER_WEEK", "PICK_PERCENTILE",
                                              "SHORT_PERCENTILE", "SHORT_ALLOCATION", "USE_TUNED_WEIGHTS")},
            "slots": bt.get("slots"),
            "perf": [{"name": c, **{k: _j(perf.loc[k, c]) for k in perf.index}} for c in perf.columns],
            "curve": {"dates": [d.strftime("%Y-%m-%d") for d in wk.index],
                      "series": {c: [_j((v - 1) * 100) for v in wk[c].values] for c in wk.columns}},
            "trade_stats": [{"name": c, **{k: _j(ts.loc[k, c]) for k in ts.index}} for c in ts.columns],
            "buy_factors": _rows(bt["buy_factors"], {"signal": "Signal", "group": "Group", "n": "Trades",
                                                     "avg": "Avg result vs SPY", "hit": "Worked (%)"}),
            "short_factors": _rows(bt["short_factors"], {"signal": "Signal", "group": "Group", "n": "Trades",
                                                         "avg": "Avg result vs SPY", "hit": "Worked (%)"}),
            "lag": [{k: _j(v) for k, v in r.items()} for r in bt["lag"].to_dict("records")],
            "coverage": {k: _j(v) for k, v in bt["coverage"].items()},
            "n_longs": len(L), "n_shorts": len(bt["shorts"]),
            "longs": _rows(L.sort_values("filed_date", ascending=False).head(500), _TRADE_COLS),
            "shorts": _rows(bt["shorts"].sort_values("filed_date", ascending=False).head(250), _TRADE_COLS),
        }
    if buys is not None or sells is not None:
        wcols = {"rank": "rank", "action": "action", "t": "ticker", "co": "company", "pct": "percentile",
                 "m": "members", "why": "why", "d": "filed_date", "move": "move_since_filing", "an": "analysts",
                 "up": "target_upside_%", "earn": "next_earnings", "news": "news_7d", "sec": "sector"}
        data["watchlist"] = {
            "updated": now, "lookback": cfg["WATCHLIST_LOOKBACK_DAYS"],
            "buys": _rows(buys, dict(wcols, s="score")),
            "sells": _rows(sells, dict(wcols, s="short_score")),
            "new": _rows(new, {"action": "action", "t": "ticker"}) if new is not None else []}
    if tuned is not None:
        comp = tuned["comparison"]
        data["tuning"] = {
            "updated": now, "split": _j(tuned["split"]), "better": tuned["tuned_better"],
            "comparison": [{"name": i, **{k: _j(comp.loc[i, k]) for k in comp.columns}} for i in comp.index],
            "buy_weights": [{"signal": i, "default": _j(r["Default"]), "tuned": _j(r["Tuned"]),
                             "n": _j(r["Training trades with this signal"])} for i, r in tuned["buy_weights"].iterrows()],
            "short_weights": [{"signal": i, "default": _j(r["Default"]), "tuned": _j(r["Tuned"]),
                               "n": _j(r["Training trades with this signal"])} for i, r in tuned["short_weights"].iterrows()]}
    txt = json.dumps(data, separators=(",", ":"), allow_nan=False)
    open(path, "w").write(txt)
    log(f"Dashboard data saved ({len(txt)/1024:,.0f} KB)")
    return path


# ############################################################################
#  CONNECTIONS (v3): every public link between a member and a stock they trade
# ############################################################################
import zlib

_RUN_START = time.time()


def out_of_time(cfg, reserve_min=25):
    """True when a time-limited run (GitHub) should stop collecting and save what it has."""
    budget = cfg.get("TIME_BUDGET_MIN")
    return bool(budget) and (time.time() - _RUN_START) / 60 > budget - reserve_min


def _datagov_key(cfg):
    return cfg.get("CONGRESS_API_KEY") or ""


def _get_json(url, params=None, headers=None, tries=3, timeout=60):
    for a in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers or UA, timeout=timeout)
            if r.status_code == 429:
                time.sleep(min(60, int(r.headers.get("Retry-After", 10) or 10)))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def _z(text):
    return zlib.compress((text or "").encode("utf-8", "ignore"))


def _unz(b):
    try:
        return zlib.decompress(b).decode("utf-8", "ignore")
    except Exception:
        return ""


def _name_hit(text_low, key):
    return bool(key) and len(key) >= 4 and re.search(r"\b" + re.escape(key) + r"\b", text_low) is not None


# ----------------------------------------------------------------------------
# 13. Company headquarters state (SEC)
# ----------------------------------------------------------------------------
def load_sec_companies(cfg, tickers):
    hdr = _sec_headers(cfg)
    cache, path = _cache_json(cfg, "sec_companies.json", {})
    if not hdr:
        return cache
    todo = [t for t in tickers if t not in cache]
    if not todo:
        return cache
    js = _get_json("https://www.sec.gov/files/company_tickers.json", headers=hdr)
    if not js:
        return cache
    cmap = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in js.values()}
    log(f"Company addresses: looking up {len(todo)} companies at the SEC")
    for i, t in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        cik = cmap.get(t)
        if not cik:
            cache[t] = {}
            continue
        sub = _get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=hdr)
        time.sleep(0.12)
        if sub:
            addr = (sub.get("addresses") or {}).get("business") or {}
            cache[t] = {"cik": cik, "state": addr.get("stateOrCountry"), "name": sub.get("name"),
                        "inc": sub.get("stateOfIncorporation")}
        if i % 200 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Company addresses: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    return cache


# ----------------------------------------------------------------------------
# 2, 3, 4. Yearly financial disclosures: holdings, family jobs, positions, paid travel, gifts
# ----------------------------------------------------------------------------
def collect_annual_disclosures(cfg):
    """Returns DataFrame: chamber, last_key, first, filed, holdings (set of tickers), text (compressed)."""
    cache = _p(cfg, "cache", "annual_fd.pkl")
    df = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame(
        columns=["chamber", "last_key", "first", "state", "filed", "doc", "holdings", "text"])
    done = set(df["doc"]) if len(df) else set()
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=500)
    rows = []

    # House: annual (O), amendments (A) and termination (T) reports sit in the same yearly index as trades
    if cfg.get("USE_HOUSE", True):
        for y in range(start.year, dt.date.today().year + 1):
            if out_of_time(cfg):
                break
            try:
                r = requests.get(f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{y}FD.zip",
                                 headers=UA, timeout=120)
                z = zipfile.ZipFile(io.BytesIO(r.content))
                idx = pd.read_xml(z.open([n for n in z.namelist() if n.lower().endswith(".xml")][0]))
            except Exception as e:
                log(f"Yearly disclosures: House {y} index failed ({e})")
                continue
            idx = idx[idx["FilingType"].astype(str).str.upper().isin(["O", "A", "T"])]
            idx = idx[~idx["DocID"].astype(str).isin(done)]
            if not len(idx):
                continue
            log(f"Yearly disclosures: House {y}: {len(idx)} reports to read")

            def work(r):
                import pdfplumber
                url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{int(r['Year'])}/{r['DocID']}.pdf"
                try:
                    resp = requests.get(url, headers=UA, timeout=90)
                    if resp.status_code != 200:
                        return r, None
                    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                        return r, "\n".join((pg.extract_text() or "") for pg in pdf.pages[:40])
                except Exception:
                    return r, None

            with ThreadPoolExecutor(cfg["WORKERS"]) as ex:
                for r, text in ex.map(work, [r for _, r in idx.iterrows()]):
                    done.add(str(r["DocID"]))
                    if not text:
                        continue
                    hold = {clean_ticker(m.group(1)) for m in HOUSE_ASSET.finditer(text) if m.group(1)}
                    ties = "\n".join(l for l in text.splitlines() if not HOUSE_ASSET.search(l))
                    rows.append({"chamber": "House", "last_key": name_key(r.get("Last") or "").split(" ")[-1],
                                 "first": str(r.get("First") or ""), "state": str(r.get("StateDst") or "")[:2],
                                 "filed": pd.to_datetime(r.get("FilingDate"), errors="coerce"),
                                 "doc": str(r["DocID"]), "holdings": {h for h in hold if h}, "text": _z(ties)})
            if out_of_time(cfg):
                break

    # Senate: annual reports (report type 7) on eFD
    if cfg.get("USE_SENATE", True) and not out_of_time(cfg):
        try:
            s = _senate_session()
            reports, offset = [], 0
            while True:
                data = {"start": str(offset), "length": "100", "report_types": "[7]", "filer_types": "[]",
                        "submitted_start_date": start.strftime("%m/%d/%Y 00:00:00"), "submitted_end_date": "",
                        "candidate_state": "", "senator_state": "", "office_id": "", "first_name": "", "last_name": ""}
                page = s.post(f"{EFD}/search/report/data/", data=data, timeout=60,
                              headers={"Referer": f"{EFD}/search/", "X-CSRFToken": s.cookies.get("csrftoken", "")}).json().get("data", [])
                if not page:
                    break
                for row in page:
                    m = re.search(r'href="([^"]+)"', row[3])
                    if m and "/annual/" in m.group(1) and m.group(1) not in done:
                        reports.append({"first": row[0], "last": row[1], "link": m.group(1), "filed": row[4]})
                offset += 100
                time.sleep(0.3)
            log(f"Yearly disclosures: Senate: {len(reports)} reports to read")
            from bs4 import BeautifulSoup
            for i, rep in enumerate(reports, 1):
                if out_of_time(cfg):
                    break
                try:
                    html = s.get(EFD + rep["link"], timeout=60).text
                except Exception:
                    continue
                soup = BeautifulSoup(html, "lxml")
                hold, ties = set(), []
                for table in soup.find_all("table"):
                    heads = [h.get_text(" ", strip=True).lower() for h in table.find_all("th")]
                    body = table.get_text("\n", strip=True)
                    if any("asset" in h for h in heads):
                        for tr in table.find_all("tr"):
                            txt = tr.get_text(" ", strip=True)
                            for t in re.findall(r"\(([A-Z]{1,5}(?:[.\-][A-Z])?)\)|Ticker:?\s*([A-Z]{1,5}(?:[.\-][A-Z])?)\b", txt):
                                tk = clean_ticker(t[0] or t[1])
                                if tk:
                                    hold.add(tk)
                    else:
                        ties.append(body)
                done.add(rep["link"])
                rows.append({"chamber": "Senate", "last_key": name_key(rep["last"]).split(" ")[-1], "first": rep["first"],
                             "state": None, "filed": pd.to_datetime(rep["filed"], errors="coerce"), "doc": rep["link"],
                             "holdings": hold, "text": _z("\n".join(ties))})
                time.sleep(0.4)
        except Exception as e:
            log(f"Yearly disclosures: Senate skipped ({e})")
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
        df.to_pickle(cache)
    log(f"Yearly disclosures: {len(df):,} reports on file")
    return df


# ----------------------------------------------------------------------------
# 1. Donations from a company's employees to the member's campaign (FEC)
# ----------------------------------------------------------------------------
def load_principal_committees(cfg):
    cache, path = _cache_json(cfg, "principal_committees.json", {})
    y0 = pd.Timestamp(cfg["START_DATE"]).year - 4
    for cyc in range(y0 + (y0 % 2), dt.date.today().year + 2, 2):
        if str(cyc) in cache.get("_cycles", []):
            continue
        try:
            cm = _fec_zip(f"https://www.fec.gov/files/bulk-downloads/{cyc}/cm{str(cyc)[2:]}.zip", CM_COLS)
        except Exception as e:
            log(f"Campaign committees: {cyc} failed ({e})")
            continue
        p = cm[(cm["CMTE_DSGN"] == "P") & cm["CAND_ID"].notna()]
        for cid, cand in zip(p["CMTE_ID"], p["CAND_ID"]):
            cache.setdefault(cand, [])
            if cid not in cache[cand]:
                cache[cand].append(cid)
        if cyc < dt.date.today().year - 1:
            cache.setdefault("_cycles", []).append(str(cyc))
    json.dump(cache, open(path, "w"))
    return cache


def collect_employee_donations(cfg, fec_ids):
    """Totals given to each member's campaign by employees of each employer, per two-year cycle."""
    key = _datagov_key(cfg)
    cache, path = _cache_json(cfg, "employee_donations.json", {})
    if not key:
        log("Employee donations: skipped - needs CONGRESS_API_KEY (the same free api.data.gov key works for the FEC)")
        return cache
    pcs = load_principal_committees(cfg)
    y0 = pd.Timestamp(cfg["START_DATE"]).year - 4
    cycles = [c for c in range(y0 + (y0 % 2), dt.date.today().year + 2, 2)]
    todo = []
    for cand in sorted(set(fec_ids)):
        for cmte in pcs.get(cand, []):
            for cyc in cycles:
                k = f"{cmte}|{cyc}"
                final = cyc < dt.date.today().year - 1
                if k not in cache or (not final and not _fresh(cache[k].get("fetched", ""), 7)):
                    todo.append((cand, cmte, cyc, k))
    if todo:
        log(f"Employee donations: {len(todo)} campaign-cycles to download from the FEC")
    for i, (cand, cmte, cyc, k) in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        js = _get_json("https://api.open.fec.gov/v1/schedules/schedule_a/by_employer/",
                       params={"committee_id": cmte, "cycle": cyc, "per_page": 100, "sort": "-total", "api_key": key})
        if js is not None:
            cache[k] = {"cand": cand, "fetched": dt.datetime.now().isoformat(),
                        "rows": [[company_key(r.get("employer")), float(r.get("total") or 0)]
                                 for r in js.get("results", []) if r.get("employer")]}
        time.sleep(3.7)            # FEC allows about 1,000 requests per hour
        if i % 50 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Employee donations: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    return cache


# ----------------------------------------------------------------------------
# 10, 12. Committee hearings: who testified, and closed briefings (congress.gov)
# ----------------------------------------------------------------------------
CLOSED_RE = re.compile(r"\b(closed|classified|executive session|members[- ]only|briefing)\b", re.I)


def _congresses(cfg):
    y0 = pd.Timestamp(cfg["START_DATE"]).year - 1
    return sorted({(y - 1789) // 2 + 1 for y in range(y0, dt.date.today().year + 1)})


def _cid(code):
    """congress.gov system code (hsas00, hsas25) -> roster id (HSAS, HSAS25)."""
    c = str(code or "").upper()
    return c[:-2] if c.endswith("00") else c


def collect_meetings(cfg):
    key = _datagov_key(cfg)
    cache, path = _cache_json(cfg, "committee_meetings.json", {"events": {}, "lists": {}})
    if not key:
        log("Hearings: skipped - needs CONGRESS_API_KEY")
        return cache
    for cong in _congresses(cfg):
        for ch in ("house", "senate"):
            lk = f"{cong}|{ch}"
            current = cong >= max(_congresses(cfg))
            if lk in cache["lists"] and (not current or _fresh(cache["lists"][lk], 1)):
                continue
            off, ids = 0, []
            while not out_of_time(cfg):
                js = _get_json(f"https://api.congress.gov/v3/committee-meeting/{cong}/{ch}",
                               params={"api_key": key, "format": "json", "limit": 250, "offset": off})
                items = (js or {}).get("committeeMeetings", [])
                ids += [str(it.get("eventId")) for it in items if it.get("eventId")]
                if len(items) < 250:
                    break
                off += 250
                time.sleep(0.3)
            for e in ids:
                cache["events"].setdefault(f"{cong}|{ch}|{e}", None)
            cache["lists"][lk] = dt.datetime.now().isoformat()
    todo = [k for k, v in cache["events"].items() if v is None]
    if todo:
        log(f"Hearings: {len(todo):,} committee meetings to read (first run only)")
    for i, k in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        cong, ch, e = k.split("|")
        js = _get_json(f"https://api.congress.gov/v3/committee-meeting/{cong}/{ch}/{e}",
                       params={"api_key": key, "format": "json"})
        m = (js or {}).get("committeeMeeting")
        if m:
            wit = m.get("witnesses") or []
            if isinstance(wit, dict):
                wit = wit.get("item") or []
            coms = m.get("committees") or []
            if isinstance(coms, dict):
                coms = coms.get("item") or []
            cache["events"][k] = {"date": (m.get("date") or "")[:10], "title": (m.get("title") or "")[:200],
                                  "type": m.get("type"), "status": m.get("meetingStatus"),
                                  "cids": [_cid(c.get("systemCode")) for c in coms],
                                  "orgs": [company_key(w.get("organization")) for w in wit if w.get("organization")]}
        elif js is not None:
            cache["events"][k] = {}
        time.sleep(0.75)           # congress.gov allows 5,000 requests per hour
        if i % 250 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Hearings: {i:,}/{len(todo):,}")
    json.dump(cache, open(path, "w"))
    return cache


def meetings_frame(cache):
    rows = []
    for k, v in (cache or {}).get("events", {}).items():
        if not v or not v.get("date") or (v.get("status") or "").lower() in ("canceled", "cancelled", "postponed"):
            continue
        rows.append({"date": pd.to_datetime(v["date"], errors="coerce"), "cids": set(c for c in v.get("cids", []) if c),
                     "orgs": set(o for o in v.get("orgs", []) if o), "closed": bool(CLOSED_RE.search(v.get("title") or ""))})
    return pd.DataFrame(rows, columns=["date", "cids", "orgs", "closed"]).dropna(subset=["date"])


# ----------------------------------------------------------------------------
# 11. Floor votes and the industry of each bill (House Clerk, Senate, congress.gov)
# ----------------------------------------------------------------------------
_BILL_TYPES = {"H R": "hr", "HR": "hr", "H RES": "hres", "H J RES": "hjres", "H CON RES": "hconres", "S": "s",
               "S RES": "sres", "S J RES": "sjres", "S CON RES": "sconres"}


def _bill_id(raw, congress):
    t = re.sub(r"[.\s]+", " ", str(raw or "")).strip().upper()
    m = re.match(r"^([A-Z ]+?)\s*(\d+)$", t)
    if not m:
        return None
    typ = _BILL_TYPES.get(m.group(1).strip())
    return f"{congress}|{typ}|{m.group(2)}" if typ else None


def collect_votes(cfg, lis_map):
    import xml.etree.ElementTree as ET
    cache_p = _p(cfg, "cache", "votes.pkl")
    votes = pd.read_pickle(cache_p) if os.path.exists(cache_p) else {}
    y0 = pd.Timestamp(cfg["START_DATE"]).year - 1
    added = 0
    # House: clerk.house.gov/evs/{year}/roll{nnn}.xml
    for y in range(y0, dt.date.today().year + 1):
        n = 1
        misses = 0
        while not out_of_time(cfg) and misses < 3:
            k = f"H|{y}|{n}"
            if k in votes:
                n += 1
                continue
            try:
                r = requests.get(f"https://clerk.house.gov/evs/{y}/roll{n:03d}.xml", headers=UA, timeout=30)
                if r.status_code != 200 or b"<rollcall-vote" not in r.content:
                    misses += 1
                    n += 1
                    continue
                misses = 0
                root = ET.fromstring(r.content)
                md = root.find("vote-metadata")
                cong = int(md.findtext("congress") or (y - 1789) // 2 + 1)
                date = pd.to_datetime(md.findtext("action-date"), errors="coerce")
                ids = [lg.get("name-id") for rv in root.iter("recorded-vote") for lg in [rv.find("legislator")]
                       if lg is not None and (rv.findtext("vote") or "") in ("Yea", "Nay", "Aye", "No")]
                votes[k] = {"date": date, "bill": _bill_id(md.findtext("legis-num"), cong), "ids": ids}
                added += 1
            except Exception:
                misses += 1
            n += 1
            time.sleep(0.15)
    # Senate: senate.gov/legislative/LIS/roll_call_votes/vote{c}{s}/vote_{c}_{s}_{nnnnn}.xml
    for cong in _congresses(cfg):
        for sess in (1, 2):
            n, misses = 1, 0
            while not out_of_time(cfg) and misses < 3:
                k = f"S|{cong}|{sess}|{n}"
                if k in votes:
                    n += 1
                    continue
                try:
                    r = requests.get(f"https://www.senate.gov/legislative/LIS/roll_call_votes/vote{cong}{sess}/"
                                     f"vote_{cong}_{sess}_{n:05d}.xml", headers=UA, timeout=30)
                    if r.status_code != 200 or b"<roll_call_vote" not in r.content:
                        misses += 1
                        n += 1
                        continue
                    misses = 0
                    root = ET.fromstring(r.content)
                    date = pd.to_datetime(root.findtext("vote_date"), errors="coerce")
                    doc = root.find("document")
                    bill = None
                    if doc is not None:
                        bill = _bill_id(f"{doc.findtext('document_type') or ''} {doc.findtext('document_number') or ''}", cong)
                    ids = [lis_map.get(m.findtext("lis_member_id")) for m in root.iter("member")
                           if (m.findtext("vote_cast") or "") in ("Yea", "Nay")]
                    votes[k] = {"date": date, "bill": bill, "ids": [i for i in ids if i]}
                    added += 1
                except Exception:
                    misses += 1
                n += 1
                time.sleep(0.15)
    if added:
        pd.to_pickle(votes, cache_p)
        log(f"Votes: {added:,} new roll calls ({len(votes):,} total)")
    return votes


def collect_bill_policy(cfg, bill_ids):
    key = _datagov_key(cfg)
    cache, path = _cache_json(cfg, "bill_policy.json", {})
    todo = [b for b in set(bill_ids) if b and b not in cache]
    if not key or not todo:
        return cache
    log(f"Votes: looking up the subject of {len(todo):,} bills")
    for i, b in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        cong, typ, num = b.split("|")
        js = _get_json(f"https://api.congress.gov/v3/bill/{cong}/{typ}/{num}", params={"api_key": key, "format": "json"})
        if js is not None:
            cache[b] = ((js.get("bill") or {}).get("policyArea") or {}).get("name") or ""
        time.sleep(0.75)
        if i % 200 == 0:
            json.dump(cache, open(path, "w"))
    json.dump(cache, open(path, "w"))
    return cache


# ----------------------------------------------------------------------------
# 15. Floor speeches that name the company (Congressional Record via GovInfo)
# ----------------------------------------------------------------------------
def collect_speeches(cfg, meta, tickers):
    key = _datagov_key(cfg)
    cache, path = _cache_json(cfg, "speeches.json", {"companies": {}, "granules": {}})
    if not key:
        log("Floor speeches: skipped - needs CONGRESS_API_KEY (the same api.data.gov key works for GovInfo)")
        return cache
    start = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    todo = [t for t in tickers if len(company_key((meta.get(t) or {}).get("name"))) >= 4
            and not _fresh(cache["companies"].get(t, {}).get("fetched", ""), 30)]
    if todo:
        log(f"Floor speeches: searching the Congressional Record for {len(todo)} companies")
    for i, t in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        ck = company_key(meta[t]["name"])
        try:
            r = requests.post("https://api.govinfo.gov/search", params={"api_key": key}, timeout=60,
                              json={"query": f'collection:(CREC) AND "{ck}" AND publishdate:range({start},)',
                                    "pageSize": 60, "offsetMark": "*",
                                    "sorts": [{"field": "publishdate", "sortOrder": "DESC"}]})
            res = r.json().get("results", []) if r.status_code == 200 else None
        except Exception:
            res = None
        if res is None:
            continue
        grans = []
        for g in res:
            gid, pid = g.get("granuleId"), g.get("packageId")
            if not gid or not pid:
                continue
            grans.append(gid)
            if gid not in cache["granules"]:
                js = _get_json(f"https://api.govinfo.gov/packages/{pid}/granules/{gid}/summary", params={"api_key": key})
                mem = (js or {}).get("members") or []
                cache["granules"][gid] = {"date": (js or {}).get("dateIssued") or g.get("dateIssued"),
                                          "ids": [m.get("bioGuideId") or m.get("bioguideId") for m in mem
                                                  if (m.get("bioGuideId") or m.get("bioguideId"))]}
                time.sleep(0.1)
        cache["companies"][t] = {"fetched": dt.datetime.now().isoformat(), "granules": grans}
        if i % 50 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Floor speeches: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    return cache


# ----------------------------------------------------------------------------
# 16. Bluesky posts that name the company (free public API)
# ----------------------------------------------------------------------------
BSKY = "https://public.api.bsky.app/xrpc/"
BSKY_PACKS = ["at://allibaldwin.bsky.social/app.bsky.graph.starterpack/3lgj4wa3ocp23"]


def collect_bluesky(cfg):
    cache, path = _cache_json(cfg, "bluesky.json", {"accounts": {}, "posts": {}})
    if not _fresh(cache.get("accounts_fetched", ""), 14):
        accounts = {}
        for pack in BSKY_PACKS:
            try:
                handle = pack.split("/")[2]
                did = _get_json(BSKY + "com.atproto.identity.resolveHandle", params={"handle": handle})["did"]
                sp = _get_json(BSKY + "app.bsky.graph.getStarterPack", params={"starterPack": pack.replace(handle, did)})
                lst = ((sp or {}).get("starterPack") or {}).get("list", {}).get("uri")
                cursor = None
                while lst:
                    js = _get_json(BSKY + "app.bsky.graph.getList", params={"list": lst, "limit": 100, **({"cursor": cursor} if cursor else {})})
                    for it in (js or {}).get("items", []):
                        sub = it.get("subject") or {}
                        accounts[sub.get("handle")] = sub.get("displayName") or ""
                    cursor = (js or {}).get("cursor")
                    if not cursor:
                        break
            except Exception as e:
                log(f"Bluesky: member list failed ({e})")
        if accounts:
            cache["accounts"] = accounts
            cache["accounts_fetched"] = dt.datetime.now().isoformat()
    since = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=400)).isoformat()
    for i, h in enumerate(list(cache["accounts"])):
        if out_of_time(cfg) or not h:
            break
        rec = cache["posts"].get(h, {})
        if _fresh(rec.get("fetched", ""), 1):
            continue
        posts, cursor, newest = list(rec.get("items", [])), None, rec.get("newest", "")
        for _ in range(40):
            js = _get_json(BSKY + "app.bsky.feed.getAuthorFeed",
                           params={"actor": h, "limit": 100, "filter": "posts_no_replies", **({"cursor": cursor} if cursor else {})})
            feed = (js or {}).get("feed", [])
            stop = False
            for f in feed:
                p = (f.get("post") or {}).get("record") or {}
                ts = p.get("createdAt") or ""
                if ts <= newest or ts < since:
                    stop = True
                    break
                posts.append([ts[:10], (p.get("text") or "")[:500]])
            cursor = (js or {}).get("cursor")
            if stop or not cursor or not feed:
                break
            time.sleep(0.2)
        posts.sort(key=lambda x: x[0])
        cache["posts"][h] = {"fetched": dt.datetime.now().isoformat(), "items": posts,
                             "newest": max([p[0] for p in posts] + [newest]) if posts else newest}
        if i % 25 == 0:
            json.dump(cache, open(path, "w"))
    json.dump(cache, open(path, "w"))
    log(f"Bluesky: {len(cache['accounts'])} member accounts, {sum(len(v['items']) for v in cache['posts'].values()):,} posts")
    return cache


# ----------------------------------------------------------------------------
# 18. Company events: earnings dates (Yahoo) and FDA approvals (openFDA)
# ----------------------------------------------------------------------------
def collect_events(cfg, meta, tickers):
    import yfinance as yf
    cache, path = _cache_json(cfg, "company_events.json", {})
    todo = [t for t in tickers if not _fresh(cache.get(t, {}).get("fetched", ""), 7)]
    if todo:
        log(f"Company events: earnings and FDA dates for {len(todo)} companies")
    for i, t in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        ev = []
        try:
            ed = yf.Ticker(t).get_earnings_dates(limit=40)
            if ed is not None and len(ed):
                ev += [["earnings", d.strftime("%Y-%m-%d")] for d in pd.to_datetime(ed.index)]
        except Exception:
            pass
        if (meta.get(t) or {}).get("sector") == "Healthcare" and company_key(meta[t].get("name")):
            js = _get_json("https://api.fda.gov/drug/drugsfda.json",
                           params={"search": f'sponsor_name:"{company_key(meta[t]["name"])}"', "limit": 100})
            for app in (js or {}).get("results", []):
                for sub in app.get("submissions", []):
                    d = sub.get("submission_status_date")
                    if sub.get("submission_status") == "AP" and d:
                        ev.append(["fda", f"{d[:4]}-{d[4:6]}-{d[6:8]}"])
        cache[t] = {"fetched": dt.datetime.now().isoformat(), "events": ev}
        time.sleep(0.3)
        if i % 100 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Company events: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    return cache


# ----------------------------------------------------------------------------
# 17. Member and company in the news together (GDELT) - watchlist context only
# ----------------------------------------------------------------------------
def news_together(member_last, company, days=180):
    try:
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", timeout=30,
                         params={"query": f'"{member_last}" "{company}"', "mode": "artlist", "maxrecords": 50,
                                 "timespan": f"{days}d", "format": "json"})
        return len(r.json().get("articles", []))
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Signals from all of the above
# ----------------------------------------------------------------------------
def _within(dates, lo, hi):
    return bool(len(dates)) and bool(((dates > lo) & (dates <= hi)).any())


def compute_connections(tx, data, cfg, mems):
    meta = data["meta"]
    n = len(tx)
    ck = {t: company_key((meta.get(t) or {}).get("name")) for t in tx["ticker"].unique()}
    tx["_ck"] = tx["ticker"].map(ck)
    fdt, tdt = tx["filed_date"], tx["trade_date"]
    last = tx["last_key"].values
    bios = [m.get("bioguide") if m else None for m in mems]
    mstate = [(m.get("state") if m else None) or s for m, s in zip(mems, tx["state"])]
    cids = [set((m.get("cids") or [])) if m else set() for m in mems]
    D = pd.Timedelta

    # 19. out of character, 20. spouse, 21. options, 22. late filing
    tx = tx.sort_values("filed_date")
    key = tx["chamber"] + "|" + tx["last_key"]
    tx["f_first_time"] = (tx.groupby([key, tx["ticker"]]).cumcount() == 0).astype(float)
    mid = (tx["amt_lo"].fillna(1001) + tx["amt_hi"].fillna(15000)) / 2
    med = mid.groupby(key).transform(lambda s: s.expanding().median().shift(1))
    tx["f_unusual_size"] = ((mid / med) >= 3).astype(float).where(med.notna(), 0.0)
    seen = {}
    ns = []
    for k_, sec in zip(key, tx["sector"]):
        s_ = seen.setdefault(k_, set())
        ns.append(1.0 if (sec and s_ and sec not in s_) else 0.0)
        if sec:
            s_.add(sec)
    tx["f_new_sector"] = ns
    tx = tx.sort_index()
    own = tx["owner"].fillna("").astype(str).str.lower()
    tx["f_spouse"] = np.where(own.isin(["sp", "spouse", "dc", "child", "dependent child"]), 1.0,
                              np.where(own.isin(["jt", "joint"]), 0.5, 0.0))
    tx["f_option"] = tx["option"].notna().astype(float) if "option" in tx else 0.0
    tx["f_late"] = (tx["lag_days"] > 45).astype(float)

    # 23. members who share a committee with this member traded the same stock the same way
    cc = np.zeros(n)
    tmp = pd.DataFrame({"t": tx["ticker"].values, "d": fdt.values, "typ": tx["tx_type"].values,
                        "who": key.values, "c": cids})
    for t, g in tmp.groupby("t"):
        if len(g) < 2:
            continue
        g = g.sort_values("d")
        for i, r in g.iterrows():
            if not r.c:
                continue
            w = g[(g.d <= r.d) & (g.d > r.d - np.timedelta64(30, "D")) & (g.typ == r.typ) & (g.who != r.who)]
            if len(w) and any(r.c & c2 for c2 in w.c):
                cc[i] = 1.0
    tx["f_committee_cluster"] = cc

    # 13. company headquartered in the member's state
    sec = data.get("sec_companies") or {}
    tx["f_home_state"] = [1.0 if (st and (sec.get(t) or {}).get("state") == st) else 0.0
                          for t, st in zip(tx["ticker"], mstate)]

    # 14. federal contract work performed in the member's state (last 12 months, public by filing date)
    con = data.get("contracts")
    tx["f_contract_in_state"] = 0.0
    if con is not None and len(con) and "pop_state" in con:
        g = {k: v for k, v in con.dropna(subset=["pop_state"]).groupby("ticker")}
        tx["f_contract_in_state"] = [1.0 if (t in g and st and
                                             ((g[t].pop_state == st) & (g[t].known_date <= f) & (g[t].known_date > f - D(days=365))).any())
                                     else 0.0 for t, st, f in zip(tx["ticker"], mstate, fdt)]

    # 2, 3, 4. yearly disclosures: already owned it; company named in jobs, income, travel or gifts
    fd = data.get("annual_fd")
    held, tie = np.zeros(n), np.zeros(n)
    if fd is not None and len(fd):
        fdg = {k: g.sort_values("filed") for k, g in fd.groupby(["chamber", "last_key"])}
        text_cache = {}
        for i, (ch, lk, t, f, c_) in enumerate(zip(tx["chamber"], last, tx["ticker"], fdt, tx["_ck"])):
            g = fdg.get((ch, lk))
            if g is None:
                continue
            w = g[(g.filed <= f) & (g.filed > f - D(days=1100))]
            if not len(w):
                continue
            if any(t in h for h in w.holdings):
                held[i] = 1.0
            if c_ and len(c_) >= 4:
                for doc, blob in zip(w.doc, w.text):
                    tl = text_cache.get(doc)
                    if tl is None:
                        tl = text_cache[doc] = _unz(blob).lower()
                    if _name_hit(tl, c_):
                        tie[i] = 1.0
                        break
    tx["f_already_owned"], tx["f_disclosure_tie"] = held, tie

    # 1. company employees donated to the member's campaign (completed cycles only)
    emp = data.get("employee_donations") or {}
    fec_of = [set(m.get("fec") or []) if m else set() for m in mems]
    by_cand = {}
    for k_, v in emp.items():
        cyc = int(k_.split("|")[1])
        by_cand.setdefault(v.get("cand"), []).append((cyc, {e: a for e, a in v.get("rows", [])}))
    ed = np.zeros(n)
    for i, (fids, c_, f) in enumerate(zip(fec_of, tx["_ck"], fdt)):
        if not fids or not c_:
            continue
        tot = 0.0
        for cand in fids:
            for cyc, rows in by_cand.get(cand, []):
                if pd.Timestamp(cyc, 12, 31) < f:
                    tot += sum(a for e, a in rows.items() if e == c_ or e.startswith(c_ + " "))
        ed[i] = tot
    tx["employee_donations"] = ed
    tx["f_employee_donations"] = (np.log10(ed + 1) / 5).clip(0, 1)

    # 5. revolving door: the company's lobbyists used to work for this member
    lb = data.get("lobbying")
    rv = np.zeros(n)
    if lb is not None and len(lb) and "covered" in lb:
        lg = {k: v for k, v in lb.dropna(subset=["posted"]).groupby("ticker")}
        for i, (t, lk, f) in enumerate(zip(tx["ticker"], last, fdt)):
            g = lg.get(t)
            if g is None or not lk:
                continue
            w = g[(g.posted <= f) & (g.posted > f - D(days=730))]
            pat = re.compile(r"\b(rep|representative|congressman|congresswoman|sen|senator)\.?\s+(\w+\s+)?" + re.escape(lk) + r"\b", re.I)
            if any(pat.search(c or "") for c in w.covered):
                rv[i] = 1.0
    tx["f_revolving_door"] = rv

    # 10, 12. testimony before the member's committees; closed briefings before the trade
    mt = data.get("meetings")
    test, closed = np.zeros(n), np.zeros(n)
    if mt is not None and len(mt):
        mt = mt.sort_values("date")
        closed_m = mt[mt.closed]
        for i, (c_, cs, f, td, fc) in enumerate(zip(tx["_ck"], cids, fdt, tdt, tx["f_committee"])):
            if not cs:
                continue
            if c_:
                w = mt[(mt.date <= f) & (mt.date > f - D(days=365))]
                if any((c_ in o) and (cs & m_) for o, m_ in zip(w.orgs, w.cids)):
                    test[i] = 1.0
            if fc > 0:
                w = closed_m[(closed_m.date <= td) & (closed_m.date > td - D(days=30))]
                if any(cs & m_ for m_ in w.cids):
                    closed[i] = 1.0
    tx["f_testified"], tx["f_closed_briefing"] = test, closed

    # 11. voted on a bill about this industry within 30 days of the trade
    votes, pol = data.get("votes") or {}, data.get("bill_policy") or {}
    vs = np.zeros(n)
    if votes:
        by_member = {}
        for v in votes.values():
            secs = POLICY_SECTORS.get(pol.get(v.get("bill")) or "", [])
            if not secs or pd.isna(v.get("date")):
                continue
            for mid_ in v["ids"]:
                by_member.setdefault(mid_, []).append((v["date"], set(secs)))
        for i, (b, s_, td, f) in enumerate(zip(bios, tx["sector"], tdt, fdt)):
            if not b or not s_:
                continue
            for d, secs in by_member.get(b, []):
                if s_ in secs and abs((d - td).days) <= 30 and d <= f:
                    vs[i] = 1.0
                    break
    tx["f_vote_sector"] = vs

    # 15. named the company in a floor speech in the prior year
    sp = data.get("speeches") or {}
    spk = np.zeros(n)
    if sp:
        gran = sp.get("granules", {})
        for i, (t, b, f) in enumerate(zip(tx["ticker"], bios, fdt)):
            if not b:
                continue
            for gid in (sp.get("companies", {}).get(t) or {}).get("granules", []):
                gr = gran.get(gid) or {}
                d = pd.to_datetime(gr.get("date"), errors="coerce")
                if b in (gr.get("ids") or []) and pd.notna(d) and f - D(days=365) < d <= f:
                    spk[i] = 1.0
                    break
    tx["f_speech"] = spk

    # 16. posted about the company on Bluesky in the prior year
    bs = data.get("bluesky") or {}
    soc = np.zeros(n)
    if bs.get("accounts"):
        by_last = {}
        for h, disp in bs["accounts"].items():
            lk = name_key(re.sub(r"^(rep|sen|senator|congressman|congresswoman)\.?\s+", "", disp or "", flags=re.I)).split(" ")[-1:]
            if lk and lk[0]:
                by_last.setdefault(lk[0], []).append(h)
        for i, (lk, t, c_, f) in enumerate(zip(last, tx["ticker"], tx["_ck"], fdt)):
            for h in by_last.get(lk, []):
                lo_, hi_ = (f - D(days=365)).strftime("%Y-%m-%d"), f.strftime("%Y-%m-%d")
                for d, text in (bs.get("posts", {}).get(h) or {}).get("items", []):
                    if lo_ < d <= hi_ and (("$" + t) in text or _name_hit(text.lower(), c_)):
                        soc[i] = 1.0
                        break
                if soc[i]:
                    break
    tx["f_social_post"] = soc

    # 18. traded shortly before a company event that was public by the filing date
    ev = data.get("events") or {}
    pre = np.zeros(n)
    evmap = {}
    for t, v in ev.items():
        evmap[t] = pd.to_datetime([e[1] for e in v.get("events", [])], errors="coerce").dropna()
    if con is not None and len(con):
        for t, g in con[con.amount >= 1e7].groupby("ticker"):
            evmap[t] = evmap.get(t, pd.DatetimeIndex([])).append(pd.DatetimeIndex(g.action_date.dropna()))
    for i, (t, td, f) in enumerate(zip(tx["ticker"], tdt, fdt)):
        d = evmap.get(t)
        if d is not None and len(d) and _within(d, td, min(td + D(days=30), f)):
            pre[i] = 1.0
    tx["f_pre_event"] = pre
    return tx.drop(columns=["_ck"])
