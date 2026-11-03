"""Read-only probe (Nov 3): why does 2024 have far fewer congressional trades on file than 2023 and 2025?
Per year 2022-2026: House Clerk yearly index PTRs (electronic vs scanned), how many are in house_done.json, how many
produced trades, trades per filing; samples of read-but-empty and not-yet-read 2024 reports re-parsed now; per-month
trade counts in all_tx.pkl by trade and filing date and source; Quiver's own counts per year as an outside check.
Reads the House site slowly. Writes nothing to data/."""
import sys, os, io, json, time, zipfile, collections
import pandas as pd, requests
sys.path.insert(0, ".")
import congress_signals.engine as E

C = "data/cache"
YRS = list(range(2022, 2027))
PAUSE = 2.5


def jload(p, d=None):
    try:
        return json.load(open(p))
    except Exception as e:
        return d if d is not None else f"<{type(e).__name__}>"


print("== cache state files ==")
for f in ("house_parser_version.txt", "house_ticker_fix.txt"):
    p = f"{C}/{f}"
    print(f, "=", open(p).read().strip() if os.path.exists(p) else "<missing>")
print("house_backlog.json =", jload(f"{C}/house_backlog.json"))
print("state/house_reread.json =", jload("data/state/house_reread.json"))
done = set(map(str, jload(f"{C}/house_done.json", [])))
print("house_done.json entries:", len(done))
ht = pd.read_pickle(f"{C}/house_tx.pkl")
ht["doc_id"] = ht["doc_id"].astype(str)
ht["td"] = pd.to_datetime(ht["trade_date"], errors="coerce")
ht["fd"] = pd.to_datetime(ht["filed_date"], errors="coerce")
print("house_tx rows:", len(ht), "sources:", ht["source"].value_counts().to_dict() if "source" in ht else "-")
per_doc = ht.groupby("doc_id").size()
orph = set(per_doc.index) - done
print(f"house_tx docs: {len(per_doc)}; docs with rows but NOT in house_done (left over from before a re-read): {len(orph)}"
      f" ({int(per_doc[list(orph)].sum()) if orph else 0} rows)")
print("house_tx rows by filed year:", ht["fd"].dt.year.value_counts().sort_index().to_dict())
print("house_tx rows by trade year:", ht["td"].dt.year.value_counts().sort_index().to_dict())

print("\n== House Clerk yearly index vs cache ==")
idx = {}
for y in YRS:
    try:
        r = requests.get(f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{y}FD.zip",
                         headers=E.UA, timeout=120)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xn = [n for n in z.namelist() if n.lower().endswith(".xml")][0]
        df = pd.read_xml(z.open(xn))
        print(f"{y}: zip {len(r.content)//1024} KB, {len(df)} filings, types {df['FilingType'].astype(str).value_counts().to_dict()}")
        df = df[df["FilingType"].astype(str).str.upper() == "P"].copy()
        df["DocID"] = df["DocID"].astype(str)
        df["FilingDate"] = pd.to_datetime(df["FilingDate"], errors="coerce")
        idx[y] = df
    except Exception as e:
        print(f"{y}: index FAILED {type(e).__name__}: {e}")
    time.sleep(PAUSE)

print(f"\n{'year':>4} {'PTRs':>5} {'elec':>5} {'paper':>5} {'done':>5} {'notdone':>7} {'w/trades':>8} {'done0':>5} "
      f"{'rows':>6} {'rows/filing':>11} {'rows/w-trades':>13} {'members':>7}")
for y, df in idx.items():
    elec = df[df["DocID"].str[0] == "2"]
    paper = df[df["DocID"].str[0].isin(["8", "9"])]
    d_in = df["DocID"].isin(done)
    w = df["DocID"].isin(per_doc.index)
    rows = int(per_doc.reindex(df["DocID"]).fillna(0).sum())
    e_done0 = int((elec["DocID"].isin(done) & ~elec["DocID"].isin(per_doc.index)).sum())
    mem = df.assign(k=df["Last"].astype(str) + df["StateDst"].astype(str))["k"].nunique()
    print(f"{y:>4} {len(df):>5} {len(elec):>5} {len(paper):>5} {int(d_in.sum()):>5} {int((~d_in).sum()):>7} "
          f"{int(w.sum()):>8} {e_done0:>5} {rows:>6} {rows/max(len(df),1):>11.2f} {rows/max(int(w.sum()),1):>13.2f} {mem:>7}")
    print(f"      by filing month: " + " ".join(f"{m}:{n}" for m, n in df["FilingDate"].dt.month.value_counts().sort_index().items()))
    print(f"      not done by month: " + " ".join(f"{m}:{n}" for m, n in df.loc[~d_in, "FilingDate"].dt.month.value_counts().sort_index().items()))
    top = (df.assign(nm=df["Last"].astype(str), rows=per_doc.reindex(df["DocID"]).fillna(0).values)
           .groupby("nm").agg(filings=("DocID", "size"), rows=("rows", "sum")).sort_values("filings", ascending=False).head(12))
    print("      top filers (filings/rows on file): " + ", ".join(f"{n} {int(a)}/{int(b)}" for n, (a, b) in top.iterrows()))
alld = pd.concat(idx.values()) if idx else pd.DataFrame(columns=["DocID"])
print("house_done entries not in any 2022-2026 index (older years):", len(done - set(alld["DocID"])))


