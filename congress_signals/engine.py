# =============================================================================
#  Congress Trade Signals - engine
#  Collects US politicians' disclosed stock trades from public sources, scores
#  each purchase, backtests the picks, and builds a ranked watchlist.
# =============================================================================
import os, re, io, json, time, math, zipfile, base64, datetime as dt, unicodedata, smtplib
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
    "START_DATE": "2014-01-01",      # how far back to collect / backtest
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
    "NET_SELL_FILTER": True,         # skip buys when more members are selling the stock than buying it
    "BUSY_TRADER_SOFTCAP": 100,      # when learning what works: a member's first 100 trades count fully, later ones a bit less
    "COST_BPS": 10,                  # trading cost per buy or sell, in hundredths of a percent (10 = 0.10%)
    "HOLDING_PERIODS": [5, 20, 60, 125, 250],   # tested in the report: 1 week to 1 year
    "DEFENSE_PICKS_PER_WEEK": 2,
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
        "small_cap": 0.4, "proven_member": 0.8, "crowded": -0.3, "ex_member_lobbyist": 0.3,
        "defense_power": 0.5, "dod_award_after_trade": 0.6, "dod_momentum": 0.4,
        "grants": 0.2, "grant_after_trade": 0.4, "campaign_vendor": 0.3, "spouse_employer": 0.5,
        "paid_travel": 0.3, "outside_position": 0.4, "buddy": 0.4, "leader": 0.3, "bill_advanced": 0.5,
        "sector_bill_momentum": 0.2, "spouse_insider": 0.5,
        "against_street": 0.3, "after_downgrade": 0.2, "no_coverage": 0.2,
    },
    "SHORT_WEIGHTS": {
        "sell_track_record": 1.0, "sell_cluster": 0.8, "buy_pressure": -0.4, "committee": 0.5,
        "subcommittee": 0.3, "committee_leader": 0.3, "size": 0.3, "freshness": 0.3, "fast_filer": 0.3,
        "full_sale": 0.3, "insider_selling": 0.4, "insider_buying": -0.4, "momentum": -0.3, "donations": 0.2,
        "option": 0.5, "testified": 0.3, "closed_briefing": 0.5, "vote_sector": 0.3, "pre_event": 0.4,
        "committee_cluster": 0.5, "first_time": 0.1, "unusual_size": 0.4, "spouse": 0.1, "late": 0.2,
        "small_cap": 0.3, "proven_seller": 0.8, "defense_power": 0.4,
    },
    "USE_TUNED_WEIGHTS": False,      # True = use weights saved by the tuning step
    "TRAIN_TEST_SPLIT": "2024-01-01",
    "ASSET_TYPES": ["ST", "EF", "OP"],
    "SEC_USER_AGENT": "",            # required by the SEC: "Your Name you@example.com"
    "CONGRESS_API_KEY": "",          # free key from api.congress.gov/sign-up (bills)
    "LDA_API_KEY": "",               # optional free key from lda.gov (faster lobbying downloads)
    "QUIVER_API_KEY": "",
    "FMP_API_KEY": "",
    "OCR_SPACE_API_KEY": "",
    "TIINGO_API_KEY": "",            # free key from tiingo.com: fills in prices for delisted stocks
    "TIINGO_PER_RUN": 45,
    "USE_HOUSE": True, "USE_SENATE": True, "USE_INSIDERS": True, "USE_CONTRACTS": True,
    "USE_LOBBYING": True, "USE_DONATIONS": True, "USE_BILLS": True, "USE_NEWS": True,
    "USE_EMPLOYEE_DONATIONS": True, "USE_ANNUAL_DISCLOSURES": True, "USE_COMPANY_INFO": True,
    "USE_BILL_STATUS": True, "USE_SPOUSE_MATCHING": True, "USE_ANALYST_HISTORY": True, "USE_CAMPAIGN_SPENDING": True, "USE_ASSISTANCE": True,
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


def chunked_map(fn, items, workers, cfg, chunk=None):
    """Parallel map in small batches, stopping cleanly when a time-limited run is nearly out of time."""
    items = list(items)
    chunk = chunk or workers * 8
    for i in range(0, len(items), chunk):
        if out_of_time(cfg):
            log("Time limit reached: stopping this step (progress is saved; the next run continues)")
            return
        with ThreadPoolExecutor(workers) as ex:
            for res in ex.map(fn, items[i:i + chunk]):
                yield res


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
           "ZI": "GTM", "ABC": "COR", "PKI": "RVTY", "FI": "FISV", "MMC": "MRSH", "BK": "BNY", "DISCA": "WBD", "DISCK": "WBD", "HFC": "DINO",
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
# House reports whose rows lost the ticker are re-read once when this changes (Oct 2: ticker before the transaction)
HOUSE_TICKER_FIX = 1
HOUSE_TICKER_BEFORE = re.compile(r"\(([A-Z][A-Z0-9.\-/]{0,7})\)\s*$")
# a House PTR row's own description ("D: ...") or comment ("C: ...") lines, written by the filer
HOUSE_NOTE = re.compile(r"(?im)^\s*(?:D(?:ESCRIPTION)?|C(?:OMMENTS?)?)\s*:\s*(.+?)\s*$")


HOUSE_PARSER_VERSION = 4     # Oct 5: case-proof parser (2014-2021 reports use a small-caps font and no [ST] tags)
HOUSE_CORE_U = re.compile(
    r"(?<![A-Z0-9])(P|S \(PARTIAL\)|S|E)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(SPOUSE/DC OVER\s+\$[\d,]+|OVER \$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)")
HOUSE_TICK_U = re.compile(r"\(([A-Z][A-Z0-9.\-/]{0,7})\)")
HOUSE_TAG_U = re.compile(r"\[([A-Z]{2})\]")
HOUSE_STOP_U = re.compile(r"^\s*(FILING STATUS|F\s+S\s*:|SUBHOLDING OF|DESCRIPTION|COMMENTS?\s*:|D\s*:|C\s*:|L\s*:|"
                          r"LOCATION|\* FOR THE COMPLETE|ID OWNER|TYPE DATE|ASSET CLASS DETAILS)", re.M)


def _upper_same_len(t):
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in t)


def parse_house_ptr_text(text):
    """Read every transaction row of a House PTR. Works on the current layout and on the 2014-2021 one, whose
    small-caps font comes out as mixed case ("aaPL", "s", "[sT]") and which often has no asset-type tag.
    Returns the owner code (SP spouse, JT joint, DC child; blank = the member) and, for options, call or put."""
    text = text.replace("\x00", "")
    U = _upper_same_len(text)
    cores = list(HOUSE_CORE_U.finditer(U))
    out = []
    for k, c in enumerate(cores):
        ls = U.rfind("\n", 0, c.start()) + 1
        if k + 1 < len(cores):
            row_end = U.rfind("\n", 0, cores[k + 1].start())
            row_end = row_end if row_end > c.end() else cores[k + 1].start()
        else:
            row_end = len(U)
        row = U[ls:row_end]
        rel = c.start() - ls
        head, tail = row[:rel], row[rel + len(c.group(0)):]
        m = HOUSE_STOP_U.search(tail)
        cont = tail[:m.start()] if m else tail
        tm = list(HOUSE_TICK_U.finditer(head))
        ticker = tm[-1].group(1) if tm else None
        if not ticker:
            t2 = HOUSE_TICK_U.search(cont)
            ticker = t2.group(1) if t2 else None
        tg = HOUSE_TAG_U.search(head) or HOUSE_TAG_U.search(cont)
        own = re.match(r"\s*(SP|JT|DC)\s", head)
        if tg:
            atype = tg.group(1)
        elif ticker:            # older reports have no tag: a listed ticker is a stock, or an option if it says so
            atype = "OP" if re.search(r"\bSTRIKE\b|\bOPTIONS?\b|\b(CALL|PUT)S?\s+OPTIONS?\b", row) else "ST"
        else:
            atype = None
        opt = None
        if atype == "OP":
            opt = "put" if re.search(r"\bPUTS?\b", row) else ("call" if re.search(r"\bCALLS?\b", row) else "option")
        lo, hi = parse_amount(c.group(4))
        if c.group(4).startswith("SPOUSE/DC"):
            lo, hi = 1000001.0, 5000000.0
        notes = [n.group(1).strip() for n in HOUSE_NOTE.finditer(text[ls:row_end])]
        out.append({"ticker": ticker, "asset_type": atype, "tx_raw": c.group(1).replace("PARTIAL", "partial"),
                    "trade_date": c.group(2), "notif_date": c.group(3), "amt_lo": lo, "amt_hi": hi,
                    "owner": own.group(1) if own else "", "option": opt, "note": " ".join(n for n in notes if n) or None})
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
    ver = open(ver_path).read().strip() if os.path.exists(ver_path) else ""
    if ver != str(HOUSE_PARSER_VERSION):
        if done:
            # re-read every report, newest first; old rows stay until their report is read again, so nothing
            # disappears in between
            log("House: parser upgraded (2014-2021 layout); re-reading all reports, keeping old rows meanwhile")
        done = set()
        open(ver_path, "w").write(str(HOUSE_PARSER_VERSION))
        json.dump([], open(cache_done, "w"))
        json.dump({"version": HOUSE_PARSER_VERSION, "rows_before": int(len(tx)),
                   "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
                  open(_p(cfg, "state", "house_reread.json"), "w"))
    fix_path = _p(cfg, "cache", "house_ticker_fix.txt")
    if not os.path.exists(fix_path) or open(fix_path).read().strip() != str(HOUSE_TICKER_FIX):
        if len(tx) and "ticker" in tx.columns:
            bad = set(tx.loc[tx["ticker"].isna() | (tx["ticker"].astype(str).str.strip().isin(["", "None"])),
                             "doc_id"].astype(str))
            if bad:
                log(f"House: re-reading {len(bad)} reports with rows missing a ticker")
                tx = tx[~tx["doc_id"].astype(str).isin(bad)].reset_index(drop=True)
                done -= bad
                tx.to_pickle(cache_tx)
                json.dump(sorted(done), open(cache_done, "w"))
        open(fix_path, "w").write(str(HOUSE_TICKER_FIX))

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
    todo = idx[~idx["DocID"].astype(str).isin(done)].sort_values("FilingDate", ascending=False)
    log(f"House: {len(idx)} reports since {start.date()}, {len(todo)} to download")

    rows, n, reread = [], 0, set()

    def work(r):
        return r, _fetch_house_pdf(int(r["year"]), str(r["DocID"]))

    def flush():
        nonlocal tx, rows, reread
        if reread and len(tx) and "doc_id" in tx.columns:
            tx = tx[~tx["doc_id"].astype(str).isin(reread)].reset_index(drop=True)
        reread = set()
        if rows:
            tx = pd.concat([tx, pd.DataFrame(rows)], ignore_index=True)
            rows = []
        tx.to_pickle(cache_tx)
        json.dump(sorted(done), open(cache_done, "w"))

    if True:
        for r, text in chunked_map(work, [r for _, r in todo.iterrows()], cfg["WORKERS"], cfg):
            n += 1
            if text is None:            # download failed: try again next run instead of skipping it for good
                continue
            done.add(str(r["DocID"]))
            reread.add(str(r["DocID"]))
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
    left = int((~todo["DocID"].astype(str).isin(done)).sum())
    json.dump({"left": left, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
              open(_p(cfg, "cache", "house_backlog.json"), "w"))
    log(f"House: {len(tx)} stock transactions total; {left} reports still to read")
    return tx


PAPER_VERSION = 2      # 2: unmatched lines kept for re-matching; older reports re-read after the backlog


def historical_company_names(cfg, per_run=16):
    """Company names with the ticker they used at the time, from the SEC's quarterly insider-filing data sets
    since the start date. Catches companies since renamed, bought or delisted ("SunTrust Banks" -> STI,
    "Halyard Health" -> HYH), which today's ticker list doesn't have. A few quarters per run, cached."""
    st, path = _cache_json(cfg, "company_names_hist.json", {"names": {}, "done": []})
    hdr = _sec_headers(cfg)
    if not hdr:
        return st["names"]
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=120)
    today = pd.Timestamp.today()
    n = 0
    for y in range(start.year, today.year + 1):
        for q in range(1, 5):
            key = f"{y}q{q}"
            if key in st["done"] or pd.Timestamp(y, 3 * q - 2, 1) > today or n >= per_run or out_of_time(cfg, 160):
                continue
            try:
                r = requests.get(f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
                                 f"{key}_form345.zip", headers=hdr, timeout=180)
                if r.status_code != 200:
                    continue
                sub = _read_tsv(zipfile.ZipFile(io.BytesIO(r.content)), "SUBMISSION.TSV",
                                ["ISSUERNAME", "ISSUERTRADINGSYMBOL", "FILING_DATE"])
                sub["t"] = sub["ISSUERTRADINGSYMBOL"].map(clean_ticker)
                sub = sub.dropna(subset=["t", "ISSUERNAME"]).drop_duplicates(["t", "ISSUERNAME"])
                for t, nm in zip(sub["t"], sub["ISSUERNAME"]):
                    lst = st["names"].setdefault(t, [])
                    if nm not in lst and len(lst) < 4:
                        lst.append(nm)
                if pd.Timestamp(y, 3 * q, 1) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=45) < today:
                    st["done"].append(key)
                n += 1
                time.sleep(0.2)
            except Exception as e:
                log(f"Company names: {key} failed ({type(e).__name__})")
    if n:
        json.dump(st, open(path, "w"))
        log(f"Company names: {len(st['names']):,} tickers with past names ({len(st['done'])} quarters read)")
    return st["names"]


_NAME_INDEX = {}


def paper_name_index(cfg):
    """Written company name -> ticker for scanned reports: today's SEC list first, then names seen in price data,
    then past names from SEC insider filings. Built once per run."""
    from congress_signals import paper as PP
    if _NAME_INDEX.get("run") == id(cfg):
        return _NAME_INDEX["nidx"], _NAME_INDEX["known"]
    pairs = []
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json", timeout=60,
                         headers={"User-Agent": cfg.get("SEC_USER_AGENT") or "congress-signals research contact@example.com"})
        pairs += [(v["ticker"].upper().replace(".", "-"), v["title"]) for v in r.json().values()]
    except Exception as e:
        log(f"Scanned reports: SEC ticker list failed ({type(e).__name__})")
    n_cur = len(pairs)
    try:
        meta = json.load(open(_p(cfg, "cache", "ticker_meta.json")))
        pairs += [(t, m["name"]) for t, m in meta.items() if isinstance(m, dict) and m.get("name")]
    except Exception:
        pass
    try:
        pairs += [(t, nm) for t, lst in historical_company_names(cfg).items() for nm in lst]
    except Exception as e:
        log(f"Company names: skipped ({type(e).__name__}: {e})")
    known = {t for t, _ in pairs}
    for f in ("house_tx.pkl", "senate_tx.pkl"):
        try:
            known |= set(pd.read_pickle(_p(cfg, "cache", f))["ticker"].dropna().astype(str).str.upper())
        except Exception:
            pass
    nidx = PP.NameIndex(pairs, primary=n_cur)
    _NAME_INDEX.update({"run": id(cfg), "nidx": nidx, "known": known})
    log(f"Scanned reports: {len(nidx.keys):,} company names to match against ({n_cur:,} current)")
    return nidx, known


def _rematch(tx, un, nidx, known, label):
    """Lines read earlier whose company couldn't be matched: try again with today's name list."""
    if un is None or not len(un):
        return tx, un
    tk = un["name"].map(lambda nm: nidx.match(nm, known))
    hit = tk.notna()
    if hit.any():
        add = un[hit].assign(ticker=tk[hit])
        tx = pd.concat([tx, add], ignore_index=True)
        un = un[~hit].reset_index(drop=True)
        log(f"{label}: {int(hit.sum())} earlier unmatched line(s) now matched")
    return tx, un


def collect_house_paper(cfg):
    """Scanned paper House PTRs (DocIDs starting 8 or 9): read the checkbox grid with OCR (congress_signals/paper.py).
    Rows whose written company name can't be matched to a ticker are skipped. Trade dates that can't be read are
    estimated as 30 days before filing (flagged in tx_raw). Resumable across runs."""
    try:
        import pytesseract, cv2  # noqa: F401
        pytesseract.get_tesseract_version()
    except Exception as e:
        log(f"House paper: OCR not available ({type(e).__name__}); skipping")
        return pd.DataFrame()
    from congress_signals import paper as PP
    start = pd.Timestamp(cfg["START_DATE"])
    cache_tx = _p(cfg, "cache", "house_paper_tx.pkl")
    cache_done = _p(cfg, "cache", "house_paper_done.json")
    vpath = _p(cfg, "cache", "house_paper_version.txt")
    tx = pd.read_pickle(cache_tx) if os.path.exists(cache_tx) else pd.DataFrame()
    done = set(json.load(open(cache_done))) if os.path.exists(cache_done) else set()
    cache_un, redo_path = _p(cfg, "cache", "house_paper_unmatched.pkl"), _p(cfg, "cache", "house_paper_redo.json")
    un = pd.read_pickle(cache_un) if os.path.exists(cache_un) else pd.DataFrame()
    redo = set(json.load(open(redo_path))) if os.path.exists(redo_path) else set()
    mode = "ocrspace" if cfg.get("OCR_SPACE_API_KEY") else "tesseract"
    old = open(vpath).read().strip() if os.path.exists(vpath) else ""
    old_ver, _, old_mode = old.partition("-")
    if old and old_mode != mode and mode == "ocrspace":
        # an OCR.space key was just added: read every scanned report again
        tx, done, un, redo = pd.DataFrame(), set(), pd.DataFrame(), set()
    elif old and old_ver != str(PAPER_VERSION):
        # newer reader: reports already read are read again once the backlog is done; their rows stay till then
        redo |= done
        done = set()
        json.dump(sorted(redo), open(redo_path, "w"))
    if old != f"{PAPER_VERSION}-{mode}" and not (old_mode == "ocrspace" and mode == "tesseract"):
        open(vpath, "w").write(f"{PAPER_VERSION}-{mode}")
    nidx, known = paper_name_index(cfg)
    tx, un = _rematch(tx, un, nidx, known, "House paper")
    ocr = PP.OcrSpace(cfg["OCR_SPACE_API_KEY"], _p(cfg, "state", "ocrspace_usage.json")) if cfg.get("OCR_SPACE_API_KEY") else None
    matcher = lambda name: nidx.match(name, known)
    idx = []
    for y in range(start.year, dt.date.today().year + 1):
        try:
            idx.append(house_index(y))
        except Exception:
            pass
    if not idx:
        return tx
    idx = pd.concat(idx, ignore_index=True)
    idx["FilingDate"] = pd.to_datetime(idx["FilingDate"], errors="coerce")
    idx = idx[(idx["FilingDate"] >= start) & idx["DocID"].astype(str).str.match(r"^[89]")]
    ids = idx["DocID"].astype(str)
    # new reports first (newest first), then ones read by an older reader version
    todo = pd.concat([idx[~ids.isin(done) & ~ids.isin(redo)].sort_values("FilingDate", ascending=False),
                      idx[ids.isin(redo) & ~ids.isin(done)].sort_values("FilingDate", ascending=False)])
    if ocr is None:
        log("House paper: no OCR_SPACE_API_KEY; using the built-in reader (handwriting mostly unreadable)")
    elif not ocr.can(2):
        log("House paper: OCR.space daily/monthly allowance used up; continuing next run")
        tx.to_pickle(cache_tx)
        un.to_pickle(cache_un)
        return tx
    log(f"House paper: {len(idx)} scanned reports since {start.date()}, {len(todo)} to read")

    def work(r):
        if ocr is not None and not ocr.can(2):      # allowance used up: leave the rest for the next run
            return r, None
        url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{int(r['year'])}/{r['DocID']}.pdf"
        try:
            b = requests.get(url, headers=UA, timeout=60)
            if b.status_code != 200:
                return r, None
            out = []
            for img in PP.render_pages(b.content):
                out += PP.parse_page(img, r["FilingDate"], ocr=ocr, matcher=matcher)
            return r, out
        except Exception:
            return r, None

    rows, urows, n, stats = [], [], 0, {"rows": 0, "matched": 0}

    def flush():
        nonlocal tx, rows, un, urows
        if rows:
            tx = pd.concat([tx, pd.DataFrame(rows)], ignore_index=True)
            rows = []
        if urows:
            un = pd.concat([un, pd.DataFrame(urows)], ignore_index=True)
            urows = []
        tx.to_pickle(cache_tx)
        un.to_pickle(cache_un)
        json.dump(sorted(done), open(cache_done, "w"))
        json.dump(sorted(redo - done), open(redo_path, "w"))

    for r, items in chunked_map(work, [r for _, r in todo.iterrows()], min(4, cfg["WORKERS"]), cfg, chunk=16):
        if items is None:
            continue
        doc = str(r["DocID"])
        if doc in redo:                      # read again by the newer reader: replace its old lines
            flush()
            tx = tx[tx["doc_id"].astype(str) != doc].reset_index(drop=True) if len(tx) else tx
            un = un[un["doc_id"].astype(str) != doc].reset_index(drop=True) if len(un) else un
        done.add(doc)
        n += 1
        first, last = str(r.get("First") or "").strip(), str(r.get("Last") or "").strip()
        for t in items:
            stats["rows"] += 1
            if not t["tx_raw"] or not t["amount"]:
                continue
            tk = t.get("ticker") or nidx.match(t["name"], known)
            td = t["trade_date"] or (r["FilingDate"] - pd.Timedelta(days=30)).date()
            row = {"member": f"{first} {last}".strip(), "first": first, "last": last, "chamber": "House",
                   "state": str(r.get("StateDst") or "")[:2], "ticker": tk, "tx_type": norm_type(t["tx_raw"]),
                   "trade_date": td, "filed_date": r["FilingDate"], "amt_lo": t["amount"][0],
                   "amt_hi": t["amount"][1], "owner": t["owner"], "source": "house_paper",
                   "doc_id": doc, "tx_raw": t["tx_raw"] + ("" if t["trade_date"] else " (date estimated)"),
                   "option": None, "name": t["name"]}
            if tk:
                stats["matched"] += 1
                rows.append(row)
            elif len(re.sub(r"[^A-Za-z]", "", t["name"] or "")) >= 3:
                urows.append(row)            # kept: matched later if a name list learns the company
        if n % 100 == 0:
            log(f"House paper: {n}/{len(todo)} read")
            flush()
    flush()
    try:
        source_health(cfg, "house_paper", ok=True, rows=int(len(tx)), read_this_run=n,
                      left=int((~idx["DocID"].astype(str).isin(done | redo)).sum()),
                      reread_left=int(idx["DocID"].astype(str).isin(redo - done).sum()), unmatched=int(len(un)),
                      lines_seen=stats["rows"], lines_matched=stats["matched"])
    except Exception:
        pass
    log(f"House paper: {len(tx)} transactions from scanned reports ({stats['matched']}/{stats['rows']} lines matched this run)")
    return tx


SENATE_PAPER_VERSION = 2      # 2: unmatched lines kept for re-matching


def collect_senate_paper(cfg, max_calls=120):
    """Scanned Senate reports: the "Periodic Disclosure of Financial Transactions" form (marks read from pixels,
    names and dates by OCR), or a typed list of trades attached instead. Newest first, resumable; uses at most
    `max_calls` OCR.space requests a run so the House's scanned reports keep moving too."""
    from congress_signals import paper as PP
    ipath = _p(cfg, "cache", "senate_paper_index.json")
    if not os.path.exists(ipath):
        return pd.DataFrame()
    try:
        import pytesseract, cv2  # noqa: F401
        pytesseract.get_tesseract_version()
    except Exception as e:
        log(f"Senate paper: OCR not available ({type(e).__name__}); skipping")
        return pd.DataFrame()
    cache_tx, cache_done = _p(cfg, "cache", "senate_paper_tx.pkl"), _p(cfg, "cache", "senate_paper_done.json")
    vpath = _p(cfg, "cache", "senate_paper_version.txt")
    tx = pd.read_pickle(cache_tx) if os.path.exists(cache_tx) else pd.DataFrame()
    done = set(json.load(open(cache_done))) if os.path.exists(cache_done) else set()
    mode = "ocrspace" if cfg.get("OCR_SPACE_API_KEY") else "tesseract"
    old = open(vpath).read().strip() if os.path.exists(vpath) else ""
    old_ver, _, old_mode = old.partition("-")
    if old and (old_ver != str(SENATE_PAPER_VERSION) or (old_mode != mode and mode == "ocrspace")):
        tx, done = pd.DataFrame(), set()        # new reader version, or an OCR.space key was just added
    if old != f"{SENATE_PAPER_VERSION}-{mode}" and not (old_ver == str(SENATE_PAPER_VERSION) and old_mode == "ocrspace"):
        open(vpath, "w").write(f"{SENATE_PAPER_VERSION}-{mode}")
    index = json.load(open(ipath))
    for r in index:
        r["filed_ts"] = pd.to_datetime(r.get("filed"), errors="coerce")
    index = [r for r in index if pd.notna(r["filed_ts"])]
    todo = sorted([r for r in index if r["link"] not in done], key=lambda r: r["filed_ts"], reverse=True)
    cache_un = _p(cfg, "cache", "senate_paper_unmatched.pkl")
    un = pd.read_pickle(cache_un) if os.path.exists(cache_un) and len(done) else pd.DataFrame()
    nidx, known = paper_name_index(cfg)
    tx, un = _rematch(tx, un, nidx, known, "Senate paper")
    urows = []
    matcher = lambda name: nidx.match(name, known)
    ocr = PP.OcrSpace(cfg["OCR_SPACE_API_KEY"], _p(cfg, "state", "ocrspace_usage.json")) if cfg.get("OCR_SPACE_API_KEY") else None
    used0 = sum((ocr.u.get("day") or {}).values()) if ocr else 0
    calls = lambda: (sum((ocr.u.get("day") or {}).values()) - used0) if ocr else 0
    log(f"Senate paper: {len(index)} scanned reports, {len(todo)} to read")
    try:
        s = _senate_session()
    except Exception as e:
        log(f"Senate paper: could not open eFD site ({e}); skipping")
        return tx
    tries, tpath = _cache_json(cfg, "senate_paper_tries.json", {})
    rows, n, stats = [], 0, {"rows": 0, "matched": 0}
    for rep_ in todo:
        # a report can take ~20 OCR calls (8 pages, 2 engines, typed lists): stop while there's room for one
        if out_of_time(cfg, 160) or (ocr is not None and (calls() > max_calls - 20 or not ocr.can(2))):
            break
        try:
            h = s.get(EFD + rep_["link"], timeout=60)
            urls = [u for u in re.findall(r'<img[^>]+src="([^"]+)"', h.text) if "efd-media" in u]
            if h.status_code != 200 or not urls:
                continue
            pages = [PP.load_image(requests.get(u, headers=UA, timeout=60).content) for u in urls[:8]]
        except Exception:
            continue
        miss0 = ocr.misses if ocr is not None else 0
        items, loose = [], []
        try:
            for img in pages:
                got = PP.parse_page(img, rep_["filed_ts"], ocr=ocr, matcher=matcher, form="senate")
                items += got
                if not got:
                    loose.append(img)
            if not items:                        # no marked form: look for a typed list of trades instead
                for img in loose[:4]:
                    items += PP.parse_statement_page(img, rep_["filed_ts"], ocr, matcher=matcher)
        except Exception as e:
            log(f"Senate paper: {rep_['link']} skipped ({type(e).__name__}: {e})")
            done.add(rep_["link"])               # a scan that breaks the reader would break it every run
            continue
        if ocr is not None and ocr.misses > miss0 and tries.get(rep_["link"], 0) < 2:
            tries[rep_["link"]] = tries.get(rep_["link"], 0) + 1
            continue                             # an OCR call failed or hit the allowance: read it again next run
        done.add(rep_["link"])
        n += 1
        first, last = str(rep_.get("first") or "").strip().title(), str(rep_.get("last") or "").strip().title()
        for t in items:
            stats["rows"] += 1
            if not t["tx_raw"] or not t["amount"]:
                continue
            tk = t.get("ticker") or nidx.match(t["name"], known)
            td = t["trade_date"] or (rep_["filed_ts"] - pd.Timedelta(days=30)).date()
            (rows if tk else urows).append({"name": t["name"],"member": f"{first} {last}".strip(), "first": first, "last": last, "chamber": "Senate",
                         "state": None, "ticker": tk, "tx_type": norm_type(t["tx_raw"]), "trade_date": td,
                         "filed_date": rep_["filed_ts"], "amt_lo": t["amount"][0], "amt_hi": t["amount"][1],
                         "owner": t["owner"], "source": "senate_paper", "doc_id": rep_["link"],
                         "tx_raw": t["tx_raw"] + ("" if t["trade_date"] else " (date estimated)"), "option": None})
            stats["matched"] += bool(tk)
        if n % 20 == 0:
            tx = pd.concat([tx, pd.DataFrame(rows)], ignore_index=True) if rows else tx
            un = pd.concat([un, pd.DataFrame(urows)], ignore_index=True) if urows else un
            rows, urows = [], []
            tx.to_pickle(cache_tx)
            un.to_pickle(cache_un)
            json.dump(sorted(done), open(cache_done, "w"))
    if rows:
        tx = pd.concat([tx, pd.DataFrame(rows)], ignore_index=True)
    if urows:
        un = pd.concat([un, pd.DataFrame(urows)], ignore_index=True)
    tx.to_pickle(cache_tx)
    un.to_pickle(cache_un)
    json.dump(sorted(done), open(cache_done, "w"))
    json.dump(tries, open(tpath, "w"))
    try:
        source_health(cfg, "senate_paper", ok=True, rows=int(len(tx)), read_this_run=n,
                      left=int(sum(1 for r in index if r["link"] not in done)), ocr_calls=calls(), unmatched=int(len(un)),
                      lines_seen=stats["rows"], lines_matched=stats["matched"])
    except Exception:
        pass
    log(f"Senate paper: {len(tx)} transactions from scanned reports ({stats['matched']}/{stats['rows']} lines matched "
        f"this run, {n} reports read)")
    return tx


def data_progress(cfg):
    """How complete the data is, for the dashboard's progress panel and the weekly email."""
    def js(*a, default=None):
        try:
            return json.load(open(_p(cfg, *a)))
        except Exception:
            return default
    h = js("state", "source_health.json", default={}) or {}
    hb = js("cache", "house_backlog.json", default={}) or {}
    rr = js("state", "house_reread.json", default={}) or {}
    use = js("state", "ocrspace_usage.json", default={}) or {}
    today, mon = dt.date.today().isoformat(), dt.date.today().strftime("%Y-%m")
    pc = js("state", "price_coverage.json", default={}) or {}
    names = js("cache", "company_names_hist.json", default={}) or {}
    mid = js("state", "member_ids.json", default={}) or {}
    ft = js("state", FILTER_FILE, default={}) or {}
    cty = js("cache", "ticker_country.json", default={}) or {}
    pick = lambda d, *k: {x: d.get(x) for x in k}
    return {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "house_reread": {"left": hb.get("left"), "at": hb.get("at"), "done_email": bool(rr.get("notified"))},
        "house_scans": pick(h.get("house_paper") or {}, "rows", "left", "reread_left", "unmatched", "read_this_run",
                            "lines_seen", "lines_matched", "at"),
        "senate_scans": pick(h.get("senate_paper") or {}, "rows", "left", "unmatched", "read_this_run", "at"),
        "ocr": {"today": (use.get("day") or {}).get(today, 0), "day_cap": 450,
                "month": (use.get("month") or {}).get(mon, 0), "month_cap": 24000},
        "prices": {"refresh_queue": len(js("cache", "price_refresh_queue.json", default=[]) or []),
                   "trades": pc.get("trades"), "with_prices": pc.get("with_prices"), "missing_share": pc.get("missing_share")},
        "company_names": {"quarters": len(names.get("done") or []), "tickers": len(names.get("names") or {})},
        "countries": len(cty),
        "members": mid,
        "filter_test": {"complete": bool(ft.get("complete")), "updated": ft.get("updated")},
        "sources": {k: {"ok": bool(v.get("ok")), "error": v.get("error")} for k, v in h.items()},
    }


def house_backlog(cfg):
    try:
        return int(json.load(open(_p(cfg, "cache", "house_backlog.json"))).get("left", 0))
    except Exception:
        return 0


