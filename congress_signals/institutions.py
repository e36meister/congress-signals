"""Institutional data, each record dated by when it became public (no peeking):
  - Schedule 13D (activist stakes over 5%, due within days) and 13G (passive 5% stakes) from EDGAR's form indexes
  - short interest twice a month from FINRA (from 2018; published ~7 business days after the settlement date)
  - quarterly fund holdings (13F) from the SEC's 13F data sets, mapped from CUSIP to ticker with OpenFIGI

Collected by update_institutions.py after trading (never slows the trade), kept in INST_DIR (default ./inst),
which GitHub caches separately from ./data. inst_features() turns them into scoring inputs."""
import os, io, re, json, time, zipfile
import numpy as np
import pandas as pd
import requests

FH = {"Accept": "application/json", "Content-Type": "application/json"}
SI_LAG_DAYS = 14          # short interest: FINRA publishes ~7 business days after settlement; 14 calendar days is safe
F13_LAG_DAYS = 46         # 13F: due 45 days after the quarter; counted only once every on-time report is public
D13_WINDOW = 180          # an activist 13D counts for 6 months
SCHED_FORMS = {"SC 13D": "13D", "SCHEDULE 13D": "13D", "SC 13D/A": "13D/A", "SCHEDULE 13D/A": "13D/A",
               "SC 13G": "13G", "SCHEDULE 13G": "13G"}


def _E():
    from . import engine as E
    return E


def _dir(cfg, *parts):
    d = os.path.join(cfg.get("INST_DIR") or "inst", *parts)
    os.makedirs(d, exist_ok=True)
    return d


def _changed(cfg):
    open(os.path.join(_dir(cfg), ".changed"), "w").write(str(time.time()))


def _old(path, hours):
    return not os.path.exists(path) or (time.time() - os.path.getmtime(path)) / 3600 > hours


# ----------------------------------------------------------------------------
# company id (CIK) -> ticker symbols
# ----------------------------------------------------------------------------
def cik_tickers(cfg):
    """Every symbol each company has used (SEC insider filings since the start date) plus today's SEC list."""
    E = _E()
    seen, _ = E._cache_json(cfg, "ticker_cik.json", {})
    out = {}
    for t, (c, _d) in seen.items():
        out.setdefault(int(c), set()).add(t)
    path = os.path.join(_dir(cfg), "company_tickers.json")
    hdr = E._sec_headers(cfg)
    if hdr and _old(path, 24 * 7):
        try:
            js = requests.get("https://www.sec.gov/files/company_tickers.json", headers=hdr, timeout=60).json()
            json.dump(js, open(path, "w"))
        except Exception:
            pass
    if os.path.exists(path):
        for v in json.load(open(path)).values():
            t = E.clean_ticker(v["ticker"])
            if t:
                out.setdefault(int(v["cik_str"]), set()).add(t)
    return out


# ----------------------------------------------------------------------------
# Schedule 13D / 13G
# ----------------------------------------------------------------------------
_IDX = re.compile(r"^(\S(?:.*?\S)?)\s{2,}.*?\s{2,}(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)")


def collect_sched13(cfg, budget_end):
    """13D/13G filings per quarter from EDGAR's form index (each filing is listed once for every company on it:
    the subject company and each filer)."""
    E = _E()
    hdr = E._sec_headers(cfg)
    if not hdr:
        return
    qd = _dir(cfg, "sched13")
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=400)
    now = pd.Timestamp.today()
    for p in pd.period_range(start, now, freq="Q"):
        path = os.path.join(qd, f"{p.year}q{p.quarter}.json")
        done = p.end_time + pd.Timedelta(days=7) < now
        if os.path.exists(path) and (done or not _old(path, 12)):
            continue
        if time.time() > budget_end:
            return
        try:
            r = requests.get(f"https://www.sec.gov/Archives/edgar/full-index/{p.year}/QTR{p.quarter}/form.idx",
                             headers=hdr, timeout=300)
            if r.status_code != 200:
                continue
            rows = []
            for line in r.text.splitlines():
                if "13" not in line[:16]:
                    continue
                m = _IDX.match(line)
                if m and m.group(1) in SCHED_FORMS:
                    acc = m.group(4).rsplit("/", 1)[-1].replace(".txt", "")
                    rows.append([SCHED_FORMS[m.group(1)], int(m.group(2)), m.group(3), acc])
            json.dump(rows, open(path, "w"))
            _changed(cfg)
            E.log(f"Institutions: 13D/13G index {p.year} Q{p.quarter}: {len(rows):,} lines")
            time.sleep(0.5)
        except Exception as e:
            E.log(f"Institutions: 13D/13G index {p.year} Q{p.quarter} failed ({type(e).__name__})")


