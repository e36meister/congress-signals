"""Oct 27: FMP free plan: does paging work (25 rows a page), and how far back do Senate pages go?"""
import os, requests
key = os.environ.get("FMP_API_KEY") or ""
hide = lambda s: str(s).replace(key, "***") if key else str(s)
for page in (1, 2, 5, 20):
    r = requests.get("https://financialmodelingprep.com/stable/senate-latest", params={"page": page, "limit": 25, "apikey": key}, timeout=60)
    try: js = r.json()
    except Exception: js = None
    if isinstance(js, list):
        ds = sorted(str(d.get("disclosureDate"))[:10] for d in js)
        print(f"senate page {page}: HTTP {r.status_code}, {len(js)} rows, filed {ds[:1]}..{ds[-1:]}")
    else:
        print(f"senate page {page}: HTTP {r.status_code}: {hide(r.text[:250])}")