# ----------------------------------------------------------------------------
# SOURCE 2: Senate eFD periodic transaction reports (official, free)
# ----------------------------------------------------------------------------
EFD = "https://efdsearch.senate.gov"
SENATE_FIX = 1


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
    if not any("transaction date" in h for h in heads):
        return None             # not a transaction table (e.g. the site's "security risk" block page)

    def col(*names):
        for i, h in enumerate(heads):
            if any(n in h for n in names):
                return i
        return None

    ci = {"date": col("transaction date"), "owner": col("owner"), "ticker": col("ticker"), "name": col("asset name"),
          "atype": col("asset type"), "type": col("type"), "amount": col("amount"), "comment": col("comment")}
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
        cm = (g("comment") or "").strip()
        tick = g("ticker")
        if (tick or "").strip() in ("", "--", "-") and g("name"):
            # ticker left blank but written in the name: "Citigroup Inc (C)", "BRK-B - Berkshire ...", "SIEGY"
            nm = g("name").strip()
            mm = (re.search(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*$", nm) or re.match(r"^([A-Z][A-Z0-9.\-]{0,6})\s+-\s+", nm)
                  or re.fullmatch(r"([A-Z]{1,5})", nm))
            tick = mm.group(1) if mm else tick
        out.append({"trade_date": g("date"), "owner": g("owner"), "ticker": tick,
                    "tx_raw": g("type"), "amt_lo": lo, "amt_hi": hi, "option": opt,
                    "note": None if cm in ("", "--", "-") else cm})
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
    fix_path = _p(cfg, "cache", "senate_fix.txt")
    if not os.path.exists(fix_path) or open(fix_path).read().strip() != str(SENATE_FIX):
        # Oct 5: a blocked page used to count as read (and lost that report's trades), and tickers written only
        # in the asset name were dropped. Re-read reports that gave no rows, and ones with a row missing a ticker.
        have = set(tx["doc_id"].astype(str)) if len(tx) and "doc_id" in tx.columns else set()
        bad = set(tx.loc[tx["ticker"].isna(), "doc_id"].astype(str)) if len(tx) and "ticker" in tx.columns else set()
        redo = {d for d in done if d not in have} | bad
        if redo:
            log(f"Senate: re-reading {len(redo)} reports (blocked pages, missing tickers)")
            tx = tx[~tx["doc_id"].astype(str).isin(bad)].reset_index(drop=True) if len(tx) else tx
            done -= redo
            tx.to_pickle(cache_tx)
            json.dump(sorted(done), open(cache_done, "w"))
        open(fix_path, "w").write(str(SENATE_FIX))
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
    paper = [r for r in reports if "/paper/" in r["link"]]
    if paper:                                  # scanned reports: read by collect_senate_paper
        json.dump(paper, open(_p(cfg, "cache", "senate_paper_index.json"), "w"))

    rows = []

    def work(rep):
        for attempt in range(3):
            try:
                r = s.get(EFD + rep["link"], timeout=60)
                if r.status_code == 200 and "<table" in r.text:
                    items = parse_senate_ptr_html(r.text)
                    if items is not None:
                        return rep, items
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        return rep, None

    if True:
        for i, (rep, items) in enumerate(chunked_map(work, todo, min(4, cfg["WORKERS"]), cfg), 1):
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
                    rows = []
                tx.to_pickle(cache_tx)
                json.dump(sorted(done), open(cache_done, "w"))
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


def source_health(cfg, name, **info):
    """Record whether an outside data source worked (status, rows, error text) for the dashboard."""
    path = _p(cfg, "state", "source_health.json")
    try:
        h = json.load(open(path))
    except Exception:
        h = {}
    h[name] = {**info, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    json.dump(h, open(path, "w"), indent=1, default=str)


def _err_text(r):
    try:
        return re.sub(r"[A-Za-z0-9]{24,}", "…", (r.text or "")[:200])     # never echo anything key-like
    except Exception:
        return ""


def collect_quiver(cfg):
    key = cfg.get("QUIVER_API_KEY")
    if not key:
        source_health(cfg, "quiver", ok=False, error="no QUIVER_API_KEY secret set")
        return pd.DataFrame()
    try:
        r = requests.get("https://api.quiverquant.com/beta/bulk/congresstrading", timeout=180,
                         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        if r.status_code != 200:
            source_health(cfg, "quiver", ok=False, status=r.status_code, error=_err_text(r))
            log(f"Quiver: HTTP {r.status_code}")
            return pd.DataFrame()
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
        df = pd.DataFrame(rows)
        yrs = pd.to_datetime(df["filed_date"], errors="coerce").dt.year.value_counts().sort_index().to_dict() if len(df) else {}
        source_health(cfg, "quiver", ok=bool(len(df)), status=200, rows=len(df),
                      by_year={int(k): int(v) for k, v in yrs.items() if k == k},
                      fields=sorted(r.json()[0].keys()) if len(df) else [])
        return df
    except Exception as e:
        source_health(cfg, "quiver", ok=False, error=f"{type(e).__name__}: {str(e)[:150]}")
        log(f"Quiver: failed ({type(e).__name__})")
        return pd.DataFrame()


def collect_fmp(cfg):
    key = cfg.get("FMP_API_KEY")
    if not key:
        source_health(cfg, "fmp", ok=False, error="no FMP_API_KEY secret set")
        return pd.DataFrame()
    first_err = None
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
                if page == 0 and first_err is None:
                    first_err = f"{chamber}: HTTP {r.status_code} {_err_text(r)}"
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
    df = pd.DataFrame(rows)
    yrs = pd.to_datetime(df["filed_date"], errors="coerce").dt.year.value_counts().sort_index().to_dict() if len(df) else {}
    source_health(cfg, "fmp", ok=bool(len(df)), rows=len(df), error=first_err,
                  by_year={int(k): int(v) for k, v in yrs.items() if k == k})
    return df


def collect_all(cfg):
    parts = []
    if cfg.get("USE_HOUSE", True):
        parts.append(("house_clerk", collect_house(cfg)))
    if cfg.get("USE_SENATE", True):
        parts.append(("senate_efd", collect_senate(cfg)))
    if cfg.get("USE_SENATE_PAPER", True) and cfg.get("USE_SENATE", True) and not out_of_time(cfg, 170):
        try:
            parts.append(("senate_paper", collect_senate_paper(cfg)))
        except Exception as e:
            log(f"Senate paper: skipped ({type(e).__name__}: {e})")
    if cfg.get("USE_HOUSE_PAPER", True) and not out_of_time(cfg, 150):
        try:
            parts.append(("house_paper", collect_house_paper(cfg)))
        except Exception as e:
            log(f"House paper: skipped ({type(e).__name__}: {e})")
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
    # who each trade belongs to (official Congress ID), so de-duplication never merges two same-surname members
    try:
        tx = assign_member_ids(tx, load_legislators(cfg))
        json.dump({"matched_share": float(tx["bio_id"].notna().mean()) if len(tx) else None,
                   "members": int(tx["who"].nunique()),
                   "unmatched_names": tx.loc[tx["bio_id"].isna(), "member"].value_counts().head(10).to_dict()},
                  open(_p(cfg, "state", "member_ids.json"), "w"))
    except Exception as e:
        log(f"Member IDs: skipped at collection ({type(e).__name__}: {e})")
        tx["who"], tx["bio_id"] = tx["chamber"] + "|" + tx["last_key"], None
    # de-duplicate across sources: official sources first
    prio = {"house_clerk": 0, "senate_efd": 0, "quiver": 1, "house_paper": 2, "senate_paper": 2, "fmp": 3}
    # a scanned-report row that Quiver also has (same member, stock and filing day) is dropped: Quiver's is typed
    if (tx["source"] == "quiver").any() and tx["source"].isin(["house_paper", "senate_paper"]).any():
        q = tx[tx["source"] == "quiver"]
        qk = set(zip(q["who"], q["ticker"], q["filed_date"].dt.date))
        pm = tx["source"].isin(["house_paper", "senate_paper"])
        dup = pm & pd.Series([(w, t, f.date()) in qk for w, t, f in
                              zip(tx["who"], tx["ticker"], tx["filed_date"])], index=tx.index)
        tx = tx[~dup]
    tx["_p"] = tx["source"].map(prio).fillna(3)
    tx = tx.sort_values("_p")
    tx = tx.drop_duplicates(["who", "ticker", "trade_date", "tx_type", "amt_lo"], keep="first")
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
# Who is who: every trade gets the member's official Congress ID (bioguide), so two members with the same
# last name (Rick Scott and Tim Scott, Barbara Lee and Susie Lee) are never treated as one person.
# ----------------------------------------------------------------------------
def _last_keys(last):
    nk = name_key(last)
    if not nk:
        return set()
    return {nk.split(" ")[-1], re.split(r"[\s\-]+", nk)[-1], nk.replace(" ", "-")}


def load_legislators(cfg):
    """Everyone who served in Congress since the start date (current and former members), indexed by
    chamber|last name, with their terms (chamber, start, end, state). Refreshed weekly."""
    path = _p(cfg, "cache", "legislators_index.json")
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path))
            if _fresh(old.get("fetched", ""), 7) and old.get("v") == 3:
                return old["idx"]
        except Exception:
            old = {}
    since = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    try:
        people = []
        for f in ("legislators-current.json", "legislators-historical.json"):
            r = requests.get(LEG_BASE + f, timeout=120)
            r.raise_for_status()
            people += r.json()
    except Exception as e:
        log(f"Member IDs: download failed ({type(e).__name__}); using the saved list")
        return old.get("idx", {})
    idx = {}
    for p in people:
        b = (p.get("id") or {}).get("bioguide")
        terms = [["Senate" if t.get("type") == "sen" else "House", t.get("start", ""), t.get("end", ""),
                  t.get("state", ""), t.get("party", "")] for t in p.get("terms") or [] if t.get("end", "") >= since]
        if not b or not terms:
            continue
        nm = p.get("name") or {}
        fst = nm.get("first", "")
        if fst.endswith(".") and nm.get("middle"):     # "C. Scott Franklin" goes by Scott
            fst = nm["middle"]
        short = f"{nm.get('nickname') or fst} {nm.get('last', '')}".strip()
        rec = {"bioguide": b, "name": short or nm.get("official_full", ""),
               "first": nm.get("first", ""), "nick": nm.get("nickname", ""), "middle": nm.get("middle", ""),
               "terms": terms}
        for ch in {t[0] for t in terms}:
            for k in _last_keys(nm.get("last", "")):
                lst = idx.setdefault(f"{ch}|{k}", [])
                if not any(x["bioguide"] == b for x in lst):
                    lst.append(rec)
    json.dump({"v": 3, "fetched": dt.datetime.now().isoformat(timespec="seconds"), "idx": idx}, open(path, "w"))
    log(f"Member IDs: {len({r['bioguide'] for v in idx.values() for r in v}):,} members who served since {since[:4]}")
    return idx


def _first_tokens(first, display=""):
    toks = [t for t in re.split(r"[\s\-]+", name_key(first)) if len(t) >= 2]
    if not toks and display:
        d = [t for t in re.split(r"[\s\-]+", name_key(display)) if len(t) >= 2]
        toks = d[:-1]
    return toks


def identify_member(idx, chamber, last_key, first, state, when, display=""):
    """The legislator record for a trade (None when it can't be told apart from another member)."""
    lk = str(last_key or "")
    cands = idx.get(f"{chamber}|{lk}") or (idx.get(f"{chamber}|{lk.split('-')[-1]}") if "-" in lk else None) or []
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    w = pd.Timestamp(when).strftime("%Y-%m-%d") if when is not None and pd.notna(when) else None
    pool = cands
    if w:
        lo = (pd.Timestamp(w) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        hi = (pd.Timestamp(w) + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        act = [c for c in cands if any(t[0] == chamber and t[1] <= hi and t[2] >= lo for t in c["terms"])]
        if len(act) == 1:
            return act[0]
        pool = act or cands
    if state:
        s = [c for c in pool if any(t[0] == chamber and t[3] == state for t in c["terms"])]
        if len(s) == 1:
            return s[0]
        pool = s or pool
    toks = _first_tokens(first, display)
    for grp in (pool, cands):             # by first name among members serving then, then among all
        for t in toks:
            f = t[:3]
            s = [c for c in grp if any(name_key(x or "")[:3] == f for x in (c["first"], c["nick"], c["middle"]))
                 or any(name_key(x or "").startswith(t) for x in (c["first"], c["nick"]))]
            if len(s) == 1:
                return s[0]
    return None


def assign_member_ids(tx, idx):
    """Adds `who` (one key per real person: their Congress ID, or chamber|last|first when unknown) and `bio_id`;
    matched trades also get the member's official name, so every source spells a member the same way."""
    tx = tx.copy()
    st = tx["state"] if "state" in tx else pd.Series([None] * len(tx), index=tx.index)
    keys = list(zip(tx["chamber"], tx["last_key"], tx["first"].fillna("").astype(str), st.fillna("").astype(str),
                    pd.to_datetime(tx["trade_date"], errors="coerce").fillna(pd.to_datetime(tx["filed_date"], errors="coerce")).dt.strftime("%Y-%m").fillna("2000-01"), tx["member"].fillna("").astype(str)))
    memo, recs = {}, []
    for k in keys:
        if k not in memo:
            memo[k] = identify_member(idx, k[0], k[1], k[2], k[3] or None, pd.Timestamp(k[4] + "-15"), k[5]) if idx else None
        recs.append(memo[k])
    bio = [r["bioguide"] if r else None for r in recs]
    fb = [f"{ch}|{lk}|{(_first_tokens(f, m) or [''])[0][:3]}" for ch, lk, f, _, _, m in keys]
    tx["bio_id"] = bio
    tx["who"] = [b or x for b, x in zip(bio, fb)]
    tx["member"] = [r["name"] if r and r.get("name") else m for r, m in zip(recs, tx["member"])]
    log(f"Member IDs: {sum(b is not None for b in bio) / max(len(bio), 1) * 100:.1f}% of trades matched to a member's "
        f"official ID; {tx['who'].nunique():,} distinct members")
    return tx


def fd_member_ids(fd, idx):
    """`who` for yearly disclosure reports, matched the same way as trades."""
    if fd is None or not len(fd) or "who" in fd:
        return fd
    fd = fd.copy()
    out = []
    for ch, lk, f, s, d in zip(fd["chamber"], fd["last_key"], fd["first"].fillna("").astype(str),
                               fd["state"].fillna("").astype(str) if "state" in fd else [""] * len(fd), fd["filed"]):
        r = identify_member(idx, ch, lk, f, s or None, d) if idx else None
        out.append(r["bioguide"] if r else f"{ch}|{lk}|{(_first_tokens(f) or [''])[0][:3]}")
    fd["who"] = out
    return fd


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


def _yf_volume(tickers, start):
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        d = yf.download(tickers, start=start, auto_adjust=False, progress=False, threads=True)
        if d is None or d.empty:
            return pd.DataFrame()
        v = d["Volume"] if isinstance(d.columns, pd.MultiIndex) else d[["Volume"]].rename(columns={"Volume": tickers[0]})
        return v.dropna(how="all", axis=1)
    except Exception as e:
        log(f"Volume: Yahoo download failed ({type(e).__name__})")
        return pd.DataFrame()


def load_volume(cfg, tickers, priority=None):
    """Daily share volume, kept in a saved table like prices: full history for new tickers (most-traded first, a
    batch at a time while the run has time), just the last month for the rest."""
    cache = _p(cfg, "cache", "volume.pkl")
    vol = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame()
    start = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    pri = priority or {}
    tickers = sorted({t for t in tickers if isinstance(t, str)}, key=lambda t: (-pri.get(t, 0), t))
    tried, tpath = _cache_json(cfg, "volume_tried.json", {})
    missing = [t for t in tickers if t not in vol.columns and _days_since(tried.get(t, "2000-01-01")) >= 7]
    frames = []
    for i in range(0, len(missing), 50):
        if out_of_time(cfg, 70):
            break
        batch = missing[i:i + 50]
        frames.append(_yf_volume(batch, start))
        for t in batch:
            tried[t] = dt.date.today().isoformat()
        time.sleep(2)
    have = [t for t in vol.columns if t in set(tickers)]
    if have and len(vol) and (pd.Timestamp.today().normalize() - vol.index.max()).days >= 1 and not out_of_time(cfg, 70):
        recent = (pd.Timestamp.today() - pd.Timedelta(days=35)).strftime("%Y-%m-%d")
        for i in range(0, len(have), 100):
            if out_of_time(cfg, 70):
                break
            frames.append(_yf_volume(have[i:i + 100], recent))
            time.sleep(1)
    for f in frames:
        if f is None or not len(f):
            continue
        f = f.copy()
        f.index = pd.to_datetime(f.index).tz_localize(None) if getattr(f.index, "tz", None) else pd.to_datetime(f.index)
        vol = f if not len(vol) else f.combine_first(vol)      # newer download wins where both have a value
    if len(vol):
        vol = vol.sort_index()
        vol.to_pickle(cache)
    json.dump(tried, open(tpath, "w"))
    left = len([t for t in tickers if t not in vol.columns])
    log(f"Volume: {len(vol.columns):,} stocks on file" + (f", {left:,} still to download" if left else ""))
    return vol


VOL_WINDOW, VOL_BASE = 10, 60      # look at the 2 weeks before a trade, against the prior 3 months
VOL_FLAG = 5.5                     # "very heavy trading" flag: about the top 10% of trades (the median trade sees ~2x)


def volume_ratio_table(vol):
    """For every stock and day: the highest daily volume of the previous VOL_WINDOW trading days divided by the
    typical (median) daily volume of the VOL_BASE trading days before that window."""
    v = vol.where(vol > 0)
    peak = v.shift(1).rolling(VOL_WINDOW, min_periods=VOL_WINDOW // 2).max()
    base = v.shift(VOL_WINDOW + 1).rolling(VOL_BASE, min_periods=VOL_BASE * 2 // 3).median()
    return peak / base


def volume_features(tx, data):
    """Unusual trading volume in the stock in the two weeks before the member's trade: someone may have been
    trading on the same information. 0 at 4x normal or less, 1 at 12x or more (the median trade sees ~2x)."""
    tx["f_unusual_volume"], tx["volume_ratio"], tx["volume_note"] = 0.0, np.nan, ""
    vol = data.get("volume")
    if vol is None or not len(vol):
        return tx
    ratio = volume_ratio_table(vol)
    idx = ratio.index
    pos = np.searchsorted(idx.values, pd.to_datetime(tx["trade_date"]).values, side="right")   # ratio as of the trade day
    col = {c: i for i, c in enumerate(ratio.columns)}
    R = ratio.values
    rr = np.full(len(tx), np.nan)
    for i, (t, p_) in enumerate(zip(tx["ticker"], pos)):
        j = col.get(t)
        if j is not None and 0 < p_ <= len(idx):
            rr[i] = R[min(p_, len(idx)) - 1, j]
    f = np.clip((rr - 4) / 8, 0, 1)       # the typical trade's peak day is ~2x normal; 8x+ is the top 5%
    f = np.where(np.isfinite(f), f, 0.0)
    tx["f_unusual_volume"], tx["volume_ratio"] = f, rr
    tx["volume_note"] = [f"trading volume hit {x:.1f}x normal in the 2 weeks before the trade" if np.isfinite(x) and x >= VOL_FLAG else ""
                         for x in rr]
    return tx


def worth_a_look(scored, cfg, days=None):
    """Recently disclosed trades with a rare red flag, whatever their score: a family tie to the company, heavy trading
    before the trade, a big company event or federal rule soon after it, or a closed committee briefing just before it."""
    days = days or cfg.get("WATCHLIST_LOOKBACK_DAYS", 30)
    since = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    r = scored[(scored["filed_date"] >= since) & scored["tx_type"].isin(["buy", "sell"])]
    if not len(r):
        return []
    col = lambda c: r[c] if c in r else pd.Series(0, index=r.index)
    flags = ((col("f_spouse_insider") > 0) | (col("f_relative_tie") > 0) | (col("volume_ratio").fillna(0) >= VOL_FLAG)
             | (col("f_reg_action") > 0) | (col("f_witness_after") > 0) | (col("f_markup_after") > 0)
             | (col("f_major_8k_after") > 0) | (col("f_closed_briefing") > 0))
    r = r[flags]
    out = {}
    for x in r.sort_values("filed_date", ascending=False).to_dict("records"):
        why = [x.get(k) for k in ("spouse_note", "relative_note", "volume_note", "event_note") if x.get(k)]
        if x.get("f_closed_briefing", 0) > 0:
            why.append("a closed committee briefing shortly before the trade")
        why = [w for part in why for w in str(part).split("; ") if w]
        if not why:
            continue
        o = out.setdefault(x["ticker"], {"t": x["ticker"], "co": x.get("company") or None, "who": [], "why": [],
                                         "d": pd.Timestamp(x["filed_date"]).strftime("%Y-%m-%d")})
        if not any(w["n"] == x["member"] and w["ty"] == x["tx_type"] for w in o["who"]):
            o["who"].append({"n": str(x["member"]), "b": x.get("bioguide") if isinstance(x.get("bioguide"), str) else None,
                             "ty": x["tx_type"], "td": pd.Timestamp(x["trade_date"]).strftime("%Y-%m-%d"),
                             "d": pd.Timestamp(x["filed_date"]).strftime("%Y-%m-%d")})
        for w in why:
            if w not in o["why"]:
                o["why"].append(w)
    res = sorted(out.values(), key=lambda o: o["d"], reverse=True)[:30]
    log(f"Worth a look: {len(res)} recently disclosed stock(s) with a rare red flag")
    return res


def unusual_activity(scored, px, cfg, days=90, min_ratio=2.5):
    """Stocks members traded in the last `days` days whose trading volume over the last week is unusually high."""
    path = _p(cfg, "cache", "volume.pkl")
    if not os.path.exists(path):
        return []
    vol = pd.read_pickle(path)
    if len(vol) < 80:
        return []
    since = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    rec = scored[(scored["trade_date"] >= since) & scored["ticker"].isin(vol.columns)]
    out = []
    for t, g in rec.groupby("ticker"):
        v = vol[t].dropna()
        v = v[v > 0]
        if len(v) < 70:
            continue
        last5, base = v.iloc[-5:].mean(), v.iloc[-70:-5].median()
        if not base or not np.isfinite(base):
            continue
        r = float(last5 / base)
        peak = float(v.iloc[-5:].max() / base)
        if r < min_ratio and peak < min_ratio * 1.6:
            continue
        p = px[t].dropna() if t in px.columns else pd.Series(dtype=float)
        mv = float(p.iloc[-1] / p.iloc[-6] - 1) if len(p) > 6 else None
        who = []
        for m, ty, td, b in g.sort_values("trade_date", ascending=False)[["member", "tx_type", "trade_date", "bioguide"]].itertuples(index=False):
            if all(w["n"] != m or w["ty"] != ty for w in who):
                who.append({"n": str(m), "ty": ty, "td": pd.Timestamp(td).strftime("%Y-%m-%d"), "b": b if isinstance(b, str) else None})
        out.append({"t": t, "ratio": round(r, 2), "peak": round(peak, 2), "move5": round(mv, 4) if mv is not None else None,
                    "asof": v.index[-1].strftime("%Y-%m-%d"), "who": who[:6],
                    "co": str(g["company"].dropna().iloc[0]) if "company" in g and g["company"].notna().any() else None})
    out.sort(key=lambda x: -max(x["ratio"], x["peak"] / 1.6))
    log(f"Unusual activity: {len(out)} recently traded stock(s) with unusual volume")
    return out[:40]


PRICE_BASIS_VERSION = 1      # Oct 5: one-time full re-download of every saved price history (split/dividend drift)


def _price_queue_add(cfg, tickers, front=False):
    q, path = _cache_json(cfg, "price_refresh_queue.json", [])
    have = set(q)
    new = [t for t in tickers if t not in have]
    q = (new + q) if front else (q + new)
    json.dump(q, open(path, "w"))


def _refresh_price_histories(cfg, px, start, per_run=1500):
    """Re-download full history for queued tickers (restated by a split/dividend, or the one-time rebuild)."""
    vpath = _p(cfg, "cache", "price_basis_version.txt")
    if px is not None and len(px) and (not os.path.exists(vpath) or open(vpath).read().strip() != str(PRICE_BASIS_VERSION)):
        _price_queue_add(cfg, [c for c in px.columns if c != "SPY"] + ["SPY"])
        open(vpath, "w").write(str(PRICE_BASIS_VERSION))
    q, path = _cache_json(cfg, "price_refresh_queue.json", [])
    if not q or px is None:
        return px
    batch, done = q[:per_run], set()
    for i in range(0, len(batch), 50):
        if out_of_time(cfg, 60):
            break
        part = batch[i:i + 50]
        f = _yf_close(part, start, tries=2)
        if len(f):
            f.index = pd.to_datetime(f.index)
            if getattr(f.index, "tz", None) is not None:
                f.index = f.index.tz_localize(None)
            f = f.loc[:, ~f.columns.duplicated()]
            def same_span(c):      # a reused symbol (another company) starts much later: keep the old series
                if c not in px.columns or px[c].first_valid_index() is None:
                    return True
                return f[c].first_valid_index() <= px[c].first_valid_index() + pd.Timedelta(days=30)
            cols = [c for c in f.columns if f[c].notna().sum() > 20 and same_span(c)]
            if cols:
                px = px.drop(columns=[c for c in cols if c in px.columns]).join(f[cols], how="outer")
        done |= set(part)          # tried; a ticker Yahoo can't return keeps its old series
        time.sleep(1)
    q = [t for t in q if t not in done]
    json.dump(q, open(path, "w"))
    log(f"Prices: re-downloaded full history for {len(done)} tickers; {len(q)} still queued")
    return px


def load_prices(cfg, tickers, max_age_hours=12, priority=None):
    """Keeps a saved price table; downloads full history only for new tickers and just the last
    couple of weeks for everything else."""
    cache = _p(cfg, "cache", "prices.pkl")
    tickers = sorted(set(tickers) | {"SPY"})
    start = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    px = pd.read_pickle(cache) if os.path.exists(cache) else None
    if px is not None and ("SPY" not in px.columns or px.index.min() > pd.Timestamp(start) + pd.Timedelta(days=30)):
        log("Prices: saved table doesn't reach back to the start date; rebuilding")
        px = None
    dead_path = _p(cfg, "cache", "no_price_tickers.json")
    dead = json.load(open(dead_path)) if os.path.exists(dead_path) else {}
    fails, fails_path = _cache_json(cfg, "no_price_fails.json", {})
    # a ticker Yahoo missed once is often just throttling: try again after 2 days, then 7, then 30
    wait = lambda t: 2 if fails.get(t, 1) <= 1 else (7 if fails.get(t, 1) <= 3 else 30)
    dead = {t: d for t, d in dead.items() if _days_since(d) < wait(t)}
    pri = priority or {}
    tickers = sorted(tickers, key=lambda t: (-pri.get(t, 0), t))       # most-traded first
    missing = tickers if px is None else [t for t in tickers if t not in px.columns]
    dead_missing = [t for t in missing if t in dead and t != "SPY"]
    missing = [t for t in missing if t not in dead or t == "SPY"]
    frames = []
    if dead_missing and cfg.get("TIINGO_API_KEY") and not out_of_time(cfg, 60):
        tf = tiingo_prices(cfg, dead_missing, start)
        if len(tf):
            frames.append(tf)
    if missing:
        log(f"Prices: downloading full history for {len(missing)} tickers")
        if px is None or "SPY" in missing:
            spy = _yf_close(["SPY"], start)
            if "SPY" not in spy.columns:
                s = _stooq_close("SPY", pd.Timestamp(start))
                spy = s.to_frame() if s is not None else pd.DataFrame()
            if "SPY" not in spy.columns:
                raise RuntimeError("Could not download SPY prices from Yahoo or Stooq.")
            frames.append(spy)
        rest = [t for t in missing if t != "SPY"]
        for i in range(0, len(rest), 50):
            if out_of_time(cfg, 60):
                break
            frames.append(_yf_close(rest[i:i + 50], start, tries=2))
            time.sleep(2)
        got = set().union(*[set(f.columns) for f in frames]) if frames else set()
        retry = [t for t in rest if t not in got]
        if retry and not out_of_time(cfg, 60):
            time.sleep(20)
            for i in range(0, len(retry), 25):
                frames.append(_yf_close(retry[i:i + 25], start, tries=2))
                time.sleep(3)
            got = set().union(*[set(f.columns) for f in frames])
            still = [t for t in retry if t not in got][:300]
            if still:
                with ThreadPoolExecutor(4) as ex:
                    for s_ in ex.map(lambda t: _stooq_close(t, pd.Timestamp(start)), still):
                        if s_ is not None and len(s_):
                            frames.append(s_.to_frame())
            got = set().union(*[set(f.columns) for f in frames])
        still = [t for t in rest if t not in got]
        if still and cfg.get("TIINGO_API_KEY") and not out_of_time(cfg, 60):
            tf = tiingo_prices(cfg, still, start)
            if len(tf):
                frames.append(tf)
                got |= set(tf.columns)
        today = dt.date.today().isoformat()
        dead.update({t: today for t in rest if t not in got})
        for t in rest:
            if t in got:
                fails.pop(t, None)
            else:
                fails[t] = fails.get(t, 0) + 1
        json.dump(dead, open(dead_path, "w"))
        json.dump(fails, open(fails_path, "w"))
        log(f"Prices: got {len(got)}/{len(missing)} new tickers")
    new = pd.concat(frames, axis=1) if frames else pd.DataFrame()
    if len(new):
        new.index = pd.to_datetime(new.index)
        if getattr(new.index, "tz", None) is not None:
            new.index = new.index.tz_localize(None)
        new = new.loc[:, ~new.columns.duplicated()]
    if px is None:
        px = new
    elif len(new):
        px = px.join(new[[c for c in new.columns if c not in px.columns]], how="outer")
    # refresh recent days for tickers already on file
    if px is not None and len(px) and (pd.Timestamp.today().normalize() - px.index.max()).days >= 1 \
            and (time.time() - (os.path.getmtime(cache) if os.path.exists(cache) else 0)) / 3600 > max_age_hours / 4:
        since = (px.index.max() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        cols = [c for c in px.columns if c not in dead]
        upd = []
        for i in range(0, len(cols), 200):
            if out_of_time(cfg, 45):
                break
            upd.append(_yf_close(cols[i:i + 200], since, tries=2))
            time.sleep(1)
        upd = pd.concat(upd, axis=1) if upd else pd.DataFrame()
        if len(upd):
            upd.index = pd.to_datetime(upd.index)
            if getattr(upd.index, "tz", None) is not None:
                upd.index = upd.index.tz_localize(None)
            upd = upd.loc[:, ~upd.columns.duplicated()]
            # Yahoo's adjusted closes restate the whole history after a split or dividend; if the overlap no
            # longer matches what's on file, that ticker's saved history is on an old basis and is re-downloaded
            common = upd.index.intersection(px.index)
            cc = [c for c in upd.columns if c in px.columns]
            if len(common) and cc:
                ratio = (upd.loc[common, cc] / px.loc[common, cc]).median()
                restated = [c for c, r in ratio.items() if pd.notna(r) and abs(r - 1) > 0.003]
                if restated:
                    _price_queue_add(cfg, restated, front=True)
            px = upd.combine_first(px)
            log(f"Prices: updated recent days for {upd.shape[1]} tickers")
    px = _refresh_price_histories(cfg, px, start)
    px = px.loc[:, ~px.columns.duplicated()].sort_index().dropna(how="all", axis=1)
    # don't save a still-forming bar for today (a run during market hours would freeze an intraday price)
    now_et = pd.Timestamp.now(tz="America/New_York")
    keep = px
    if len(px) and px.index.max().normalize() == now_et.normalize().tz_localize(None) and \
            (now_et.hour, now_et.minute) < (16, 30):
        keep = px.iloc[:-1]
    keep.to_pickle(cache)
    log(f"Prices: {px.shape[1]} tickers, {px.index.min().date()} to {px.index.max().date()}")
    return px



# ============================================================================
# Ticker metadata (sector, company name) from Yahoo
# ============================================================================
def load_meta(cfg, tickers):
    import yfinance as yf
    cache = _p(cfg, "cache", "ticker_meta.json")
    meta = json.load(open(cache)) if os.path.exists(cache) else {}
    # caps need the date they were looked up (to scale them back in time correctly); older entries get refreshed
    todo = [t for t in set(tickers) if not isinstance(meta.get(t), dict) or "cap" not in meta[t]]
    todo += sorted(t for t in set(tickers) if isinstance(meta.get(t), dict) and "cap" in meta[t]
                   and meta[t].get("cap") and "cap_at" not in meta[t])
    if todo:
        log(f"Company info: looking up {len(todo)} tickers")

        def get(t):
            for a in range(2):
                try:
                    i = yf.Ticker(t).info or {}
                    return t, {"sector": i.get("sector") or i.get("quoteType"), "industry": i.get("industry"),
                               "name": i.get("longName") or i.get("shortName") or "", "type": i.get("quoteType"),
                               "cap": i.get("marketCap"), "cap_at": dt.date.today().isoformat()}
                except Exception:
                    time.sleep(2)
            return t, {"sector": None, "industry": None, "name": "", "type": None, "cap": None}

        todo = todo[:2500] if cfg.get("TIME_BUDGET_MIN") else todo
        if True:
            for i, (t, m) in enumerate(chunked_map(get, todo, min(4, cfg["WORKERS"]), cfg), 1):
                if m.get("name") or not isinstance(meta.get(t), dict):
                    meta[t] = m
                elif isinstance(meta.get(t), dict) and m.get("cap"):
                    meta[t].update(cap=m["cap"], cap_at=m["cap_at"])
                if i % 300 == 0:
                    json.dump(meta, open(cache, "w"))
                    log(f"Company info: {i}/{len(todo)}")
        json.dump(meta, open(cache, "w"))
    return meta


def load_countries(cfg, tickers, priority=None, per_run=1500):
    """Country of each company (Yahoo's company profile), cached in cache/ticker_country.json and filled in a
    batch per run, most-traded first. Funds have no country."""
    import yfinance as yf
    cache, path = _cache_json(cfg, "ticker_country.json", {})
    pri = priority or {}
    todo = sorted({t for t in tickers if isinstance(t, str) and t not in cache}, key=lambda t: (-pri.get(t, 0), t))[:per_run]

    def get(t):
        try:
            i = yf.Ticker(t).info or {}
            return t, {"c": i.get("country"), "type": i.get("quoteType")}
        except Exception:
            return t, None
    n = 0
    for t, v in chunked_map(get, todo, min(4, cfg.get("WORKERS", 4)), cfg):
        if v is not None:
            cache[t] = v
            n += 1
    if n:
        json.dump(cache, open(path, "w"))
    left = len({t for t in tickers if isinstance(t, str)} - set(cache))
    log(f"Countries: {len(cache):,} companies on file" + (f", {left:,} still to look up" if left else ""))
    return cache


def _cluster_se(x, when):
    """Standard error of the average trade result when trades in the same quarter move together (a market
    swing hits every holding at once, and members often buy the same stock): trades are grouped by the quarter
    they were filed, and the spread is measured between groups, not between single trades."""
    x = pd.Series(np.asarray(x, dtype=float))
    q = pd.Series(pd.to_datetime(pd.Series(when).values, errors="coerce")).dt.to_period("Q").astype(str).values
    ok = x.notna().values
    x, q = x[ok].values, q[ok]
    n = len(x)
    if n < 2:
        return np.nan
    naive = x.std(ddof=1) / np.sqrt(n)
    e = pd.Series(x - x.mean()).groupby(q).sum().values
    G = len(e)
    if G < 8:                                     # too few quarters to measure: fall back, never narrower
        return naive
    se = np.sqrt((e ** 2).sum() * G / (G - 1)) / n
    return float(max(se, naive))


def _ci(x, when=None):
    x = pd.Series(x, dtype=float)
    keep = x.notna()
    if keep.sum() < 2:
        return None, None
    se = _cluster_se(x[keep].values, pd.Series(when).values[keep.values]) if when is not None else x.std() / np.sqrt(keep.sum())
    m = x[keep].mean()
    return float(m - 1.96 * se), float(m + 1.96 * se)


def foreign_report(scored, countries, cfg):
    """How often members buy foreign companies, and how those purchases did vs US ones (and vs the S&P)."""
    b = scored[scored["tx_type"] == "buy"].copy()
    b = b.drop_duplicates(["member", "ticker", "trade_date"])
    otc_f = b["ticker"].str.fullmatch(r"[A-Z]{4}[FY]").fillna(False)

    def kind(t, otc):
        c = countries.get(t) or {}
        if (c.get("type") or "").upper() in ("ETF", "MUTUALFUND"):
            return "Fund"
        if c.get("c"):
            return "United States" if c["c"] == "United States" else "Foreign"
        return "Foreign" if otc else "Unknown"
    b["origin"] = [kind(t, o) for t, o in zip(b["ticker"], otc_f)]
    b["country"] = [((countries.get(t) or {}).get("c") or "") for t in b["ticker"]]
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "looked_up": int(sum(1 for t in b["ticker"].unique() if t in countries)),
           "tickers": int(b["ticker"].nunique()), "groups": [], "countries": [], "top": []}
    closed = b[b["closed"].astype(bool) & b["excess"].notna()]
    for g in ("United States", "Foreign", "Fund", "Unknown"):
        x, c = b[b["origin"] == g], closed[closed["origin"] == g]
        lo, hi = _ci(c["excess"], c["filed_date"] if "filed_date" in c else None)
        out["groups"].append({"group": g, "purchases": int(len(x)), "share": float(len(x) / max(len(b), 1)),
                              "stocks": int(x["ticker"].nunique()), "finished": int(len(c)),
                              "avg_vs_spy": float(c["excess"].mean()) if len(c) else None,
                              "median_vs_spy": float(c["excess"].median()) if len(c) else None,
                              "beat_spy": float((c["excess"] > 0).mean()) if len(c) else None, "low": lo, "high": hi})
    f = b[b["origin"] == "Foreign"]
    for ctry, x in f[f["country"] != ""].groupby("country"):
        c = closed[(closed["origin"] == "Foreign") & (closed["country"] == ctry)]
        out["countries"].append({"country": ctry, "purchases": int(len(x)), "stocks": int(x["ticker"].nunique()),
                                 "avg_vs_spy": float(c["excess"].mean()) if len(c) >= 10 else None, "finished": int(len(c))})
    out["countries"].sort(key=lambda r: -r["purchases"])
    out["countries"] = out["countries"][:15]
    for t, x in f.groupby("ticker"):
        out["top"].append({"t": t, "country": x["country"].iloc[0] or "OTC (foreign)", "purchases": int(len(x)),
                           "members": int(x["member"].nunique())})
    out["top"] = sorted(out["top"], key=lambda r: -r["purchases"])[:15]
    out["hold_days"] = int(load_adaptive(cfg)["policy"].get("hold_other", cfg.get("HOLD_DAYS", 60)))
    json.dump(out, open(_p(cfg, "state", "foreign_report.json"), "w"), default=str)
    g = {r["group"]: r for r in out["groups"]}
    log(f"Foreign companies: {g['Foreign']['purchases']:,} of {len(b):,} purchases "
        f"({g['Foreign']['share']*100:.1f}%); country known for {out['looked_up']:,} of {out['tickers']:,} stocks")
    return out


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


def ticker_renames(cfg):
    """Finds tickers that changed (e.g. FI -> FISV) without a hand-made list. SEC insider filings record
    the ticker each company used on every filing date; the SEC's current ticker list gives today's ticker
    for the same company. Old symbols no company uses today map to the company's current ticker."""
    hdr = _sec_headers(cfg)
    seen, seen_path = _cache_json(cfg, "ticker_cik.json", {})          # symbol -> [cik, last filing date]
    done, done_path = _cache_json(cfg, "ticker_cik_quarters.json", [])
    if not hdr:
        return {}
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=120)
    today = pd.Timestamp.today()
    n_new = 0
    for y in range(start.year, today.year + 1):
        for q in range(1, 5):
            key = f"{y}q{q}"
            if key in done or pd.Timestamp(y, 3 * q - 2, 1) > today or pd.Timestamp(y, 3 * q, 1) < start - pd.Timedelta(days=90):
                continue
            if out_of_time(cfg, 75):
                break
            try:
                r = requests.get(f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
                                 f"{key}_form345.zip", headers=hdr, timeout=180)
                if r.status_code != 200:
                    continue
                sub = _read_tsv(zipfile.ZipFile(io.BytesIO(r.content)), "SUBMISSION.TSV",
                                ["ISSUERCIK", "ISSUERTRADINGSYMBOL", "FILING_DATE"])
                sub["t"] = sub["ISSUERTRADINGSYMBOL"].map(clean_ticker)
                sub["d"] = pd.to_datetime(sub["FILING_DATE"], format="%d-%b-%Y", errors="coerce").dt.strftime("%Y-%m-%d")
                sub = sub.dropna(subset=["t", "d", "ISSUERCIK"]).sort_values("d").drop_duplicates("t", keep="last")
                for t, c, d in zip(sub["t"], sub["ISSUERCIK"], sub["d"]):
                    if t not in seen or d >= seen[t][1]:
                        seen[t] = [int(c), d]
                if pd.Timestamp(y, 3 * q, 1) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=45) < today:
                    done.append(key)
                n_new += 1
                time.sleep(0.2)
            except Exception as e:
                log(f"Ticker changes: {key} failed ({e})")
    if n_new:
        json.dump(seen, open(seen_path, "w"))
        json.dump(done, open(done_path, "w"))
    try:
        cur = requests.get("https://www.sec.gov/files/company_tickers.json", headers=hdr, timeout=60).json().values()
    except Exception as e:
        log(f"Ticker changes: SEC ticker list failed ({e})")
        return {}
    now_tickers, cik_now = set(), {}
    for v in cur:                                    # ordered by size; first ticker per company is its main one
        t = clean_ticker(v["ticker"])
        if t:
            now_tickers.add(t)
            cik_now.setdefault(int(v["cik_str"]), t)
    ren = {t: cik_now[c] for t, (c, _) in seen.items()
           if t not in now_tickers and c in cik_now and cik_now[c] != t}
    log(f"Ticker changes: {len(ren)} old symbols map to a company's current ticker")
    return ren


DELIST_OUTCOME = {   # assumed result vs the S&P 500 for a trade in a stock we have no prices for, by why it vanished
    "bought out": 0.15,          # typical takeover premium, paid in cash or stock
    "bankrupt": -0.90,           # shareholders are usually wiped out
    "failed": -0.50,             # delisted for low price or missed rules: research puts the average loss at 30-55%
    "moved": 0.0,                # still trades somewhere else (OTC or a new ticker); no clear direction
}
MERGER_FORMS = {"DEFM14A", "DEFM14C", "PREM14A", "SC 14D9", "SC TO-T", "SC 13E3", "425"}


def classify_missing(cfg, tickers):
    """Why stocks we have no prices for disappeared, from each company's SEC filing history: still trading
    under a new ticker, bought out, bankrupt, delisted for failing listing rules, or unknown."""
    hdr = _sec_headers(cfg)
    out, path = _cache_json(cfg, "delistings.json", {})
    if not hdr or not tickers:
        return out
    ciks, _ = _cache_json(cfg, "ticker_cik.json", {})
    try:
        cur = requests.get("https://www.sec.gov/files/company_tickers.json", headers=hdr, timeout=60).json().values()
        now = {int(v["cik_str"]): clean_ticker(v["ticker"]) for v in cur}
    except Exception:
        now = {}
    n = 0
    for t in tickers:
        if t in out and _fresh(out[t].get("checked"), 30):
            continue
        if out_of_time(cfg, 60) or n >= 400:
            break
        rec = {"status": "unknown", "checked": dt.datetime.now().isoformat()}
        c = (ciks.get(t) or [None])[0]
        if c:
            try:
                js = requests.get(f"https://data.sec.gov/submissions/CIK{int(c):010d}.json", headers=hdr, timeout=30).json()
                time.sleep(0.12)
                f = js.get("filings", {}).get("recent", {})
                forms, items, dates = f.get("form", []), f.get("items", []), f.get("filingDate", [])
                its = [(fm, str(it or ""), d) for fm, it, d in zip(forms, items or [""] * len(forms), dates)]
                new = now.get(int(c)) or next((clean_ticker(x) for x in js.get("tickers") or [] if clean_ticker(x)), None)
                rec.update({"name": js.get("name"), "last_filing": max(dates) if dates else None})
                if new and new != t:
                    rec.update({"status": "renamed", "new": new})
                elif any(fm == "8-K" and "1.03" in it for fm, it, _ in its):
                    rec["status"] = "bankrupt"
                elif any(fm in MERGER_FORMS for fm, _, _ in its) or any(fm == "8-K" and "5.01" in it for fm, it, _ in its):
                    rec["status"] = "bought out"
                elif any(fm.startswith("25") for fm, _, _ in its) or any(fm == "8-K" and "3.01" in it for fm, it, _ in its):
                    rec["status"] = "failed"
                elif any(fm.startswith("15-") for fm, _, _ in its):
                    rec["status"] = "moved"
            except Exception:
                pass
        out[t] = rec
        n += 1
    json.dump(out, open(path, "w"), indent=0)
    return out


def missing_price_scenarios(tx, px, cfg, horizons=None):
    """How much the purchases we can't price could move the headline average, using why each stock vanished."""
    buys = tx[(tx["tx_type"] == "buy") & tx["ticker"].notna()]
    if not len(buys):
        return None
    miss = buys[~buys["ticker"].isin(px.columns)]
    cl, _ = _cache_json(cfg, "delistings.json", {})
    status = miss["ticker"].map(lambda t: (cl.get(t) or {}).get("status", "unknown"))
    status = status.where(status != "renamed", "unknown")       # renamed ones get prices on the next run
    counts = status.value_counts().to_dict()
    share = len(miss) / len(buys)
    known = status.map(DELIST_OUTCOME)
    best = known.fillna(0.0).mean() if len(miss) else 0.0
    worst = known.fillna(DELIST_OUTCOME["failed"]).mean() if len(miss) else 0.0
    base = None
    for h in horizons or []:
        if h.get("Group") == "All purchases" and h.get("Holding period") == "3 months":
            base = h.get("Avg vs SPY")
    res = {"missing_buys": int(len(miss)), "all_buys": int(len(buys)), "share": share, "by_reason": counts,
           "assumptions": DELIST_OUTCOME, "avg_missing_best": best, "avg_missing_worst": worst}
    if base is not None:
        res.update({"reported": base, "with_missing_best": base * (1 - share) + best * share,
                    "with_missing_worst": base * (1 - share) + worst * share})
    return res


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
            if out_of_time(cfg):
                break
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
        if out_of_time(cfg):
            break
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
    todo = [t for t in tickers if (not _fresh(cache.get(t, {}).get("fetched", ""), 7) or cache.get(t, {}).get("v") != 3)
            and company_key((meta.get(t) or {}).get("name"))]
    if todo:
        log(f"Contracts: checking {len(todo)} companies on USAspending.gov")

    def work(t):
        key = company_key(meta[t]["name"])
        rows = []
        pages = 6 if is_defense(t, meta) else 2
        for page in range(1, pages + 1):
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

    if True:
        for i, (t, rows) in enumerate(chunked_map(work, todo, 4, cfg), 1):
            if rows is not None:
                cache[t] = {"fetched": dt.datetime.now().isoformat(), "rows": rows, "v": 3}
            if i % 100 == 0:
                json.dump(cache, open(path, "w"))
                log(f"Contracts: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    out = [{"ticker": t, "action_date": r[0], "amount": r[1], "agency": r[2], "pop_state": (r[3] if len(r) > 3 else None) or None}
           for t, v in cache.items() for r in v.get("rows", [])]
    c = pd.DataFrame(out, columns=["ticker", "action_date", "amount", "agency", "pop_state"])
    c["action_date"] = pd.to_datetime(c["action_date"], errors="coerce")
    # When the public learned of each award: the Defense Department announces every award of $7.5M+ on the
    # day it is made (defense.gov); smaller defense awards reach USAspending after 90 days; others after ~2 weeks.
    dod = c["agency"].str.contains("Defense", case=False, na=False)
    c["known_date"] = c["action_date"] + pd.to_timedelta(
        np.where(dod & (c["amount"] >= 7.5e6), 1, np.where(dod, 90, 14)), unit="D")
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
    todo = [t for t in tickers if (not _fresh(cache.get(t, {}).get("fetched", ""), 14) or cache.get(t, {}).get("v") != 3)
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
                    who = sorted({" ".join(x for x in ((l.get("lobbyist") or {}).get("first_name"),
                                                           (l.get("lobbyist") or {}).get("last_name")) if x)
                                  for a in acts for l in (a.get("lobbyists") or [])} - {""})
                    rows.append([f.get("dt_posted"), float(amt or 0),
                                 sorted({a.get("general_issue_code") for a in acts if a.get("general_issue_code")}), cov[:2000],
                                 who[:60]])
                url, params, pages = js.get("next"), None, pages + 1
                if not key:
                    time.sleep(4)
            if ok:
                break
        if ok:
            cache[t] = {"fetched": dt.datetime.now().isoformat(), "rows": rows, "v": 3}
        if i % 50 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Lobbying: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    out = [{"ticker": t, "posted": r[0], "amount": r[1], "issues": r[2], "covered": r[3] if len(r) > 3 else "",
            "lobbyists": r[4] if len(r) > 4 else []}
           for t, v in cache.items() for r in v.get("rows", [])]
    lb = pd.DataFrame(out, columns=["ticker", "posted", "amount", "issues", "covered", "lobbyists"])
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
            if out_of_time(cfg):
                break
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
        if out_of_time(cfg):
            break
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


def committee_records(tx, history, committees):
    """Each trade's committee record. Looked up by the member's Congress ID when known (so a same-surname
    colleague's seats are never used), by name otherwise; only rosters in force at the trade date (no peeking)."""
    by_bio = [(d, {m["bioguide"]: m for ms in snap.values() for m in ms if m.get("bioguide")}) for d, snap in history or []]
    cur = {m["bioguide"]: m for ms in (committees or {}).values() for m in ms if m.get("bioguide")}
    dates = [d for d, _ in by_bio]
    recent = pd.Timestamp.today() - pd.Timedelta(days=120)       # today's roster only stands in for recent trades
    bio = tx["bio_id"] if "bio_id" in tx else pd.Series([None] * len(tx), index=tx.index)
    out = []
    for r, b in zip(tx[["chamber", "last_key", "first", "state", "trade_date"]].itertuples(index=False), bio):
        m = None
        if isinstance(b, str) and b:
            if by_bio:
                pos = int(np.searchsorted(np.array(dates, dtype="datetime64[ns]"), np.datetime64(r.trade_date), side="right")) - 1
                for j in (pos, pos - 1):
                    if 0 <= j < len(by_bio) and b in by_bio[j][1]:
                        m = by_bio[j][1][b]
                        break
            if m is None and (not by_bio or r.trade_date >= recent):
                m = cur.get(b)
            out.append(m)
            continue
        if history:
            m = match_member_at(history, r.trade_date, r.chamber, r.last_key, r.first, r.state) \
                or (match_member(committees, r.chamber, r.last_key, r.first, r.state) if r.trade_date >= recent else None)
        else:
            m = match_member(committees, r.chamber, r.last_key, r.first, r.state)
        out.append(m)
    return out


def match_member_at(history, when, chamber, last_key, first, state):
    """Committee record for this member as of `when` (latest snapshot on or before it)."""
    if not history:
        return None
    pos = -1
    for i, (d, _) in enumerate(history):
        if d <= when:
            pos = i
    if pos < 0:                      # trade predates the first saved roster
        return None
    # the snapshot in force at the time, then the one before it; never a later roster (no peeking)
    for j in [pos, pos - 1]:
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


_FFILL = {}
_COST_RT = [0.0]          # round-trip trading cost of the last forward_returns call (for short-side stats)


def _px_ffill(px):
    """Prices carried forward over missing days (and after a stock's last trade, so a delisted stock
    exits at its last price instead of vanishing from the results)."""
    key = (id(px), px.shape)
    if key not in _FFILL:
        _FFILL.clear()
        _FFILL[key] = px.ffill()
    return _FFILL[key]


def forward_returns(tx, px, hold, cost=0.0, stop=None, exit_pos=None):
    """Return after entering the day after disclosure and holding `hold` trading days, minus trading costs.
    `exit_pos` (optional, one price-index position per row) replaces the scheduled exit (price rules);
    `stop` (optional, one price-index position per row) ends a trade early on that day."""
    idx = px.index
    n = len(idx)
    ent = _entry_positions(idx, tx["filed_date"])
    ext = ent + hold
    if exit_pos is not None:
        exit_pos = np.asarray(exit_pos)
        ext = np.where(exit_pos > ent, exit_pos, ext)
    if stop is not None:
        stop = np.asarray(stop)
        ext = np.where(stop > ent, np.minimum(ext, stop), ext)
    col = {c: i for i, c in enumerate(px.columns)}
    _COST_RT[0] = 2 * cost
    raw = px.values
    pf = _px_ffill(px)
    arr = pf.values                     # exits use the last price on or before the exit day
    spy = pf["SPY"].values
    ci = tx["ticker"].map(col)
    ok = ci.notna().values & (ent < n)
    res = pd.DataFrame(index=tx.index, columns=["entry_date", "exit_date", "entry_px", "exit_px",
                                                "ret", "spy_ret", "excess", "closed"], dtype=object)
    e_i = np.where(ok, ent, 0)
    x_i = np.minimum(np.where(ok, ext, 0), n - 1)
    c_i = np.where(ok, ci.fillna(0).astype(int).values, 0)
    p0, p1 = raw[e_i, c_i], arr[x_i, c_i]
    s0, s1 = spy[e_i], spy[x_i]
    res["entry_date"] = np.where(ok, idx.values[e_i], np.datetime64("NaT"))
    res["exit_date"] = np.where(ok, idx.values[x_i], np.datetime64("NaT"))
    res["entry_px"], res["exit_px"] = np.where(ok, p0, np.nan), np.where(ok, p1, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = p1 / p0 - 1
        sr = s1 / s0 - 1
    res["ret"] = np.where(ok, r - 2 * cost, np.nan)
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
    for w, g in tx.groupby("who"):
        past = done[done["who"] == w].sort_values("exit_date")
        if past.empty:
            continue
        csum = np.concatenate([[0.0], np.cumsum((sign * past["excess"]).clip(-1, 3).values)])
        k = np.searchsorted(past["exit_date"].values, g["filed_date"].values, side="left")
        ii = [pos[x] for x in g.index]
        n[ii] = k
        val[ii] = csum[k]
    return val, n


def _party_letter(p):
    p = str(p or "").strip().lower()
    return {"republican": "R", "democrat": "D", "democratic": "D", "independent": "I"}.get(p, p[:1].upper() if p else "")


def save_member_tags(cfg, tx, mems):
    """Party-state tags like "R-TX" for the dashboard, keyed by member name and by congress ID."""
    path = _p(cfg, "state", "member_tags.json")
    try:
        tags = json.load(open(path)) if os.path.exists(path) else {}
    except Exception:
        tags = {}
    names, bios = tags.setdefault("names", {}), tags.setdefault("bio", {})
    for name, st, m in zip(tx["member"], tx["state"] if "state" in tx else [None] * len(tx), mems):
        if not m:
            continue
        pl, state = _party_letter(m.get("party")), (m.get("state") or st or "")
        if not (pl and state):
            continue
        tag = f"{pl}-{str(state).upper()}"
        if isinstance(name, str) and name:
            names[name] = tag
        if m.get("bioguide"):
            bios[m["bioguide"]] = tag
    json.dump(tags, open(path, "w"))


def compute_features(tx, px, data, cfg):
    meta, committees = data["meta"], data["committees"]
    hold = cfg["HOLD_DAYS"]
    K = cfg["TRACK_PRIOR_TRADES"]
    tx = tx.copy().reset_index(drop=True)
    if "who" not in tx:
        tx = assign_member_ids(tx, data.get("legislators") or {})
    tx = tx.join(forward_returns(tx, px, hold, cost=cfg.get("COST_BPS", 0) / 1e4))

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
        d, who, typ = g["filed_date"].values, g["who"].values, g["tx_type"].values
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
    # foreign company (Yahoo's company country; OTC symbols ending F/Y are foreign shares). A fixed fact about the
    # company, so no look-ahead. Funds don't count.
    cty = data.get("countries") or {}

    def _foreign(t):
        c = cty.get(t) or {}
        if (c.get("type") or "").upper() in ("ETF", "MUTUALFUND"):
            return 0.0
        if c.get("c"):
            return float(c["c"] != "United States")
        return float(bool(re.fullmatch(r"[A-Z]{4}[FY]", str(t))))
    tx["f_foreign"] = tx["ticker"].map(_foreign).astype(float)
    tx["company"] = tx["ticker"].map(lambda t: (meta.get(t) or {}).get("name") or "")
    history = data.get("committee_history")
    mems = committee_records(tx, history, committees)
    tx["committees"] = ["; ".join(m["committees"]) if m else "" for m in mems]
    tx["bioguide"] = [(m.get("bioguide") if m else None) or b for m, b in
                      zip(mems, tx["bio_id"] if "bio_id" in tx else [None] * len(tx))]
    tx["party"] = [m.get("party") if m else None for m in mems]
    try:
        save_member_tags(cfg, tx, mems)
    except Exception as e:
        log(f"Member tags: skipped ({e})")
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
    tx["member_median_lag"] = tx.groupby("who")["lag_days"].transform(
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
                        # quarterly FEC reports can arrive up to ~105 days after a donation
                        m = (g["date"] <= e - pd.Timedelta(days=105)) & (g["date"] > e - pd.Timedelta(days=1461))
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
            # cosponsorships carry only the bill's introduction date, not when the member signed on (often later,
            # possibly after this filing), so only bills the member sponsored count
            w = w[w["role"] == "sponsor"]
            if len(w):
                sp = (w["role"] == "sponsor").any()
                f[i] = 1.0 if sp else 0.5
                note[i] = f"{'sponsored' if sp else 'cosponsored'} {len(w)} {w['policy'].iloc[0]} bill(s)"
        tx["f_bills"], tx["bill_note"] = f, note
    tx = compute_connections(tx, data, cfg, mems)
    tx = relationship_features(tx, data, cfg, mems)
    tx = volume_features(tx, data)
    tx = event_features(tx, data, mems)
    tx = analyst_features(tx, data)
    tx = extra_features(tx, px, data, cfg, mems)
    return tx


def _score(tx, weights):
    raw = sum(w * tx[f"f_{k}"] for k, w in weights.items() if f"f_{k}" in tx)
    lo = sum(w * (FACTOR_RANGE.get(k, (0, 1))[0 if w > 0 else 1]) for k, w in weights.items())
    hi = sum(w * (FACTOR_RANGE.get(k, (0, 1))[1 if w > 0 else 0]) for k, w in weights.items())
    return ((raw - lo) / max(hi - lo, 1e-9) * 100).clip(0, 100).round(1)


# New signals start switched off; the weekly check turns one on (with this weight) only when it's clearly better.
TRIAL_SIGNALS = {"relative_tie": 0.5, "unusual_volume": 0.4, "reg_action": 0.3, "witness_after": 0.4,
                 "markup_after": 0.3, "major_8k_after": 0.3,
                 "foreign": -0.3}      # negative: a foreign company's purchase scores lower (they've lagged US ones)
TRIAL_LABELS = {"relative_tie": "Signal: a relative is a company insider (SEC)",
                "unusual_volume": "Signal: unusual trading volume before the trade",
                "reg_action": "Signal: federal rule naming the company soon after the trade",
                "witness_after": "Signal: company testified to their committee soon after the trade",
                "markup_after": "Signal: their committee marked up an industry bill soon after the trade",
                "major_8k_after": "Signal: major company announcement (8-K) soon after the trade",
                "foreign": "Signal: score foreign companies lower (they've lagged US purchases)"}


def active_weights(cfg, signals_on=None):
    bw, sw = cfg["BUY_WEIGHTS"], cfg["SHORT_WEIGHTS"]
    on = load_adaptive(cfg)["policy"].get("signals_on", []) if signals_on is None else signals_on
    bw = {**{k: 0.0 for k in TRIAL_SIGNALS}, **bw, **{k: TRIAL_SIGNALS[k] for k in on if k in TRIAL_SIGNALS}}
    path = os.path.join(cfg["DATA_DIR"], "state", "tuned_weights.json")
    use = cfg.get("USE_TUNED_WEIGHTS")
    if use == "auto":
        use = load_adaptive(cfg).get("use_tuned", False)
    if use and os.path.exists(path):
        t = json.load(open(path))
        bw, sw = t.get("buy", bw), t.get("short", sw)
        log("Using tuned weights from the last tuning run")
    lpath = os.path.join(cfg["DATA_DIR"], "state", "learned_weights.json")
    if load_adaptive(cfg)["policy"].get("main_method") == "learned" and os.path.exists(lpath):
        bw = {**{k: 0.0 for k in cfg["BUY_WEIGHTS"]}, **json.load(open(lpath)).get("buy", {})}
    # trial signals follow the weekly check's on/off list whichever weight set is in use
    for k in TRIAL_SIGNALS:
        bw[k] = (bw.get(k) or TRIAL_SIGNALS[k]) if k in on else 0.0
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
                   ("f_late", lambda: f"filed late ({int(r['lag_days'])}d)"),
                   ("f_proven_member", lambda: f"member's past buys beat the market consistently ({int(r['proven_n'])} trades)"),
                   ("f_small_cap", lambda: "smaller company"),
                   ("f_ex_member_lobbyist", lambda: "a former member of Congress lobbies for the company"),
                   ("f_defense_power", lambda: "defense company, and the member sits on a defense committee"),
                   ("f_dod_award_after_trade", lambda: "Defense Department award announced after the trade"),
                   ("f_dod_momentum", lambda: f"defense awards rising (${r['dod_awards_180d']/1e6:,.0f}M in 6 months)"),
                   ("f_grant_after_trade", lambda: "federal grant or loan to the company after the trade"),
                   ("f_grants", lambda: f"company got federal grants or loans (${r['grants_12m']/1e6:,.0f}M in 12 months)"),
                   ("f_campaign_vendor", lambda: f"their campaign paid the company (${r['vendor_paid']:,.0f})"),
                   ("f_spouse_employer", lambda: "spouse works for or is paid by the company (yearly disclosure)"),
                   ("f_paid_travel", lambda: "company paid for their travel (yearly disclosure)"),
                   ("f_outside_position", lambda: "holds a position with the company (yearly disclosure)"),
                   ("f_leader", lambda: "this member usually trades before their trading partners"),
                   ("f_sector_bill_momentum", lambda: f"a {r['sector']} bill passed recently"),
                   ("f_crowded", lambda: "but the stock already jumped on disclosure day")):
        if r.get(f, 0) and r.get(f, 0) >= 0.5:
            b.append(txt())
    for k in ("street_note", "spouse_note", "relative_note", "volume_note", "event_note", "buddy_note", "bill_adv_note"):
        if r.get(k):
            b.append(r[k])
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
    if "religion" in rows:
        labels = [f"{m} ({r})" if isinstance(r, str) and r else m for m, r in zip(rows["member"], rows["religion"])]
        mem = pd.Series(labels, index=rows.index).groupby(rows["ticker"]).agg(lambda s: ", ".join(sorted(set(s))))
    else:
        mem = rows.groupby("ticker")["member"].agg(lambda s: ", ".join(sorted(set(s))))
    best["members"] = best["ticker"].map(mem)
    # one entry per member for the dashboard cards: name, faith label and congress ID (for the official photo)
    ppl = {}
    for t, m, r, b in zip(rows["ticker"], rows["member"],
                          rows["religion"] if "religion" in rows else [None] * len(rows),
                          rows["bioguide"] if "bioguide" in rows else [None] * len(rows)):
        lst = ppl.setdefault(t, [])
        if all(p["n"] != m for p in lst):
            lst.append({"n": str(m), "r": r if isinstance(r, str) and r else None,
                        "b": b if isinstance(b, str) and b else None})
    best["people"] = best["ticker"].map(lambda t: ppl.get(t, [])[:6])
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


def _portfolio_slots(picks, px, idx, slots, idle="spy", sign=1, cost=0.0):
    """Each pick gets 1/slots of the money; money not in a pick sits in SPY (or cash). Trading costs are
    charged when a pick is bought and sold."""
    rets = _px_ffill(px).pct_change(fill_method=None).reindex(idx)
    spy = rets["SPY"].fillna(0).values
    col = {c: i for i, c in enumerate(px.columns)}
    R = rets.values
    acc, cnt = np.zeros(len(idx)), np.zeros(len(idx))
    for p in picks.itertuples():
        if p.ticker not in col or pd.isna(p.entry_date):
            continue
        a = idx.searchsorted(p.entry_date, side="right")
        b = idx.searchsorted(p.exit_date, side="right")
        k = float(getattr(p, "size_mult", 1.0) or 1.0)   # bigger or smaller position by confidence (1 = standard)
        acc[a:b] += k * sign * np.nan_to_num(R[a:b, col[p.ticker]])
        cnt[a:b] += k
        if cost and b > a:
            acc[a] -= k * cost
            acc[b - 1] -= k * cost
    over = cnt > slots                      # more money wanted than there is: scale every pick down
    w = np.where(over, 1 / np.maximum(cnt, 1), 1 / slots)
    invested = np.minimum(cnt, slots) / slots
    fill = spy if idle == "spy" else 0.0
    return pd.Series(acc * w + (1 - invested) * fill, index=idx), pd.Series(cnt, index=idx)


def _portfolio(picks, px, idx, sign=1):
    rets = _px_ffill(px).pct_change(fill_method=None).reindex(idx)
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


def _select(rows, score_col, pct, per_week, blocked=None, short=False, per_member=None, skip_late=False,
            net_sell=False, start=None):
    """Picks as filings arrive: each day's filings must clear the percentile of all EARLIER scores, best first,
    no re-buying an open ticker, at most `per_week` picks per week (used up day by day, first come first served,
    so a Monday pick never depends on what gets filed later that week), at most `per_member` per member each
    week, and (optionally) no trades filed past the 45-day deadline."""
    rows = rows.sort_values("filed_date")
    rows = rows.assign(week=rows["filed_date"].dt.to_period("W-FRI"), day=rows["filed_date"].dt.normalize())
    hist = np.array([])
    picks, held = [], {}
    blocked = blocked or {}
    taken, by_member, cur_wk = 0, {}, None
    for (wk, day), g in rows.groupby(["week", "day"], sort=True):
        if wk != cur_wk:
            taken, by_member, cur_wk = 0, {}, wk
        bar = np.percentile(hist, pct) if len(hist) >= 100 else np.inf
        prev = np.sort(hist)
        hist = np.concatenate([hist, g[score_col].values])
        if taken >= per_week or (start is not None and day < start):     # earlier rows only set the bar
            continue
        cand = ticker_level(g, score_col, short)
        cand = cand[cand[score_col] >= bar]
        if skip_late and "lag_days" in cand:
            cand = cand[cand["lag_days"] <= 45]
        if net_sell and not short:
            cand = cand[cand["n_sellers"] <= cand["n_buyers"]]
        for _, r in cand.iterrows():
            if taken >= per_week:
                break
            mk = r["who"] if "who" in r and isinstance(r["who"], str) else r["member"]
            if per_member and by_member.get(mk, 0) >= per_member:
                continue
            t = r["ticker"]
            if (held.get(t) is not None and r["entry_date"] <= held[t]) or \
               (blocked.get(t) is not None and r["entry_date"] <= blocked[t]):
                continue
            r = r.copy()
            r["bar"] = bar
            r["prank"] = float(np.searchsorted(prev, r[score_col], side="left") / max(len(prev), 1) * 100)
            picks.append(r)
            held[t] = r["exit_date"]
            taken += 1
            by_member[mk] = by_member.get(mk, 0) + 1
    return pd.DataFrame(picks).reset_index(drop=True), held


def member_weights(rows, cap=100):
    """How much each trade counts when learning which signals work. A member's first `cap` trades count fully;
    past that each trade counts sqrt(cap / their trade count), so a member with 1,100 trades counts about as
    much as 330 trades instead of 1,100. Busy members still count the most, just not overwhelmingly.
    No trade is dropped, and picks and the watchlist are unaffected."""
    if not cap or "member" not in rows or not len(rows):
        return np.ones(len(rows))
    k = "who" if "who" in rows else "member"
    n = rows.groupby(k)[k].transform("size").values.astype(float)
    return np.minimum(1.0, np.sqrt(cap / n))


def _attribution(rows, factors, sign, w=None):
    out = []
    y = sign * rows["excess"]
    w = pd.Series(np.ones(len(rows)) if w is None else w, index=rows.index)
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
            m = (grp == gname) & y.notna().values
            if m.sum():
                ww = w[m]
                out.append({"Signal": label, "Group": gname, "Trades": int(m.sum()),
                            "Avg result vs SPY": float(np.average(y[m], weights=ww)),
                            "Worked (%)": float(np.average(y[m] > 0, weights=ww)) * 100})
    return pd.DataFrame(out)


def busy_check(rows, factors, cfg, top=3):
    """Does each signal still work without the most active traders? Compares the signal's effect
    (yes/high group minus no/low group) with and without the `top` busiest members."""
    if not len(rows):
        return pd.DataFrame(), []
    mk = "who" if "who" in rows else "member"
    busiest = list(rows[mk].value_counts().head(top).index)
    cap = cfg.get("BUSY_TRADER_SOFTCAP", 100)
    rest = rows[~rows[mk].isin(busiest)]
    a_all = _attribution(rows, factors, 1, member_weights(rows, cap))
    a_rest = _attribution(rest, factors, 1, member_weights(rest, cap))

    def effects(a):
        out = {}
        for sig, g in a.groupby("Signal"):
            g = g.set_index("Group")
            hi = "yes" if "yes" in g.index else ("high" if "high" in g.index else None)
            lo = "no" if "no" in g.index else ("low" if "low" in g.index else None)
            if hi and lo and g.loc[hi, "Trades"] >= 30 and g.loc[lo, "Trades"] >= 30:
                out[sig] = (g.loc[hi, "Avg result vs SPY"] - g.loc[lo, "Avg result vs SPY"], int(g.loc[hi, "Trades"]))
        return out
    e_all, e_rest = effects(a_all), effects(a_rest)
    out = []
    for sig, (ea, n) in e_all.items():
        er = e_rest.get(sig, (np.nan, 0))[0]
        if np.isnan(er):
            verdict = "Not enough trades from others"
        elif abs(ea) < 0.002:
            verdict = "No real effect either way"
        elif np.sign(er) != np.sign(ea):
            verdict = "Flips: mostly the busiest traders"
        elif abs(er) >= 0.5 * abs(ea):
            verdict = "Holds up"
        else:
            verdict = "Weaker without them"
        out.append({"Signal": sig, "Trades with signal": n, "Effect (everyone)": ea,
                    "Effect without busiest 3": er, "Verdict": verdict})
    shown = [str(rows.loc[rows[mk] == b_, "member"].iloc[0]) for b_ in busiest]      # names, not IDs, for the dashboard
    return pd.DataFrame(out).sort_values("Effect (everyone)", ascending=False), shown


BUY_FACTORS = [("f_track_record", "Member track record"), ("f_cluster", "Cluster buying"),
               ("f_committee", "Committee relevance"), ("f_subcommittee", "Subcommittee relevance"),
               ("f_committee_leader", "Chairs or leads that committee"), ("f_insider_buying", "Company insiders buying"),
               ("f_insider_selling", "Company insiders selling"), ("f_contracts", "Federal contracts"),
               ("f_lobbying", "Lobbying link"), ("f_donations", "PAC donations to member"),
               ("f_bills", "Member's bills in sector"), ("f_size", "Trade size"), ("f_freshness", "Freshness"),
               ("f_fast_filer", "Member files fast"), ("f_sell_pressure", "Others selling"),
               ("f_momentum", "Prior 3m momentum"), ("f_employee_donations", "Employee donations to member"), ("f_already_owned", "Already held it"),
               ("f_disclosure_tie", "Company in yearly disclosure"), ("f_revolving_door", "Lobbyists are ex-staff"),
               ("f_grants", "Federal grants or loans"), ("f_grant_after_trade", "Grant or loan after the trade"),
               ("f_campaign_vendor", "Campaign paid the company"), ("f_spouse_employer", "Spouse works for the company"),
               ("f_paid_travel", "Company paid for travel"), ("f_outside_position", "Holds a position at the company"),
               ("f_buddy", "Trading partner also bought"), ("f_leader", "Member usually trades first"),
               ("f_bill_advanced", "Their bill advanced after the trade"), ("f_sector_bill_momentum", "Industry bill just passed"),
               ("f_spouse_insider", "Spouse is a company insider (SEC)"),
               ("f_relative_tie", "Relative is a company insider (SEC)"), ("f_unusual_volume", "Unusual trading volume before the trade"),
               ("f_reg_action", "Federal rule named the company after the trade"), ("f_witness_after", "Company testified to their committee after the trade"),
               ("f_markup_after", "Committee marked up an industry bill after the trade"), ("f_major_8k_after", "Major company announcement after the trade"),
               ("f_against_street", "Bought against Wall Street"), ("f_after_downgrade", "Bought after a downgrade"),
               ("f_no_coverage", "No analyst coverage"),
               ("f_testified", "Company testified to their committee"), ("f_closed_briefing", "Closed briefing before trade"),
               ("f_vote_sector", "Voted on industry bill near trade"), ("f_home_state", "Company based in their state"),
               ("f_contract_in_state", "Contracts in their state"), ("f_speech", "Named company in floor speech"),
               ("f_social_post", "Posted about company"), ("f_pre_event", "Traded before company event"),
               ("f_committee_cluster", "Committee colleague same trade"), ("f_option", "Options trade"),
               ("f_spouse", "Spouse or family account"), ("f_first_time", "First trade in this stock"),
               ("f_unusual_size", "Unusually large for them"), ("f_new_sector", "New industry for them"),
               ("f_late", "Filed late"), ("f_small_cap", "Smaller company"), ("f_proven_member", "Proven member (past record)"),
               ("f_crowded", "Jumped on disclosure day"), ("f_ex_member_lobbyist", "Former member lobbies for company"),
               ("f_defense_company", "Defense company"), ("f_defense_power", "Defense company + defense committee"),
               ("f_dod_award_after_trade", "Defense award announced after trade"), ("f_dod_momentum", "Rising defense awards"),
               ("score", "Overall buy score")]
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
                 ("f_late", "Filed late"), ("f_small_cap", "Smaller company"), ("f_proven_seller", "Proven seller (past record)"),
                 ("f_defense_power", "Defense company + defense committee"), ("short_score", "Overall short score")]


def _trade_stats(rows, sign):
    c = rows[rows["closed"] & rows["excess"].notna() & rows["ret"].notna()]
    # costs hit a short too: a short's net result is the opposite of the gross move minus the round trip
    adj = 2 * _COST_RT[0] if sign < 0 else 0.0
    c = c.assign(ret=c["ret"] + adj, excess=c["excess"] + adj)      # sign*(ret+4c) = -gross - 2c for a short
    y = sign * c["excess"]
    return {"Trades": len(rows), "Closed trades": len(c),
            "Made money (%)": ((sign * c["ret"]) > 0).mean() * 100 if len(c) else np.nan,
            "Beat SPY (%)": (y > 0).mean() * 100 if len(c) else np.nan,
            "Avg return per trade (%)": (sign * c["ret"]).mean() * 100 if len(c) else np.nan,
            "Avg vs SPY per trade (%)": y.mean() * 100 if len(c) else np.nan,
            "Median vs SPY (%)": y.median() * 100 if len(c) else np.nan,
            "Avg vs its industry (%)": (sign * c["excess_sector"]).mean() * 100 if len(c) and "excess_sector" in c else np.nan,
            "Range low (%)": (y.mean() - 1.96 * _cluster_se(y.values, c["filed_date"].values)) * 100 if len(c) > 1 else np.nan,
            "Range high (%)": (y.mean() + 1.96 * _cluster_se(y.values, c["filed_date"].values)) * 100 if len(c) > 1 else np.nan}


# Position sizing by confidence: a pick's size grows with how far its score sits above the buy bar
# (its percentile among all earlier buy scores). "equal" = every pick the same size.
SIZING = {"equal": (1.0, 1.0), "moderate": (0.75, 1.25), "strong": (0.5, 1.5)}
SIZING_LABEL = "Position size by confidence"


def size_mult(percentile, cfg, scheme):
    lo, hi = SIZING.get(scheme or "equal", (1.0, 1.0))
    bar = float(cfg.get("PICK_PERCENTILE", 80))
    z = np.clip((np.asarray(percentile, dtype=float) - bar) / max(100 - bar, 1e-9), 0, 1)
    return np.where(np.isfinite(z), lo + (hi - lo) * z, 1.0)


def run_backtest(scored, px, cfg, since=None):
    all_buys = scored[(scored["tx_type"] == "buy") & scored["entry_px"].notna()]
    all_sells = scored[(scored["tx_type"] == "sell") & scored["entry_px"].notna()]
    longs, held = _select(all_buys, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"],
                          per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), skip_late=cfg.get("SKIP_LATE_FILINGS"),
                          net_sell=cfg.get("NET_SELL_FILTER"))
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
    eff_hold = float(longs["hold_days_row"].mean()) if len(longs) and "hold_days_row" in longs else cfg["HOLD_DAYS"]
    slots_l = max(1, int(cfg["PICKS_PER_WEEK"] * eff_hold / 5))
    slots_s = max(1, int(cfg["SHORTS_PER_WEEK"] * cfg["HOLD_DAYS"] / 5))
    empty = pd.Series(0.0, index=idx)
    cst = cfg.get("COST_BPS", 0) / 1e4
    scheme = cfg.get("_sizing") or load_adaptive(cfg)["policy"].get("sizing", "equal")
    if len(longs) and "prank" in longs and scheme != "equal":
        longs = longs.assign(size_mult=size_mult(longs["prank"], cfg, scheme))
    ld, n_long = _portfolio_slots(longs, px, idx, slots_l, "spy", cost=cst) if len(longs) else (px["SPY"].pct_change(fill_method=None).reindex(idx), empty)
    sd, _ = _portfolio_slots(shorts, px, idx, slots_s, "cash", sign=-1, cost=cst) if len(shorts) else (empty, empty)
    a = cfg["SHORT_ALLOCATION"]
    series = {"Long picks": ld, "Short picks": sd,
              **({f"Long {100-a*100:.0f}% / short {a*100:.0f}%": (1 - a) * ld + a * sd} if cfg.get("ENABLE_SHORTS") else {}),
              "Copy every purchase": _portfolio(buys.drop_duplicates(["ticker", "filed_date"]), px, idx),
              "S&P 500 (SPY)": px["SPY"].pct_change(fill_method=None).reindex(idx)}
    perf, curves = {}, {}
    spy_d = series["S&P 500 (SPY)"]
    for k, d in series.items():
        perf[k], curves[k] = _perf(d)
        if k != "S&P 500 (SPY)":
            ann, lo_, hi_ = excess_range(d.fillna(0), spy_d.fillna(0))
            perf[k].update({"Per year vs S&P 500": ann, "Likely range low": lo_, "Likely range high": hi_})

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
                    ("f_option", "Options trades"), ("f_spouse", "Spouse or family"),
                    ("f_grants", "Company got grants or loans"), ("f_campaign_vendor", "Campaign paid the company"),
                    ("f_spouse_employer", "Spouse works for the company"), ("f_paid_travel", "Company-paid travel"),
                    ("f_outside_position", "Position at the company"), ("f_buddy", "Trading partner also bought"),
                    ("f_bill_advanced", "Bill advanced after the trade"),
                    ("f_spouse_insider", "Spouse is a company insider"),
                    ("f_relative_tie", "Relative is a company insider"), ("f_unusual_volume", "Unusual volume before the trade"),
                    ("f_reg_action", "Federal rule after the trade"), ("f_witness_after", "Testimony after the trade"),
                    ("f_markup_after", "Industry markup after the trade"), ("f_major_8k_after", "Major 8-K after the trade"),
                    ("f_against_street", "Bought against Wall Street"), ("f_after_downgrade", "Bought after a downgrade"),
                    ("f_no_coverage", "No analyst coverage")) if f in scored}}
    extras = {}
    if not cfg.get("_nested") and since is None:
        pt, pv = persistence_table(scored)
        extras = {"horizons": horizons_table(scored, px, cfg), "persistence": pt, "persistence_verdict": pv,
                  "crowding": crowding_table(scored), "sizes": size_table(scored),
                  "defense": defense_backtest(scored, px, cfg) if "is_defense" in scored else None,
                  "small": small_cap_backtest(scored, px, cfg)}
        bc, busiest = busy_check(buys[buys["closed"]], BUY_FACTORS, cfg)
        extras.update({"busy_check": bc, "busiest": busiest})
        try:
            if not out_of_time(cfg, 60):
                extras["walk_forward"] = walk_forward(scored, px, cfg)
        except Exception as e:
            log(f"Never-seen-years test skipped ({type(e).__name__}: {e})")
        try:
            hz = extras["horizons"]
            extras["missing"] = missing_price_scenarios(_TX_ALL, px, cfg, hz.to_dict("records") if hz is not None else None) \
                if _TX_ALL is not None else None
        except Exception as e:
            log(f"Missing prices: scenarios skipped ({type(e).__name__})")
    return {**extras, "longs": fmt_trades(longs, False), "shorts": fmt_trades(shorts, True), "perf": pd.DataFrame(perf),
            "curves": pd.DataFrame(curves), "trade_stats": trade_stats,
            "buy_factors": _attribution(cb, BUY_FACTORS, 1, member_weights(cb, cfg.get("BUSY_TRADER_SOFTCAP", 100))),
            "short_factors": _attribution(cs, SHORT_FACTORS, -1, member_weights(cs, cfg.get("BUSY_TRADER_SOFTCAP", 100))),
            "lag": pd.DataFrame(lag), "stability": stab, "coverage": coverage, "since": since, "cfg": cfg,
            "avg_open": float(n_long[n_long > 0].mean()) if len(n_long) and (n_long > 0).any() else 0.0,
            "slots": slots_l}