def _subject_from_header(cfg, cik, acc, cache):
    """For a filing where more than one company has a ticker: read the SUBJECT COMPANY from the filing header."""
    if acc in cache:
        return cache[acc]
    E = _E()
    try:
        u = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index-headers.html"
        r = requests.get(u, headers=E._sec_headers(cfg), timeout=30)
        m = re.search(r"SUBJECT COMPANY:.*?CENTRAL INDEX KEY:\s*(\d+)", r.text, re.S)
        cache[acc] = int(m.group(1)) if m else None
        time.sleep(0.12)
    except Exception:
        return None
    return cache[acc]


def sched13_events(cfg, budget_end=None):
    """One row per (ticker, filing): ticker, date filed, kind (13D, 13D/A, 13G)."""
    qd = _dir(cfg, "sched13")
    rows = []
    for fn in sorted(os.listdir(qd)):
        rows += json.load(open(os.path.join(qd, fn)))
    if not rows:
        return pd.DataFrame(columns=["ticker", "date", "kind"])
    df = pd.DataFrame(rows, columns=["kind", "cik", "date", "acc"])
    c2t = cik_tickers(cfg)
    df["has_t"] = df["cik"].isin(c2t.keys())
    hpath = os.path.join(_dir(cfg), "sched13_subjects.json")
    hcache = {k: v for k, v in (json.load(open(hpath)).items() if os.path.exists(hpath) else [])}
    n0 = len(hcache)
    out = []
    for acc, g in df[df["has_t"]].groupby("acc", sort=False):
        ciks = list(dict.fromkeys(g["cik"]))
        subj = ciks[0] if len(ciks) == 1 else None
        if subj is None and budget_end is not None and time.time() < budget_end:
            subj = _subject_from_header(cfg, ciks[0], acc, hcache)
        elif subj is None:
            subj = hcache.get(acc)
        if subj is None or subj not in c2t:
            continue
        r = g.iloc[0]
        for t in c2t[subj]:
            out.append((t, r["date"], r["kind"]))
    if len(hcache) > n0:
        json.dump(hcache, open(hpath, "w"))
        _changed(cfg)
    ev = pd.DataFrame(out, columns=["ticker", "date", "kind"]).drop_duplicates()
    ev["date"] = pd.to_datetime(ev["date"])
    return ev


# ----------------------------------------------------------------------------
# FINRA short interest
# ----------------------------------------------------------------------------
def _finra(body, tries=4):
    for i in range(tries):
        try:
            r = requests.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest",
                              headers=FH, json=body, timeout=120)
            if r.status_code == 204:
                return []
            if r.status_code == 200:
                return r.json()
            if r.status_code not in (429, 500, 502, 503, 504):
                return None
        except Exception:
            pass
        time.sleep(5 * (i + 1))
    return None


def _settlement_dates(cfg):
    """Every settlement date FINRA has, read off one always-listed stock's history."""
    got = set()
    for sym in ("AAPL", "MSFT", "SPY"):
        js = _finra({"limit": 5000, "fields": ["settlementDate"],
                     "compareFilters": [{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": sym}],
                     "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": "2017-06-01",
                                           "endDate": pd.Timestamp.today().strftime("%Y-%m-%d")}]})
        if js:
            got |= {r["settlementDate"] for r in js if r.get("settlementDate")}
    return sorted(got)


