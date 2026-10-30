"""Bonds, Treasuries and other non-stock trades (owner, Oct 29).

The trade readers keep only stocks, ETFs and options for the picks. Members also file municipal bonds, Treasury
bills, corporate bonds and private funds; those rows are kept here, apart from the picks, for:
  - the "moving to safety" gauge (stocks vs bonds/Treasuries, by party, chamber, committee, trading partners),
  - the "they helped fund it" flag (a member buying bonds of a place or project they steered money to),
  - bond -> company links (a corporate bond is a view on that company).

Saved in data/cache/other_tx.pkl, one row per filed transaction:
  member, first, last, chamber, state, trade_date, filed_date, tx_type, tx_raw, amt_lo, amt_hi, owner,
  asset (as written), asset_type (the filing's own code/type), kind (muni / treasury / corp_bond / fund / other),
  source (house_clerk / senate_efd / senate_import), doc_id
"""
import os, re, json, time
import datetime as dt
import pandas as pd

FILE = ("cache", "other_tx.pkl")
HOUSE_DONE = ("cache", "other_house_done.json")     # House reports read again for these rows (history fill)
KEY = ["doc_id", "trade_date", "asset", "tx_raw", "amt_lo", "owner"]

TREASURY = re.compile(r"\b(u\.?\s?s\.?\s+treas\w*|us\s+tsy|treasury|t-?bills?|treas\s+bills?|t-?notes?|tips\b|"
                      r"united states treas\w*|fannie\s+mae|freddie\s+mac|fnma|fhlmc|federal national mortgage|"
                      r"federal home loan|fhlb|ginnie\s+mae|gnma)", re.I)      # Treasuries and US agency debt
MUNI_WORDS = re.compile(r"\b(go|g\.o\.|gen(?:eral)?\s+oblig\w*|rev(?:enue)?|bds?|bonds?|auth(?:ority)?|sch(?:ool)?\s+dist|"
                        r"cnty|county|city|twp|township|util\w*|muni\w*|hosp\w*|tpk|turnpike|arpt|airport|"
                        r"dorm|fing|financ\w*\s+auth|water|sewer|swr|transit|port\s+auth|ref\b)", re.I)
FUND_WORDS = re.compile(r"\b(fund|funds|lp\b|l\.p\.|llc|etf|trust|class [a-z]\b|bdc|portfolio)\b", re.I)


def classify(asset_type, asset):
    """muni / treasury / corp_bond / fund / other, from the filing's asset type (House code or Senate words) and
    the asset as written."""
    t, a = str(asset_type or "").strip().lower(), str(asset or "")
    if TREASURY.search(a) or "treasury" in t:
        return "treasury"
    if "municipal" in t:
        return "muni"
    if t == "gs" or "government" in t:
        return "muni" if MUNI_WORDS.search(a) else "treasury" if re.search(r"\bu\.?s\.?\b|federal|agency", a, re.I) else "muni"
    if t == "cs" or "corporate" in t:
        return "fund" if re.search(r"\bfund\b", a, re.I) else "corp_bond"
    if t in ("mf", "ef") or "mutual" in t or FUND_WORDS.search(a):
        return "fund"
    return "other"


def clean_asset(a):
    """The asset as written, without the amount column that wraps into it ("... 5% due 6/1/28 $100,000")."""
    a = re.sub(r"\s*\$[\d,]+(?:\.\d+)?\s*$", "", str(a or "")).strip(" -")
    a = re.sub(r"\s+\$[\d,]+(?:\.\d+)?(?=\s)", " ", a)
    return re.sub(r"\s+", " ", a).strip(" -")[:200] or None