# ============================================================================
# Weight tuning: learn on early years, test on later years
# ============================================================================
def _fit(rows, names, y, defaults, w=None):
    """Ridge regression of the trade's result on the signals, each trade weighted by member_weights.
    Signals with too little data keep their default."""
    X = rows[[f"f_{n}" for n in names]].values.astype(float)
    w = np.ones(len(X)) if w is None else np.asarray(w, float)
    mode = np.array([pd.Series(X[:, j]).mode().iloc[0] for j in range(X.shape[1])])
    usable = ((X != mode).sum(0) >= 30)
    out = {}
    if usable.sum():
        Xu = X[:, usable]
        mu = np.average(Xu, axis=0, weights=w)
        sd = np.sqrt(np.average((Xu - mu) ** 2, axis=0, weights=w))
        sd[sd == 0] = 1
        Xs = (Xu - mu) / sd
        lam = 0.5 * w.sum()
        yc = y - np.average(y, weights=w)
        coef = np.linalg.solve((Xs * w[:, None]).T @ Xs + lam * np.eye(Xs.shape[1]), (Xs * w[:, None]).T @ yc) / sd
        coef = coef / max(np.abs(coef).max(), 1e-12)
        out = dict(zip([n for n, u in zip(names, usable) if u], coef))
    return {n: round(float(out[n]), 3) if n in out else defaults[n] for n in names}