def sample(label, df, k):
    print(f"\n-- {label}: re-reading {min(k, len(df))} of {len(df)} --")
    agg = collections.Counter()
    for _, r in df.head(k).iterrows():
        y = int(r["FilingDate"].year) if pd.notna(r["FilingDate"]) else 0
        url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{r['Year']}/{r['DocID']}.pdf"
        try:
            g = requests.get(url, headers=E.UA, timeout=60)
            st = g.status_code
            if st == 200:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(g.content)) as pdf:
                    text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
                    npg = len(pdf.pages)
                items = E.parse_house_ptr_text(text)
                types = collections.Counter(str(i["asset_type"]) for i in items)
                agg.update(types)
                ok = sum(1 for i in items if i["asset_type"] in E.DEFAULT_CONFIG["ASSET_TYPES"])
                print(f"  {r['DocID']} {r['Last']} filed {str(r['FilingDate'])[:10]}: HTTP 200, {npg}p, {len(text)} chars, "
                      f"{len(items)} rows {dict(types)}, {ok} kept; head={text[:90]!r}")
                if not items and text:
                    print("     text sample:", repr(text[300:700]))
            else:
                print(f"  {r['DocID']} {r['Last']} filed {str(r['FilingDate'])[:10]}: HTTP {st} {g.text[:80]!r}")
        except Exception as e:
            print(f"  {r['DocID']}: {type(e).__name__}: {e}")
        time.sleep(PAUSE)
    print("  asset types in sample:", dict(agg))


print("\nASSET_TYPES kept:", E.DEFAULT_CONFIG.get("ASSET_TYPES"))
rng = 7
for y, k in ((2024, 14), (2023, 5), (2025, 5)):
    if y not in idx:
        continue
    df = idx[y]
    elec = df[df["DocID"].str[0] == "2"]
    d0 = elec[elec["DocID"].isin(done) & ~elec["DocID"].isin(per_doc.index)].sample(frac=1, random_state=rng)
    sample(f"{y} electronic, read (in house_done) but 0 trades on file", d0, k)
    nd = elec[~elec["DocID"].isin(done)].sample(frac=1, random_state=rng)
    sample(f"{y} electronic, NOT in house_done", nd, 4 if y == 2024 else 2)

print("\n== all_tx.pkl per month ==")
tx = pd.read_pickle(f"{C}/all_tx.pkl")
tx["td"] = pd.to_datetime(tx["trade_date"], errors="coerce")
tx["fd"] = pd.to_datetime(tx["filed_date"], errors="coerce")
for col in ("td", "fd"):
    t = tx[tx[col].dt.year.isin(YRS)]
    tab = t.groupby([t[col].dt.to_period("M"), "source"]).size().unstack(fill_value=0)
    tab["ALL"] = tab.sum(axis=1)
    print(f"\nby {'trade' if col == 'td' else 'filed'} month x source:")
    print(tab.to_string())
for col in ("td", "fd"):
    t = tx[tx[col].dt.year.isin(YRS)]
    print(f"\nby {col} year x chamber x source:")
    print(t.groupby([t[col].dt.year, "chamber", "source"]).size().unstack(fill_value=0).to_string())

print("\n== senate_tx.pkl by year ==")
try:
    st = pd.read_pickle(f"{C}/senate_tx.pkl")
    for c in ("trade_date", "filed_date"):
        print(c, pd.to_datetime(st[c], errors="coerce").dt.year.value_counts().sort_index().to_dict())
    if "source" in st:
        print(st["source"].value_counts().to_dict())
except Exception as e:
    print("senate_tx:", type(e).__name__, e)

print("\n== source health by_year (saved) ==")
for p in ("data/state/source_health.json", f"{C}/source_health.json"):
    if os.path.exists(p):
        sh = jload(p, {})
        for k in ("quiver", "fmp", "house_clerk", "senate_efd"):
            v = sh.get(k) if isinstance(sh, dict) else None
            if v:
                print(p, k, {kk: v.get(kk) for kk in ("ok", "rows", "by_year", "at", "error") if kk in v})

print("\n== Quiver bulk (outside check), one request ==")
key = os.environ.get("QUIVER_API_KEY")
if key:
    try:
        r = requests.get("https://api.quiverquant.com/beta/bulk/congresstrading", timeout=180,
                         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        print("HTTP", r.status_code)
        if r.status_code == 200:
            q = pd.DataFrame(r.json())
            print("fields:", list(q.columns)[:25], "rows:", len(q))
            tdc = next((c for c in ("TransactionDate", "Traded") if c in q), None)
            fdc = next((c for c in ("ReportDate", "Filed", "Disclosed") if c in q), None)
            chc = next((c for c in ("House", "Chamber") if c in q), None)
            for c in (tdc, fdc):
                if c:
                    yy = pd.to_datetime(q[c], errors="coerce").dt.year
                    print(f"Quiver by {c} year x chamber:")
                    print(q.assign(y=yy)[yy.isin(YRS)].groupby(["y", chc]).size().unstack(fill_value=0).to_string())
    except Exception as e:
        print("Quiver failed:", type(e).__name__, str(e)[:200])
else:
    print("no QUIVER_API_KEY")
print("\ndone.")
