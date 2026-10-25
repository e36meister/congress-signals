"""Read-only probe (Oct 25): why disclosures-clerk.house.gov and clerk.house.gov answered HTTP 403.

Reads slowly (one request at a time, 3 s apart) and saves nothing.
"""
import io, os, sys, time, zipfile
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from congress_signals import engine as E

UA = E.UA
B = "https://disclosures-clerk.house.gov/public_disc"


def get(url, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=60, **kw)
        print(f"  {r.status_code} {len(r.content):>9,} B  {r.headers.get('content-type','')[:30]:30} {url}")
        if r.status_code != 200:
            print("     ", r.text[:200].replace("\n", " "))
        return r
    except Exception as e:
        print(f"  EXC {type(e).__name__}: {e}  {url}")
    finally:
        time.sleep(3)


print("== basic reachability")
for u in ["https://disclosures-clerk.house.gov/FinancialDisclosure",
          f"{B}/financial-pdfs/2026FD.zip",
          f"{B}/financial-pdfs/2013FD.zip",
          "https://clerk.house.gov/evs/2026/roll001.xml",
          "https://www.house.gov/"]:
    get(u)

print("== the 2012 yearly reports the daily run keeps trying")
cache = os.path.join("data", "cache", "annual_fd.pkl")
df = pd.read_pickle(cache) if os.path.exists(cache) else pd.DataFrame(columns=["chamber", "doc", "filed"])
h = df[df["chamber"] == "House"]
print("saved House yearly reports:", len(h))
print(pd.to_datetime(h["filed"], errors="coerce").dt.year.value_counts().sort_index().to_string())
r = get(f"{B}/financial-pdfs/2012FD.zip")
if r is not None and r.status_code == 200:
    z = zipfile.ZipFile(io.BytesIO(r.content))
    idx = pd.read_xml(z.open([n for n in z.namelist() if n.lower().endswith(".xml")][0]))
    idx = idx[idx["FilingType"].astype(str).str.upper().isin(["O", "A", "T"])]
    print("2012 index O/A/T rows:", len(idx), " already saved:", int(idx["DocID"].astype(str).isin(set(h["doc"])).sum()))
    print("FilingDate years in 2012 index:", pd.to_datetime(idx["FilingDate"], errors="coerce").dt.year.value_counts().to_dict())
    todo = idx[~idx["DocID"].astype(str).isin(set(h["doc"]))]
    for _, row in todo.head(6).iterrows():
        get(f"{B}/financial-pdfs/{int(row['Year'])}/{row['DocID']}.pdf")
print("done")