def tune_weights(scored, px, cfg):
    split = pd.Timestamp(cfg["TRAIN_TEST_SPLIT"])
    tr = scored[scored["closed"] & scored["excess"].notna() & (scored["exit_date"] < split)]
    trb, trs = tr[tr["tx_type"] == "buy"], tr[tr["tx_type"] == "sell"]
    if len(trb) < 200:
        raise RuntimeError(f"Only {len(trb)} completed purchases before {split.date()}; move TRAIN_TEST_SPLIT later.")
    cap = cfg.get("BUSY_TRADER_SOFTCAP", 100)
    bw = _fit(trb, list(cfg["BUY_WEIGHTS"]), trb["excess"].clip(-0.5, 1).values, cfg["BUY_WEIGHTS"], member_weights(trb, cap))
    sw = (_fit(trs, list(cfg["SHORT_WEIGHTS"]), (-trs["excess"]).clip(-1, 0.5).values, cfg["SHORT_WEIGHTS"],
               member_weights(trs, cap))
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


def walk_forward(scored, px, cfg, min_train=500):
    """The honest version of the main strategy: each year is picked using only what was known before it.
    For every year, signal weights are learned from trades that had finished before January 1 (signals with
    too little data get no weight, not a hand-picked one), the holding period is the one that worked best on
    those earlier trades, and the bar a pick must clear comes from earlier scores. The years are then strung
    together into one portfolio. Nothing about a year is used to pick trades in that same year."""
    cost = cfg.get("COST_BPS", 0) / 1e4
    buys_all = scored[(scored["tx_type"] == "buy") & scored["entry_px"].notna()]
    if not len(buys_all):
        return None
    names = list(cfg["BUY_WEIGHTS"])
    zeros = {n: 0.0 for n in names}
    by_hold = {h: apply_hold_policy(buys_all, px, cfg, {"hold_small": h, "hold_other": h}) for h in HOLD_CHOICES}
    base = by_hold[60] if 60 in by_hold else next(iter(by_hold.values()))
    years = sorted(base["filed_date"].dt.year.unique())
    picks, log_rows, held = [], [], {}
    for y in years:
        start = pd.Timestamp(y, 1, 1)
        tr = base[base["closed"] & base["excess"].notna() & (base["exit_date"] < start)]
        if len(tr) < min_train:
            continue
        w = _fit(tr, names, tr["excess"].clip(-0.5, 1).values, zeros, member_weights(tr, cfg.get("BUSY_TRADER_SOFTCAP", 100)))
        # holding period: best average result of the top-scored earlier trades that had finished by January 1
        best_h, best_v = 60, -np.inf
        for h, dfh in by_hold.items():
            t2 = dfh[dfh["closed"] & dfh["excess"].notna() & (dfh["exit_date"] < start)]
            if len(t2) < min_train:
                continue
            sc = _score(t2, w)
            top = t2[sc >= np.percentile(sc, cfg["PICK_PERCENTILE"])]
            if len(top) >= 30 and top["excess"].mean() > best_v:
                best_h, best_v = h, top["excess"].mean()
        dfh = by_hold[best_h]
        # score this year's filings (plus earlier ones, only so the percentile bar has history) with this year's weights
        rows = dfh[(dfh["filed_date"] >= start - pd.Timedelta(days=3 * 365)) & (dfh["filed_date"] < pd.Timestamp(y + 1, 1, 1))].copy()
        rows["score"] = _score(rows, w)
        pk, held_y = _select(rows, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"], blocked=dict(held),
                             per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), skip_late=cfg.get("SKIP_LATE_FILINGS"),
                             net_sell=cfg.get("NET_SELL_FILTER"), start=start)
        held.update(held_y)                          # a stock still held from last year isn't bought again
        if len(pk):
            pk = pk.assign(hold_days_row=best_h)
            picks.append(pk)
        top_w = sorted(w.items(), key=lambda kv: -abs(kv[1]))[:5]
        log_rows.append({"Year": int(y), "Learned from trades": int(len(tr)), "Holding period": f"{best_h} days",
                         "Picks": int(len(pk)), "Main signals": ", ".join(f"{k} {v:+.2f}" for k, v in top_w)})
    # today's version: learned from every trade that has finished by now, for live picks if the weekly check chooses it
    try:
        now = pd.Timestamp.today().normalize()
        tr = base[base["closed"] & base["excess"].notna() & (base["exit_date"] < now)]
        w_now = _fit(tr, names, tr["excess"].clip(-0.5, 1).values, zeros, member_weights(tr, cfg.get("BUSY_TRADER_SOFTCAP", 100)))
        h_now, v_now = 60, -np.inf
        for h, dfh in by_hold.items():
            t2 = dfh[dfh["closed"] & dfh["excess"].notna() & (dfh["exit_date"] < now)]
            sc = _score(t2, w_now)
            top = t2[sc >= np.percentile(sc, cfg["PICK_PERCENTILE"])] if len(t2) else t2
            if len(top) >= 30 and top["excess"].mean() > v_now:
                h_now, v_now = h, top["excess"].mean()
        json.dump({"buy": w_now, "hold": int(h_now), "trained_on": int(len(tr)), "made": dt.datetime.now().isoformat()},
                  open(_p(cfg, "state", "learned_weights.json"), "w"), indent=1)
    except Exception as e:
        log(f"Learned weights: not saved ({type(e).__name__})")
    if not picks:
        return None
    longs = pd.concat(picks, ignore_index=True)
    idx = px.index[px.index >= longs["entry_date"].min()]
    # each year sizes its picks for its own holding period (a 20-day year holds ~20 positions, a 250-day
    # year ~250), like the live tool would: weight per pick = 1 / that year's position count
    per = float(cfg["PICKS_PER_WEEK"])
    slots = max(1, int(per * 60 / 5))
    slots_y = (per * longs["hold_days_row"].astype(float) / 5).clip(lower=1)
    base_k = longs["size_mult"].astype(float).fillna(1.0) if "size_mult" in longs else 1.0
    longs = longs.assign(size_mult=base_k * slots / slots_y)
    ld, _ = _portfolio_slots(longs, px, idx, slots, "spy", cost=cost)
    spy = px["SPY"].pct_change(fill_method=None).reindex(idx)
    # the regular (full-history) strategy over exactly the same years, for comparison
    # always with the hand-set weights, so the weekly check compares the two methods whichever one is in use
    hand = apply_scores(scored, cfg, cfg["BUY_WEIGHTS"], cfg["SHORT_WEIGHTS"])
    reg = run_backtest(hand, px, dict(cfg, _nested=True), since=longs["filed_date"].min())
    series = {"Never-seen-years picks": ld, "Regular backtest, same years": _long_daily(reg).reindex(idx).fillna(0),
              "S&P 500 (SPY)": spy}
    perf, curves = {}, {}
    for k, d in series.items():
        perf[k], curves[k] = _perf(d)
        if k != "S&P 500 (SPY)":
            ann, lo_, hi_ = excess_range(d.fillna(0), spy.fillna(0))
            perf[k].update({"Per year vs S&P 500": ann, "Likely range low": lo_, "Likely range high": hi_})
    df = pd.DataFrame(series).fillna(0)
    yrs = df.groupby(df.index.year).apply(lambda g: (1 + g).prod() - 1)
    return {"perf": pd.DataFrame(perf), "curves": pd.DataFrame(curves), "trade_stats": _trade_stats(longs, 1),
            "years": yrs, "log": pd.DataFrame(log_rows), "n_picks": int(len(longs)), "slots": slots}


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
        if "trade_date" in df:
            df["price_at_trade"] = [px[t].asof(d) if t in px.columns and pd.notna(d) else np.nan
                                    for t, d in zip(df["ticker"], df["trade_date"])]
            df["move_since_trade"] = df["price_now"] / df["price_at_trade"] - 1
        return df

    buys = ticker_level(recent[recent["tx_type"] == "buy"], "score")
    if len(buys):
        buys = prices(buys.sort_values("score", ascending=False).reset_index(drop=True))
        buys["percentile"] = [pct(v, hist_b) for v in buys["score"]]
        buys["action"] = np.where(buys["percentile"] >= cfg["PICK_PERCENTILE"], "BUY", "watch")
        buys["size_mult"] = size_mult(buys["percentile"], cfg, load_adaptive(cfg)["policy"].get("sizing", "equal"))
        if cfg.get("SKIP_LATE_FILINGS"):
            late = buys["lag_days"] > 45
            buys.loc[late & (buys["action"] == "BUY"), "action"] = "watch"
            buys.loc[late, "why"] = buys.loc[late, "why"] + "; filed after the 45-day deadline, so not a buy"
        cap = cfg.get("MAX_WATCHLIST_BUYS_PER_MEMBER")
        if cap:
            n = buys[buys["action"] == "BUY"].groupby("who" if "who" in buys else "member").cumcount()
            over = n[n >= cap].index
            buys.loc[over, "action"] = "watch"
            buys.loc[over, "why"] = buys.loc[over, "why"] + f"; this member already has {cap} buy picks"
        if cfg.get("NET_SELL_FILTER"):
            ns_ = (buys["n_sellers"] > buys["n_buyers"]) & (buys["action"] == "BUY")
            buys.loc[ns_, "action"] = "mixed"
            buys.loc[ns_, "why"] = buys.loc[ns_, "why"] + "; more members selling than buying, so not a buy"
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
def bad_price_tickers(px, meta):
    """Price series that are clearly data errors, not real moves. Left in, a single one can fake a big result
    (a large foreign company's OTC ticker once showed a 17x gain in a year). Removing a real stock is the
    safe mistake here: it can only make results look worse, never better.
      - a one-day jump or drop of 3x or more that is mostly undone within 20 trading days
      - a one-day 3x jump in a foreign over-the-counter ticker (5 letters ending in F or Y)
      - a company worth $10B+ today whose price rose more than 10x within a year"""
    bad = {}
    skip = set(BENCHMARK_ETFS)
    for t in px.columns:
        if t in skip:
            continue
        s = px[t].dropna()
        s = s[s > 0]
        if len(s) < 30:
            continue
        lr = np.log(s.values)
        d = np.diff(lr)
        big = np.where(np.abs(d) >= np.log(3))[0]
        for i in big:
            after = lr[i + 1:i + 22]                        # the new level and the 20 days after it
            undone = (after[0] - after.min()) if d[i] > 0 else (after.max() - after[0])
            if undone >= 0.5 * abs(d[i]):
                bad[t] = "spike that reversed"
                break
            if d[i] > 0 and len(t) == 5 and t[-1] in "FY":
                bad[t] = "jump in a foreign OTC ticker"
                break
        if t in bad:
            continue
        cap = (meta.get(t) or {}).get("cap") or 0
        if cap >= 1e10 and len(lr) > 250 and (lr[250:] - lr[:-250]).max() > np.log(10):
            bad[t] = "large company up 10x+ within a year"
    return bad


_TX_ALL = None


def _jsonable(x):
    """Plain JSON types for the dashboard (numpy numbers become floats)."""
    return json.loads(json.dumps(x, default=lambda v: float(v) if hasattr(v, "__float__") else str(v))) if x is not None else None


def prepare(cfg):
    global _RUN_START
    tx = collect_all(cfg)
    renames = ticker_renames(cfg) if cfg.get("USE_INSIDERS", True) else {}
    try:
        dl, _ = _cache_json(cfg, "delistings.json", {})
        renames.update({t: v["new"] for t, v in dl.items() if v.get("status") == "renamed" and v.get("new") and t not in renames})
    except Exception:
        pass
    if renames:
        changed = tx["ticker"].isin(renames)
        tx.loc[changed, "ticker"] = tx.loc[changed, "ticker"].map(renames)
        log(f"Ticker changes: moved {int(changed.sum()):,} trades to their company's current ticker")
    px = load_prices(cfg, list(tx["ticker"].unique()) + BENCHMARK_ETFS,
                     priority=tx["ticker"].value_counts().to_dict())
    cov = price_coverage(tx, px)
    json.dump(cov, open(_p(cfg, "state", "price_coverage.json"), "w"))
    log(f"Prices: {cov['with_prices']:,} of {cov['trades']:,} trades have price data; {cov['missing']:,} "
        f"({cov['missing_share']*100:.1f}%) are missing, mostly delisted companies")
    try:
        miss_t = [t for t, _ in tx.loc[~tx["ticker"].isin(px.columns), "ticker"].value_counts().items()]
        cl = classify_missing(cfg, miss_t)
        cnt = pd.Series([(cl.get(t) or {}).get("status", "unknown") for t in miss_t]).value_counts().to_dict()
        log(f"Missing prices: why {len(miss_t)} stocks vanished: " + ", ".join(f"{v} {k}" for k, v in cnt.items()))
        global _TX_ALL
        _TX_ALL = tx[["ticker", "tx_type"]].copy()
    except Exception as e:
        log(f"Missing prices: classification skipped ({type(e).__name__}: {e})")
    tx = tx[tx["ticker"].isin(px.columns)].reset_index(drop=True)
    if tx.empty:
        raise RuntimeError("No trades have price data yet (price download was cut short); the next run continues.")
    meta = load_meta(cfg, tx["ticker"].unique())
    bad = bad_price_tickers(px, meta)
    if bad:
        json.dump(bad, open(_p(cfg, "state", "bad_prices.json"), "w"), indent=1)
        px = px.drop(columns=list(bad))
        tx = tx[~tx["ticker"].isin(bad)].reset_index(drop=True)
        log(f"Prices: set aside {len(bad)} stock(s) whose price history has clear data errors")
    committees = load_committees(cfg)
    history = load_committee_history(cfg, committees)
    data = {"meta": meta, "committees": committees, "committee_history": history, "religion": load_religion(cfg)}
    try:
        data["countries"] = json.load(open(_p(cfg, "cache", "ticker_country.json")))   # filled by the daily run
    except Exception:
        data["countries"] = {}
    try:
        data["legislators"] = load_legislators(cfg)
    except Exception as e:
        log(f"Member IDs: skipped ({type(e).__name__}: {e})")
    enrich = _enrich_tickers(tx, cfg)
    step = lambda flag: cfg.get(flag, True) and not out_of_time(cfg)
    if step("USE_VOLUME"):
        try:
            data["volume"] = load_volume(cfg, list(tx["ticker"].unique()), priority=tx["ticker"].value_counts().to_dict())
        except Exception as e:
            log(f"Volume: skipped ({type(e).__name__}: {e})")
    if step("USE_INSIDERS"):
        ins = collect_insiders(cfg)
        if renames and len(ins):
            ins = ins.copy()
            ins["ticker"] = ins["ticker"].replace(renames)
        recent = tx.loc[tx["filed_date"] >= pd.Timestamp.today() - pd.Timedelta(days=60), "ticker"].value_counts()
        live = recent_form4(cfg, list(recent.index[:150]))
        data["insiders"] = pd.concat([ins, live], ignore_index=True).drop_duplicates(
            ["ticker", "filed", "owner", "code"]) if len(live) else ins
    if step("USE_CONTRACTS"):
        defense = [t for t in tx["ticker"].unique() if is_defense(t, meta)]
        data["contracts"] = collect_contracts(cfg, meta, list(dict.fromkeys(defense + enrich)))
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
        data["annual_fd"] = collect_annual_disclosures(cfg, set(tx["last_key"]))
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
    if step("USE_FEDREG"):
        try:
            data["fedreg"] = collect_fedreg(cfg, meta, enrich)
        except Exception as e:
            log(f"Federal rules: skipped ({type(e).__name__}: {e})")
    if step("USE_8K"):
        try:
            data["k8"] = collect_8k(cfg, data.get("sec_companies") or load_sec_companies(cfg, enrich), enrich)
        except Exception as e:
            log(f"Company 8-K filings: skipped ({type(e).__name__}: {e})")
    if step("USE_HEARINGS") and data.get("meetings") is not None and len(data["meetings"]) and "bills" in data["meetings"]:
        try:      # the subject of every bill a committee marked up, so markups map to industries
            mb = [b for bl in data["meetings"].loc[data["meetings"]["type"].str.lower().str.contains("markup"), "bills"] for b in bl]
            data["bill_policy"] = {**(data.get("bill_policy") or {}), **collect_bill_policy(cfg, mb)}
        except Exception as e:
            log(f"Markups: bill subjects skipped ({e})")
    if step("USE_BILL_STATUS"):
        data["bill_actions"] = collect_bill_status(cfg)
    if step("USE_CAMPAIGN_SPENDING"):
        data["campaign_vendors"] = collect_campaign_vendors(cfg, fecs)
    if step("USE_ANALYST_HISTORY"):
        try:
            recent_first = list(tx.sort_values("filed_date", ascending=False)["ticker"].drop_duplicates())
            data["analyst_history"] = collect_analyst_history(cfg, recent_first)
        except Exception as e:
            log(f"Analyst ratings: skipped ({e})")
    if step("USE_SPOUSE_MATCHING"):
        try:
            members = {}
            for _, snap in (history or []) + [(None, committees)]:
                for ms in snap.values():
                    for m in ms:
                        if m.get("bioguide"):
                            members.setdefault(m["bioguide"], m)
            sp = {b: v for b, v in collect_spouses(cfg).items() if b in bios}
            data["spouse_ties"] = spouse_ties(sp, collect_spouse_insiders(cfg, sp), members)
            log(f"Spouse insiders: {len(data['spouse_ties'])} possible spouse-company ties")
        except Exception as e:
            log(f"Spouse insiders: skipped ({e})")
        try:      # public-figure relatives: SEC insider roles and lobbyist registrations
            rel = {b: v for b, v in collect_relatives(cfg).items() if b in bios}
            names = {b: [x[0] for x in v] for b, v in rel.items()}
            rt = spouse_ties(names, collect_spouse_insiders(cfg, names, prefix="relative"), members)
            if len(rt):
                kind = {(b, n): k for b, v in rel.items() for n, k in v}
                rt = rt.rename(columns={"spouse": "relative"})
                rt["relation"] = [kind.get((b, n), "relative") for b, n in zip(rt["bioguide"], rt["relative"])]
                rt["source"] = "SEC insider filings"
            lt = relative_lobbyist_ties(rel, data.get("lobbying"), members)
            data["relative_ties"] = pd.concat([x for x in (rt, lt) if len(x)], ignore_index=True) if (len(rt) or len(lt)) else pd.DataFrame()
            log(f"Relatives: {len(data['relative_ties'])} possible relative-company ties")
        except Exception as e:
            log(f"Relatives: skipped ({e})")
    if step("USE_ASSISTANCE"):
        data["assistance"] = collect_assistance(cfg, meta, list(dict.fromkeys(
            [t for t in tx["ticker"].unique() if is_defense(t, meta)] + enrich)))
    if out_of_time(cfg):
        log("Time limit reached: saved progress; the next run continues where this one stopped")
    log("Computing signals for every transaction...")
    feats = compute_features(tx, px, data, cfg)
    try:
        ties = []
        st_ = data.get("spouse_ties")
        if st_ is not None and len(st_):
            for r in st_.sort_values("first_filed").drop_duplicates(["bioguide", "ticker"]).to_dict("records"):
                ties.append({"Member": r["member"], "Spouse": r["spouse"], "Company": f"{r['issuer']} ({r['ticker']})",
                             "Role": r["role"], "Since": str(pd.Timestamp(r["first_filed"]).date()),
                             "Confidence": r["confidence"], "Source": "SEC insider filings",
                             "link": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={r['owner_cik']}&type=4&owner=include&count=40"})
        rt_ = data.get("relative_ties")
        if rt_ is not None and len(rt_):
            for r in rt_.sort_values("first_filed").drop_duplicates(["bioguide", "ticker", "relative"]).to_dict("records"):
                ties.append({"Member": r["member"], "Spouse": f"{r['relative']} ({r['relation']})",
                             "Company": f"{r['issuer']} ({r['ticker']})", "Role": r["role"],
                             "Since": str(pd.Timestamp(r["first_filed"]).date()) if pd.notna(r["first_filed"]) else "",
                             "Confidence": r["confidence"], "Source": r.get("source") or "SEC insider filings",
                             "link": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={r['owner_cik']}&type=4&owner=include&count=40"
                             if r.get("owner_cik") else ""})
        dj = feats[feats.get("f_spouse_employer", 0) > 0] if "f_spouse_employer" in feats else feats.iloc[0:0]
        for r in dj.drop_duplicates(["member", "ticker"]).to_dict("records"):
            ties.append({"Member": r["member"], "Spouse": "", "Company": f"{(data['meta'].get(r['ticker']) or {}).get('name') or r['ticker']} ({r['ticker']})",
                         "Role": "Spouse income source", "Since": "", "Confidence": "Stated by the member",
                         "Source": "Yearly financial disclosure", "link": ""})
        json.dump(ties, open(_p(cfg, "state", "spouse_ties.json"), "w"), default=str)
    except Exception as e:
        log(f"Spouse ties: table skipped ({e})")
    try:
        ev, names = data.get("_pairs") or (None, {})
        json.dump(pairs_table(ev, names), open(_p(cfg, "state", "trading_pairs.json"), "w"))
    except Exception as e:
        log(f"Trading partners: table skipped ({e})")
    scored = apply_scores(feats, cfg)
    pol = load_adaptive(cfg)["policy"]
    if (int(pol["hold_small"]) != int(cfg["HOLD_DAYS"]) or int(pol["hold_other"]) != int(cfg["HOLD_DAYS"])
            or pol.get("exit_on_member_sell") or pol.get("price_exit", "none") != "none"
            or pol.get("extend", "none") != "none"):
        scored = apply_hold_policy(scored, px, cfg, pol)
        log(f"Holding periods in use: {pol['hold_other']} trading days, {pol['hold_small']} for companies under $2B")
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


def _sig(v, n=5):
    """Round a price to about n significant digits (keeps the chart file small)."""
    if v is None or not np.isfinite(v):
        return None
    if v == 0:
        return 0.0
    return float(round(v, max(0, n - 1 - int(np.floor(np.log10(abs(v)))))))


NOTE_SHARES = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:sh(?:ares?|s)?\b|shrs?\b)", re.I)
NOTE_PRICE = re.compile(r"(?:@|\bat\b|price(?:\s+of)?|per\s+share\s+of)\s*:?\s*\$\s?([\d,]+(?:\.\d+)?)|\$\s?([\d,]+(?:\.\d+)?)\s*(?:per\s+share|/\s*sh(?:are)?|a\s+share|each)", re.I)


def parse_note(note):
    """Shares and price a filer wrote in a transaction's note, when they did ("Purchased 200 shares at $41.10")."""
    if not note:
        return None, None
    sh = pr = None
    m = NOTE_SHARES.search(note)
    if m:
        try:
            sh = float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = NOTE_PRICE.search(note)
    if m:
        try:
            pr = float((m.group(1) or m.group(2)).replace(",", ""))
        except ValueError:
            pass
    return (sh if sh and sh > 0 else None), (pr if pr and pr > 0 else None)


def trade_notes(cfg, trades, per_run=150):
    """Notes the filer wrote on each transaction, read from the original report. Reports are fetched once and
    cached (cache/trade_notes.json, by report), at most `per_run` new reports per run."""
    cache, path = _cache_json(cfg, "trade_notes.json", {})
    want = {}
    for t in trades:
        src, doc = t.get("source"), t.get("doc_id")
        if src in ("house_clerk", "senate_efd") and isinstance(doc, str) and doc and doc not in cache:
            want.setdefault(doc, t)
    todo = list(want.items())[:per_run]
    sess = None
    if any(t["source"] == "senate_efd" for _, t in todo):
        try:
            sess = _senate_session()
        except Exception as e:
            log(f"Trade notes: Senate site unavailable ({e})")

    def work(item):
        doc, t = item
        try:
            if t["source"] == "house_clerk":
                yr = pd.Timestamp(t["filed_date"]).year
                text = _fetch_house_pdf(yr, doc) or (_fetch_house_pdf(yr - 1, doc) if pd.Timestamp(t["filed_date"]).month == 1 else None)
                items = parse_house_ptr_text(text) if text else None
            elif sess is not None:
                r = sess.get(EFD + doc, timeout=60)
                items = parse_senate_ptr_html(r.text) if r.status_code == 200 and "<table" in r.text else None
            else:
                items = None
        except Exception:
            items = None
        return doc, items

    n = 0
    for doc, items in chunked_map(work, todo, min(4, cfg.get("WORKERS", 4)), cfg):
        if items is None:
            continue
        cache[doc] = [{"t": it.get("ticker"), "td": str(pd.to_datetime(it.get("trade_date"), errors="coerce").date())
                       if pd.notna(pd.to_datetime(it.get("trade_date"), errors="coerce")) else None,
                       "ty": norm_type(it.get("tx_raw")), "note": it.get("note")} for it in items if it.get("note")]
        n += 1
    if n:
        json.dump(cache, open(path, "w"))
    left = len(want) - n
    log(f"Trade notes: read {n} report(s)" + (f", {left} still to read" if left > 0 else ""))
    out = {}
    for t in trades:
        doc = t.get("doc_id")
        for it in cache.get(doc) or []:
            if it["t"] == t.get("ticker") and it["td"] == t.get("td") and (it["ty"] == t.get("ty") or not it["ty"]):
                out[(doc, t.get("ticker"), t.get("td"), t.get("ty"))] = it["note"]
                break
    return out


def day_ranges(tickers, start):
    """Each day's low and high (split/dividend adjusted, like the closes), for the chart tickers."""
    try:
        import yfinance as yf
        d = yf.download(sorted(tickers), start=start, auto_adjust=True, progress=False, threads=True)
        if d is None or d.empty:
            return None, None
        lo, hi = d["Low"], d["High"]
        if isinstance(lo, pd.Series):
            lo, hi = lo.to_frame(sorted(tickers)[0]), hi.to_frame(sorted(tickers)[0])
        return lo, hi
    except Exception as e:
        log(f"Ticker charts: day ranges unavailable ({type(e).__name__})")
        return None, None


def export_ticker_charts(cfg, scored, px, tickers, years=5):
    """Price history plus every member trade for the dashboard's ticker popups: ticker_charts.json.
    Prices are daily closes adjusted for splits and dividends; a trade's price is that day's close."""
    tickers = sorted({t for t in tickers if isinstance(t, str) and t in px.columns})
    end = px.index[-1]
    sub = px.loc[px.index >= end - pd.DateOffset(years=years), tickers + (["SPY"] if "SPY" in px.columns else [])]
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "dates": [d.strftime("%Y-%m-%d") for d in sub.index], "px": {}, "trades": {}, "photos": {}}
    for t in tickers:
        col = sub[t]
        out["px"][t] = [_sig(v) if pd.notna(v) else None for v in col.values]
    cols = [c for c in ("ticker", "tx_type", "member", "bioguide", "trade_date", "filed_date", "amt_lo", "amt_hi", "owner",
                        "source", "doc_id") if c in scored]
    tr = scored.loc[scored["ticker"].isin(tickers) & scored["tx_type"].isin(["buy", "sell"]), cols]
    tr = tr[tr["trade_date"] >= sub.index[0] - pd.Timedelta(days=7)].drop_duplicates(
        ["ticker", "member", "tx_type", "trade_date", "amt_lo"])
    bios = set()
    dlo, dhi = day_ranges([t for t in tickers if t in set(tr["ticker"])], (sub.index[0] - pd.Timedelta(days=10)).strftime("%Y-%m-%d")) \
        if len(tr) else (None, None)
    try:
        keys = [{"source": r.get("source"), "doc_id": r.get("doc_id"), "ticker": r["ticker"], "filed_date": r["filed_date"],
                 "td": pd.Timestamp(r["trade_date"]).strftime("%Y-%m-%d"), "ty": r["tx_type"]} for r in tr.to_dict("records")]
        notes = trade_notes(cfg, keys)
    except Exception as e:
        log(f"Ticker charts: filing notes skipped ({type(e).__name__}: {e})")
        notes = {}

    def rng(frame, t, td):
        try:
            v = frame[t].asof(td) if frame is not None and t in frame.columns else np.nan
            return _sig(float(v)) if pd.notna(v) else None
        except Exception:
            return None
    for r in tr.sort_values("trade_date").itertuples(index=False):
        r = r._asdict()
        t, td = r["ticker"], pd.Timestamp(r["trade_date"])
        p = px[t].asof(td) if t in px.columns else np.nan
        b = r.get("bioguide") if isinstance(r.get("bioguide"), str) and r.get("bioguide") else None
        bios.add(b)
        lo, hi = r.get("amt_lo"), r.get("amt_hi")
        out["trades"].setdefault(t, []).append({
            "n": str(r["member"]), "b": b, "ty": r["tx_type"], "td": td.strftime("%Y-%m-%d"),
            "d": pd.Timestamp(r["filed_date"]).strftime("%Y-%m-%d") if pd.notna(r.get("filed_date")) else None,
            "p": _sig(float(p)) if pd.notna(p) else None,
            "lo": float(lo) if lo is not None and pd.notna(lo) else None,
            "hi": float(hi) if hi is not None and pd.notna(hi) else None,
            "own": str(r["owner"]) if isinstance(r.get("owner"), str) and r.get("owner") else None,
            "dl": rng(dlo, t, td), "dh": rng(dhi, t, td)})
        note = notes.get((r.get("doc_id"), t, td.strftime("%Y-%m-%d"), r["tx_type"]))
        if note:
            sh, pr = parse_note(note)
            out["trades"][t][-1].update({"note": note[:300], "nsh": sh, "npx": pr})
    try:
        out["photos"] = member_photos(cfg, [b for b in bios if b])
    except Exception as e:
        log(f"Ticker charts: photos skipped ({e})")
    path = os.path.join(cfg["DATA_DIR"], "ticker_charts.json")
    json.dump(out, open(path, "w"), separators=(",", ":"))
    log(f"Ticker charts: {len(tickers)} stocks, {sum(len(v) for v in out['trades'].values())} member trades")
    return path


PHOTO_URL = "https://unitedstates.github.io/images/congress/225x275/{}.jpg"   # official photos, public domain


