"""Read-only: why yearly disclosures give no House holdings 2014-2017, no schedule headers from 2022, and no
company-paid travel since 2021. Prints stats per year and raw text samples."""
import sys, os, re, io, collections
import pandas as pd, requests
sys.path.insert(0, ".")
import congress_signals.engine as E

D = "data"
fd = pd.read_pickle(f"{D}/cache/annual_fd.pkl")
fd["y"] = pd.to_datetime(fd["filed"], errors="coerce").dt.year
print("reports:", len(fd), "| columns:", list(fd.columns))
print("version counts:", fd["v"].value_counts().to_dict())

print("\n=== per chamber/year: reports, with holdings, with a schedule header, letters, 'travel' lines ===")
for (ch, y), g in fd.groupby(["chamber", "y"]):
    nh = int(g["holdings"].map(bool).sum())
    hdr, letters, trav, tlines = 0, collections.Counter(), 0, 0
    for blob in g["text"].head(60):
        t = E._unz(blob).lower()
        found = set()
        for ln in t.splitlines():
            m = E.FD_SCHEDULE.search(E._fd_norm(ln))
            if m:
                found.add(m.group(1))
            if re.search(r"\btravel|\btrip\b|lodging|airfare|itinerary", ln):
                tlines += 1
        hdr += bool(found)
        letters.update(found)
    print(f"{ch:6} {y}: {len(g):4} reports | holdings {nh:4} | header found {hdr}/{min(60, len(g))} | "
          f"letters {''.join(sorted(letters))} | travel-word lines {tlines}")

def sample(ch, y, n_lines=90, grep=None, k=0):
    g = fd[(fd["chamber"] == ch) & (fd["y"] == y)]
    if not len(g):
        print(f"(no {ch} {y})"); return
    g = g.assign(L=g["text"].map(lambda b: len(E._unz(b)))).sort_values("L", ascending=False)
    t = E._unz(g.iloc[min(k, len(g) - 1)]["text"])
    print(f"\n--- {ch} {y} doc {g.iloc[min(k, len(g)-1)]['doc']} ({len(t)} chars) ---")
    lines = t.splitlines()
    if grep:
        for i, ln in enumerate(lines):
            if re.search(grep, ln, re.I):
                for x in lines[max(0, i - 2):i + 4]:
                    print("   ", repr(x[:160]))
                print("   ..")
    else:
        for x in lines[:n_lines]:
            print("   ", repr(x[:160]))

sample("House", 2015, 110)
sample("House", 2016, grep=r"schedule|s\W*c\W*h", k=1)
sample("House", 2023, grep=r"sched|^\s*s\s", k=0)
sample("House", 2023, grep=r"travel|trip|lodging|itinerary|sponsor", k=0)
sample("House", 2019, grep=r"travel|trip|lodging|itinerary|sponsor", k=0)
sample("Senate", 2023, grep=r"travel|trip|lodging|sponsor|reimburs", k=0)
sample("Senate", 2019, grep=r"travel|trip|lodging|sponsor|reimburs", k=0)

print("\n=== raw PDF text (fresh download) ===")
UA = E.UA
for y in (2016, 2023):
    try:
        r = requests.get(f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{y}FD.zip", headers=UA, timeout=120)
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(r.content))
        idx = pd.read_xml(z.open([n for n in z.namelist() if n.lower().endswith(".xml")][0]))
        idx = idx[idx["FilingType"].astype(str).str.upper() == "O"]
        for _, row in idx.sample(3, random_state=1).iterrows():
            import pdfplumber
            url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{int(row['Year'])}/{row['DocID']}.pdf"
            pr = requests.get(url, headers=UA, timeout=90)
            if pr.status_code != 200:
                print(y, row["DocID"], "status", pr.status_code); continue
            with pdfplumber.open(io.BytesIO(pr.content)) as pdf:
                txt = "\n".join((pg.extract_text() or "") for pg in pdf.pages[:12])
            print(f"\n--- raw {y} {row['DocID']} {row.get('Last')} ({len(txt)} chars) ---")
            ls = txt.splitlines()
            for i, ln in enumerate(ls):
                if re.search(r"(?i)sched|s\W{0,6}c\W{0,6}h\W{0,6}e|travel|trip|sponsor|asset", ln) or i < 25:
                    print("   ", repr(ln[:170]))
    except Exception as e:
        print("raw", y, "failed", type(e).__name__, e)

print("\n=== scored travel/position/spouse job by filing year ===")
sc = pd.read_pickle(f"{D}/cache/scored.pkl")
sc["yr"] = pd.to_datetime(sc["filed_date"]).dt.year
for c in ("f_paid_travel", "f_outside_position", "f_spouse_employer", "f_disclosure_tie", "f_already_owned"):
    if c in sc:
        print(c, sc.groupby("yr")[c].apply(lambda s: int((s > 0).sum())).to_dict())