def collect_short_interest(cfg, budget_end):
    E = _E()
    sd = _dir(cfg, "short")
    dates = _settlement_dates(cfg)
    if not dates:
        E.log("Institutions: FINRA short interest dates unavailable")
        return
    fields = ["symbolCode", "currentShortPositionQuantity", "previousShortPositionQuantity",
              "averageDailyVolumeQuantity", "daysToCoverQuantity", "marketClassCode"]
    n = 0
    for d in dates:
        path = os.path.join(sd, d + ".pkl")
        if os.path.exists(path) or time.time() > budget_end:
            continue
        rows, off = [], 0
        while True:
            js = _finra({"limit": 5000, "offset": off, "fields": fields,
                         "compareFilters": [{"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": d}]})
            if js is None:
                rows = None
                break
            rows += js
            if len(js) < 5000:
                break
            off += 5000
        if not rows:
            continue
        f = pd.DataFrame(rows)
        f = f[f["marketClassCode"].astype(str).str.upper() != "OTC"]
        f = pd.DataFrame({"ticker": f["symbolCode"].map(E.clean_ticker),
                          "short": pd.to_numeric(f["currentShortPositionQuantity"], errors="coerce").astype("float32"),
                          "prev": pd.to_numeric(f["previousShortPositionQuantity"], errors="coerce").astype("float32"),
                          "adv": pd.to_numeric(f["averageDailyVolumeQuantity"], errors="coerce").astype("float32"),
                          "dtc": pd.to_numeric(f["daysToCoverQuantity"], errors="coerce").astype("float32")}).dropna(subset=["ticker"])
        f.to_pickle(path)
        n += 1
        _changed(cfg)
    if n:
        E.log(f"Institutions: short interest for {n} new settlement date(s); {len(dates)} on file from {dates[0]}")


def short_table(cfg):
    sd = _dir(cfg, "short")
    fr = []
    for fn in sorted(os.listdir(sd)):
        f = pd.read_pickle(os.path.join(sd, fn))
        f["settle"] = pd.Timestamp(fn[:-4])
        fr.append(f)
    if not fr:
        return pd.DataFrame(columns=["ticker", "short", "prev", "adv", "dtc", "settle", "avail"])
    s = pd.concat(fr, ignore_index=True)
    s["avail"] = s["settle"] + pd.Timedelta(days=SI_LAG_DAYS)
    return s


# ----------------------------------------------------------------------------
# 13F fund holdings
# ----------------------------------------------------------------------------
def _f13_zips(cfg):
    E = _E()
    r = requests.get("https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
                     headers=E._sec_headers(cfg), timeout=60)
    links = sorted(set(re.findall(r'href="([^"]+form13f\.zip)"', r.text)))
    return [("https://www.sec.gov" + u if u.startswith("/") else u) for u in links]


