"""Read-only probe (Oct 26): the House yearly backfill got HTTP 403 again after 60 2012 reports.

Is it a firewall block (every House URL refused) or do some 2012 PDFs simply answer 403 instead of 404?
Reads slowly (one request at a time, 2 s apart) and saves nothing.
"""
import io, json, os, sys, time, zipfile
from collections import Counter
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from congress_signals import engine as E

UA = E.UA
B = "https://disclosures-clerk.house.gov/public_disc"


def get(url, show=True):
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if show:
            hd = {k: r.headers.get(k) for k in ("server", "content-type", "x-cache", "via", "x-amz-error-code")
                  if r.headers.get(k)}
            print(f"  {r.status_code} {len(r.content):>9,} B {url}  {hd}")
            if r.status_code != 200:
                print("     ", r.text[:300].replace("\n", " "))
        return r
    except Exception as e:
        print(f"  EXC {type(e).__name__}: {e}  {url}")
    finally:
        time.sleep(2)


print("== reachability before")
for u in [f"{B}/financial-pdfs/2026FD.zip", "https://disclosures-clerk.house.gov/FinancialDisclosure"]:
    get(u)

df = pd.read_pickle("data/cache/annual_fd.pkl")
done = set(df["doc"].astype(str))
miss = json.load(open("data/cache/annual_fd_missing.json")) if os.path.exists("data/cache/annual_fd_missing.json") else {}
print("remembered missing:", len(miss))
r = get(f"{B}/financial-pdfs/2012FD.zip")
z = zipfile.ZipFile(io.BytesIO(r.content))
idx = pd.read_xml(z.open([n for n in z.namelist() if n.lower().endswith(".xml")][0]))
idx = idx[idx["FilingType"].astype(str).str.upper().isin(["O", "A", "T"])]
todo = idx[~idx["DocID"].astype(str).isin(done | set(miss))]
print("2012 to read:", len(todo), "DocID samples:", todo["DocID"].astype(str).head(10).tolist())
print("Year column values:", todo["Year"].value_counts().to_dict())

print("== 2012 reports, slowly")
codes = Counter()
first403 = []
for i, (_, row) in enumerate(todo.head(60).iterrows()):
    url = f"{B}/financial-pdfs/{int(row['Year'])}/{row['DocID']}.pdf"
    rr = get(url, show=False)
    c = rr.status_code if rr is not None else None
    codes[c] += 1
    if c == 403 and len(first403) < 4:
        first403.append(url)
        get(url)
    if c == 403 and codes[403] in (3, 10):     # is the whole site refusing now?
        print("   check after 403s:")
        get(f"{B}/financial-pdfs/2026FD.zip")
print("2012 codes:", dict(codes))
print("first 403s:", first403)
# Does a 403 PDF exist under another path (e.g. ptr-pdfs)?
for u in first403[:2]:
    doc = u.rsplit("/", 1)[-1]
    get(f"{B}/ptr-pdfs/2012/{doc}")
    get(f"{B}/financial-pdfs/2013/{doc}")
print("== reachability after")
get(f"{B}/financial-pdfs/2026FD.zip")
get(f"{B}/financial-pdfs/2013FD.zip")
print("done")