def member_photos(cfg, bioguides, size=96):
    """Small round-crop-ready thumbnails of members' official photos, as data: URIs the dashboard can show
    (the dashboard page can't load images from other sites). Cached on disk; a missing photo is retried weekly."""
    cache, path = _cache_json(cfg, "photos.json", {})
    changed = False
    for b in sorted({x for x in bioguides if isinstance(x, str) and re.fullmatch(r"[A-Z]\d{6}", x)}):
        hit = cache.get(b)
        if hit and (hit.get("uri") or _fresh(hit.get("tried", ""), 7)):
            continue
        uri = None
        try:
            from PIL import Image
            r = requests.get(PHOTO_URL.format(b), timeout=30)
            if r.status_code == 200:
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                w, h = im.size
                side = min(w, h)
                im = im.crop(((w - side) // 2, 0, (w - side) // 2 + side, side))   # square, keep the top (face)
                im = im.resize((size, size), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=72, optimize=True)
                uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            log(f"Photos: {b} failed ({e})")
        cache[b] = {"uri": uri, "tried": dt.datetime.now().isoformat()}
        changed = True
    if changed:
        json.dump(cache, open(path, "w"))
    return {b: cache[b]["uri"] for b in bioguides if isinstance(b, str) and cache.get(b, {}).get("uri")}


def export_dashboard(cfg, bt=None, buys=None, sells=None, new=None, tuned=None):
    path = os.path.join(cfg["DATA_DIR"], "dashboard_data.json")
    try:
        data = json.load(open(path)) if os.path.exists(path) else {}
    except Exception:
        data = {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    data.update({"version": 2, "updated": now})
    try:
        tp = _p(cfg, "state", "member_tags.json")
        if os.path.exists(tp):
            data["member_tags"] = json.load(open(tp))
    except Exception as e:
        log(f"Member tags: not added ({e})")
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
            "longs": _rows(L.sort_values("filed_date", ascending=False).head(500), _TRADE_COLS)
                     if len(L) and "filed_date" in L else [],
            "shorts": _rows(bt["shorts"].sort_values("filed_date", ascending=False).head(250), _TRADE_COLS)
                      if len(bt["shorts"]) and "filed_date" in bt["shorts"] else [],
        }
        tbl = lambda df: [{k: _j(x) for k, x in r.items()} for r in df.to_dict("records")] if df is not None and len(df) else []
        cpath = os.path.join(cfg["DATA_DIR"], "state", "price_coverage.json")
        data["backtest"]["price_coverage"] = json.load(open(cpath)) if os.path.exists(cpath) else {}
        ppath = os.path.join(cfg["DATA_DIR"], "state", "trading_pairs.json")
        data["backtest"]["pairs"] = json.load(open(ppath)) if os.path.exists(ppath) else []
        spath_ = os.path.join(cfg["DATA_DIR"], "state", "spouse_ties.json")
        data["backtest"]["spouse_ties"] = json.load(open(spath_)) if os.path.exists(spath_) else []
        data["backtest"].update({"horizons": tbl(bt.get("horizons")), "persistence": tbl(bt.get("persistence")),
                                 "persistence_verdict": bt.get("persistence_verdict") or {},
                                 "crowding": tbl(bt.get("crowding")), "sizes": tbl(bt.get("sizes")),
                                 "busy_check": tbl(bt.get("busy_check")), "busiest": bt.get("busiest") or [],
                                 "missing": _jsonable(bt.get("missing"))})
        dbt = bt.get("defense")
        if dbt is not None:
            dperf, dts, DL = dbt["perf"], dbt["trade_stats"], dbt["longs"]
            dwk = dbt["curves"].resample("W-FRI").last().dropna(how="all")
            data["defense"] = {
                "updated": now,
                "perf": [{"name": c, **{k: _j(dperf.loc[k, c]) for k in dperf.index}} for c in dperf.columns],
                "curve": {"dates": [d.strftime("%Y-%m-%d") for d in dwk.index],
                          "series": {c: [_j((x - 1) * 100) for x in dwk[c].values] for c in dwk.columns}},
                "trade_stats": [{"name": c, **{k: _j(dts.loc[k, c]) for k in dts.index}} for c in dts.columns],
                "buy_factors": _rows(dbt["buy_factors"], {"signal": "Signal", "group": "Group", "n": "Trades",
                                                          "avg": "Avg result vs SPY", "hit": "Worked (%)"}),
                "n_longs": len(DL), "longs": _rows(DL.sort_values("filed_date", ascending=False).head(300), _TRADE_COLS)}
        wf = bt.get("walk_forward")
        if wf is not None:
            wp, wwk = wf["perf"], wf["curves"].resample("W-FRI").last().dropna(how="all")
            data["walk_forward"] = {
                "perf": [{"name": c, **{k: _j(wp.loc[k, c]) for k in wp.index}} for c in wp.columns],
                "curve": {"dates": [d.strftime("%Y-%m-%d") for d in wwk.index],
                          "series": {c: [_j((x - 1) * 100) for x in wwk[c].values] for c in wwk.columns}},
                "years": [{"year": int(y), **{c: _j(wf["years"].loc[y, c]) for c in wf["years"].columns}} for y in wf["years"].index],
                "trade_stats": {k: _j(v) for k, v in wf["trade_stats"].items()},
                "log": tbl(wf["log"]), "n_picks": wf["n_picks"], "slots": wf["slots"]}
        sbt = bt.get("small")
        if sbt is not None:
            sp, swk = sbt["perf"], sbt["curves"].resample("W-FRI").last().dropna(how="all")
            P = sbt["picks"].sort_values("filed_date", ascending=False).head(300).copy()
            P["members"] = P["member"]
            P["why"] = [_why_buy(r) for _, r in P.iterrows()]
            P["result"] = np.where(~P["closed"], "open", np.where(P["excess"] > 0, "beat SPY",
                                   np.where(P["ret"] > 0, "gained, lagged SPY", "lost money")))
            data["small"] = {
                "updated": now, "hold": sbt["hold"], "slots": sbt["slots"], "per_year": sbt["per_year_trades"],
                "perf": [{"name": c, **{k: _j(sp.loc[k, c]) for k in sp.index}} for c in sp.columns],
                "curve": {"dates": [d.strftime("%Y-%m-%d") for d in swk.index],
                          "series": {c: [_j((x - 1) * 100) for x in swk[c].values] for c in swk.columns}},
                "trade_stats": {k: _j(v) for k, v in sbt["trade_stats"].items()},
                "years": [{"year": int(y), **{c: _j(v) for c, v in r.items()}} for y, r in sbt["years"].iterrows()],
                "n_picks": len(sbt["picks"]), "picks": _rows(P, _TRADE_COLS)}
    try:
        data["data_progress"] = data_progress(cfg)
    except Exception as e:
        log(f"Data progress: skipped ({type(e).__name__}: {e})")
    hpath = os.path.join(cfg["DATA_DIR"], "state", "source_health.json")
    if os.path.exists(hpath):
        try:
            data["source_health"] = json.load(open(hpath))
        except Exception:
            pass
    fpath = os.path.join(cfg["DATA_DIR"], "state", FILTER_FILE)
    if os.path.exists(fpath):
        try:
            ft = json.load(open(fpath))
            data["filter_test"] = {k: ft.get(k) for k in ("summary", "updated", "complete")}
            data["filter_test"]["years"] = {r: v["years"] for r, v in ft.get("rules", {}).items()}
            data["filter_test"]["switches"] = {r: v["switches"] for r, v in ft.get("rules", {}).items()}
        except Exception:
            pass
    apath = os.path.join(cfg["DATA_DIR"], "state", ADAPT_FILE)
    if os.path.exists(apath):
        try:
            data["adaptive"] = json.load(open(apath))
        except Exception:
            pass
    if buys is not None or sells is not None:
        wcols = {"rank": "rank", "action": "action", "t": "ticker", "co": "company", "pct": "percentile",
                 "m": "members", "why": "why", "d": "filed_date", "move": "move_since_filing", "td": "trade_date", "mt": "move_since_trade", "an": "analysts",
                 "up": "target_upside_%", "earn": "next_earnings", "news": "news_7d", "sec": "sector",
                 "nwm": "news_with_member", "def": "is_defense", "cap": "market_cap", "dodm": "dod_awards_180d",
                 "ppl": "people", "px": "price_now", "sz": "size_mult", "ags": "f_against_street", "sm": "f_small_cap", "lag": "lag_days", "nc": "f_no_coverage"}
        data["watchlist"] = {
            "updated": now, "lookback": cfg["WATCHLIST_LOOKBACK_DAYS"],
            "buys": _rows(buys, dict(wcols, s="score")),
            "sells": _rows(sells, dict(wcols, s="short_score")),
            "new": _rows(new, {"action": "action", "t": "ticker"}) if new is not None else []}
        bios = [p.get("b") for r in data["watchlist"]["buys"] + data["watchlist"]["sells"] for p in (r.get("ppl") or [])]
        try:
            data["photos"] = member_photos(cfg, bios)
        except Exception as e:
            log(f"Photos: skipped ({e})")
    if tuned is not None:
        comp = tuned["comparison"]
        data["tuning"] = {
            "updated": now, "split": _j(tuned["split"]), "better": tuned["tuned_better"],
            "comparison": [{"name": i, **{k: _j(comp.loc[i, k]) for k in comp.columns}} for i in comp.index],
            "buy_weights": [{"signal": i, "default": _j(r["Default"]), "tuned": _j(r["Tuned"]),
                             "n": _j(r["Training trades with this signal"])} for i, r in tuned["buy_weights"].iterrows()],
            "short_weights": [{"signal": i, "default": _j(r["Default"]), "tuned": _j(r["Tuned"]),
                               "n": _j(r["Training trades with this signal"])} for i, r in tuned["short_weights"].iterrows()]}
    def _clean(o):          # any NaN/inf left anywhere becomes null instead of stopping the export
        if isinstance(o, float):
            return o if math.isfinite(o) else None
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, (np.floating,)):
            v = float(o)
            return v if math.isfinite(v) else None
        if isinstance(o, np.integer):
            return int(o)
        return o
    txt = json.dumps(_clean(data), separators=(",", ":"), allow_nan=False, default=str)
    open(path, "w").write(txt)
    log(f"Dashboard data saved ({len(txt)/1024:,.0f} KB)")
    return path


# ############################################################################
#  CONNECTIONS (v3): every public link between a member and a stock they trade
# ############################################################################
import zlib

_RUN_START = time.time()
HIT_TIME_LIMIT = False


def out_of_time(cfg, reserve_min=60):
    """True when a time-limited run (GitHub) should stop collecting and save what it has."""
    global HIT_TIME_LIMIT
    budget = cfg.get("TIME_BUDGET_MIN")
    over = bool(budget) and (time.time() - _RUN_START) / 60 > budget - reserve_min
    if over:
        HIT_TIME_LIMIT = True      # the run stopped a step early, so there is more data to collect
    return over


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
FD_MAX_PAGES = 80


def collect_annual_disclosures(cfg, traders=None):
    """Returns DataFrame: chamber, last_key, first, filed, holdings (set of tickers), text (compressed)."""
    cache = _p(cfg, "cache", "annual_fd.pkl")
    df = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame(
        columns=["chamber", "last_key", "first", "state", "filed", "doc", "holdings", "text"])
    if "v" not in df.columns:
        df["v"] = 1
    # version 2 reads up to FD_MAX_PAGES pages (version 1 stopped at 20, cutting off long reports)
    done = set(df.loc[(df["chamber"] != "House") | (df["v"].fillna(1) >= 2), "doc"]) if len(df) else set()
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
            if traders:     # only members who actually trade stocks
                idx = idx[idx["Last"].astype(str).map(lambda s: name_key(s).split(" ")[-1] if name_key(s) else "").isin(traders)]
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
                        return r, "\n".join((pg.extract_text() or "") for pg in pdf.pages[:FD_MAX_PAGES])
                except Exception:
                    return r, None

            if True:
                for r, text in chunked_map(work, [r for _, r in idx.iterrows()], cfg["WORKERS"], cfg):
                    done.add(str(r["DocID"]))
                    if len(done) % 200 == 0 and rows:
                        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
                        rows = []
                        df.to_pickle(cache)
                    if not text:
                        continue
                    hold = {clean_ticker(m.group(1)) for m in HOUSE_ASSET.finditer(text) if m.group(1)}
                    ties = "\n".join(l for l in text.splitlines() if not HOUSE_ASSET.search(l))
                    rows.append({"chamber": "House", "last_key": name_key(r.get("Last") or "").split(" ")[-1],
                                 "first": str(r.get("First") or ""), "state": str(r.get("StateDst") or "")[:2],
                                 "filed": pd.to_datetime(r.get("FilingDate"), errors="coerce"),
                                 "doc": str(r["DocID"]), "holdings": {h for h in hold if h}, "text": _z(ties), "v": 2})
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
                             "holdings": hold, "text": _z("\n".join(ties)), "v": 2})
                time.sleep(0.4)
        except Exception as e:
            log(f"Yearly disclosures: Senate skipped ({e})")
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    if len(df):          # a re-read report replaces its older, shorter copy
        df = df.sort_values("v", kind="stable").drop_duplicates("doc", keep="last").reset_index(drop=True)
        df.to_pickle(cache)
    left = 0 if not len(df) else int(((df["chamber"] == "House") & (df["v"].fillna(1) < 2)).sum())
    log(f"Yearly disclosures: {len(df):,} reports on file" + (f"; {left:,} long House reports still to re-read in full" if left else ""))
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
    soon = (dt.date.today() - dt.timedelta(days=14)).isoformat()

    def stale(v):
        if v is None:
            return True
        if not v:
            return False
        if v.get("v") != 2:                      # older reads lack the bills and meeting type
            return True
        # scheduled meetings change (witnesses added, postponed): re-read upcoming ones daily
        return (v.get("date") or "") >= soon and not _fresh(v.get("fetched", ""), 1)
    todo = [k for k, v in cache["events"].items() if stale(v)]
    todo.sort(key=lambda k: (cache["events"][k] is not None, -(int(k.split("|")[0]))))   # new, then recent congresses
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
            rel = m.get("relatedItems") or {}
            bl = rel.get("bills") or []
            if isinstance(bl, dict):
                bl = bl.get("item") or []
            bills = []
            for b in bl:
                typ = str(b.get("type") or "").lower().replace(".", "").replace(" ", "")
                if typ in ("hr", "s", "hres", "sres", "hjres", "sjres", "hconres", "sconres") and b.get("number"):
                    bills.append(f"{b.get('congress') or cong}|{typ}|{b.get('number')}")
            cache["events"][k] = {"date": (m.get("date") or "")[:10], "title": (m.get("title") or "")[:200],
                                  "type": m.get("type"), "status": m.get("meetingStatus"),
                                  "cids": [_cid(c.get("systemCode")) for c in coms],
                                  "orgs": [company_key(w.get("organization")) for w in wit if w.get("organization")],
                                  "bills": bills, "v": 2, "fetched": dt.datetime.now().isoformat()}
        elif js is not None:
            cache["events"][k] = {}
        time.sleep(0.75)           # congress.gov allows 5,000 requests per hour
        if i % 250 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Hearings: {i:,}/{len(todo):,}")
    json.dump(cache, open(path, "w"))
    return cache


_NAME_TAIL = re.compile(r"[,\s]+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|llc|lp|"
                        r"l\.p|n\.v|nv|s\.a|sa|ag|se|the|class [a-c]|common stock)\.?$", re.I)


def _fr_name(name):
    """A company's name as it would appear in a federal document ("Lockheed Martin"), or None if too generic."""
    n = re.sub(r"^the\s+", "", (name or "").strip(), flags=re.I)
    for _ in range(4):
        n2 = _NAME_TAIL.sub("", n).strip(" ,.")
        if n2 == n:
            break
        n = n2
    words = n.split()
    if not words or (len(words) == 1 and len(n) < 6):
        return None          # single short words ("Apple", "Intel") match too many unrelated documents
    return n


def collect_fedreg(cfg, meta, tickers, per_run=500):
    """Federal rules and proposed rules that name the company (Federal Register), with their publication date."""
    cache, path = _cache_json(cfg, "fedreg.json", {})
    todo = [t for t in tickers if not _fresh((cache.get(t) or {}).get("fetched", ""), 14)][:per_run]
    if todo:
        log(f"Federal rules: checking {len(todo)} companies")
    start = pd.Timestamp(cfg["START_DATE"]).strftime("%Y-%m-%d")
    for i, t in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        nm = _fr_name((meta.get(t) or {}).get("name"))
        if not nm:
            cache[t] = {"fetched": dt.datetime.now().isoformat(), "docs": [], "skip": True}
            continue
        docs, page = [], 1
        while page <= 5:
            js = _get_json("https://www.federalregister.gov/api/v1/documents.json", params=[
                ("conditions[term]", f'"{nm}"'), ("conditions[type][]", "RULE"), ("conditions[type][]", "PRORULE"),
                ("conditions[publication_date][gte]", start), ("fields[]", "publication_date"), ("fields[]", "type"),
                ("fields[]", "agencies"), ("fields[]", "title"), ("per_page", "1000"), ("page", str(page)), ("order", "newest")])
            if js is None:
                break
            for r in js.get("results") or []:
                ag = ", ".join(a.get("name") or "" for a in (r.get("agencies") or [])[-1:])
                docs.append([r.get("publication_date"), r.get("type"), ag, (r.get("title") or "")[:160]])
            if not js.get("next_page_url"):
                break
            page += 1
            time.sleep(0.3)
        if js is not None or docs:
            cache[t] = {"fetched": dt.datetime.now().isoformat(), "docs": docs, "name": nm}
        time.sleep(0.3)
        if i % 100 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Federal rules: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    return cache


# 8-K items that mark a major company announcement (earnings, 2.02, are already a company event elsewhere)
MAJOR_8K = {"1.01": "a major agreement", "1.02": "the end of a major agreement", "2.01": "an acquisition or sale",
            "2.05": "a restructuring", "2.06": "a write-down", "3.01": "a delisting notice", "4.02": "a restatement",
            "5.01": "a change of control", "5.02": "a top executive change"}


def collect_8k(cfg, sec_companies, tickers, per_run=600):
    """Each company's 8-K filings (major-event reports) with filing date and item numbers, from SEC EDGAR."""
    hdr = _sec_headers(cfg)
    cache, path = _cache_json(cfg, "filings_8k.json", {})
    if not hdr:
        return cache
    start = pd.Timestamp(cfg["START_DATE"]).strftime("%Y-%m-%d")
    todo = [t for t in tickers if (sec_companies.get(t) or {}).get("cik")
            and not _fresh((cache.get(t) or {}).get("fetched", ""), 7)][:per_run]
    if todo:
        log(f"Company 8-K filings: checking {len(todo)} companies")

    def pick(block):
        return [[d, it] for f, d, it in zip(block.get("form", []), block.get("filingDate", []), block.get("items", []))
                if f in ("8-K", "8-K/A") and d >= start and it]
    for i, t in enumerate(todo, 1):
        if out_of_time(cfg):
            break
        cik = int(sec_companies[t]["cik"])
        sub = _get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=hdr)
        time.sleep(0.12)
        if not sub:
            continue
        old = cache.get(t) or {}
        rows = pick((sub.get("filings") or {}).get("recent") or {})
        if not old.get("old_done"):            # older filings sit in extra files; read them once
            for f in (sub.get("filings") or {}).get("files") or []:
                if (f.get("filingTo") or "") < start:
                    continue
                more = _get_json(f"https://data.sec.gov/submissions/{f.get('name')}", headers=hdr)
                time.sleep(0.12)
                rows += pick(more or {})
        else:
            rows += [r for r in old.get("k", []) if r[0] < min((r_[0] for r_ in rows), default="9999")]
        seen, k = set(), []
        for r in sorted(rows, reverse=True):
            if tuple(r) not in seen:
                seen.add(tuple(r))
                k.append(r)
        cache[t] = {"fetched": dt.datetime.now().isoformat(), "k": k, "old_done": True}
        if i % 100 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Company 8-K filings: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    return cache


def event_features(tx, data, mems):
    """Things that happened between the member's trade and its disclosure (so they were public by the filing date):
    a federal rule naming the company, the company testifying before the member's committee, the member's committee
    marking up an industry bill, and a major company announcement (8-K). Window: 30 days after the trade."""
    D = pd.Timedelta
    n = len(tx)
    tdt, fdt = pd.to_datetime(tx["trade_date"]), pd.to_datetime(tx["filed_date"])
    hi = np.minimum(tdt + D(days=30), fdt)
    meta = data.get("meta") or {}
    ck = [company_key((meta.get(t) or {}).get("name")) for t in tx["ticker"]]
    cids = [set((m.get("cids") or [])) if m else set() for m in mems]
    for c in ("f_reg_action", "f_witness_after", "f_markup_after", "f_major_8k_after"):
        tx[c] = 0.0
    tx["event_note"] = ""
    notes = [[] for _ in range(n)]

    def win(dates, i):
        return [d for d in dates if tdt.iloc[i] < d <= hi.iloc[i]]
    # federal rules naming the company
    fr = data.get("fedreg") or {}
    frd = {t: sorted(pd.to_datetime([d[0] for d in v.get("docs", []) if d[0]], errors="coerce").dropna())
           for t, v in fr.items() if v.get("docs")}
    f1 = np.zeros(n)
    for i, t in enumerate(tx["ticker"]):
        w = win(frd.get(t, []), i)
        if w:
            f1[i] = 1.0
            notes[i].append(f"a federal rule naming the company was published {(w[0] - tdt.iloc[i]).days} days after the trade")
    tx["f_reg_action"] = f1
    # committee hearings and markups after the trade
    mt = data.get("meetings")
    pol = data.get("bill_policy") or {}
    f2, f3 = np.zeros(n), np.zeros(n)
    if mt is not None and len(mt) and "bills" in mt:
        mt = mt.sort_values("date")
        md = mt["date"].values
        for i in range(n):
            if not cids[i]:
                continue
            a, b = np.searchsorted(md, np.datetime64(tdt.iloc[i]), side="right"), np.searchsorted(md, np.datetime64(hi.iloc[i]), side="right")
            if b <= a:
                continue
            sec = tx["sector"].iloc[i] if "sector" in tx else None
            for _, r in mt.iloc[a:b].iterrows():
                if not (cids[i] & r["cids"]):
                    continue
                if ck[i] and not f2[i] and any(ck[i] in o for o in r["orgs"]):
                    f2[i] = 1.0
                    notes[i].append(f"company testified before their committee {(r['date'] - tdt.iloc[i]).days} days after the trade")
                if sec and not f3[i] and "markup" in r["type"].lower() and any(
                        sec in POLICY_SECTORS.get(pol.get(bl) or "", []) for bl in r["bills"]):
                    f3[i] = 1.0
                    notes[i].append(f"their committee marked up a bill affecting {sec} {(r['date'] - tdt.iloc[i]).days} days after the trade")
    tx["f_witness_after"], tx["f_markup_after"] = f2, f3
    # major company announcements (8-K)
    k8 = data.get("k8") or {}
    f4 = np.zeros(n)
    kd = {}
    for t, v in k8.items():
        lst = []
        for d, items in v.get("k", []):
            its = [x.strip() for x in str(items).split(",") if x.strip() in MAJOR_8K]
            if its:
                lst.append((pd.Timestamp(d), its))
        kd[t] = sorted(lst)
    for i, t in enumerate(tx["ticker"]):
        for d, its in kd.get(t, []):
            if tdt.iloc[i] < d <= hi.iloc[i]:
                f4[i] = 1.0
                notes[i].append(f"company announced {MAJOR_8K[its[0]]} {(d - tdt.iloc[i]).days} days after the trade (8-K)")
                break
    tx["f_major_8k_after"] = f4
    tx["event_note"] = ["; ".join(x) for x in notes]
    return tx


def upcoming_hearings(cfg, rows, days=45):
    """Scheduled hearings in the next `days` days where the company is a witness, for the watchlist cards."""
    cache, _ = _cache_json(cfg, "committee_meetings.json", {"events": {}})
    today, end = dt.date.today().isoformat(), (dt.date.today() + dt.timedelta(days=days)).isoformat()
    up = [v for v in (cache.get("events") or {}).values() if v and today <= (v.get("date") or "") <= end
          and (v.get("status") or "").lower() not in ("canceled", "cancelled", "postponed")]
    out = {}
    for r in rows:
        c_ = company_key(r.get("co") or "")
        if not c_:
            continue
        hits = sorted((v["date"], v.get("title") or "") for v in up if any(c_ in (o or "") for o in v.get("orgs", [])))
        if hits:
            out[r["t"]] = [{"date": d, "title": t_} for d, t_ in hits[:3]]
    return out


def meetings_frame(cache):
    rows = []
    for k, v in (cache or {}).get("events", {}).items():
        if not v or not v.get("date") or (v.get("status") or "").lower() in ("canceled", "cancelled", "postponed"):
            continue
        rows.append({"date": pd.to_datetime(v["date"], errors="coerce"), "cids": set(c for c in v.get("cids", []) if c),
                     "orgs": set(o for o in v.get("orgs", []) if o), "closed": bool(CLOSED_RE.search(v.get("title") or "")),
                     "type": str(v.get("type") or ""), "bills": list(v.get("bills") or []), "title": v.get("title") or ""})
    return pd.DataFrame(rows, columns=["date", "cids", "orgs", "closed", "type", "bills", "title"]).dropna(subset=["date"])


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
                if added % 500 == 0:
                    pd.to_pickle(votes, cache_p)
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
    key = tx["who"]
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
                        "who": key.reindex(tx.index).values, "c": cids})      # same row order as tx
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
    fd = data["annual_fd"] = fd_member_ids(data.get("annual_fd"), data.get("legislators") or {})
    held, tie = np.zeros(n), np.zeros(n)
    if fd is not None and len(fd):
        fdg = {k: g.sort_values("filed") for k, g in fd.groupby("who")}
        text_cache = {}
        for i, (wk, t, f, c_) in enumerate(zip(tx["who"], tx["ticker"], fdt, tx["_ck"])):
            g = fdg.get(wk)
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
                if pd.Timestamp(cyc + 1, 1, 31) < f:          # the year-end report is due Jan 31
                    tot += sum(a for e, a in rows.items() if e == c_ or e.startswith(c_ + " "))
        ed[i] = tot
    tx["employee_donations"] = ed
    tx["f_employee_donations"] = (np.log10(ed + 1) / 5).clip(0, 1)

    # 5. revolving door: the company's lobbyists used to work for this member
    lb = data.get("lobbying")
    rv = np.zeros(n)
    if lb is not None and len(lb) and "covered" in lb:
        lg = {k: v for k, v in lb.dropna(subset=["posted"]).groupby("ticker")}
        for i, (t, lk, f, ch, mem) in enumerate(zip(tx["ticker"], last, fdt, tx["chamber"], tx["member"])):
            g = lg.get(t)
            if g is None or not lk:
                continue
            w = g[(g.posted <= f) & (g.posted > f - D(days=730))]
            if not len(w):
                continue
            # the right chamber's title, and if a first name is written it must be this member's
            title = r"(rep|representative|congressman|congresswoman)" if ch == "House" else r"(sen|senator)"
            pat = re.compile(r"\b" + title + r"\.?\s+(?:([A-Za-z]+)\.?\s+)?(?:[A-Z]\.\s+)?" + re.escape(lk) + r"\b", re.I)
            firsts = {x[:3] for x in _first_tokens("", str(mem))}
            for c in w.covered:
                for mm in pat.finditer(c or ""):
                    fw = (mm.group(2) or "").lower()
                    if not fw or not firsts or fw[:3] in firsts:
                        rv[i] = 1.0
                        break
                if rv[i]:
                    break
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
            nm = name_key(re.sub(r"^(rep|sen|senator|congressman|congresswoman)\.?\s+", "", disp or "", flags=re.I))
            lk = nm.split(" ")[-1:]
            if lk and lk[0]:
                by_last.setdefault(lk[0], []).append((h, nm.split(" ")[0][:3] if " " in nm else ""))
        for i, (lk, t, c_, f, mem) in enumerate(zip(last, tx["ticker"], tx["_ck"], fdt, tx["member"])):
            firsts = {x[:3] for x in _first_tokens("", str(mem))}
            for h, f3 in by_last.get(lk, []):
                if f3 and firsts and f3 not in firsts:        # Tim Scott's account isn't Rick Scott's
                    continue
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
            # a contract counts from the day it was made public, so it must be public by the filing date
            evmap[t] = evmap.get(t, pd.DatetimeIndex([])).append(pd.DatetimeIndex(g.known_date.dropna()))
    for i, (t, td, f) in enumerate(zip(tx["ticker"], tdt, fdt)):
        d = evmap.get(t)
        if d is not None and len(d) and _within(d, td, min(td + D(days=30), f)):
            pre[i] = 1.0
    tx["f_pre_event"] = pre
    return tx.drop(columns=["_ck"])


# ############################################################################
#  v4: company size, proven members, crowding, industry benchmarks, costs,
#      holding periods, confidence ranges, former-member lobbyists, DEFENSE
# ############################################################################
SECTOR_ETF = {"Technology": "XLK", "Industrials": "XLI", "Financial Services": "XLF", "Healthcare": "XLV",
              "Energy": "XLE", "Utilities": "XLU", "Basic Materials": "XLB", "Consumer Cyclical": "XLY",
              "Consumer Defensive": "XLP", "Real Estate": "XLRE", "Communication Services": "XLC"}
DEFENSE_ETF = "ITA"
BENCHMARK_ETFS = sorted(set(SECTOR_ETF.values()) | {DEFENSE_ETF, "IWM"})
# committees and subcommittees that oversee the military, its budget and intelligence
DEFENSE_CIDS = {"HSAS", "SSAS", "HSAP02", "SSAP02", "HLIG", "SLIN", "HSHM", "SSGA"}
DEFENSE_EXTRA = {"PLTR", "BAH", "SAIC", "CACI", "LDOS", "KTOS", "AVAV", "MRCY", "BWXT", "HII", "GD", "LMT", "NOC",
                 "RTX", "LHX", "TDG", "HEI", "TXT", "CW", "PSN", "KBR", "V2X", "DRS", "AXON", "RKLB", "ONDS",
                 "RCAT", "KRMN", "PKE", "DCO", "MOG-A", "ESLT", "BA", "GE", "HWM", "SPR", "ATRO", "VSAT", "IRDM",
                 "BBAI", "PL", "RDW", "LUNR", "ASTS", "SWBI", "RGR", "AOUT", "NPK", "OSIS", "TGI", "HXL", "WWD"}
EX_MEMBER_RE = re.compile(r"^(former\s+)?(u\.?\s?s\.?\s+)?(member of (the )?(u\.?\s?s\.?\s+)?(house|congress|senate)|"
                          r"member,? (u\.?\s?s\.?\s+)?house|representative|senator|congressman|congresswoman)\b", re.I)


def is_defense(t, meta, dod_share=None):
    """Aerospace & defense companies, known defense contractors, and industrial companies that get most of
    their federal contract money from the Defense Department (not tech giants that merely sell to it)."""
    m = meta.get(t) or {}
    ind = (m.get("industry") or "").lower()
    if t in DEFENSE_EXTRA or "aerospace & defense" in ind:
        return True
    return bool(dod_share is not None and dod_share.get(t, 0) >= 0.6 and m.get("sector") == "Industrials"
                and (m.get("cap") or 0) < 1e11)


def dod_share_by_ticker(con):
    if con is None or not len(con):
        return {}
    dod = con["agency"].str.contains("Defense", case=False, na=False)
    tot = con.groupby("ticker")["amount"].sum()
    d = con[dod].groupby("ticker")["amount"].sum()
    big = tot[tot >= 5e7].index
    return {t: float(d.get(t, 0) / tot[t]) for t in big}


def _running_stats(tx, typ, sign, value_col="excess"):
    """For each trade, n / mean / t-stat of the member's EARLIER closed trades of that type (public by filing)."""
    done = tx[(tx["tx_type"] == typ) & tx["closed"] & tx[value_col].notna()]
    n, mean, tstat = np.zeros(len(tx)), np.zeros(len(tx)), np.zeros(len(tx))
    pos = {ix: i for i, ix in enumerate(tx.index)}
    for w, g in tx.groupby("who"):
        past = done[done["who"] == w].sort_values("exit_date")
        if len(past) < 2:
            continue
        x = (sign * past[value_col]).clip(-1, 3).values
        c1, c2 = np.concatenate([[0], np.cumsum(x)]), np.concatenate([[0], np.cumsum(x * x)])
        k = np.searchsorted(past["exit_date"].values, g["filed_date"].values, side="left")
        with np.errstate(invalid="ignore", divide="ignore"):
            mu = np.where(k > 0, c1[k] / np.maximum(k, 1), 0)
            var = np.where(k > 1, (c2[k] - k * mu * mu) / np.maximum(k - 1, 1), np.nan)
            t = np.where(k > 1, mu / np.sqrt(np.maximum(var, 1e-12) / k), 0)
        ii = [pos[i] for i in g.index]
        n[ii], mean[ii], tstat[ii] = k, mu, np.nan_to_num(t)
    return n, mean, tstat


def extra_features(tx, px, data, cfg, mems):
    meta = data["meta"]
    D = pd.Timedelta
    # company size at the time of the trade: today's value scaled by the (split-adjusted) price change since then
    cap_now = tx["ticker"].map(lambda t: (meta.get(t) or {}).get("cap")).astype(float)
    last_px = px.ffill().iloc[-1]
    idx0 = px.index
    p_then = []
    for tk, d in zip(tx["ticker"], tx["filed_date"]):
        if tk in px.columns and pd.notna(d):
            j = idx0.searchsorted(d, side="right") - 1
            p_then.append(px[tk].iat[j] if j >= 0 else np.nan)
        else:
            p_then.append(np.nan)
    # scale by the price change between the trade and the day the cap was looked up (not today: that would
    # leak the stock's later performance into its size). Old entries without a lookup date fall back to today.
    def p_at(t):
        m = meta.get(t) or {}
        if t not in px.columns:
            return np.nan
        if m.get("cap_at"):
            j = idx0.searchsorted(pd.Timestamp(m["cap_at"]), side="right") - 1
            s_ = px[t].iloc[:j + 1].dropna() if j >= 0 else px[t].iloc[0:0]
            return float(s_.iloc[-1]) if len(s_) else np.nan
        return float(last_px.get(t, np.nan))
    pc = {t: p_at(t) for t in set(tx["ticker"])}
    p_now = tx["ticker"].map(pc).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        cap = cap_now * np.array(p_then, dtype=float) / p_now
    cap = pd.Series(np.where(np.isfinite(cap), cap, np.nan), index=tx.index)
    tx["market_cap_now"] = cap_now
    tx["market_cap"] = cap
    tx["f_small_cap"] = np.where(cap.isna(), 0.0, np.where(cap < 2e9, 1.0, np.where(cap < 1e10, 0.5, 0.0)))

    # proven members: past picks beat the market by a statistically meaningful margin
    n, mu, t = _running_stats(tx, "buy", 1)
    tx["proven_n"], tx["proven_mean"], tx["proven_t"] = n, mu, t
    # needs a real edge over the typical member (3%+ per trade), not just over the market, and enough trades
    done = tx[(tx["tx_type"] == "buy") & tx["closed"] & tx["excess"].notna()].sort_values("exit_date")
    if len(done):
        c = np.concatenate([[0], np.cumsum(done["excess"].clip(-1, 3).values)])
        k = np.searchsorted(done["exit_date"].values, tx["filed_date"].values, side="left")
        mu = mu - np.where(k > 0, c[k] / np.maximum(k, 1), 0)
    tx["f_proven_member"] = np.where((n >= 20) & (t >= 2.5) & (mu >= 0.03), 1.0,
                                     np.where((n >= 12) & (t >= 2) & (mu >= 0.02), 0.5, 0.0))
    n, mu, t = _running_stats(tx, "sell", -1)
    done = tx[(tx["tx_type"] == "sell") & tx["closed"] & tx["excess"].notna()].sort_values("exit_date")
    if len(done):
        c = np.concatenate([[0], np.cumsum((-done["excess"]).clip(-1, 3).values)])
        k = np.searchsorted(done["exit_date"].values, tx["filed_date"].values, side="left")
        mu = mu - np.where(k > 0, c[k] / np.maximum(k, 1), 0)
    tx["f_proven_seller"] = np.where((n >= 20) & (t >= 2.5) & (mu >= 0.03), 1.0,
                                     np.where((n >= 12) & (t >= 2) & (mu >= 0.02), 0.5, 0.0))

    # crowding: the stock jumped the day the trade was disclosed (copy-traders piling in)
    idx = px.index
    colmap = {c: i for i, c in enumerate(px.columns)}
    ci = tx["ticker"].map(colmap)
    pos = np.searchsorted(idx.values, tx["filed_date"].values, side="right") - 1
    ok = ci.notna().values & (pos >= 1)
    p_i, c_i = np.where(ok, pos, 1), np.where(ok, ci.fillna(0).astype(int), 0)
    arr, spy = px.values, px["SPY"].values
    with np.errstate(invalid="ignore", divide="ignore"):
        jump = arr[p_i, c_i] / arr[p_i - 1, c_i] - spy[p_i] / spy[p_i - 1]
    tx["disclosure_jump"] = np.where(ok, jump, np.nan)
    tx["f_crowded"] = (np.nan_to_num(tx["disclosure_jump"]) > 0.03).astype(float)

    # stock vs its own industry fund over the same holding period
    etf = [DEFENSE_ETF if is_defense(tk, meta) else SECTOR_ETF.get(s) for tk, s in zip(tx["ticker"], tx["sector"])]
    tx["benchmark_etf"] = etf
    if not len(tx):
        tx["sector_ret"] = tx["excess_sector"] = np.nan
    ent = np.searchsorted(idx.values, tx["entry_date"].values, side="left")
    ext = np.searchsorted(idx.values, tx["exit_date"].values, side="left")
    ei = [colmap.get(e) for e in etf]
    ok = np.array([e is not None for e in ei], dtype=bool) & tx["entry_date"].notna().values.astype(bool) & (ent < len(idx)) & (ext < len(idx))
    ev = np.full(len(tx), np.nan)
    for i in np.where(ok)[0]:
        a, b = arr[ent[i], ei[i]], arr[ext[i], ei[i]]
        if a and b and not np.isnan(a) and not np.isnan(b):
            ev[i] = b / a - 1
    tx["sector_ret"] = ev
    tx["excess_sector"] = tx["ret"] - tx["sector_ret"]

    # former members of Congress lobbying for the company (last 2 years)
    lb = data.get("lobbying")
    exm = np.zeros(len(tx))
    if lb is not None and len(lb) and "covered" in lb:
        lg = {k: v for k, v in lb.dropna(subset=["posted"]).groupby("ticker")}
        for i, (tk, f) in enumerate(zip(tx["ticker"], tx["filed_date"])):
            g = lg.get(tk)
            if g is None:
                continue
            w = g[(g.posted <= f) & (g.posted > f - D(days=730))]
            if any(EX_MEMBER_RE.match(part.strip()) for c in w.covered for part in (c or "").split("|")):
                exm[i] = 1.0
    tx["f_ex_member_lobbyist"] = exm

    # ---------------- defense ----------------
    con = data.get("contracts")
    share = dod_share_by_ticker(con)
    tx["is_defense"] = [is_defense(tk, meta, share) for tk in tx["ticker"]]
    tx["f_defense_company"] = tx["is_defense"].astype(float)
    cids = [set(m.get("cids") or []) if m else set() for m in mems]
    tx["f_defense_power"] = [1.0 if (d and (c & DEFENSE_CIDS)) else 0.0 for d, c in zip(tx["is_defense"], cids)]
    after, mom, recent = np.zeros(len(tx)), np.zeros(len(tx)), np.zeros(len(tx))
    if con is not None and len(con):
        dod = con[con["agency"].str.contains("Defense", case=False, na=False)].dropna(subset=["action_date"])
        g = {k: v.sort_values("action_date") for k, v in dod.groupby("ticker")}
        for i, (tk, td, fd) in enumerate(zip(tx["ticker"], tx["trade_date"], tx["filed_date"])):
            v = g.get(tk)
            if v is None:
                continue
            known = v[v.known_date <= fd]
            # award announced after the member traded but before we saw the trade
            if ((known.action_date > td) & (known.amount >= 7.5e6)).any():
                after[i] = 1.0
            last = known[known.action_date > fd - D(days=180)].amount.sum()
            prior = known[(known.action_date <= fd - D(days=180)) & (known.action_date > fd - D(days=900))].amount.sum() / 4
            recent[i] = last
            if last > 0 and (prior == 0 or last / prior >= 2):
                mom[i] = 1.0
            elif prior > 0 and last / prior >= 1.3:
                mom[i] = 0.5
    tx["f_dod_award_after_trade"], tx["f_dod_momentum"], tx["dod_awards_180d"] = after, mom, recent
    # display only: never used in scoring
    ridx = religion_lookup(data.get("religion") or [])
    tx["religion"] = [religion_for(ridx, ch, lk, fi, st) for ch, lk, fi, st in
                      zip(tx["chamber"], tx["last_key"], tx["first"], tx["state"])]
    return tx


# ----------------------------------------------------------------------------
# Tables for the report and dashboard
# ----------------------------------------------------------------------------
def horizons_table(scored, px, cfg):
    """How purchases did over several holding periods (does the edge show up fast, slow, or never?)."""
    hs = cfg.get("HOLDING_PERIODS", [5, 20, 60, 125, 250])
    buys = scored[(scored["tx_type"] == "buy")].copy()
    if buys.empty:
        return pd.DataFrame()
    hist = buys.sort_values("filed_date")["score"].expanding().quantile(cfg["PICK_PERCENTILE"] / 100).shift(1)
    buys["top"] = buys["score"] >= hist.reindex(buys.index).fillna(np.inf)
    rows = []
    labels = {5: "1 week", 20: "1 month", 60: "3 months", 125: "6 months", 250: "1 year"}
    for h in hs:
        fr = forward_returns(buys, px, h, cost=cfg.get("COST_BPS", 0) / 1e4)
        fr = fr[fr["closed"]]
        for name, m in (("All purchases", pd.Series(True, index=fr.index)),
                        ("Top-scored purchases", buys.loc[fr.index, "top"]),
                        ("Defense purchases", buys.loc[fr.index, "is_defense"].astype(bool) if "is_defense" in buys else None),
                        ("Small companies", buys.loc[fr.index, "f_small_cap"] >= 1 if "f_small_cap" in buys else None)):
            if m is None or not m.any():
                continue
            x = fr.loc[m[m].index, "excess"].dropna()
            if len(x) < 20:
                continue
            se = _cluster_se(x.values, buys.loc[x.index, "filed_date"].values)
            rows.append({"Holding period": labels.get(h, f"{h} days"), "Group": name, "Trades": len(x),
                         "Avg vs SPY": x.mean(), "Range low": x.mean() - 1.96 * se, "Range high": x.mean() + 1.96 * se,
                         "Beat SPY (%)": (x > 0).mean() * 100})
    return pd.DataFrame(rows)


