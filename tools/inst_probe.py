"""Read-only probe #2: history depth of FINRA short interest / short volume, and where the SEC 13F data sets live."""
import os, re, requests

UA = {"User-Agent": os.environ.get("SEC_USER_AGENT") or "research probe admin@example.com"}
FH = {"Accept": "application/json", "Content-Type": "application/json"}


def si(date):
    r = requests.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", headers=FH, timeout=60,
                      json={"limit": 5000, "compareFilters": [{"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": date}],
                            "fields": ["symbolCode", "currentShortPositionQuantity", "averageDailyVolumeQuantity", "marketClassCode"]})
    try:
        n = len(r.json())
    except Exception:
        n = r.text[:150]
    return r.status_code, n, {k: v for k, v in r.headers.items() if k.lower() in ("record-total", "record-limit", "record-offset")}


for d in ("2013-12-31", "2014-01-15", "2016-01-15", "2018-01-12", "2019-01-15", "2020-01-15", "2021-06-15", "2025-09-15", "2025-09-30"):
    print("short interest", d, si(d))

for d in ("20180102", "20190102", "20200102", "20210104", "20220103", "20230103", "20240102"):
    r = requests.head(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt", timeout=60)
    print("short volume file", d, r.status_code)
r = requests.post("https://api.finra.org/data/group/otcMarket/name/regShoDaily", headers=FH, timeout=60,
                  json={"limit": 3, "compareFilters": [{"compareType": "EQUAL", "fieldName": "tradeReportDate", "fieldValue": "2015-01-05"}]})
print("regShoDaily 2015-01-05:", r.status_code, r.text[:200])

for u in ("https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets", "https://www.sec.gov/dera/data/form-13f"):
    r = requests.get(u, headers=UA, timeout=60)
    links = sorted(set(re.findall(r'href="([^"]+\.zip)"', r.text)))
    print(u, r.status_code, len(links), links[:3], links[-3:])