def collect_13f(cfg, budget_end):
    """Per quarter and CUSIP: how many funds held it (original 13F-HR reports filed on time, stock positions only)."""
    E = _E()
    hdr = E._sec_headers(cfg)
    if not hdr:
        return
    fd = _dir(cfg, "f13")
    try:
        zips = _f13_zips(cfg)
    except Exception as e:
        E.log(f"Institutions: 13F list failed ({type(e).__name__})")
        return
    start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=500)
    for u in zips:
        name = u.rsplit("/", 1)[-1].replace(".zip", "")
        path = os.path.join(fd, name + ".pkl")
        if os.path.exists(path):
            continue
        if time.time() > budget_end - 240:          # one file takes up to ~4 minutes
            return
        try:
            t0 = time.time()
            r = requests.get(u, headers=hdr, timeout=900)
            if r.status_code != 200:
                continue
            z = zipfile.ZipFile(io.BytesIO(r.content))
            sub = E._read_tsv(z, "SUBMISSION.TSV", ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"])
            sub = sub[sub["SUBMISSIONTYPE"].str.upper() == "13F-HR"]
            sub["filed"] = pd.to_datetime(sub["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
            sub["period"] = pd.to_datetime(sub["PERIODOFREPORT"], format="%d-%b-%Y", errors="coerce")
            sub = sub[(sub["filed"] <= sub["period"] + pd.Timedelta(days=45)) & (sub["period"] >= start)]
            info = E._read_tsv(z, "INFOTABLE.TSV", ["ACCESSION_NUMBER", "CUSIP", "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL",
                                                    "NAMEOFISSUER", "TITLEOFCLASS"])
            info = info[info["ACCESSION_NUMBER"].isin(set(sub["ACCESSION_NUMBER"]))]
            info = info[(info["PUTCALL"].fillna("").str.strip() == "") & (info["SSHPRNAMTTYPE"].str.upper() == "SH")]
            info = info.merge(sub[["ACCESSION_NUMBER", "CIK", "period"]], on="ACCESSION_NUMBER")
            info["CUSIP"] = info["CUSIP"].str.upper().str.strip().str[:9]
            info["sh"] = pd.to_numeric(info["SSHPRNAMT"], errors="coerce")
            agg = info.groupby(["period", "CUSIP"]).agg(holders=("CIK", "nunique"), shares=("sh", "sum"),
                                                        name=("NAMEOFISSUER", "first"), cls=("TITLEOFCLASS", "first")).reset_index()
            agg["holders"] = agg["holders"].astype("int32")
            agg["shares"] = agg["shares"].astype("float64")
            agg.to_pickle(path)
            _changed(cfg)
            E.log(f"Institutions: 13F {name}: {sub['ACCESSION_NUMBER'].nunique():,} reports, {len(agg):,} holdings "
                  f"({time.time() - t0:.0f}s)")
            del z, info, r
        except Exception as e:
            E.log(f"Institutions: 13F {name} failed ({type(e).__name__}: {e})")


def f13_table(cfg):
    fd = _dir(cfg, "f13")
    fr = [pd.read_pickle(os.path.join(fd, fn)) for fn in sorted(os.listdir(fd)) if fn.endswith(".pkl")]
    if not fr:
        return pd.DataFrame(columns=["period", "CUSIP", "holders", "shares", "name", "cls"])
    a = pd.concat(fr, ignore_index=True)
    # a quarter's reports can straddle two files: add them up
    return a.groupby(["period", "CUSIP"]).agg(holders=("holders", "sum"), shares=("shares", "sum"),
                                             name=("name", "first"), cls=("cls", "first")).reset_index()


def map_cusips(cfg, budget_end, min_holders=10):
    """CUSIP -> ticker with OpenFIGI (free: 25 requests a minute, 10 CUSIPs each; faster with OPENFIGI_API_KEY)."""
    E = _E()
    path = os.path.join(_dir(cfg), "cusip_ticker.json")
    m = json.load(open(path)) if os.path.exists(path) else {}
    a = f13_table(cfg)
    if not len(a):
        return m
    top = a.groupby("CUSIP")["holders"].max()
    todo = [c for c in top[top >= min_holders].sort_values(ascending=False).index
            if c not in m and re.fullmatch(r"[0-9A-Z]{9}", c or "")]
    key = os.environ.get("OPENFIGI_API_KEY", "")
    per, wait = (100, 0.25) if key else (10, 2.45)
    h = dict(FH, **({"X-OPENFIGI-APIKEY": key} if key else {}))
    n = 0
    for i in range(0, len(todo), per):
        if time.time() > budget_end:
            break
        batch = todo[i:i + per]
        try:
            r = requests.post("https://api.openfigi.com/v3/mapping", headers=h, timeout=60,
                              json=[{"idType": "ID_CUSIP", "idValue": c} for c in batch])
            if r.status_code == 429:
                time.sleep(30)
                continue
            if r.status_code != 200:
                break
            for c, res in zip(batch, r.json()):
                t = None
                for d in res.get("data") or []:
                    if d.get("exchCode") == "US" and d.get("marketSector") == "Equity":
                        t = E.clean_ticker((d.get("ticker") or "").replace("/", "-"))
                        if t:
                            break
                m[c] = t
            n += len(batch)
        except Exception:
            time.sleep(5)
        time.sleep(wait)
    if n:
        json.dump(m, open(path, "w"))
        _changed(cfg)
        E.log(f"Institutions: mapped {n:,} CUSIPs to tickers ({len(todo) - n:,} left)")
    return m


def f13_by_ticker(cfg):
    """Funds holding each ticker per quarter, with the day that quarter's count became fully public."""
    a = f13_table(cfg)
    path = os.path.join(_dir(cfg), "cusip_ticker.json")
    m = json.load(open(path)) if os.path.exists(path) else {}
    if not len(a) or not m:
        return pd.DataFrame(columns=["ticker", "period", "holders", "avail"])
    a["ticker"] = a["CUSIP"].map(m)
    a = a.dropna(subset=["ticker"])
    t = a.groupby(["ticker", "period"])["holders"].max().reset_index()
    t["avail"] = t["period"] + pd.Timedelta(days=F13_LAG_DAYS)
    return t


# ----------------------------------------------------------------------------
# collection entry point
# ----------------------------------------------------------------------------
def update_all(cfg, minutes):
    E = _E()
    end = time.time() + minutes * 60
    try:
        os.remove(os.path.join(_dir(cfg), ".changed"))      # set again only if something new is saved
    except OSError:
        pass
    for name, fn in (("13D/13G", collect_sched13), ("short interest", collect_short_interest), ("13F", collect_13f),
                     ("CUSIPs", map_cusips)):
        if time.time() > end:
            break
        try:
            fn(cfg, end)
        except Exception as e:
            E.log(f"Institutions: {name} step failed ({type(e).__name__}: {e})")
    try:
        sched13_events(cfg, budget_end=end)         # resolves (and caches) the subject of shared filings
    except Exception as e:
        E.log(f"Institutions: 13D subjects failed ({type(e).__name__}: {e})")
    st = status(cfg)
    E.log("Institutions: on file: " + ", ".join(f"{k} {v}" for k, v in st.items()))
    return st


def status(cfg):
    d = lambda *p: os.listdir(_dir(cfg, *p))
    path = os.path.join(_dir(cfg), "cusip_ticker.json")
    m = json.load(open(path)) if os.path.exists(path) else {}
    return {"13D/13G quarters": len(d("sched13")), "short-interest dates": len(d("short")),
            "13F files": len([f for f in d("f13") if f.endswith(".pkl")]), "CUSIPs mapped": sum(1 for v in m.values() if v)}


# ----------------------------------------------------------------------------
# scoring inputs
# ----------------------------------------------------------------------------
_CACHE = {}


def _asof(tx, table, by, on, cols):
    """For each trade, the newest row of `table` for its ticker that was public strictly before the filing day."""
    if not len(table):
        return pd.DataFrame(index=tx.index, columns=cols, dtype=float)
    left = pd.DataFrame({"ticker": tx["ticker"].values, "d": pd.to_datetime(tx["filed_date"]).values - np.timedelta64(1, "D"),
                         "i": np.arange(len(tx))}).dropna(subset=["d"]).sort_values("d")
    right = table[[by, on] + cols].dropna(subset=[on]).sort_values(on).rename(columns={by: "ticker", on: "d"})
    right = right[right["ticker"].isin(set(left["ticker"]))]
    m = pd.merge_asof(left, right, on="d", by="ticker", direction="backward")
    return m.set_index("i").reindex(np.arange(len(tx)))[cols].set_axis(tx.index)


def inst_features(tx, cfg):
    """f_activist_13d: an activist (13D) filed on the company in the 6 months before the member's filing.
    f_new_5pct: a new passive 5% holder (13G) in the 6 months before.
    f_short_heavy: short sellers hold 8+ days of trading volume (the latest report public by then).
    f_short_jump: short interest up 50%+ over the last ~month (and at least 2 days of volume).
    f_funds_adding / f_funds_leaving: number of funds holding it (13F) up / down 10%+ over the last quarter on file."""
    E = _E()
    t0 = time.time()
    tx = tx.copy()
    for c in ("f_activist_13d", "f_new_5pct", "f_short_heavy", "f_short_jump", "f_funds_adding", "f_funds_leaving"):
        tx[c] = 0.0
    tx["short_dtc"], tx["funds_chg"], tx["inst_note"] = np.nan, np.nan, ""
    if not os.path.isdir(cfg.get("INST_DIR") or "inst"):
        return tx
    key = (cfg.get("INST_DIR") or "inst")
    if key not in _CACHE:
        try:
            _CACHE[key] = (sched13_events(cfg), short_table(cfg), f13_by_ticker(cfg))
        except Exception as e:
            E.log(f"Institutions: couldn't load ({type(e).__name__}: {e})")
            return tx
    ev, si, ft = _CACHE[key]
    fd = pd.to_datetime(tx["filed_date"]).values
    notes = [[] for _ in range(len(tx))]

    # 13D / 13G: any initial filing in the window before the filing day
    for kind, col, label in (("13D", "f_activist_13d", "activist 13D"), ("13G", "f_new_5pct", "new 5% holder (13G)")):
        e = ev[ev["kind"] == kind]
        by = {t: np.sort(g["date"].values) for t, g in e.groupby("ticker")}
        v = np.zeros(len(tx))
        for i, (t, d) in enumerate(zip(tx["ticker"], fd)):
            a = by.get(t)
            if a is None or pd.isna(d):
                continue
            lo = np.searchsorted(a, d - np.timedelta64(D13_WINDOW, "D"), side="left")
            hi = np.searchsorted(a, d, side="left")              # strictly before the filing day
            if hi > lo:
                v[i] = 1.0
                notes[i].append(label)
        tx[col] = v

    # short interest: the newest report public before the filing day, and the one ~a month earlier
    if len(si):
        s = si.sort_values(["ticker", "settle"])
        s["short_1m"] = s.groupby("ticker")["short"].shift(2)
        cur = _asof(tx, s, "ticker", "avail", ["dtc", "short", "short_1m", "settle"])
        fresh = (pd.to_datetime(tx["filed_date"]) - pd.to_datetime(cur["settle"])).dt.days <= 45
        dtc = pd.to_numeric(cur["dtc"], errors="coerce").where(fresh)
        jump = (pd.to_numeric(cur["short"], errors="coerce") / pd.to_numeric(cur["short_1m"], errors="coerce")).where(fresh)
        tx["short_dtc"] = dtc.values
        tx["f_short_heavy"] = (dtc >= 8).astype(float).values
        tx["f_short_jump"] = ((jump >= 1.5) & (dtc >= 2)).astype(float).values
        for i in np.nonzero(tx["f_short_heavy"].values)[0]:
            notes[i].append(f"short sellers hold {dtc.iloc[i]:.0f} days of volume")

    # 13F: change in the number of funds holding it, latest quarter public vs the quarter before
    if len(ft):
        f = ft.sort_values(["ticker", "period"])
        f["prev"] = f.groupby("ticker")["holders"].shift(1)
        f["prev_period"] = f.groupby("ticker")["period"].shift(1)
        ok = (f["period"] - f["prev_period"]).dt.days.between(80, 100)
        f["chg"] = (f["holders"] / f["prev"] - 1).where(ok & (f["prev"] >= 5))
        cur = _asof(tx, f, "ticker", "avail", ["chg", "period"])
        fresh = (pd.to_datetime(tx["filed_date"]) - pd.to_datetime(cur["period"])).dt.days <= 150
        chg = pd.to_numeric(cur["chg"], errors="coerce").where(fresh)
        tx["funds_chg"] = chg.values
        tx["f_funds_adding"] = (chg >= 0.10).astype(float).values
        tx["f_funds_leaving"] = (chg <= -0.10).astype(float).values
        for i in np.nonzero(tx["f_funds_adding"].values)[0]:
            notes[i].append(f"funds holding it up {chg.iloc[i] * 100:.0f}% last quarter")
    tx["inst_note"] = ["; ".join(n) for n in notes]
    E.log(f"Institutions: scoring inputs in {time.time() - t0:.0f}s "
          f"(13D {int(tx['f_activist_13d'].sum())}, 13G {int(tx['f_new_5pct'].sum())}, heavy short {int(tx['f_short_heavy'].sum())}, "
          f"short jump {int(tx['f_short_jump'].sum())}, funds adding {int(tx['f_funds_adding'].sum())}, leaving {int(tx['f_funds_leaving'].sum())})")
    return tx