def _path(cfg, parts):
    p = os.path.join(cfg.get("DATA_DIR", "./data"), *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def load(cfg):
    p = _path(cfg, FILE)
    try:
        return pd.read_pickle(p) if os.path.exists(p) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def save_rows(cfg, rows, replace_docs=(), log=print):
    """Adds rows (dicts); reports listed in replace_docs have their old rows replaced (a report read again)."""
    old = load(cfg)
    if replace_docs and len(old):
        old = old[~old["doc_id"].astype(str).isin({str(d) for d in replace_docs})]
    if not rows and not replace_docs:
        return old
    new = pd.DataFrame(rows)
    if len(new):
        new["asset"] = new["asset"].map(clean_asset)
        new["kind"] = [classify(t, a) for t, a in zip(new.get("asset_type"), new["asset"])]
    df = pd.concat([old, new], ignore_index=True) if len(old) else new
    if len(df):
        df = df.drop_duplicates([k for k in KEY if k in df.columns], keep="last").reset_index(drop=True)
    df.to_pickle(_path(cfg, FILE))
    return df


def house_rows(items, first, last, state, filed, doc, stock_types=("ST", "EF", "OP")):
    """Non-stock rows of one House report (from engine.parse_house_ptr_text)."""
    out = []
    for t in items or []:
        if t.get("asset_type") in stock_types:
            continue
        if t.get("asset_type") is None and t.get("ticker"):
            continue                       # an untagged row with a ticker is a stock (the picks reader's call)
        out.append({"member": f"{first} {last}".strip(), "first": first, "last": last, "chamber": "House",
                    "state": state, "trade_date": t.get("trade_date"), "filed_date": filed, "tx_raw": t.get("tx_raw"),
                    "amt_lo": t.get("amt_lo"), "amt_hi": t.get("amt_hi"), "owner": t.get("owner"),
                    "asset": t.get("asset"), "asset_type": t.get("asset_type"), "source": "house_clerk",
                    "doc_id": str(doc)})
    return out


def senate_rows(items, first, last, filed, doc, source="senate_efd"):
    """Rows of one Senate report from engine.parse_senate_other_html."""
    return [{"member": f"{first} {last}".strip(), "first": first, "last": last, "chamber": "Senate", "state": None,
             "trade_date": t.get("trade_date"), "filed_date": filed, "tx_raw": t.get("tx_raw"), "amt_lo": t.get("amt_lo"),
             "amt_hi": t.get("amt_hi"), "owner": t.get("owner"), "asset": t.get("asset"),
             "asset_type": t.get("asset_type"), "source": source, "doc_id": str(doc)} for t in items or []]


def backfill_house(cfg, minutes, log=print, pause=1.0):
    """History fill: every House trade report since the start date is read again (slowly) for its bond and other
    rows, newest first, resumable. Stops early when the House site refuses requests. A report that can't be
    downloaded on 3 different runs is set aside and counted in the log line. Returns reports left."""
    import congress_signals.engine as E
    # every House trade report since the start date (bond-only reports never reached the stock table)
    idx = []
    for y in range(pd.Timestamp(cfg.get("START_DATE", "2014-01-01")).year, dt.date.today().year + 1):
        try:
            idx.append(E.house_index(y, live=False))
        except Exception as e:
            log(f"Bonds history: House {y} list failed ({type(e).__name__}); those reports wait for the next run")
    if not idx:
        return None
    docs = pd.concat(idx, ignore_index=True)
    docs["filed"] = pd.to_datetime(docs["FilingDate"], errors="coerce")
    docs = docs.drop_duplicates("DocID").sort_values("filed", ascending=False)
    docs = docs.rename(columns={"DocID": "doc_id", "First": "first", "Last": "last"})
    docs["state"] = docs["StateDst"].astype(str).str[:2]
    docs["filed_date"] = docs["FilingDate"]
    dp = _path(cfg, HOUSE_DONE)
    done = set(json.load(open(dp))) if os.path.exists(dp) else set()
    fp = _path(cfg, ("cache", "other_house_failed.json"))
    failed = json.load(open(fp)) if os.path.exists(fp) else {}
    todo = [r for _, r in docs.iterrows() if str(r["doc_id"]) not in done and failed.get(str(r["doc_id"]), 0) < 3]
    end, n, rows, read, refused = time.time() + minutes * 60, 0, [], [], 0
    for r in todo:
        if time.time() > end:
            break
        doc, filed = str(r["doc_id"]), r["filed"]
        yr = int(r["year"]) if pd.notna(r.get("year")) else (filed.year if pd.notna(filed) else dt.date.today().year)
        text = E._fetch_house_pdf(yr, doc)
        time.sleep(pause)
        if text is None:
            failed[doc] = failed.get(doc, 0) + 1
            refused += 1
            if refused >= 5:
                log("Bonds history: House site refused or failed 5 reports in a row; stopped for this run")
                break
            continue
        refused = 0
        items = E.parse_house_ptr_text(text) if text else []
        rows += house_rows(items, str(r["first"] or "").strip(), str(r["last"] or "").strip(), r["state"],
                           str(r["filed_date"]), doc)
        read.append(doc)
        done.add(doc)
        n += 1
        if n % 200 == 0:
            save_rows(cfg, rows, replace_docs=read, log=log)
            rows, read = [], []
            json.dump(sorted(done), open(dp, "w"))
    save_rows(cfg, rows, replace_docs=read, log=log)
    json.dump(sorted(done), open(dp, "w"))
    json.dump(failed, open(fp, "w"))
    left = max(0, len(todo) - n)
    gave_up = sum(1 for v in failed.values() if v >= 3)
    log(f"Bonds history: {n} House reports read again for bonds and other non-stock rows; {left:,} left"
        + (f"; {gave_up} report(s) couldn't be downloaded on 3 runs and are set aside" if gave_up else ""))
    return left


def mark_house_done(cfg, docs):
    """House reports read by the daily update: the history fill doesn't need to read them again."""
    dp = _path(cfg, HOUSE_DONE)
    done = set(json.load(open(dp))) if os.path.exists(dp) else set()
    done |= {str(d) for d in docs}
    json.dump(sorted(done), open(dp, "w"))
