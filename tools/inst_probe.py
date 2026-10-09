"""Read-only probe: are the institutional data sources reachable from GitHub, and what do they look like?
Prints only public data."""
import os, re, io, json, zipfile, requests

UA = {"User-Agent": os.environ.get("SEC_USER_AGENT") or "research probe admin@example.com"}


def show(name, f):
    try:
        f()
    except Exception as e:
        print(f"{name}: FAILED {type(e).__name__}: {e}")


def edgar_index():
    r = requests.get("https://www.sec.gov/Archives/edgar/full-index/2024/QTR1/form.idx", headers=UA, timeout=120)
    print("form.idx 2024Q1", r.status_code, len(r.content))
    lines = r.text.splitlines()
    kinds = {}
    for l in lines:
        f = l[:17].strip()
        if "13D" in f or "13G" in f:
            kinds[f] = kinds.get(f, 0) + 1
    print("  13D/13G form types:", kinds)
    ex = [l for l in lines if l.startswith("SC 13D ")][:6]
    print("  sample:", *ex, sep="\n    ")


def edgar_index_new():
    r = requests.get("https://www.sec.gov/Archives/edgar/full-index/2025/QTR2/form.idx", headers=UA, timeout=120)
    kinds = {}
    for l in r.text.splitlines():
        f = l[:17].strip()
        if "13D" in f or "13G" in f:
            kinds[f] = kinds.get(f, 0) + 1
    print("form.idx 2025Q2 13D/13G form types:", kinds)
    ex = [l for l in r.text.splitlines() if l.startswith("SCHEDULE 13D ")][:4]
    print("  sample:", *ex, sep="\n    ")


def f13_list():
    r = requests.get("https://www.sec.gov/dera/data/form-13f-data-sets", headers=UA, timeout=60)
    links = sorted(set(re.findall(r'href="([^"]+form13f[^"]*\.zip)"', r.text)))
    print("13F data sets page", r.status_code, len(links), "zips; first/last:", links[:3], links[-3:])
    if links:
        u = links[-1] if links[-1].startswith("http") else "https://www.sec.gov" + links[-1]
        h = requests.head(u, headers=UA, timeout=60)
        print("  newest size", h.headers.get("Content-Length"))


def finra_api():
    for ds in ("consolidatedShortInterest", "regShoDaily"):
        r = requests.get(f"https://api.finra.org/data/group/otcMarket/name/{ds}?limit=2",
                         headers={"Accept": "application/json"}, timeout=60)
        print(f"FINRA {ds}:", r.status_code, r.text[:400].replace("\n", " "))
    r = requests.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest",
                      headers={"Accept": "application/json", "Content-Type": "application/json"},
                      json={"limit": 3, "sortFields": ["settlementDate"],
                            "compareFilters": [{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": "AAPL"}]}, timeout=60)
    print("  AAPL earliest:", r.status_code, r.text[:600].replace("\n", " "))


def finra_cdn():
    for d in ("20140102", "20250102"):
        r = requests.get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt", timeout=60)
        print(f"FINRA short volume file {d}:", r.status_code, len(r.content), r.text[:200].replace("\n", " | "))
    r = requests.get("https://cdn.finra.org/equity/otcmarket/biweekly/shrt20140115.csv", timeout=60)
    print("FINRA biweekly short interest 2014-01-15:", r.status_code, len(r.content), r.text[:200].replace("\n", " | "))


def openfigi():
    r = requests.post("https://api.openfigi.com/v3/mapping", json=[{"idType": "ID_CUSIP", "idValue": "037833100"},
                                                                  {"idType": "ID_CUSIP", "idValue": "594918104"}], timeout=60)
    print("OpenFIGI:", r.status_code, r.text[:300], "limits:", {k: v for k, v in r.headers.items() if "limit" in k.lower()})


def thirteen_d_doc():
    # a recent XML-era Schedule 13D primary doc: is the CUSIP structured?
    r = requests.get("https://efts.sec.gov/LATEST/search-index?forms=SCHEDULE%2013D&dateRange=custom&startdt=2025-06-01&enddt=2025-06-05",
                     headers=UA, timeout=60)
    print("EDGAR full-text search:", r.status_code, r.text[:300].replace("\n", " "))


for n, f in [("edgar", edgar_index), ("edgar new", edgar_index_new), ("13f", f13_list), ("finra api", finra_api),
             ("finra cdn", finra_cdn), ("openfigi", openfigi), ("efts", thirteen_d_doc)]:
    show(n, f)