def persistence_table(scored, min_trades=10):
    """Does a member's record in earlier years predict their next year?"""
    b = scored[(scored["tx_type"] == "buy") & scored["closed"] & scored["excess"].notna()].copy()
    if b.empty:
        return pd.DataFrame(), {}
    b["year"] = b["filed_date"].dt.year
    if "who" not in b:
        b["who"] = b["chamber"] + "|" + b["last_key"]
    by = b.groupby(["who", "year"])["excess"].agg(["mean", "count"]).reset_index()
    rows, pairs = [], []
    for y in sorted(b["year"].unique())[2:]:
        past = b[(b.year < y) & (b.year >= y - 2)].groupby("who")["excess"].agg(["mean", "count"])
        now = by[(by.year == y)].set_index("who")
        j = past[past["count"] >= min_trades].join(now[now["count"] >= min_trades], lsuffix="_past", rsuffix="_now", how="inner")
        if len(j) < 8:
            continue
        top = j["mean_past"] >= j["mean_past"].quantile(0.8)
        corr = j["mean_past"].rank().corr(j["mean_now"].rank())
        rows.append({"Year": int(y), "Members compared": len(j), "Rank correlation": corr,
                     "Past top 20%: next year vs SPY": j.loc[top, "mean_now"].mean(),
                     "Everyone else: next year vs SPY": j.loc[~top, "mean_now"].mean()})
        pairs.append(corr)
    t = pd.DataFrame(rows)
    verdict = {}
    if len(t):
        gap = (t["Past top 20%: next year vs SPY"] - t["Everyone else: next year vs SPY"]).mean()
        verdict = {"avg_corr": float(np.nanmean(pairs)), "avg_gap": float(gap),
                   "persists": bool(np.nanmean(pairs) > 0.1 and gap > 0.01)}
    return t, verdict


def crowding_table(scored):
    b = scored[(scored["tx_type"] == "buy") & scored["closed"] & scored["disclosure_jump"].notna()]
    rows = []
    for lab, m in (("Jumped over 3% on disclosure day", b["disclosure_jump"] > 0.03),
                   ("Moved normally", b["disclosure_jump"].abs() <= 0.03),
                   ("Fell over 3% on disclosure day", b["disclosure_jump"] < -0.03)):
        if m.sum() >= 20:
            rows.append({"Disclosure day": lab, "Purchases": int(m.sum()), "Disclosure-day move vs SPY": b.loc[m, "disclosure_jump"].mean(),
                         "Next 3 months vs SPY": b.loc[m, "excess"].mean(), "Beat SPY (%)": (b.loc[m, "excess"] > 0).mean() * 100})
    return pd.DataFrame(rows)


def size_table(scored):
    b = scored[(scored["tx_type"] == "buy") & scored["closed"] & scored["market_cap"].notna()]
    rows = []
    for lab, lo, hi in (("Small (under $2B)", 0, 2e9), ("Mid ($2B-$10B)", 2e9, 1e10), ("Large ($10B-$200B)", 1e10, 2e11),
                        ("Mega (over $200B)", 2e11, 1e16)):
        m = (b["market_cap"] >= lo) & (b["market_cap"] < hi)
        if m.sum() >= 20:
            x = b.loc[m, "excess"]
            xs = b.loc[m, "excess_sector"].dropna()
            rows.append({"Company size": lab, "Purchases": int(m.sum()), "Avg vs SPY": x.mean(),
                         "Avg vs its industry": xs.mean() if len(xs) else np.nan, "Beat SPY (%)": (x > 0).mean() * 100})
    return pd.DataFrame(rows)


def excess_range(daily, bench):
    """Yearly return above the benchmark, with an approximate 95% range."""
    d = (daily - bench).dropna()
    if len(d) < 60:
        return np.nan, np.nan, np.nan
    # compound yearly return minus the benchmark's over the same days (the average daily gap overstates
    # the edge of a bumpier portfolio); the range uses the daily gaps' spread
    both = pd.concat([daily, bench], axis=1).dropna()
    yrs = len(both) / 252
    ann = float((1 + both.iloc[:, 0]).prod() ** (1 / yrs) - (1 + both.iloc[:, 1]).prod() ** (1 / yrs))
    se = _nw_se(d.values) * 252
    return ann, ann - 1.96 * se, ann + 1.96 * se


def defense_backtest(scored, px, cfg):
    sub = scored[scored["is_defense"].astype(bool)]
    if (sub["tx_type"] == "buy").sum() < 50:
        return None
    dcfg = dict(cfg, PICKS_PER_WEEK=max(1, cfg.get("DEFENSE_PICKS_PER_WEEK", 2)), _nested=True)
    bt = run_backtest(sub, px, dcfg)
    if not cfg.get("ENABLE_SHORTS"):
        bt["perf"] = bt["perf"].drop(columns=["Short picks"], errors="ignore")
        bt["curves"] = bt["curves"].drop(columns=["Short picks"], errors="ignore")
    if DEFENSE_ETF in px.columns:
        idx = bt["curves"].index
        ita = px[DEFENSE_ETF].pct_change(fill_method=None).reindex(idx)
        p, eq = _perf(ita)
        bt["perf"]["Defense fund (ITA)"] = pd.Series(p)
        bt["curves"]["Defense fund (ITA)"] = eq
    return bt


# ----------------------------------------------------------------------------
# Live DoD contract announcements (best effort: the site sometimes blocks automated requests)
# ----------------------------------------------------------------------------
def dod_announcements(cfg, days=30):
    cache, path = _cache_json(cfg, "dod_announcements.json", {"items": []})
    if _fresh(cache.get("fetched", ""), 0.5):
        return cache["items"]
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36", "Accept": "application/rss+xml,text/xml,*/*"}
    try:
        r = requests.get("https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=30",
                         headers=hdr, timeout=30)
        if r.status_code != 200:
            log(f"DoD announcements: not reachable today ({r.status_code}); using the contract database instead")
            return cache["items"]
        import xml.etree.ElementTree as ET
        from bs4 import BeautifulSoup
        items = []
        for it in ET.fromstring(r.content).iter("item"):
            date = pd.to_datetime(it.findtext("pubDate"), errors="coerce")
            text = BeautifulSoup(it.findtext("description") or "", "lxml").get_text(" ")
            link = it.findtext("link") or ""
            if len(text) < 500 and link:
                try:
                    page = requests.get(link, headers=hdr, timeout=30)
                    if page.status_code == 200:
                        soup = BeautifulSoup(page.text, "lxml")
                        body = soup.find("div", class_=re.compile("body|content", re.I)) or soup
                        text = body.get_text(" ")
                except Exception:
                    pass
            for para in re.split(r"\n\s*\n|(?<=\.)\s{2,}", text):
                m = re.match(r"\s*([A-Z][\w.,&'\- ]{2,80}?),\s+[A-Z][\w .'\-]+,\s+[A-Z][\w .]+,\s+(?:is|was|has been)\s+awarded\s+an?\s+"
                             r"(?:\w+[- ])*\$([\d,]+)", para)
                if m:
                    items.append([str(date.date()) if pd.notna(date) else "", company_key(m.group(1)),
                                  float(m.group(2).replace(",", "")), para.strip()[:300]])
        cache = {"fetched": dt.datetime.now().isoformat(), "items": (items + cache["items"])[:3000]}
        json.dump(cache, open(path, "w"))
        log(f"DoD announcements: {len(items)} awards in the latest announcements")
    except Exception as e:
        log(f"DoD announcements: skipped ({e})")
    return cache["items"]


# ----------------------------------------------------------------------------
# Prices for delisted stocks (Tiingo, free key; 50 requests an hour on the free plan)
# ----------------------------------------------------------------------------
def tiingo_prices(cfg, missing, start):
    key = (cfg.get("TIINGO_API_KEY") or "").strip()
    if not key or not missing:
        return pd.DataFrame()
    tried, tpath = _cache_json(cfg, "tiingo_tried.json", {})
    month = dt.date.today().strftime("%Y-%m")
    used = sum(1 for d in tried.values() if str(d).startswith(month) and not str(d).endswith("x"))
    room = max(0, min(int(cfg.get("TIINGO_PER_RUN", 45)), 480 - used))
    todo = [t for t in missing if _days_since(str(tried.get(t, "2000-01-01"))[:10]) >= 60]
    if not todo or not room:
        return pd.DataFrame()
    sup = _p(cfg, "cache", "tiingo_supported.csv")
    if not os.path.exists(sup) or (time.time() - os.path.getmtime(sup)) > 7 * 86400:
        try:
            r = requests.get("https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip", timeout=120)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            open(sup, "wb").write(z.read(z.namelist()[0]))
        except Exception as e:
            log(f"Delisted prices: Tiingo ticker list failed ({e})")
    try:
        st = pd.read_csv(sup, dtype=str)
        st["t"] = st["ticker"].str.upper().str.replace(".", "-", regex=False)
        st = st[st["exchange"].isin(["NYSE", "NASDAQ", "AMEX", "NYSE ARCA", "NYSE MKT", "BATS", "OTC", "PINK"]) |
                st["exchange"].isna()]
        st["endDate"] = pd.to_datetime(st["endDate"], errors="coerce")
    except Exception:
        st = pd.DataFrame(columns=["t", "ticker", "endDate", "startDate"])
    frames, today, picked = [], dt.date.today().isoformat(), []
    for t in todo:
        # a delisted ticker keeps its symbol until reused; reused ones get a digit suffix (e.g. ABC1)
        cand = st[(st["t"] == t) | st["t"].str.fullmatch(re.escape(t) + r"\d")]
        cand = cand[cand["endDate"].isna() | (cand["endDate"] >= pd.Timestamp(start))]
        if len(st) and cand.empty:
            tried[t] = today + "x"            # Tiingo doesn't have it: no API call spent
            continue
        picked.append((t, cand))
        if len(picked) >= room:
            break
    if not picked:
        json.dump(tried, open(tpath, "w"))
        return pd.DataFrame()
    log(f"Delisted prices: trying Tiingo for {len(picked)} stocks Yahoo doesn't have "
        f"({used + len(picked)} of 480 this month)")
    for t, cand in picked:
        tried[t] = today
        for sym in list(cand.sort_values("endDate")["ticker"]) or [t]:
            try:
                r = requests.get(f"https://api.tiingo.com/tiingo/daily/{sym}/prices",
                                 params={"startDate": start, "token": key, "resampleFreq": "daily"}, timeout=60)
                if r.status_code == 429:
                    log("Delisted prices: Tiingo hourly limit reached; the next run continues")
                    json.dump(tried, open(tpath, "w"))
                    return pd.concat(frames, axis=1) if frames else pd.DataFrame()
                js = r.json() if r.status_code == 200 else []
            except Exception:
                js = []
            if isinstance(js, list) and js:
                s = pd.Series({pd.Timestamp(x["date"][:10]): x.get("adjClose") for x in js}, name=t).dropna()
                if len(s) > 20:
                    frames.append(s.to_frame())
                    break
    json.dump(tried, open(tpath, "w"))
    got = pd.concat(frames, axis=1) if frames else pd.DataFrame()
    log(f"Delisted prices: found {got.shape[1]} of {len(picked)} on Tiingo")
    return got


def price_coverage(tx_all, px):
    """How many disclosed stock trades have no price data (mostly delisted companies)."""
    t = tx_all.dropna(subset=["ticker"])
    have = t["ticker"].isin(px.columns)
    miss = t.loc[~have, "ticker"].value_counts()
    return {"trades": int(len(t)), "with_prices": int(have.sum()), "missing": int((~have).sum()),
            "missing_share": float((~have).mean()) if len(t) else 0.0,
            "missing_tickers": int(len(miss)),
            "top_missing": [[k, int(v)] for k, v in miss.head(15).items()]}


# ----------------------------------------------------------------------------
# Members' religious affiliation (Pew Research Center, 119th Congress) - shown next to names only;
# deliberately NOT used in any score or analysis
# ----------------------------------------------------------------------------
PEW_URL = ("https://www.pewresearch.org/wp-content/uploads/sites/20/2024/12/"
           "pr_2025-01-02_faith-on-the-hill_member-list.pdf")
# rows look like "AL 7 Terri A. Sewell D Continuing Methodist" or "AK Senator Lisa Murkowski R Continuing Catholic"
PEW_ROW = re.compile(r"^([A-Z]{2})\s+(AL|\d{1,2}|Senator|Delegate|Resident Commissioner)\s+(.+?)\s+([RDI])\s+"
                     r"(Freshman|Continuing|Returning)\s+(.+?)\s*$")


def parse_pew_text(text):
    out = []
    for line in text.splitlines():
        m = PEW_ROW.match(line.strip())
        if m:
            st, dist, name, party, _status, rel = m.groups()
            rel = rel.strip().replace("Protestant unspecified", "Protestant")
            out.append({"state": st, "name": name.strip(), "chamber": "Senate" if dist == "Senator" else "House",
                        "religion": rel})
    return out


def load_religion(cfg):
    cache, path = _cache_json(cfg, "religion.json", {})
    if cache.get("rows") and _fresh(cache.get("fetched", ""), 30):
        return cache["rows"]
    try:
        import pdfplumber
        r = requests.get(PEW_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}, timeout=60)
        r.raise_for_status()
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            rows = parse_pew_text("\n".join((p.extract_text() or "") for p in pdf.pages))
        if len(rows) > 300:
            cache = {"fetched": dt.datetime.now().isoformat(), "rows": rows}
            json.dump(cache, open(path, "w"))
            log(f"Religious affiliation: {len(rows)} members from Pew Research Center")
        else:
            log(f"Religious affiliation: couldn't read the Pew list ({len(rows)} rows)")
    except Exception as e:
        log(f"Religious affiliation: download failed ({e})")
    return cache.get("rows", [])


def religion_lookup(rows):
    idx = {}
    for r in rows:
        parts = [p for p in name_key(r["name"]).split(" ") if p]
        if parts:                                   # index "drew" and "van drew" so two-word last names match
            for k in {parts[-1], " ".join(parts[-2:])}:
                idx.setdefault((r["chamber"], k), []).append(r)
    return idx


def religion_for(idx, chamber, last_key, first, state):
    lk = name_key(last_key or "")
    c = idx.get((chamber, lk), []) or idx.get((chamber, lk.split(" ")[-1] if lk else ""), [])
    if len(c) > 1 and state:
        c = [r for r in c if r["state"] == state] or c
    if len(c) > 1 and first:
        f = name_key(first)[:3]
        c = [r for r in c if name_key(r["name"]).startswith(f)] or c
    return c[0]["religion"] if len(c) == 1 else None


# ############################################################################
#  RELATIONSHIPS (v5): grants and loans, campaign vendors, yearly-disclosure details,
#  trading partners, bill momentum
# ############################################################################
ASSIST_GROUPS = {"grant": ["02", "03", "04", "05"], "loan": ["07", "08"], "payment": ["06", "10"]}


def collect_assistance(cfg, meta, tickers):
    """Federal grants, loans and direct payments to each company (CHIPS grants, energy loans, subsidies...)."""
    cache, path = _cache_json(cfg, "assistance.json", {})
    start = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    today = dt.date.today().isoformat()
    todo = [t for t in tickers if not _fresh(cache.get(t, {}).get("fetched", ""), 14)
            and company_key((meta.get(t) or {}).get("name"))]
    if todo:
        log(f"Grants and loans: checking {len(todo)} companies on USAspending.gov")

    def work(t):
        key = company_key(meta[t]["name"])
        rows = []
        for kind, codes in ASSIST_GROUPS.items():
            body = {"filters": {"recipient_search_text": [key], "award_type_codes": codes,
                                "time_period": [{"start_date": start, "end_date": today}]},
                    "fields": ["Action Date", "Transaction Amount", "Recipient Name", "Awarding Agency"],
                    "sort": "Action Date", "order": "desc", "limit": 100, "page": 1}
            try:
                r = requests.post("https://api.usaspending.gov/api/v2/search/spending_by_transaction/",
                                  json=body, timeout=60)
                if r.status_code != 200:
                    continue
                res = r.json().get("results", [])
            except Exception:
                return t, None
            for x in res:
                rn = company_key(x.get("Recipient Name"))
                if (rn == key or rn.startswith(key + " ")) and x.get("Action Date"):
                    rows.append([x["Action Date"], float(x.get("Transaction Amount") or 0), x.get("Awarding Agency") or "", kind])
        return t, rows

    for i, (t, rows) in enumerate(chunked_map(work, todo, 4, cfg), 1):
        if rows is not None:
            cache[t] = {"fetched": dt.datetime.now().isoformat(), "rows": rows}
        if i % 100 == 0:
            json.dump(cache, open(path, "w"))
            log(f"Grants and loans: {i}/{len(todo)}")
    json.dump(cache, open(path, "w"))
    out = [{"ticker": t, "action_date": r[0], "amount": r[1], "agency": r[2], "kind": r[3]}
           for t, v in cache.items() for r in v.get("rows", [])]
    a = pd.DataFrame(out, columns=["ticker", "action_date", "amount", "agency", "kind"])
    a["action_date"] = pd.to_datetime(a["action_date"], errors="coerce")
    a["known_date"] = a["action_date"] + pd.Timedelta(days=30)       # agencies report to USAspending within ~30 days
    log(f"Grants and loans: {a['ticker'].nunique()} companies with federal assistance")
    return a


OPPEXP_COLS = ["CMTE_ID", "AMNDT_IND", "RPT_YR", "RPT_TP", "IMAGE_NUM", "LINE_NUM", "FORM_TP_CD", "SCHED_TP_CD",
               "NAME", "CITY", "STATE", "ZIP_CODE", "TRANSACTION_DT", "TRANSACTION_AMT", "TRANSACTION_PGI", "PURPOSE",
               "CATEGORY", "CATEGORY_DESC", "MEMO_CD", "MEMO_TEXT", "ENTITY_TP", "SUB_ID", "FILE_NUM", "TRAN_ID",
               "BACK_REF_TRAN_ID"]


def _fec_date(s):
    s = s.fillna("").astype(str).str.strip()
    d = pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")
    return d.fillna(pd.to_datetime(s, format="%m%d%Y", errors="coerce"))


def collect_campaign_vendors(cfg, fec_ids):
    """Companies each trading member's campaign paid (FEC operating expenditures, bulk files)."""
    cache = _p(cfg, "cache", "campaign_vendors.pkl")
    df = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame(
        columns=["cand", "payee", "date", "amount", "purpose"])
    state, spath = _cache_json(cfg, "campaign_vendor_cycles_v2.json", {})
    principal = load_principal_committees(cfg)
    cm2cand = {c: cand for cand, cs in principal.items() if not cand.startswith("_") and cand in fec_ids for c in cs}
    if not cm2cand:
        return df
    y0 = pd.Timestamp(cfg["START_DATE"]).year - 2
    this_cycle = dt.date.today().year + (dt.date.today().year % 2)
    for cyc in range(y0 + (y0 % 2), this_cycle + 1, 2):
        done = state.get(str(cyc))
        if done and (cyc < this_cycle - 1 or _fresh(done, 7)):
            continue
        if out_of_time(cfg, 75):
            break
        url = f"https://www.fec.gov/files/bulk-downloads/{cyc}/oppexp{str(cyc)[2:]}.zip"
        tmp = _p(cfg, "cache", f"oppexp{cyc}.zip")
        try:
            with requests.get(url, headers=UA, timeout=600, stream=True) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            z = zipfile.ZipFile(tmp)
            name = [n for n in z.namelist() if n.lower().endswith(".txt")][0]
            keep = []
            pos = [OPPEXP_COLS.index(c) for c in ("CMTE_ID", "NAME", "TRANSACTION_DT", "TRANSACTION_AMT", "PURPOSE", "ENTITY_TP")]
            for ch in pd.read_csv(z.open(name), sep="|", header=None, usecols=pos, dtype=str, quoting=3,
                                  encoding="latin-1", on_bad_lines="skip", chunksize=500_000):
                ch.columns = [OPPEXP_COLS[p] for p in sorted(pos)]
                ch = ch[ch["CMTE_ID"].isin(cm2cand)]
                if len(ch):
                    keep.append(ch)
            if keep:
                k = pd.concat(keep, ignore_index=True)
                k = k[~k["ENTITY_TP"].fillna("").isin(["IND", "CAN"])]         # companies, not people
                new = pd.DataFrame({"cand": k["CMTE_ID"].map(cm2cand), "payee": k["NAME"].map(company_key),
                                    "date": _fec_date(k["TRANSACTION_DT"]),
                                    "amount": pd.to_numeric(k["TRANSACTION_AMT"], errors="coerce"),
                                    "purpose": k["PURPOSE"].fillna("").str.slice(0, 60)})
                new = new.dropna(subset=["date"])
                new = new[new["payee"].str.len() >= 3]
                lo, hi = pd.Timestamp(cyc - 1, 1, 1), pd.Timestamp(cyc, 12, 31)
                df = df[~((df["date"] >= lo) & (df["date"] <= hi))] if len(df) else df
                df = pd.concat([df, new], ignore_index=True)
                df.to_pickle(cache)
            state[str(cyc)] = dt.datetime.now().isoformat()
            json.dump(state, open(spath, "w"))
            log(f"Campaign spending: {cyc} cycle loaded ({sum(len(x) for x in keep):,} payments by trading members, "
                f"{len(new) if keep else 0:,} to companies)")
        except Exception as e:
            log(f"Campaign spending: {cyc} failed ({e})")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return df


def _congress_of(year):
    return (year - 1789) // 2 + 1


def _billstatus_zip_urls(cong, typ):
    """govinfo's directory listing says where the zip for a Congress and bill type lives; common names as a fallback."""
    base = f"https://www.govinfo.gov/bulkdata/BILLSTATUS/{cong}/{typ}"
    urls = []
    try:
        js = requests.get(f"https://www.govinfo.gov/bulkdata/json/BILLSTATUS/{cong}/{typ}",
                          headers={**UA, "Accept": "application/json"}, timeout=60).json()
        for f in js.get("files", []):
            link = f.get("link") or ""
            if link.lower().endswith(".zip"):
                urls.append(link)
    except Exception:
        pass
    return urls + [f"{base}/BILLSTATUS-{cong}-{typ}.zip", f"{base}/BILLSTATUS-{cong}{typ}.zip"]


def collect_bill_status(cfg):
    """Every bill's policy area, sponsors, cosponsors and major actions (GovInfo BILLSTATUS bulk data)."""
    import xml.etree.ElementTree as ET
    cache = _p(cfg, "cache", "bill_actions.pkl")
    df = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame(
        columns=["bill", "policy", "title", "date", "kind", "backers"])
    state, spath = _cache_json(cfg, "bill_status_done.json", {})
    first = _congress_of(pd.Timestamp(cfg["START_DATE"]).year - 1)
    cur = _congress_of(dt.date.today().year)
    for cong in range(first, cur + 1):
        for typ in ("hr", "s", "hjres", "sjres"):
            key = f"{cong}{typ}-v2"                      # v2: backers carry the date they joined
            if state.get(key) and (cong < cur or _fresh(state[key], 3)):
                continue
            if out_of_time(cfg, 75):
                break
            try:
                r = None
                for url in _billstatus_zip_urls(cong, typ):
                    r = requests.get(url, headers=UA, timeout=600)
                    if r.status_code == 200 and r.content[:2] == b"PK":
                        break
                    r = None
                if r is None:
                    raise RuntimeError("no zip found on govinfo")
                z = zipfile.ZipFile(io.BytesIO(r.content))
                rows = []
                for n in z.namelist():
                    if not n.lower().endswith(".xml"):
                        continue
                    try:
                        root = ET.fromstring(z.read(n))
                    except Exception:
                        continue
                    b = root.find(".//bill")
                    if b is None:
                        continue
                    pol = b.findtext("policyArea/name") or ""
                    title = (b.findtext("title") or "")[:140]
                    # who backs the bill and since when (a cosponsor who joined later, or withdrew, must not count
                    # for actions before they joined; that would be peeking)
                    intro = b.findtext("introducedDate") or ""
                    backers = {e.findtext("bioguideId"): intro for e in b.findall("sponsors/item") if e.findtext("bioguideId")}
                    for e in b.findall("cosponsors/item"):
                        bid = e.findtext("bioguideId")
                        if bid and not (e.findtext("sponsorshipWithdrawnDate") or "").strip():
                            backers.setdefault(bid, (e.findtext("sponsorshipDate") or intro)[:10])
                    num = b.findtext("number") or b.findtext("billNumber") or ""
                    seen = set()
                    for a in b.findall("actions/item"):
                        txt = (a.findtext("text") or "").lower()
                        d = a.findtext("actionDate")
                        atype = (a.findtext("type") or "").lower()
                        if atype == "becamelaw" or "became public law" in txt or "signed by president" in txt:
                            kind = "became law"
                        elif txt.startswith("passed ") or "on passage passed" in txt or "passed/agreed to" in txt:
                            kind = "passed a chamber"
                        elif "reported" in txt and ("committee" in txt or atype in ("committee", "calendars")):
                            kind = "cleared committee"
                        else:
                            continue
                        if d and (kind, d) not in seen:
                            seen.add((kind, d))
                            rows.append({"bill": f"{cong}-{typ}{num}", "policy": pol, "title": title, "date": d,
                                         "kind": kind, "backers": backers})
                new = pd.DataFrame(rows, columns=["bill", "policy", "title", "date", "kind", "backers"])
                new["date"] = pd.to_datetime(new["date"], errors="coerce")
                pre = f"{cong}-{typ}"
                df = df[~df["bill"].astype(str).str.startswith(pre)] if len(df) else df
                df = pd.concat([df, new], ignore_index=True)
                df.to_pickle(cache)
                state[key] = dt.datetime.now().isoformat()
                json.dump(state, open(spath, "w"))
                log(f"Bill progress: {cong}th Congress {typ.upper()}: {len(new):,} major actions")
            except Exception as e:
                log(f"Bill progress: {key} failed ({e})")
    return df


FD_SCHEDULE = re.compile(r"^\s*s(?:chedule)?\s+([a-j])\s*:")


def _fd_kinds(text_lower, ck):
    """Where a company appears in a yearly disclosure: spouse's job, the member's income, an outside position,
    a gift, sponsored travel, or elsewhere."""
    kinds, sched = set(), ""
    for ln in text_lower.splitlines():
        m = FD_SCHEDULE.search(ln)
        if m:
            sched = m.group(1)
        if not _name_hit(ln, ck):
            continue
        spouse = re.search(r"\bspouse\b|\bsp\b", ln) is not None
        if sched == "c" and spouse or (spouse and re.search(r"salary|income|employ|compensation|wages|bonus", ln)):
            kinds.add("spouse_job")
        elif sched == "e" or re.search(r"\b(director|board|trustee|officer|partner|advisor)\b", ln) and sched not in ("a", "b"):
            kinds.add("position")
        elif sched == "h" or re.search(r"\btravel|\btrip\b|lodging|airfare|itinerary", ln):
            kinds.add("travel")
        elif sched == "g" or re.search(r"\bgift", ln):
            kinds.add("gift")
        else:
            kinds.add("other")
    return kinds


def trading_pairs(tx, window_days=7, popular_top=60):
    """Pairs of members who keep trading the same stocks the same way within a week of each other.
    Very widely traded stocks (the most-traded 60) are left out, so pairs aren't just two people buying Apple."""
    d = tx[[c for c in ("member", "chamber", "last_key", "who", "ticker", "tx_type", "trade_date", "filed_date")
            if c in tx]].dropna(subset=["trade_date", "filed_date"]).copy()
    if "who" not in d:
        d["who"] = d["chamber"] + "|" + d["last_key"]
    popular = set(d["ticker"].value_counts().head(popular_top).index)
    d = d[~d["ticker"].isin(popular)].drop_duplicates(["who", "ticker", "tx_type", "trade_date"])
    names = d.groupby("who")["member"].agg(lambda s: s.mode().iloc[0]).to_dict()
    ev = []
    for (t, typ), g in d.groupby(["ticker", "tx_type"]):
        if g["who"].nunique() < 2:
            continue
        g = g.sort_values("trade_date")
        arr = list(zip(g["who"], g["trade_date"], g["filed_date"]))
        for i in range(len(arr)):
            j = i + 1
            while j < len(arr) and (arr[j][1] - arr[i][1]).days <= window_days:
                if arr[j][0] != arr[i][0]:
                    a, b = arr[i], arr[j]
                    first = a[0] if a[1] < b[1] else (b[0] if b[1] < a[1] else None)
                    ev.append((tuple(sorted((a[0], b[0]))), t, typ, max(a[2], b[2]), first, abs((b[1] - a[1]).days)))
                j += 1
    ev = pd.DataFrame(ev, columns=["pair", "ticker", "typ", "known", "first", "gap"])
    return ev, names


def relationship_features(tx, data, cfg, mems):
    D = pd.Timedelta
    n = len(tx)
    fdt, tdt = tx["filed_date"], tx["trade_date"]
    tx["f_spouse_insider"], tx["spouse_note"] = 0.0, ""
    st_ = data.get("spouse_ties")
    if st_ is not None and len(st_):
        ok = st_[st_["confidence"] == "Confirmed by state"]
        tie = {(b, t): (f0, sp, role) for b, t, f0, sp, role in zip(ok["bioguide"], ok["ticker"], ok["first_filed"], ok["spouse"], ok["role"])}
        fs, nt = np.zeros(n), [""] * n
        for i, (m, t, f) in enumerate(zip(mems, tx["ticker"], fdt)):
            h = tie.get(((m or {}).get("bioguide"), t))
            if h and h[0] <= f:
                fs[i], nt[i] = 1.0, f"spouse {h[1]} is a company insider ({h[2].lower()}) per SEC filings"
        tx["f_spouse_insider"], tx["spouse_note"] = fs, nt
    tx["f_relative_tie"], tx["relative_note"] = 0.0, ""
    rt_ = data.get("relative_ties")
    if rt_ is not None and len(rt_):
        ok = rt_[rt_["confidence"] == "Confirmed by state"].dropna(subset=["first_filed"])
        tie = {}
        for b, t, f0, nm, rel, role in zip(ok["bioguide"], ok["ticker"], ok["first_filed"], ok["relative"],
                                           ok["relation"], ok["role"]):
            if (b, t) not in tie or f0 < tie[(b, t)][0]:
                tie[(b, t)] = (f0, nm, rel, role)
        fr, nr = np.zeros(n), [""] * n
        for i, (m, t, f) in enumerate(zip(mems, tx["ticker"], fdt)):
            h = tie.get(((m or {}).get("bioguide"), t))
            if h and h[0] <= f:
                fr[i], nr[i] = 1.0, f"{h[2]} {h[1]} is a company insider ({str(h[3]).lower()}) per SEC filings"
        tx["f_relative_tie"], tx["relative_note"] = fr, nr
    for c in ("f_grants", "f_grant_after_trade", "f_campaign_vendor", "f_spouse_employer", "f_paid_travel",
              "f_outside_position", "f_buddy", "f_leader", "f_bill_advanced", "f_sector_bill_momentum"):
        tx[c] = 0.0
    tx["grants_12m"], tx["vendor_paid"], tx["buddy_note"], tx["bill_adv_note"] = 0.0, 0.0, "", ""
    meta = data.get("meta") or {}
    tx["_ck"] = tx["ticker"].map({t: company_key((meta.get(t) or {}).get("name")) for t in tx["ticker"].unique()})

    # grants, loans and direct payments
    a = data.get("assistance")
    if a is not None and len(a):
        ag = {k: g for k, g in a.groupby("ticker")}
        g12, fg, fa = np.zeros(n), np.zeros(n), np.zeros(n)
        for i, (t, f, td) in enumerate(zip(tx["ticker"], fdt, tdt)):
            g = ag.get(t)
            if g is None:
                continue
            k = g[(g.known_date <= f) & (g.known_date > f - D(days=365))]
            g12[i] = k["amount"].clip(lower=0).sum()
            fg[i] = 1.0 if (g12[i] >= 1e6 or (k["kind"] == "loan").any()) else (0.5 if len(k) else 0.0)
            after = g[(g.action_date > td) & (g.known_date <= f)]           # public by disclosure day
            fa[i] = 1.0 if len(after) and (after["amount"].sum() >= 1e6 or (after["kind"] == "loan").any()) else 0.0
        tx["grants_12m"], tx["f_grants"], tx["f_grant_after_trade"] = g12, fg, fa

    # the member's campaign paid the company (routine vendors most campaigns use are ignored)
    cv = data.get("campaign_vendors")
    if cv is not None and len(cv):
        if cv["cand"].nunique() >= 20:                       # airlines, card companies, ad platforms: everyone pays them
            share = cv.groupby("payee")["cand"].nunique() / cv["cand"].nunique()
            cv = cv[cv["payee"].map(share) < 0.2]
        payees = set(cv["payee"])
        byk = {k: g for k, g in cv.groupby(["cand", "payee"])}
        fec_of = [set(m.get("fec") or []) if m else set() for m in mems]
        ck_map = {}
        for ck in tx["_ck"].dropna().unique():
            if ck and len(ck) >= 4:
                ck_map[ck] = {p for p in payees if p == ck or p.startswith(ck + " ")}
        paid, f = np.zeros(n), np.zeros(n)
        for i, (fids, ck, fd) in enumerate(zip(fec_of, tx["_ck"], fdt)):
            if not fids or not ck or not ck_map.get(ck):
                continue
            tot = 0.0
            for c in fids:
                for p in ck_map[ck]:
                    g = byk.get((c, p))
                    if g is not None:
                        m = (g["date"] + D(days=105) <= fd) & (g["date"] > fd - D(days=730))
                        tot += g.loc[m, "amount"].sum()
            paid[i] = tot
            f[i] = 1.0 if tot > 0 else 0.0
        tx["vendor_paid"], tx["f_campaign_vendor"] = paid, f

    # yearly disclosures, by schedule: spouse's employer, sponsored travel, outside positions
    fd = data["annual_fd"] = fd_member_ids(data.get("annual_fd"), data.get("legislators") or {})
    if fd is not None and len(fd):
        fdg = {k: g.sort_values("filed") for k, g in fd.groupby("who")}
        memo, texts = {}, {}
        sj, tr, po = np.zeros(n), np.zeros(n), np.zeros(n)
        for i, (wk, f, ck) in enumerate(zip(tx["who"], fdt, tx["_ck"])):
            if not ck or len(ck) < 4:
                continue
            g = fdg.get(wk)
            if g is None:
                continue
            w = g[(g.filed <= f) & (g.filed > f - D(days=1100))]
            kinds = set()
            for doc, blob in zip(w.doc, w.text):
                key = (doc, ck)
                if key not in memo:
                    if doc not in texts:
                        texts[doc] = _unz(blob).lower()
                    memo[key] = _fd_kinds(texts[doc], ck) if _name_hit(texts[doc], ck) else set()
                kinds |= memo[key]
            sj[i], tr[i], po[i] = float("spouse_job" in kinds), float("travel" in kinds), float("position" in kinds)
        tx["f_spouse_employer"], tx["f_paid_travel"], tx["f_outside_position"] = sj, tr, po

    # trading partners: pairs who keep making the same trades within a week (known only once both are filed)
    ev, names = trading_pairs(tx)
    data["_pairs"] = (ev, names)
    if len(ev):
        pair_first = {}
        for p, g in ev.sort_values("known").groupby("pair"):
            g = g.drop_duplicates("ticker")
            pair_first[p] = list(g["known"])                     # when each distinct shared stock became known
        whoS = tx["who"]
        who = whoS.values
        partners = {}
        for p in pair_first:
            for x in p:
                partners.setdefault(x, []).append(p)
        lead = {}
        for x in partners:
            e = ev[ev["pair"].map(lambda p: x in p)].sort_values("known")
            lead[x] = (e["known"].values, (e["first"] == x).values.astype(float), e["first"].notna().values.astype(float))
        buys = tx[tx["tx_type"] == "buy"]
        bt_idx = {k: g for k, g in buys.assign(_w=whoS).groupby("ticker")}
        fb, fl, note = np.zeros(n), np.zeros(n), [""] * n
        for i in np.where(tx["tx_type"].values == "buy")[0]:
            me, t, f, td = who[i], tx["ticker"].iat[i], fdt.iat[i], tdt.iat[i]
            strong = {q for q in partners.get(me, []) if sum(k < f for k in pair_first[q]) >= 3}
            if strong:
                g = bt_idx.get(t)
                if g is not None:
                    others = g[(g["_w"] != me) & (g["filed_date"] <= f) & ((g["trade_date"] - td).abs() <= D(days=30))]
                    hit = [w_ for w_ in others["_w"].unique() if tuple(sorted((me, w_))) in strong]
                    if hit:
                        fb[i] = 1.0
                        note[i] = "frequent trading partner " + ", ".join(names.get(h, h) for h in hit[:2]) + " also bought"
            L = lead.get(me)
            if L is not None:
                k = L[0] < np.datetime64(f)
                decided = L[2][k].sum()
                if decided >= 5 and L[1][k].sum() / decided >= 0.6:
                    fl[i] = 1.0
        tx["f_buddy"], tx["f_leader"], tx["buddy_note"] = fb, fl, note

    # bill momentum
    ba = data.get("bill_actions")
    if ba is not None and len(ba):
        ba = ba.dropna(subset=["date"]).assign(sectors=lambda x: x["policy"].map(lambda p: set(POLICY_SECTORS.get(p, []))))
        ba = ba[ba["sectors"].map(len) > 0]
        by_bs = {}                                     # (member, sector) -> actions sorted by date
        for r in ba.sort_values("date").itertuples():
            for b in r.backers:
                if isinstance(r.backers, dict):          # only backers who had joined by the time of the action
                    j = pd.to_datetime(r.backers[b], errors="coerce")
                    if pd.isna(j) or j > r.date:
                        continue
                for s_ in r.sectors:
                    by_bs.setdefault((b, s_), []).append((r.date, r.policy, r.kind))
        dates_bs = {k: np.array([x[0] for x in v], dtype="datetime64[ns]") for k, v in by_bs.items()}
        bios = [m.get("bioguide") if m else None for m in mems]
        adv, mom, note = np.zeros(n), np.zeros(n), [""] * n
        passed = ba[ba["kind"] != "cleared committee"]
        ps = {s: g for s, g in passed.explode("sectors").groupby("sectors")} if len(passed) else {}
        for i, (b, s, td, f) in enumerate(zip(bios, tx["sector"], tdt, fdt)):
            if not s:
                continue
            arr = dates_bs.get((b, s))
            if arr is not None and pd.notna(td):
                j = np.searchsorted(arr, np.datetime64(td), side="right")
                if j < len(arr) and arr[j] <= np.datetime64(f):
                    d_, pol, kind = by_bs[(b, s)][j]
                    adv[i] = 1.0
                    note[i] = f"a {pol} bill they back {kind} after the trade"
            g = ps.get(s)
            if g is not None and ((g["date"] <= f) & (g["date"] > f - D(days=45))).any():
                mom[i] = 1.0
        tx["f_bill_advanced"], tx["f_sector_bill_momentum"], tx["bill_adv_note"] = adv, mom, note
    return tx.drop(columns=["_ck"])


def pairs_table(ev, names, top=15):
    if ev is None or not len(ev):
        return []
    out = []
    for p, g in ev.groupby("pair"):
        k = g["ticker"].nunique()
        if k < 3:
            continue
        firsts = g["first"].dropna()
        lead = firsts.value_counts()
        leader = names.get(lead.index[0], lead.index[0]) if len(lead) and lead.iloc[0] / max(len(firsts), 1) >= 0.6 else "Neither"
        out.append({"Members": " & ".join(names.get(x, x) for x in p), "Shared stocks": int(k),
                    "Usually first": leader, "Typical gap (days)": float(g["gap"].median()),
                    "Last shared trade": str(pd.Timestamp(g["known"].max()).date())})
    out.sort(key=lambda r: -r["Shared stocks"])
    return out[:top]


# ----------------------------------------------------------------------------
# Spouses who are company insiders (Wikidata spouse names x SEC Forms 3/4/5 filers)
# ----------------------------------------------------------------------------
WIKIDATA_SPOUSES = """SELECT ?bioguide ?spouseLabel WHERE {
  ?p wdt:P1157 ?bioguide . ?p wdt:P26 ?spouse .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }"""


def collect_spouses(cfg):
    """Spouse names for members of Congress, from Wikidata (bioguide ID -> names)."""
    cache, path = _cache_json(cfg, "spouses.json", {})
    if cache.get("rows") and _fresh(cache.get("fetched", ""), 30):
        return cache["rows"]
    try:
        r = requests.get("https://query.wikidata.org/sparql", params={"query": WIKIDATA_SPOUSES, "format": "json"},
                         headers={"User-Agent": "congress-signals research tool (github.com/e36meister/congress-signals)",
                                  "Accept": "application/sparql-results+json"}, timeout=180)
        r.raise_for_status()
        rows = {}
        for b in r.json()["results"]["bindings"]:
            bio, sp = b["bioguide"]["value"], b["spouseLabel"]["value"]
            if re.fullmatch(r"Q\d+", sp):
                continue
            rows.setdefault(bio, [])
            if sp not in rows[bio]:
                rows[bio].append(sp)
        cache = {"fetched": dt.datetime.now().isoformat(), "rows": rows}
        json.dump(cache, open(path, "w"))
        log(f"Spouses: names for {len(rows):,} current and former members (Wikidata)")
    except Exception as e:
        log(f"Spouses: Wikidata lookup failed ({e})")
    return cache.get("rows", {})


# Relatives with their own Wikidata entry (Wikidata lists notable people): children, siblings, parents and other
# relatives such as in-laws. Only public figures are covered; private family members are not looked up.
WIKIDATA_RELATIVES = """SELECT ?bioguide ?relLabel ?kind ?kinLabel WHERE {
  ?p wdt:P1157 ?bioguide .
  { ?p wdt:P40 ?rel . BIND("child" AS ?kind) } UNION { ?rel wdt:P22 ?p . BIND("child" AS ?kind) } UNION
  { ?rel wdt:P25 ?p . BIND("child" AS ?kind) } UNION { ?p wdt:P3373 ?rel . BIND("sibling" AS ?kind) } UNION
  { ?rel wdt:P3373 ?p . BIND("sibling" AS ?kind) } UNION { ?p wdt:P22 ?rel . BIND("parent" AS ?kind) } UNION
  { ?p wdt:P25 ?rel . BIND("parent" AS ?kind) } UNION
  { ?p p:P1038 ?st . ?st ps:P1038 ?rel . OPTIONAL { ?st pq:P1039 ?kin } BIND("relative" AS ?kind) }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }"""


def collect_relatives(cfg):
    """Public-figure relatives of members, from Wikidata: bioguide ID -> [[name, relation], ...]."""
    cache, path = _cache_json(cfg, "relatives.json", {})
    if cache.get("rows") and _fresh(cache.get("fetched", ""), 30):
        return cache["rows"]
    try:
        r = requests.get("https://query.wikidata.org/sparql", params={"query": WIKIDATA_RELATIVES, "format": "json"},
                         headers={"User-Agent": "congress-signals research tool (github.com/e36meister/congress-signals)",
                                  "Accept": "application/sparql-results+json"}, timeout=180)
        r.raise_for_status()
        rows = {}
        for b in r.json()["results"]["bindings"]:
            bio, nm, kind = b["bioguide"]["value"], b["relLabel"]["value"], b["kind"]["value"]
            kin = (b.get("kinLabel") or {}).get("value")
            if re.fullmatch(r"Q\d+", nm):
                continue
            rel = kin if kind == "relative" and kin and not re.fullmatch(r"Q\d+", kin) else kind
            lst = rows.setdefault(bio, [])
            if all(x[0] != nm for x in lst):
                lst.append([nm, rel])
        cache = {"fetched": dt.datetime.now().isoformat(), "rows": rows}
        json.dump(cache, open(path, "w"))
        log(f"Relatives: {sum(len(v) for v in rows.values()):,} public-figure relatives of {len(rows):,} members (Wikidata)")
    except Exception as e:
        log(f"Relatives: Wikidata lookup failed ({e})")
    return cache.get("rows", {})


def relative_lobbyist_ties(relatives, lobbyists, members):
    """Relatives whose name matches a lobbyist on a company's lobbying filings. A name match alone isn't proof,
    so these are always shown as 'Needs review' and never scored."""
    rows = []
    if lobbyists is None or not len(lobbyists):
        return pd.DataFrame(rows)
    by_name = {}
    for t, d, names in zip(lobbyists["ticker"], lobbyists["posted"], lobbyists["lobbyists"]):
        for nm in names or []:
            p = name_key(nm).split()
            if len(p) >= 2:
                by_name.setdefault((p[0], p[-1]), []).append((t, d))
    for bio, rels in relatives.items():
        m = members.get(bio)
        if not m:
            continue
        for nm, rel in rels:
            p = name_key(nm).split()
            if len(p) < 2:
                continue
            hits = by_name.get((p[0], p[-1]), [])
            for t in sorted({h[0] for h in hits}):
                f0 = min((h[1] for h in hits if h[0] == t and pd.notna(h[1])), default=pd.NaT)
                rows.append({"bioguide": bio, "member": m.get("name"), "relative": nm, "relation": rel, "ticker": t,
                             "issuer": t, "role": "Registered lobbyist for the company", "first_filed": f0,
                             "owner_cik": "", "confidence": "Needs review", "source": "Lobbying disclosures"})
    return pd.DataFrame(rows)


def _sec_owner_parts(name):
    """SEC filer names are written 'LAST FIRST MIDDLE'."""
    p = name_key(name or "").split()
    return (p[0], p[1]) if len(p) >= 2 else (None, None)


def collect_spouse_insiders(cfg, spouses, prefix="spouse"):
    """Every SEC insider filing (Forms 3/4/5) by someone whose first and last name match one of the given names
    (members' spouses, or with prefix="relative", their public-figure relatives)."""
    hdr = _sec_headers(cfg)
    cache = _p(cfg, "cache", f"{prefix}_insiders.pkl")
    cols = ["owner_cik", "owner_name", "last", "first", "state", "role", "ticker", "issuer", "filed"]
    df = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame(columns=cols)
    done, dpath = _cache_json(cfg, f"{prefix}_insider_quarters.json", [])
    want = {}
    for names in spouses.values():
        for nm in names:
            p = name_key(nm).split()
            if len(p) >= 2:
                want.setdefault(p[-1], set()).add(p[0])
    if not hdr or not want:
        return df
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=365)
    today = pd.Timestamp.today()
    for y in range(start.year, today.year + 1):
        for q in range(1, 5):
            key = f"{y}q{q}"
            if key in done or pd.Timestamp(y, 3 * q - 2, 1) > today or pd.Timestamp(y, 3 * q, 1) < start:
                continue
            if out_of_time(cfg, 75):
                break
            try:
                r = requests.get(f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
                                 f"{key}_form345.zip", headers=hdr, timeout=180)
                if r.status_code != 200:
                    continue
                z = zipfile.ZipFile(io.BytesIO(r.content))
                own = _read_tsv(z, "REPORTINGOWNER.TSV", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                                                           "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_STATE"])
                if "RPTOWNERNAME" not in own:
                    continue
                lf = own["RPTOWNERNAME"].map(_sec_owner_parts)
                own["last"], own["first"] = lf.str[0], lf.str[1]
                own = own[[l in want and f in want[l] for l, f in zip(own["last"], own["first"])]]
                if len(own):
                    sub = _read_tsv(z, "SUBMISSION.TSV", ["ACCESSION_NUMBER", "FILING_DATE", "ISSUERTRADINGSYMBOL", "ISSUERNAME"])
                    m = own.merge(sub, on="ACCESSION_NUMBER", how="left")
                    new = pd.DataFrame({
                        "owner_cik": m["RPTOWNERCIK"], "owner_name": m["RPTOWNERNAME"], "last": m["last"],
                        "first": m["first"], "state": m.get("RPTOWNER_STATE"),
                        "role": (m.get("RPTOWNER_RELATIONSHIP", pd.Series("", index=m.index)).fillna("") + " " +
                                 m.get("RPTOWNER_TITLE", pd.Series("", index=m.index)).fillna("")).str.strip(),
                        "ticker": m["ISSUERTRADINGSYMBOL"].map(clean_ticker), "issuer": m.get("ISSUERNAME"),
                        "filed": pd.to_datetime(m["FILING_DATE"], format="%d-%b-%Y", errors="coerce")})
                    df = pd.concat([df, new], ignore_index=True)
                if pd.Timestamp(y, 3 * q, 1) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=45) < today:
                    done.append(key)
                df.to_pickle(cache)
                json.dump(done, open(dpath, "w"))
                time.sleep(0.2)
            except Exception as e:
                log(f"{prefix.title()} insiders: {key} failed ({e})")
    log(f"{prefix.title()} insiders: {len(df):,} SEC filings by people named like a member's {prefix}")
    return df


def spouse_ties(spouses, ins, members):
    """Match members' spouses to SEC insiders. 'Confirmed by state' = same first and last name AND the filer's
    address is in the member's state, and the name isn't shared by several different filers there.
    Everything else is 'Needs review' and is shown but not scored."""
    if ins is None or not len(ins) or not spouses:
        return pd.DataFrame(columns=["bioguide", "member", "spouse", "ticker", "issuer", "role", "first_filed",
                                     "owner_cik", "confidence"])
    ins = ins.dropna(subset=["ticker", "filed"])
    rows = []
    for bio, names in spouses.items():
        m = members.get(bio)
        if not m:
            continue
        for nm in names:
            p = name_key(nm).split()
            if len(p) < 2:
                continue
            hit = ins[(ins["last"] == p[-1]) & (ins["first"] == p[0])]
            if not len(hit):
                continue
            same_state = hit[hit["state"].fillna("") == (m.get("state") or "")]
            ambiguous = same_state["owner_cik"].nunique() > 1
            for (cik, t), g in hit.groupby(["owner_cik", "ticker"]):
                ok = (g["state"].fillna("") == (m.get("state") or "")).any() and not ambiguous
                rows.append({"bioguide": bio, "member": m.get("name"), "spouse": nm, "ticker": t,
                             "issuer": g["issuer"].dropna().iloc[0] if g["issuer"].notna().any() else t,
                             "role": g["role"].mode().iloc[0] if g["role"].str.len().gt(0).any() else "Insider",
                             "first_filed": g["filed"].min(), "owner_cik": str(cik),
                             "confidence": "Confirmed by state" if ok else "Needs review"})
    return pd.DataFrame(rows)


# ############################################################################
#  AUTOMATIC ADJUSTMENTS: holding periods and tuned weights change themselves when a
#  switch is clearly better in the backtest; other settings are shown as proposals
# ############################################################################
HOLD_CHOICES = [20, 60, 125, 250]          # ~1 month, 3 months, 6 months, 1 year of trading days
ADAPT_FILE = "adaptive.json"
HOLD_LABELS = {"hold_other": "Holding period (most stocks)", "hold_small": "Holding period (companies under $2B)",
               "hold_sleeve": "Small-company portfolio: holding period"}


# ---------- "the member who bought has sold" ----------
MEMBER_SELL_WINDOW = 30      # purchase disclosures filed this many days before our buy count as the reason we bought


def member_trades(scored):
    """Each ticker's purchase and sale disclosures, sorted by filing date, for member_sales()."""
    import bisect  # noqa: F401  (used by member_sales)
    df = scored[[c for c in ("ticker", "tx_type", "filed_date", "trade_date", "member", "bioguide") if c in scored]]
    df = df[df["tx_type"].isin(["buy", "sell"]) & df["ticker"].notna() & df["filed_date"].notna()]
    bio = df["bioguide"] if "bioguide" in df else pd.Series([None] * len(df), index=df.index)
    td = df["trade_date"] if "trade_date" in df else df["filed_date"]
    out = {"buy": {}, "sell": {}}
    for t, ty, f, d, m, b in zip(df["ticker"], df["tx_type"], df["filed_date"], td, df["member"], bio):
        b = b if isinstance(b, str) and b else None
        f = pd.Timestamp(f)
        d = pd.Timestamp(d) if pd.notna(d) else f
        out[ty].setdefault(t, []).append((f, d, b or str(m), str(m), b))
    for side in out.values():
        for k in side:
            side[k].sort(key=lambda x: x[0])
            side[k] = ([x[0] for x in side[k]], side[k])
    return out


def member_sales(mt, ticker, ref, after=None, window=MEMBER_SELL_WINDOW):
    """Sales of `ticker` by the members whose purchase disclosures were filed in the `window` days up to `ref`
    (the reason for our buy). A sale counts when it was filed after that member's purchase disclosure (and after
    `after`, if given) and traded on or after their purchase."""
    import bisect
    ref = pd.Timestamp(ref)
    fb, rb = mt["buy"].get(ticker, ([], []))
    lo_i, hi_i = bisect.bisect_left(fb, ref - pd.Timedelta(days=window)), bisect.bisect_right(fb, ref)
    trig = {}
    for f, d, k, _, _ in rb[lo_i:hi_i]:
        if k not in trig or d < trig[k][0]:
            trig[k] = (d, f)
    if not trig:
        return []
    fs, rs = mt["sell"].get(ticker, ([], []))
    start = min(f for _, f in trig.values())
    if after is not None:
        start = max(start, pd.Timestamp(after))
    out = []
    for f, d, k, n, b in rs[bisect.bisect_right(fs, start):]:
        if k in trig and f > trig[k][1] and d >= trig[k][0]:
            out.append({"n": n, "b": b, "td": d.date().isoformat(), "d": f.date().isoformat()})
    return out


_STOP_CACHE = {}


def member_sell_stops(rows, scored, px):
    """For each purchase row: the price-index position of the first trading day after a buying member's sale is
    disclosed (len(px) when there's none). Sale rows never get a stop."""
    key = (id(rows), len(rows), id(px), len(px))
    if key in _STOP_CACHE:
        return _STOP_CACHE[key]
    mt = member_trades(scored)
    n = len(px.index)
    stops = np.full(len(rows), n)
    buy = (rows["tx_type"] == "buy").values if "tx_type" in rows else np.ones(len(rows), bool)
    for i, (t, f) in enumerate(zip(rows["ticker"], rows["filed_date"])):
        if not buy[i] or pd.isna(f):
            continue
        s_ = member_sales(mt, t, f, after=f)
        if s_:
            stops[i] = _entry_positions(px.index, [min(x["d"] for x in s_)])[0]
    _STOP_CACHE.clear()
    _STOP_CACHE[key] = stops
    return stops


EXIT_LABEL = "Sell when a member who bought discloses a sale"


def check_exit_rule(scored, px, cfg, a, pol, rows, today):
    """Compare selling when a buying member's sale is disclosed against holding for the full period.
    Switches (either way) only when clearly better; appends the result rows to `rows`."""
    base = dict(cfg, _nested=True)
    run = lambda p: run_backtest(apply_hold_policy(scored, px, cfg, p), px, base)
    vs = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Long picks"])
    on = bool(pol.get("exit_on_member_sell"))
    cur, alt = run(pol), run(dict(pol, exit_on_member_sell=not on))
    c = _compare(_long_daily(alt), _long_daily(cur))
    rows[:] = [r for r in rows if r.get("Setting") != EXIT_LABEL]
    rows.append({"Setting": EXIT_LABEL, "Option": ("On" if on else "Off") + " (current)", "Per year vs S&P": vs(cur),
                 "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
    rows.append({"Setting": EXIT_LABEL, "Option": "Off" if on else "On", "Per year vs S&P": vs(alt),
                 "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                 "Verdict": "Switched automatically" if clearly_better(c) else _verdict(c, True), **_sure_fields(c)})
    if clearly_better(c):
        a["history"].append({"date": today, "change": EXIT_LABEL, "from": "on" if on else "off",
                             "to": "off" if on else "on", "gain_per_year": c["gain"], "sureness": c["t"]})
        log(f"Adjustments: {EXIT_LABEL} -> {'off' if on else 'on'} (+{c['gain']*100:.1f}%/yr in the backtest)")
        pol["exit_on_member_sell"] = not on
    else:
        log(f"Adjustments: {EXIT_LABEL}: stays {'on' if on else 'off'}"
            + (f" (switching: {c['gain']*100:+.1f}%/yr, t={c['t']:.2f})" if c else ""))
    a["exit_checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return c


def check_trial_signals(scored, px, cfg, a, pol, rows, today):
    """Test each new signal on and off (rescoring every trade); switch only when clearly better."""
    base = dict(cfg, _nested=True)
    vs = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Long picks"])
    for k in TRIAL_SIGNALS:
        label = TRIAL_LABELS[k]
        rows[:] = [r for r in rows if r.get("Setting") != label]
        on = k in pol.get("signals_on", [])
        n = int((scored.get(f"f_{k}", pd.Series(0, index=scored.index)) > 0).sum())
        if n < 30:
            rows.append({"Setting": label, "Option": ("On" if on else "Off") + " (current)", "Per year vs S&P": None,
                         "Gain vs current": None, "Sureness": None, "Verdict": f"Not enough data yet ({n} trades)"})
            continue

        def run(sig):
            bw, sw = active_weights(cfg, signals_on=sig)
            return run_backtest(apply_hold_policy(apply_scores(scored, cfg, buy_w=bw, short_w=sw), px, cfg, pol), px, base)
        cur_sig = list(pol.get("signals_on", []))
        alt_sig = [x for x in cur_sig if x != k] if on else cur_sig + [k]
        cur, alt = run(cur_sig), run(alt_sig)
        c = _compare(_long_daily(alt), _long_daily(cur))
        rows.append({"Setting": label, "Option": ("On" if on else "Off") + " (current)", "Per year vs S&P": vs(cur),
                     "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
        rows.append({"Setting": label, "Option": "Off" if on else "On", "Per year vs S&P": vs(alt),
                     "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                     "Verdict": "Switched automatically" if clearly_better(c) else _verdict(c, True), **_sure_fields(c)})
        if clearly_better(c):
            a["history"].append({"date": today, "change": label, "from": "on" if on else "off", "to": "off" if on else "on",
                                 "gain_per_year": c["gain"], "sureness": c["t"]})
            log(f"Adjustments: {label} -> {'off' if on else 'on'} (+{c['gain']*100:.1f}%/yr in the backtest)")
            pol["signals_on"] = alt_sig


def check_sizing(scored, px, cfg, a, pol, rows, today):
    """Compare position sizing schemes (equal vs bigger positions for stronger picks); switch only when clearly better."""
    base = dict(cfg, _nested=True)
    sc = apply_hold_policy(scored, px, cfg, pol)
    run = lambda k: run_backtest(sc, px, dict(base, _sizing=k))
    vs = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Long picks"])
    names = {"equal": "Same size for every pick", "moderate": "0.75x-1.25x by score", "strong": "0.5x-1.5x by score"}
    curk = pol.get("sizing", "equal")
    cur = run(curk)
    r_cur = _long_daily(cur)
    rows[:] = [r for r in rows if r.get("Setting") != SIZING_LABEL]
    rows.append({"Setting": SIZING_LABEL, "Option": names[curk] + " (current)", "Per year vs S&P": vs(cur),
                 "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
    best = None
    for k in SIZING:
        if k == curk:
            continue
        b = run(k)
        c = _compare(_long_daily(b), r_cur)
        rows.append({"Setting": SIZING_LABEL, "Option": names[k], "Per year vs S&P": vs(b),
                     "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                     "Verdict": _verdict(c, True, len(SIZING) - 1), **_sure_fields(c, len(SIZING) - 1)})
        if wins_earlier(c, len(SIZING) - 1) and (best is None or _rank(c) > _rank(best[1])):
            best = (k, c)
    if best and not confirmed(best[1]):      # chosen on earlier years; must also hold over the last 3
        best = None
    if best:
        for r in rows:
            if r["Setting"] == SIZING_LABEL and r["Option"] == names[best[0]]:
                r["Verdict"] = "Switched automatically"
        a["history"].append({"date": today, "change": SIZING_LABEL, "from": names[curk], "to": names[best[0]],
                             "gain_per_year": best[1]["gain"], "sureness": best[1]["t"]})
        log(f"Adjustments: {SIZING_LABEL} -> {names[best[0]]} (+{best[1]['gain']*100:.1f}%/yr in the backtest)")
        pol["sizing"] = best[0]
    else:
        log(f"Adjustments: {SIZING_LABEL}: stays {names[curk]}")
    a["sizing_checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def check_sizing_once(scored, px, cfg):
    """First run after sizing was added: test it right away instead of waiting for the weekly check."""
    a = load_adaptive(cfg)
    if a.get("sizing_checked_at"):
        return
    pol = dict(a["policy"])
    rows = list(a.get("evaluation") or [])
    check_sizing(scored, px, cfg, a, pol, rows, dt.date.today().isoformat())
    a["policy"], a["evaluation"] = pol, rows
    save_adaptive(cfg, a)


def check_trial_signals_once(scored, px, cfg):
    """A newly added trial signal is tested right away instead of waiting for the weekly check."""
    a = load_adaptive(cfg)
    seen = set(a.get("trial_checked") or [])
    if all(k in seen for k in TRIAL_SIGNALS):
        return
    pol = dict(a["policy"])
    rows = list(a.get("evaluation") or [])
    check_trial_signals(scored, px, cfg, a, pol, rows, dt.date.today().isoformat())
    a["policy"], a["evaluation"], a["trial_checked"] = pol, rows, sorted(TRIAL_SIGNALS)
    save_adaptive(cfg, a)


def check_exit_rule_once(scored, px, cfg):
    """First run after this rule was added: test it right away instead of waiting for the weekly check."""
    a = load_adaptive(cfg)
    if a.get("exit_checked_at"):
        return
    pol = dict(a["policy"])
    rows = list(a.get("evaluation") or [])
    check_exit_rule(scored, px, cfg, a, pol, rows, dt.date.today().isoformat())
    a["policy"], a["evaluation"] = pol, rows
    save_adaptive(cfg, a)


# ---- price rules: sell early when a stock falls, or keep holding while it keeps rising ----------------------
# Applied to purchases (main picks). Decisions use daily closes; a sale happens at that day's close.
PRICE_EXITS = {"none": "Hold the full period",
               "stop10": "Sell if it falls 10% below the buy price",
               "stop15": "Sell if it falls 15% below the buy price",
               "stop20": "Sell if it falls 20% below the buy price",
               "trail15": "Sell after a 15% drop from its high since buying"}
EXTENDS = {"none": "Sell on the scheduled day",
           "ride10": "Keep holding while rising (sell after a 10% drop from its high, at most 2x the hold)",
           "ride15": "Keep holding while rising (sell after a 15% drop from its high, at most 2x the hold)"}
PRICE_EXIT_LABEL = "Sell early when a stock falls"
EXTEND_LABEL = "Hold longer while a stock keeps rising"
_PEXIT_CACHE = {}


def _rule_pct(rule):
    m = re.search(r"(\d+)$", rule or "")
    return int(m.group(1)) / 100 if m else 0.0


def price_exit_positions(rows, px, hold, price_exit="none", extend="none"):
    """Exit position (in the price index) for each row under the price rules. Stop rules end a trade early;
    the extend rule keeps a trade that is in profit and near its high past the scheduled day, until it drops
    X% from its high (at most 2x the hold). A trade whose exit is past the last price is left open.
    Sale rows keep the normal schedule."""
    key = (id(rows), len(rows), id(px), len(px), int(hold), price_exit, extend)
    if key in _PEXIT_CACHE:
        return _PEXIT_CACHE[key]
    idx = px.index
    n = len(idx)
    ent = _entry_positions(idx, rows["filed_date"])
    out = ent + int(hold)
    col = {c: i for i, c in enumerate(px.columns)}
    if not hasattr(price_exit_positions, "_ff") or price_exit_positions._ff[0] is not px:
        price_exit_positions._ff = (px, px.ffill().values)
    arr = price_exit_positions._ff[1]
    buy = (rows["tx_type"] == "buy").values if "tx_type" in rows else np.ones(len(rows), bool)
    sx, ex = _rule_pct(price_exit), _rule_pct(extend)
    trail = price_exit.startswith("trail")
    for i, (t, e) in enumerate(zip(rows["ticker"].values, ent)):
        c = col.get(t)
        if c is None or not buy[i] or e >= n:
            continue
        base = e + int(hold)
        p0 = arr[e, c]
        if not np.isfinite(p0) or p0 <= 0:
            continue
        stopped = False
        if sx:
            seg = arr[e + 1:min(base, n - 1) + 1, c]
            if len(seg):
                ref = np.fmax.accumulate(np.concatenate([[p0], seg]))[:-1] if trail else p0
                hit = np.nonzero(seg <= ref * (1 - sx))[0]
                if len(hit):
                    out[i] = e + 1 + int(hit[0])
                    stopped = True
        if ex and not stopped and base < n:
            pb = arr[base, c]
            hi = np.nanmax(arr[e:base + 1, c])
            if np.isfinite(pb) and pb > p0 and pb >= hi * (1 - ex):
                cap = e + 2 * int(hold)
                seg = arr[base + 1:min(cap, n - 1) + 1, c]
                run_hi = np.fmax.accumulate(np.concatenate([[hi], seg]))[1:]
                hit = np.nonzero(seg <= run_hi * (1 - ex))[0] if len(seg) else []
                out[i] = base + 1 + int(hit[0]) if len(hit) else cap
    _PEXIT_CACHE.clear()
    _PEXIT_CACHE[key] = out
    return out


def _price_rule_check(scored, px, cfg, a, pol, rows, today, key, options, label):
    """Try every option of one price rule against the current one; switch only when clearly better.
    Rows also report the account's worst drop and the worst single trade, so risk is visible."""
    base = dict(cfg, _nested=True)
    vs = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Long picks"])

    def risk(b):
        dd = float(b["perf"].loc["Worst drawdown", "Long picks"])
        lg = b.get("longs")
        closed = lg[lg["closed"]] if lg is not None and len(lg) else None
        worst = float(closed["ret"].min()) if closed is not None and len(closed) else None
        big = float((closed["ret"] <= -0.25).mean()) if closed is not None and len(closed) else None
        return {"Worst drop": dd, "Worst trade": worst, "Trades down 25%+": big}

    curk = pol.get(key, "none")
    cur = run_backtest(apply_hold_policy(scored, px, cfg, pol), px, base)
    r_cur = _long_daily(cur)
    rows[:] = [r for r in rows if r.get("Setting") != label]
    rows.append({"Setting": label, "Option": options[curk] + " (current)", "Per year vs S&P": vs(cur),
                 "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current", **risk(cur)})
    best = None
    for k in options:
        if k == curk:
            continue
        b = run_backtest(apply_hold_policy(scored, px, cfg, dict(pol, **{key: k})), px, base)
        c = _compare(_long_daily(b), r_cur)
        rows.append({"Setting": label, "Option": options[k], "Per year vs S&P": vs(b),
                     "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                     "Verdict": _verdict(c, True, len(options) - 1), **_sure_fields(c, len(options) - 1), **risk(b)})
        log(f"Adjustments: {label}: {options[k]}: " + (f"{c['gain']*100:+.2f}%/yr, t={c['t']:.2f}" if c else "no data"))
        if wins_earlier(c, len(options) - 1) and (best is None or _rank(c) > _rank(best[1])):
            best = (k, c)
    if best and not confirmed(best[1]):      # chosen on earlier years; must also hold over the last 3
        best = None
    if best:
        for r in rows:
            if r["Setting"] == label and r["Option"] == options[best[0]]:
                r["Verdict"] = "Switched automatically"
        a["history"].append({"date": today, "change": label, "from": options[curk], "to": options[best[0]],
                             "gain_per_year": best[1]["gain"], "sureness": best[1]["t"]})
        log(f"Adjustments: {label} -> {options[best[0]]} (+{best[1]['gain']*100:.1f}%/yr in the backtest)")
        pol[key] = best[0]
    else:
        log(f"Adjustments: {label}: stays {options[curk]}")


SLEEVE_HOLDS = (20, 60, 125, 250, 375, 500)
SLEEVE_EXTENDS = {"none": "Sell on the scheduled day",
                  "ride10": "Keep holding while rising (sell after a 10% drop from its high, at most 2x the hold)",
                  "ride15": "Keep holding while rising (sell after a 15% drop from its high, at most 2x the hold)"}


def check_sleeve_price_rules(scored, px, cfg, a, pol, rows, today):
    """The same stop-loss and hold-longer rules, tried on the small-company portfolio with its own hold."""
    sd = lambda b: b["curves"]["Small-company picks"].pct_change().fillna(0)
    sv = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Small-company picks"])

    def risk(b):
        dd = float(b["perf"].loc["Worst drawdown", "Small-company picks"])
        pk = b["picks"]
        closed = pk[pk["closed"]] if len(pk) else pk
        return {"Worst drop": dd, "Worst trade": float(closed["ret"].min()) if len(closed) else None,
                "Trades down 25%+": float((closed["ret"] <= -0.25).mean()) if len(closed) else None}

    for key, options, label in (("sleeve_price_exit", PRICE_EXITS, "Small-company portfolio: " + PRICE_EXIT_LABEL.lower()),
                                ("sleeve_extend", SLEEVE_EXTENDS, "Small-company portfolio: " + EXTEND_LABEL.lower())):
        curk = pol.get(key, "none")
        kw = lambda k: {"price_exit": pol.get("sleeve_price_exit", "none"), "extend": pol.get("sleeve_extend", "none"),
                        key.replace("sleeve_", ""): k}
        cur = small_cap_backtest(scored, px, cfg, hold=pol["hold_sleeve"], coverage=pol.get("sleeve_coverage"), **kw(curk))
        if cur is None:
            return
        r_cur = sd(cur)
        rows[:] = [r for r in rows if r.get("Setting") != label]
        rows.append({"Setting": label, "Option": options[curk] + " (current)", "Per year vs S&P": sv(cur),
                     "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current", **risk(cur)})
        best = None
        for k in options:
            if k == curk:
                continue
            b = small_cap_backtest(scored, px, cfg, hold=pol["hold_sleeve"], coverage=pol.get("sleeve_coverage"), **kw(k))
            c = _compare(sd(b), r_cur)
            rows.append({"Setting": label, "Option": options[k], "Per year vs S&P": sv(b),
                         "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                         "Verdict": _verdict(c, True, len(options) - 1), **_sure_fields(c, len(options) - 1), **risk(b)})
            if wins_earlier(c, len(options) - 1) and (best is None or _rank(c) > _rank(best[1])):
                best = (k, c)
        if best and not confirmed(best[1]):      # chosen on earlier years; must also hold over the last 3
            best = None
        if best:
            for r in rows:
                if r["Setting"] == label and r["Option"] == options[best[0]]:
                    r["Verdict"] = "Switched automatically"
            a["history"].append({"date": today, "change": label, "from": options[curk], "to": options[best[0]],
                                 "gain_per_year": best[1]["gain"], "sureness": best[1]["t"]})
            log(f"Adjustments: {label} -> {options[best[0]]} (+{best[1]['gain']*100:.1f}%/yr in the backtest)")
            pol[key] = best[0]
        else:
            log(f"Adjustments: {label}: stays {options[curk]}")


def check_price_rules(scored, px, cfg, a, pol, rows, today):
    _price_rule_check(scored, px, cfg, a, pol, rows, today, "price_exit", PRICE_EXITS, PRICE_EXIT_LABEL)
    _price_rule_check(scored, px, cfg, a, pol, rows, today, "extend", EXTENDS, EXTEND_LABEL)
    a["price_rules_checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def check_price_rules_once(scored, px, cfg):
    """First run after the price rules were added: test them right away instead of waiting for the weekly check."""
    a = load_adaptive(cfg)
    if a.get("price_rules_checked_at"):
        return
    pol = dict(a["policy"])
    rows = list(a.get("evaluation") or [])
    check_price_rules(scored, px, cfg, a, pol, rows, dt.date.today().isoformat())
    a["policy"], a["evaluation"] = pol, rows
    save_adaptive(cfg, a)


# ---- how strict should the weekly check be? Replay the switching rule on never-seen years -------------------
FILTER_FILE = "filter_test.json"
FILTER_VERSION = 4
FILTER_RULES = {
    "strict": "Strict (now): at least +1%/yr on earlier years, sure enough for the number of options tried (t ≥ 1.65 for one, higher for more), then confirmed on the last 3 years",
    "medium": "Middle: at least +0.5%/yr and somewhat sure (t ≥ 1.0)",
    "loose": "Loose: any gain above zero over the past, no sureness needed",
    "never": "Never switch: keep the starting settings",
}


def _rule_passes(rule, c, k=1):
    if not c or rule == "never":
        return False
    if rule == "strict":
        return wins_earlier(c, k)
    if rule == "medium":
        return c["gain"] >= 0.005 and c["t"] >= 1.0
    return c["gain"] > 0


def load_filter_state(cfg):
    try:
        st = json.load(open(_p(cfg, "state", FILTER_FILE)))
        return st if st.get("version") == FILTER_VERSION else {}
    except Exception:
        return {}


def filter_rule_test(scored, px, cfg):
    """Each year from 2017, every rule looks only at the years before it, switches settings the way it would
    have (holding periods, stop and hold-longer rules, sell-when-member-sells, sizing, new signals), then
    trades the next year with what it chose. Resumable: years already done are kept between runs."""
    path = _p(cfg, "state", FILTER_FILE)
    try:
        st = json.load(open(path))
        if st.get("version") != FILTER_VERSION:
            st = {}
    except Exception:
        st = {}
    start_pol = {"hold_other": 60, "hold_small": 60, "price_exit": "none", "extend": "none",
                 "exit_on_member_sell": False, "sizing": "equal", "signals_on": []}
    st.setdefault("version", FILTER_VERSION)
    st.setdefault("rules", {r: {"pol": dict(start_pol), "years": {}, "switches": []} for r in FILTER_RULES})
    settings = [("hold_other", [20, 60, 125, 250]), ("hold_small", [20, 60, 125, 250]),
                ("price_exit", list(PRICE_EXITS)), ("extend", list(EXTENDS)),
                ("exit_on_member_sell", [False, True]), ("sizing", list(SIZING))]
    settings += [("sig:" + k, [False, True]) for k in TRIAL_SIGNALS
                 if int((scored.get(f"f_{k}", pd.Series(0, index=scored.index)) > 0).sum()) >= 30]
    spy = px["SPY"].pct_change(fill_method=None).fillna(0)
    cache = {}

    def series(pol):
        key = json.dumps(pol, sort_keys=True, default=str)
        if key not in cache:
            sc = scored
            if pol["signals_on"]:
                bw, sw = active_weights(cfg, signals_on=list(pol["signals_on"]))
                sc = apply_scores(scored, cfg, buy_w=bw, short_w=sw)
            bt = run_backtest(apply_hold_policy(sc, px, cfg, pol), px, dict(cfg, _nested=True, _sizing=pol["sizing"]))
            cache[key] = _long_daily(bt)
        return cache[key]

    def with_opt(pol, key, o):
        p = dict(pol, signals_on=list(pol["signals_on"]))
        if key.startswith("sig:"):
            k = key[4:]
            p["signals_on"] = sorted(set(p["signals_on"]) | {k}) if o else [x for x in p["signals_on"] if x != k]
        else:
            p[key] = o
        return p

    def current(pol, key):
        return (key[4:] in pol["signals_on"]) if key.startswith("sig:") else pol[key]

    first = max(pd.Timestamp(cfg["START_DATE"]).year + 3, 2017)
    for y in range(first, dt.date.today().year + 1):
        if all(str(y) in st["rules"][r]["years"] for r in FILTER_RULES):
            continue
        if out_of_time(cfg, 70):
            log(f"Filter test: paused before {y}; continues next run")
            break
        cut = pd.Timestamp(f"{y}-01-01")
        for r in FILTER_RULES:
            R = st["rules"][r]
            if str(y) in R["years"]:
                continue
            pol = R["pol"]
            if r != "never":
                for key, opts in settings:
                    cur_v = current(pol, key)
                    base = series(pol)
                    past = base.index < cut
                    best = None
                    for o in opts:
                        if o == cur_v:
                            continue
                        alt = series(with_opt(pol, key, o))
                        c = _compare(alt[past], base[past])
                        if _rule_passes(r, c, len(opts) - 1) and (best is None or _rank(c) > _rank(best[1])):
                            best = (o, c)
                    if best and r == "strict" and not confirmed(best[1]):
                        best = None
                    if best:
                        R["switches"].append({"year": y, "setting": key, "from": cur_v, "to": best[0],
                                              "gain": best[1]["gain"], "t": best[1]["t"]})
                        pol = with_opt(pol, key, best[0])
            R["pol"] = pol
            d = series(pol)
            m = d.index.year == y
            if m.sum() < 20:
                continue
            ret = float((1 + d[m]).prod() - 1)
            sr = float((1 + spy.reindex(d.index)[m].fillna(0)).prod() - 1)
            R["years"][str(y)] = {"ret": ret, "spy": sr, "vs": ret - sr, "days": int(m.sum()), "pol": dict(pol)}
            log(f"Filter test {y} {r}: {ret*100:+.1f}% vs S&P {sr*100:+.1f}%")
        json.dump(st, open(path, "w"), indent=1, default=str)
    # summary
    summ = []
    for r, label in FILTER_RULES.items():
        ys = st["rules"][r]["years"]
        if not ys:
            continue
        full = list(ys.values())
        eq = float(np.prod([1 + v["ret"] for v in full])) if full else None
        es = float(np.prod([1 + v["spy"] for v in full])) if full else None
        n = sum(v["days"] for v in full) / 252 if full else 0
        summ.append({"rule": r, "label": label, "years": n,
                     "per_year": (eq ** (1 / n) - 1) if n else None, "spy_per_year": (es ** (1 / n) - 1) if n else None,
                     "vs_per_year": ((eq ** (1 / n)) - (es ** (1 / n))) if n else None,
                     "beat_years": sum(v["vs"] > 0 for v in full), "switches": len(st["rules"][r]["switches"])})
    st["summary"] = summ
    st["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    st["complete"] = all(str(dt.date.today().year) in st["rules"][r]["years"] for r in FILTER_RULES)
    json.dump(st, open(path, "w"), indent=1, default=str)
    return st


# ---- one-time settings the owner chose (from policy_overrides.json in the repo) ------------------------------
def _apply_overrides(cfg, a):
    try:
        ov = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "policy_overrides.json")))
    except Exception:
        return False
    done = set(a.get("overrides_applied", []))
    changed = False
    for o in ov.get("overrides", []):
        if o.get("id") in done:
            continue
        for k, v in (o.get("set") or {}).items():
            old = a["policy"].get(k)
            a["policy"][k] = v
            lab = {"price_exit": (PRICE_EXIT_LABEL, PRICE_EXITS), "extend": (EXTEND_LABEL, EXTENDS)}.get(k)
            a.setdefault("history", []).append({
                "date": dt.date.today().isoformat(), "change": (lab[0] if lab else k) + " (your choice)",
                "from": lab[1].get(old, old) if lab else old, "to": lab[1].get(v, v) if lab else v,
                "gain_per_year": None, "sureness": None, "by": "owner", "key": k, "note": o.get("note")})
            log(f"Settings: {k} set to {v} ({o.get('note') or 'owner choice'})")
        done.add(o.get("id"))
        changed = True
    a["overrides_applied"] = sorted(done)
    return changed


def load_adaptive(cfg):
    try:
        a = json.load(open(_p(cfg, "state", ADAPT_FILE)))
    except Exception:
        a = {}
    a.setdefault("policy", {"hold_small": int(cfg["HOLD_DAYS"]), "hold_other": int(cfg["HOLD_DAYS"])})
    a["policy"].setdefault("hold_sleeve", 250)
    a["policy"].setdefault("sleeve_coverage", False)
    a["policy"].setdefault("exit_on_member_sell", False)
    a["policy"].setdefault("signals_on", [])
    a["policy"].setdefault("price_exit", "none")           # sell early when a stock falls (stop rules)
    a["policy"].setdefault("extend", "none")               # keep holding while a stock keeps rising
    a["policy"].setdefault("sleeve_price_exit", "none")    # the same two rules for the small-company portfolio
    a["policy"].setdefault("sleeve_extend", "none")
    a["policy"].setdefault("sizing", "equal")              # position size by confidence                # new signals the weekly check has switched on   # sell when a member who bought discloses a sale
    a["policy"].setdefault("main_method", "hand")       # "learned": buy weights refit from all finished trades     # small-company portfolio skips companies no analyst covers
    a.setdefault("use_tuned", False)
    a.setdefault("history", [])
    if _apply_overrides(cfg, a):
        try:
            save_adaptive(cfg, a)
        except Exception:
            pass
    return a


def save_adaptive(cfg, a):
    json.dump(a, open(_p(cfg, "state", ADAPT_FILE), "w"), indent=1, default=str)


def row_holds(df, policy):
    small = (df["f_small_cap"].fillna(0) >= 1).values if "f_small_cap" in df else np.zeros(len(df), bool)
    return np.where(small, int(policy["hold_small"]), int(policy["hold_other"]))


def apply_hold_policy(scored, px, cfg, policy):
    """Recompute each trade's exit with its group's holding period."""
    h = row_holds(scored, policy)
    out = scored.copy()
    cost = cfg.get("COST_BPS", 0) / 1e4
    cols = ["entry_date", "exit_date", "entry_px", "exit_px", "ret", "spy_ret", "excess", "closed"]
    stops = member_sell_stops(scored, scored, px) if policy.get("exit_on_member_sell") else None
    pe, ex = policy.get("price_exit", "none"), policy.get("extend", "none")
    for hv in np.unique(h):
        m = h == hv
        ep = price_exit_positions(scored.loc[m], px, int(hv), pe, ex) if (pe != "none" or ex != "none") else None
        fr = forward_returns(scored.loc[m], px, int(hv), cost=cost, stop=None if stops is None else stops[m],
                             exit_pos=ep)
        for c in cols:
            out.loc[m, c] = fr[c].values
    for c in ("entry_date", "exit_date"):
        out[c] = pd.to_datetime(out[c])
    for c in ("entry_px", "exit_px", "ret", "spy_ret", "excess"):
        out[c] = out[c].astype(float)
    out["closed"] = out["closed"].astype(bool)
    out["hold_days_row"] = h
    if "benchmark_etf" in out:          # industry fund over the same (new) holding window
        pf = _px_ffill(px)
        idx, colmap = pf.index, {c: i for i, c in enumerate(pf.columns)}
        arr = pf.values
        ent = np.searchsorted(idx.values, out["entry_date"].values, side="left")
        ext = np.searchsorted(idx.values, out["exit_date"].values, side="left")
        ev = np.full(len(out), np.nan)
        for i, e in enumerate(out["benchmark_etf"].values):
            j = colmap.get(e) if isinstance(e, str) else None
            if j is None or pd.isna(out["entry_date"].values[i]) or ent[i] >= len(idx) or ext[i] >= len(idx):
                continue
            a, b = arr[ent[i], j], arr[ext[i], j]
            if a and b and np.isfinite(a) and np.isfinite(b):
                ev[i] = b / a - 1
        out["sector_ret"] = ev
        out["excess_sector"] = out["ret"] - out["sector_ret"]
    return out


def _long_daily(bt):
    return bt["curves"]["Long picks"].pct_change().fillna(0)


HOLDOUT_DAYS = 756          # the last 3 years: never used to pick a setting, only to confirm it


def _nw_se(d, lags=10):
    """Standard error of a daily average, allowing for runs of good or bad days (Newey-West); never below the
    plain one."""
    x = np.asarray(d, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return np.nan
    e = x - x.mean()
    s = e @ e / n
    for l in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - l / (lags + 1)) * (e[l:] @ e[:-l]) / n
    return float(max(np.sqrt(max(s, 0) / n), x.std(ddof=1) / np.sqrt(n)))


def _nw_t(d, lags=10):
    x = np.asarray(d, dtype=float)
    if len(x) < 30:
        return 0.0
    se = _nw_se(x, lags)
    return float(np.nanmean(x) / se) if se and np.isfinite(se) and se > 0 else 0.0


def _compare(r_new, r_cur):
    """Yearly gain of switching and how sure it is (on daily differences, allowing for runs of days), measured
    three ways: all years, the years before the last 3 (used to choose), and the last 3 (held back to confirm)."""
    d = (r_new - r_cur).dropna()
    if len(d) < 250 or d.std() == 0:
        return None
    tr, ho = d.iloc[:-HOLDOUT_DAYS], d.iloc[-HOLDOUT_DAYS:]
    ok = len(tr) >= 500 and tr.std() > 0
    return {"gain": float(d.mean() * 252), "t": _nw_t(d), "recent": float(ho.mean() * 252),
            "train_gain": float(tr.mean() * 252) if ok else None, "train_t": _nw_t(tr) if ok else None}


def sure_bar(k=1):
    """How sure a switch must be when the best of k options is picked (one option: 1.65; three: 2.13; five: 2.33).
    Picking the best of several makes a lucky winner more likely, so the bar rises with the number tried."""
    from statistics import NormalDist
    return float(NormalDist().inv_cdf(1 - 0.05 / max(int(k), 1)))


def _rank(c):
    return c["train_gain"] if c.get("train_gain") is not None else c["gain"]


def wins_earlier(c, k=1):
    """Won on the years before the last 3, by enough (+1%/yr) and surely enough for the number of options tried."""
    if not c:
        return False
    if c.get("train_gain") is None:            # short history: all years, same bar
        return c["gain"] >= 0.01 and c["t"] >= sure_bar(k)
    return c["train_gain"] >= 0.01 and c["train_t"] >= sure_bar(k)


def confirmed(c):
    """Also better over the last 3 years, which played no part in choosing it."""
    return bool(c) and c["recent"] > 0 and c["gain"] > 0


def clearly_better(c, k=1):
    """Switch only when the option won on the earlier years and then also won on the last 3 years."""
    return wins_earlier(c, k) and confirmed(c)


def pick_switch(cands, k):
    """Best of several options: chosen on the earlier years only (the most gain among those that clear the bar),
    then switched to only if that one option also holds up over the last 3 years. cands: [(option, c, ...)]."""
    ok = [x for x in cands if wins_earlier(x[1], k)]
    if not ok:
        return None
    best = max(ok, key=lambda x: _rank(x[1]))
    return best if confirmed(best[1]) else None


def _verdict(c, auto, k=1):
    if not c:
        return "Not enough data"
    if clearly_better(c, k):
        return "Switched automatically" if auto else "Clearly better: ask Claude to apply"
    tg, tt = (c["train_gain"], c["train_t"]) if c.get("train_gain") is not None else (c["gain"], c["t"])
    if tg >= 0.01 and tt >= sure_bar(k) and c["recent"] <= 0:
        return "Better before, but not in the last 3 years"
    if c["gain"] > 0:
        return "Slightly better, not sure enough to switch"
    return "Worse"


def _sure_fields(c, k=1):
    """Extra columns for the dashboard: the sureness used to decide and the bar it had to clear."""
    if not c:
        return {"Bar": sure_bar(k), "Options tried": int(k)}
    return {"Bar": sure_bar(k), "Options tried": int(k), "Sureness (earlier years)": c.get("train_t"),
            "Gain (last 3 years)": c.get("recent")}


RECHECK_LABEL = "Earlier automatic switches, re-checked with the stricter test"


def recheck_past_switches(scored, px, cfg, a, pol, rows, today):
    """A setting the weekly check switched on its own (not one you chose) stays only while it still clearly beats
    the starting setting under today's test; otherwise it goes back to the start."""
    start = {"hold_other": int(cfg["HOLD_DAYS"]), "hold_small": int(cfg["HOLD_DAYS"]), "hold_sleeve": 250,
             "sleeve_coverage": False, "exit_on_member_sell": False, "price_exit": "none", "extend": "none",
             "sleeve_price_exit": "none", "sleeve_extend": "none", "sizing": "equal"}
    fam = {"hold_other": len(HOLD_CHOICES) - 1, "hold_small": len(HOLD_CHOICES) - 1, "hold_sleeve": len(SLEEVE_HOLDS) - 1,
           "price_exit": len(PRICE_EXITS) - 1, "extend": len(EXTENDS) - 1, "sleeve_price_exit": len(PRICE_EXITS) - 1,
           "sleeve_extend": len(SLEEVE_EXTENDS) - 1, "sizing": len(SIZING) - 1}
    labels = dict(HOLD_LABELS, sleeve_coverage="Small-company portfolio: skip companies no analyst covers",
                  exit_on_member_sell=EXIT_LABEL, price_exit=PRICE_EXIT_LABEL, extend=EXTEND_LABEL,
                  sleeve_price_exit="Small-company portfolio: " + PRICE_EXIT_LABEL.lower(),
                  sleeve_extend="Small-company portfolio: " + EXTEND_LABEL.lower(), sizing=SIZING_LABEL)
    # yours: any setting whose latest change was your choice is left alone
    last_by = {}
    for h in a.get("history", []):
        ch = str(h.get("change", ""))
        for k_, lab in labels.items():
            if h.get("key") == k_ or ch.startswith(lab) or ch.startswith(f"{k_} (your choice)"):
                last_by[k_] = h.get("by") or "auto"
    if pol.get("main_method") == "learned":    # the learned method sets its own holding period
        last_by["hold_other"] = last_by["hold_small"] = "owner"
    sleeve_keys = {"hold_sleeve", "sleeve_coverage", "sleeve_price_exit", "sleeve_extend"}

    def series(p, sleeve):
        if sleeve:
            b = small_cap_backtest(scored, px, cfg, hold=p["hold_sleeve"], coverage=p.get("sleeve_coverage"),
                                   price_exit=p.get("sleeve_price_exit", "none"), extend=p.get("sleeve_extend", "none"))
            return None if b is None else b["curves"]["Small-company picks"].pct_change().fillna(0)
        sc = scored
        if p.get("signals_on"):
            bw, sw = active_weights(cfg, signals_on=list(p["signals_on"]))
            sc = apply_scores(scored, cfg, buy_w=bw, short_w=sw)
        b = run_backtest(apply_hold_policy(sc, px, cfg, p), px, dict(cfg, _nested=True, _sizing=p.get("sizing", "equal")))
        return _long_daily(b)

    rows[:] = [r for r in rows if r.get("Setting") != RECHECK_LABEL]
    for key, v0 in start.items():
        if pol.get(key, v0) == v0 or last_by.get(key) == "owner":
            continue
        k = fam.get(key, 1)
        s_now, s_start = series(pol, key in sleeve_keys), series(dict(pol, **{key: v0}), key in sleeve_keys)
        c = _compare(s_now, s_start) if s_now is not None and s_start is not None else None
        keep = clearly_better(c, k) if c else True          # can't measure it: leave it as it is
        rows.append({"Setting": RECHECK_LABEL, "Option": f"{labels[key]}: {pol[key]} (vs starting {v0})",
                     "Per year vs S&P": None, "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                     "Verdict": ("Kept: still clearly better" if c else "Kept: not enough data to re-check") if keep
                     else "Undone: back to the starting setting",
                     **_sure_fields(c, k)})
        if not keep:
            a["history"].append({"date": today, "change": labels[key] + " (undone by the stricter test)",
                                 "from": f"{pol[key]} days" if key.startswith("hold") else pol[key],
                                 "to": f"{v0} days" if key.startswith("hold") else v0, "gain_per_year": c["gain"] if c else None,
                                 "sureness": c["train_t"] if c else None})
            log(f"Adjustments: {labels[key]}: {pol[key]} -> {v0} (didn't pass the stricter test"
                + (f": {c['gain']*100:+.1f}%/yr, earlier-years t={c['train_t'] or 0:.2f} vs bar {sure_bar(k):.2f}, "
                   f"last 3 years {c['recent']*100:+.1f}%/yr)" if c else ")"))
            pol[key] = v0
        else:
            log(f"Adjustments: {labels[key]}: keeps {pol[key]} (passes the stricter test)")


def evaluate_adjustments(scored, px, cfg, wf=None):
    """Weekly: try other holding periods (switch when clearly better) and a few other settings (proposals only)."""
    a = load_adaptive(cfg)
    pol = dict(a["policy"])
    base = dict(cfg, _nested=True)
    recheck_rows = []
    try:
        recheck_past_switches(scored, px, cfg, a, pol, recheck_rows, dt.date.today().isoformat())
    except Exception as e:
        log(f"Adjustments: re-check of earlier switches skipped ({type(e).__name__}: {e})")

    def run(policy, **over):
        return run_backtest(apply_hold_policy(scored, px, cfg, policy), px, dict(base, **over))

    def vs_spy(b):
        return float(b["perf"].loc["Per year vs S&P 500", "Long picks"])

    today = dt.date.today().isoformat()
    cur = run(pol)
    rows = []
    for key in ("hold_other", "hold_small"):
        r_cur, best = _long_daily(cur), None
        rows.append({"Setting": HOLD_LABELS[key], "Option": f"{pol[key]} trading days (current)",
                     "Per year vs S&P": vs_spy(cur), "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
        for h in HOLD_CHOICES:
            if h == pol[key]:
                continue
            b = run(dict(pol, **{key: h}))
            c = _compare(_long_daily(b), r_cur)
            rows.append({"Setting": HOLD_LABELS[key], "Option": f"{h} trading days", "Per year vs S&P": vs_spy(b),
                         "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                         "Verdict": _verdict(c, True, len(HOLD_CHOICES) - 1), **_sure_fields(c, len(HOLD_CHOICES) - 1)})
            if wins_earlier(c, len(HOLD_CHOICES) - 1) and (best is None or _rank(c) > _rank(best[1])):
                best = (h, c, b)
        if best and not confirmed(best[1]):      # chosen on earlier years; must also hold over the last 3
            best = None
        if best:
            a["history"].append({"date": today, "change": HOLD_LABELS[key], "from": f"{pol[key]} days",
                                 "to": f"{best[0]} days", "gain_per_year": best[1]["gain"], "sureness": best[1]["t"]})
            log(f"Adjustments: {HOLD_LABELS[key]} {pol[key]} -> {best[0]} trading days "
                f"(+{best[1]['gain']*100:.1f}%/yr in the backtest)")
            pol[key] = best[0]
            for r in rows:
                if r["Setting"] == HOLD_LABELS[key] and r["Option"].startswith(f"{best[0]} "):
                    r["Verdict"] = "Switched automatically"
            cur = best[2]
    # the separate small-company portfolio picks its own holding period the same way
    sb = small_cap_backtest(scored, px, cfg, hold=pol["hold_sleeve"])
    if sb is not None:
        sd = lambda b: b["curves"]["Small-company picks"].pct_change().fillna(0)
        sv = lambda b: float(b["perf"].loc["Per year vs S&P 500", "Small-company picks"])
        key, r_cur, best = "hold_sleeve", sd(sb), None
        rows.append({"Setting": HOLD_LABELS[key], "Option": f"{pol[key]} trading days (current)", "Per year vs S&P": sv(sb),
                     "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
        for h in SLEEVE_HOLDS:
            if h == pol[key]:
                continue
            b = small_cap_backtest(scored, px, cfg, hold=h)
            c = _compare(sd(b), r_cur)
            rows.append({"Setting": HOLD_LABELS[key], "Option": f"{h} trading days", "Per year vs S&P": sv(b),
                         "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                         "Verdict": _verdict(c, True, len(SLEEVE_HOLDS) - 1), **_sure_fields(c, len(SLEEVE_HOLDS) - 1)})
            if wins_earlier(c, len(SLEEVE_HOLDS) - 1) and (best is None or _rank(c) > _rank(best[1])):
                best = (h, c)
        if best and not confirmed(best[1]):      # chosen on earlier years; must also hold over the last 3
            best = None
        if best:
            a["history"].append({"date": today, "change": HOLD_LABELS[key], "from": f"{pol[key]} days",
                                 "to": f"{best[0]} days", "gain_per_year": best[1]["gain"], "sureness": best[1]["t"]})
            pol[key] = best[0]
            for r in rows:
                if r["Setting"] == HOLD_LABELS[key] and r["Option"].startswith(f"{best[0]} "):
                    r["Verdict"] = "Switched automatically"
        # skip companies no analyst covers? switches only when clearly better, in either direction
        cov = bool(pol.get("sleeve_coverage"))
        cur_b = small_cap_backtest(scored, px, cfg, hold=pol["hold_sleeve"], coverage=cov)
        alt_b = small_cap_backtest(scored, px, cfg, hold=pol["hold_sleeve"], coverage=not cov)
        label = "Small-company portfolio: skip companies no analyst covers"
        if cur_b is not None and alt_b is not None:
            c = _compare(sd(alt_b), sd(cur_b))
            rows.append({"Setting": label, "Option": ("On" if cov else "Off") + " (current)", "Per year vs S&P": sv(cur_b),
                         "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
            rows.append({"Setting": label, "Option": "On" if not cov else "Off", "Per year vs S&P": sv(alt_b),
                         "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                         "Verdict": "Switched automatically" if clearly_better(c) else _verdict(c, True), **_sure_fields(c)})
            if clearly_better(c):
                a["history"].append({"date": today, "change": label, "from": "on" if cov else "off",
                                     "to": "off" if cov else "on", "gain_per_year": c["gain"], "sureness": c["t"]})
                log(f"Adjustments: {label} -> {'off' if cov else 'on'} (+{c['gain']*100:.1f}%/yr in the backtest)")
                pol["sleeve_coverage"] = not cov
        try:
            check_sleeve_price_rules(scored, px, cfg, a, pol, rows, today)
        except Exception as e:
            log(f"Adjustments: small-company price-rule check skipped ({e})")
    # how buy picks are scored: hand-set weights, or weights learned each year from earlier trades only.
    # The learned side is the never-seen-years test, so its result has no hindsight in it.
    if wf is not None:
        label = "How buy picks are scored"
        cv = wf["curves"].pct_change().fillna(0)
        learned, regular = cv["Never-seen-years picks"], cv["Regular backtest, same years"]
        cur_m = pol.get("main_method", "hand")
        new, old = (learned, regular) if cur_m == "hand" else (regular, learned)
        c = _compare(new, old)
        names_ = {"hand": "Hand-set weights", "learned": "Learned from earlier trades"}
        alt = "learned" if cur_m == "hand" else "hand"
        ann = lambda d: float(_perf(d)[0]["Annual return (CAGR)"] - _perf(cv["S&P 500 (SPY)"])[0]["Annual return (CAGR)"])
        rows.append({"Setting": label, "Option": names_[cur_m] + " (current)", "Per year vs S&P": ann(old),
                     "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
        rows.append({"Setting": label, "Option": names_[alt], "Per year vs S&P": ann(new),
                     "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                     "Verdict": "Switched automatically" if clearly_better(c) else _verdict(c, True), **_sure_fields(c)})
        if clearly_better(c):
            a["history"].append({"date": today, "change": label, "from": names_[cur_m], "to": names_[alt],
                                 "gain_per_year": c["gain"], "sureness": c["t"]})
            log(f"Adjustments: {label} -> {names_[alt]} (+{c['gain']*100:.1f}%/yr)")
            pol["main_method"] = alt
            if alt == "learned":                      # the learned method brings its own holding period
                try:
                    h = int(json.load(open(_p(cfg, "state", "learned_weights.json"))).get("hold", pol["hold_other"]))
                    pol["hold_other"] = pol["hold_small"] = h
                except Exception:
                    pass
    try:
        check_exit_rule(scored, px, cfg, a, pol, rows, today)
    except Exception as e:
        log(f"Adjustments: sell-when-member-sells check skipped ({e})")
    try:
        check_trial_signals(scored, px, cfg, a, pol, rows, today)
        a["trial_checked"] = sorted(TRIAL_SIGNALS)
    except Exception as e:
        log(f"Adjustments: new-signal check skipped ({e})")
    try:
        check_sizing(scored, px, cfg, a, pol, rows, today)
    except Exception as e:
        log(f"Adjustments: sizing check skipped ({e})")
    try:
        check_price_rules(scored, px, cfg, a, pol, rows, today)
    except Exception as e:
        log(f"Adjustments: price-rule check skipped ({e})")
    a["policy"] = pol
    rows = recheck_rows + rows

    # proposals: measured the same way, never applied without the user's go-ahead
    props, r_cur = [], _long_daily(cur)
    for name, key, opts in (("How high a score a buy needs (percentile)", "PICK_PERCENTILE", (70, 90)),
                            ("Picks per week", "PICKS_PER_WEEK", (3, 8))):
        props.append({"Setting": name, "Option": f"{cfg[key]} (current)", "Per year vs S&P": vs_spy(cur),
                      "Gain vs current": 0.0, "Sureness": None, "Verdict": "Current"})
        for o in opts:
            b = run(pol, **{key: o})
            c = _compare(_long_daily(b), r_cur)
            props.append({"Setting": name, "Option": str(o), "Per year vs S&P": vs_spy(b),
                          "Gain vs current": c["gain"] if c else None, "Sureness": c["t"] if c else None,
                          "Verdict": _verdict(c, False, len(opts)), **_sure_fields(c, len(opts))})
    a.update({"evaluated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "evaluation": rows, "proposals": props})
    save_adaptive(cfg, a)
    return a


def decide_tuned(cfg, tuned):
    """Use the tuned weights automatically only when they clearly beat the defaults on years they never saw."""
    a = load_adaptive(cfg)
    try:
        c = _compare(_long_daily(tuned["tuned_bt"]), _long_daily(tuned["default_bt"]))
    except Exception:
        c = None
    use = clearly_better(c)
    if use != bool(a.get("use_tuned")):
        a["history"].append({"date": dt.date.today().isoformat(), "change": "Tuned signal weights",
                             "from": "on" if a.get("use_tuned") else "off", "to": "on" if use else "off",
                             "gain_per_year": c["gain"] if c else None, "sureness": c["t"] if c else None})
    a["use_tuned"] = use
    a["tuned_eval"] = {"gain": c["gain"] if c else None, "sureness": c["t"] if c else None,
                       "verdict": _verdict(c, True) if use else ("Kept off: " + _verdict(c, True).lower())}
    save_adaptive(cfg, a)
    return a


# ############################################################################
#  WALL STREET VIEW AT THE TIME OF THE TRADE: consensus rebuilt from analyst rating changes
# ############################################################################
GRADE_POS = ("strong buy", "buy", "outperform", "overweight", "accumulate", "positive", "add", "top pick",
             "conviction buy", "long-term buy", "speculative buy", "sector outperform", "market outperform", "strong-buy")
GRADE_NEU = ("hold", "neutral", "equal-weight", "equal weight", "market perform", "sector perform", "in-line", "in line",
             "peer perform", "perform", "mixed", "fair value", "sector weight", "peer weight", "market weight")
GRADE_NEG = ("sell", "underperform", "underweight", "reduce", "strong sell", "negative", "sector underperform",
             "market underperform")


def grade_score(g):
    g = str(g or "").strip().lower()
    if not g:
        return None
    if any(g == x for x in GRADE_NEG) or "underperform" in g or "underweight" in g or "sell" in g:
        return -1
    if any(g == x for x in GRADE_NEU):
        return 0
    if any(g == x for x in GRADE_POS) or "outperform" in g or "overweight" in g or "buy" in g:
        return 1
    return None


def collect_analyst_history(cfg, tickers, per_run=700):
    """Every analyst upgrade/downgrade on record for each stock (Yahoo Finance), cached for two weeks."""
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cache, path = _cache_json(cfg, "analyst_history.json", {})
    todo = [t for t in tickers if not _fresh(cache.get(t, {}).get("fetched", ""), 14)][:per_run]
    if todo:
        log(f"Analyst ratings: downloading rating history for {len(todo)} stocks")

    def work(t):
        try:
            d = yf.Ticker(t).upgrades_downgrades
        except Exception:
            return t, None
        if d is None or not len(d):
            return t, []
        d = d.reset_index()
        dc = next((c for c in d.columns if "date" in str(c).lower()), d.columns[0])
        rows = []
        for r in d.to_dict("records"):
            when = pd.to_datetime(r.get(dc), errors="coerce")
            if pd.isna(when):
                continue
            rows.append([when.strftime("%Y-%m-%d"), str(r.get("Firm") or ""), str(r.get("ToGrade") or ""),
                         str(r.get("FromGrade") or ""), str(r.get("Action") or "")])
        return t, rows

    for i, (t, rows) in enumerate(chunked_map(work, todo, 4, cfg), 1):
        if rows is not None:
            cache[t] = {"fetched": dt.datetime.now().isoformat(), "rows": rows}
        if i % 100 == 0:
            json.dump(cache, open(path, "w"))
    json.dump(cache, open(path, "w"))
    return cache


def archive_analysts(cfg, frames):
    """Keep today's ratings for watchlist stocks, building an exact point-in-time record going forward."""
    path = _p(cfg, "state", "analyst_snapshots.json")
    try:
        arc = json.load(open(path))
    except Exception:
        arc = {}
    day = dt.date.today().isoformat()
    snap = arc.setdefault(day, {})
    for f in frames:
        if f is None or not len(f) or "analysts" not in f:
            continue
        for t, a, u in zip(f["ticker"], f["analysts"], f.get("target_upside_%", pd.Series(np.nan, index=f.index))):
            if isinstance(a, str) and a:
                snap[t] = {"rec": a, "upside": None if pd.isna(u) else round(float(u), 1)}
    json.dump(arc, open(path, "w"))


def analyst_features(tx, data):
    """Consensus when the member traded (from rating changes), downgrades just before, and stocks nobody covers."""
    n = len(tx)
    tx["f_against_street"], tx["f_after_downgrade"], tx["f_no_coverage"] = 0.0, 0.0, 0.0
    tx["street_note"] = ""
    hist = data.get("analyst_history") or {}
    if not hist:
        return tx
    per = {}
    for t, v in hist.items():
        rows = []
        for d, firm, to, fr, act in v.get("rows", []):
            s_to, s_fr = grade_score(to), grade_score(fr)
            if s_to is None:
                continue
            down = act.lower() == "down" or (s_fr is not None and s_to < s_fr)
            rows.append((pd.Timestamp(d), firm, s_to, down))
        rows.sort(key=lambda x: x[0])
        per[t] = rows
    ag, dn, nc, note = np.zeros(n), np.zeros(n), np.zeros(n), [""] * n
    label = {-1: "sell", 0: "hold", 1: "buy"}
    for i, (t, td, typ) in enumerate(zip(tx["ticker"], tx["trade_date"], tx["tx_type"])):
        if typ != "buy" or t not in hist or pd.isna(td):
            continue
        rows = per.get(t, [])
        latest = {}
        recent_down = False
        for d, firm, s, down in rows:
            if d > td:
                break
            if d > td - pd.Timedelta(days=540):
                latest[firm] = s
            if down and d > td - pd.Timedelta(days=30):
                recent_down = True
        if not latest:
            # only call it "no coverage" when the rating record reaches back far enough to know;
            # older years are thin in the source, and treating a gap in the record as "nobody covered it" would
            # mostly mark the era, not the company
            if rows and rows[0][0] <= td - pd.Timedelta(days=540):
                nc[i] = 1.0
                note[i] = "no analyst ratings on record when they bought"
            continue
        avg = float(np.mean(list(latest.values())))
        if len(latest) >= 2 and avg <= 0.2:
            ag[i] = 1.0
            note[i] = f"bought against Wall Street (consensus about {label[int(round(avg))]}, {len(latest)} analysts)"
        if recent_down:
            dn[i] = 1.0
            note[i] = (note[i] + "; " if note[i] else "") + "bought within 30 days after an analyst downgrade"
    tx["f_against_street"], tx["f_after_downgrade"], tx["f_no_coverage"], tx["street_note"] = ag, dn, nc, note
    return tx


# ############################################################################
#  SMALL-COMPANY STRATEGY: every timely purchase of a company under $2B, held longer
# ############################################################################
SMALL_ETF = "IWM"          # Russell 2000, the usual small-company benchmark


def small_cap_rows(scored, coverage=False, hold=None):
    """Purchases of companies under $2B (at the time), filed on time, at most one entry per stock per 30 days.
    With coverage=True, only companies at least one Wall Street analyst covered when the member bought."""
    b = scored[(scored["tx_type"] == "buy") & scored["entry_px"].notna()]
    if "f_small_cap" not in b or not len(b):
        return b.iloc[0:0]
    b = b[(b["f_small_cap"].fillna(0) >= 1) & (b["lag_days"].fillna(999) <= 45)]
    if coverage and "f_no_coverage" in b:
        b = b[b["f_no_coverage"].fillna(0) == 0]
    b = b.sort_values("filed_date")
    keep, last = [], {}
    gap = max(30, int(hold * 7 / 5)) if hold else 30      # not again while the earlier position is still held
    for i, (t, d) in enumerate(zip(b["ticker"], b["filed_date"])):
        if t in last and (d - last[t]).days < gap:
            continue
        last[t] = d
        keep.append(i)
    return b.iloc[keep]


def export_research(scored, px, cfg, max_cap=1e10):
    """Every timely purchase of a company under $10B, with all its signal values and what the stock did
    afterwards (vs the S&P 500 and the small-company index), for studying what the big winners share."""
    b = scored[(scored["tx_type"] == "buy") & (scored["lag_days"].fillna(999) <= 45)
               & scored["market_cap"].notna() & (scored["market_cap"] < max_cap)].copy()
    if not len(b):
        return None
    text = ("ticker", "member", "chamber", "state", "party", "sector", "industry", "owner", "trade_date", "filed_date", "bioguide")
    keep = [c for c in b.columns if c in text or (pd.api.types.is_numeric_dtype(b[c]) or pd.api.types.is_bool_dtype(b[c]))]
    out = b[[c for c in keep if c in b.columns]].copy()
    for h in (20, 60, 125, 250):
        fr = forward_returns(b, px, h)
        out[f"ret_{h}"] = fr["ret"]
        out[f"vs_spy_{h}"] = fr["excess"]
        out[f"closed_{h}"] = fr["closed"]
        if SMALL_ETF in px.columns:
            p2 = pd.DataFrame({"SPY": px[SMALL_ETF]})       # same math with the small-company index
            out[f"vs_iwm_{h}"] = fr["ret"] - forward_returns(b.assign(ticker="SPY"), p2, h)["ret"]
    path = os.path.join(cfg["DATA_DIR"], "research_buys.csv.gz")
    out.to_csv(path, index=False, compression="gzip")
    log(f"Research export: {len(out):,} purchases saved")
    return path


def small_cap_backtest(scored, px, cfg, hold=None, coverage=None, price_exit=None, extend=None):
    pol = load_adaptive(cfg)["policy"]
    coverage = bool(pol.get("sleeve_coverage")) if coverage is None else coverage
    hold = int(hold or pol.get("hold_sleeve", 250))
    rows = small_cap_rows(scored, coverage, hold=hold)
    if len(rows) < 50:
        return None
    cost = cfg.get("COST_BPS", 0) / 1e4
    pe = pol.get("sleeve_price_exit", "none") if price_exit is None else price_exit
    ex = pol.get("sleeve_extend", "none") if extend is None else extend
    ep = price_exit_positions(rows, px, hold, pe, ex) if (pe != "none" or ex != "none") else None
    fr = forward_returns(rows, px, hold, cost=cost, exit_pos=ep)
    picks = rows.drop(columns=[c for c in fr.columns if c in rows.columns]).join(fr)
    idx = px.index[px.index >= picks["entry_date"].min()]
    weeks = max(1.0, (picks["filed_date"].max() - picks["filed_date"].min()).days / 7)
    slots = max(5, int(round(len(picks) / weeks * hold / 5)))          # positions open at once, on average
    ld, n_open = _portfolio_slots(picks, px, idx, slots, "spy", cost=cost)
    spy = px["SPY"].pct_change(fill_method=None).reindex(idx)
    series = {"Small-company picks": ld, "S&P 500 (SPY)": spy}
    if SMALL_ETF in px.columns:
        series["Small-company index (IWM)"] = px[SMALL_ETF].pct_change(fill_method=None).reindex(idx)
    perf, curves = {}, {}
    for k, d in series.items():
        perf[k], curves[k] = _perf(d)
        if k != "S&P 500 (SPY)":
            ann, lo_, hi_ = excess_range(d.fillna(0), spy.fillna(0))
            perf[k].update({"Per year vs S&P 500": ann, "Likely range low": lo_, "Likely range high": hi_})
    years = pd.DataFrame(series).fillna(0).groupby(pd.DataFrame(series).index.year).apply(lambda g: (1 + g).prod() - 1)
    return {"perf": pd.DataFrame(perf), "curves": pd.DataFrame(curves), "trade_stats": _trade_stats(picks, 1),
            "picks": picks, "slots": slots, "hold": hold, "years": years,
            "per_year_trades": float(len(picks) / weeks * 52)}
