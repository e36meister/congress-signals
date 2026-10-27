"""Oct 27 (read-only): every filer comment/description the app has saved, and partnership/LLC lines from yearly
reports, written to out/ for the description-sorting and partner-network design. Nothing is changed."""
import os, sys, json, re, zlib, collections
import pandas as pd
D = "./data/cache"
os.makedirs("out", exist_ok=True)
notes = json.load(open(f"{D}/trade_notes.json")) if os.path.exists(f"{D}/trade_notes.json") else {}
c = collections.Counter()
src = collections.Counter()
for doc, items in notes.items():
    for it in items:
        n = re.sub(r"\s+", " ", str(it.get("note") or "")).strip()
        if n:
            c[n] += 1
            src["senate" if "/ptr/" in doc else "house"] += 1
print("reports with notes:", len(notes), "notes:", sum(c.values()), "distinct:", len(c), dict(src))
json.dump([[k, v] for k, v in c.most_common()], open("out/notes.json", "w"))
# yearly reports: lines naming an LLC / LP / partnership
p = f"{D}/annual_fd.pkl"
rows = []
if os.path.exists(p):
    df = pd.read_pickle(p)
    print("yearly reports:", len(df), "columns:", list(df.columns))
    pat = re.compile(r"\b(L\.?L\.?C|L\.?\s?P\.?|LLLP|Partners(hip)?|Fund\s+[IVX]+|Holdings)\b", re.I)
    for r in df.itertuples(index=False):
        try:
            t = zlib.decompress(r.text).decode("utf-8", "ignore") if isinstance(r.text, (bytes, bytearray)) else str(r.text or "")
        except Exception:
            t = ""
        for line in t.splitlines():
            if pat.search(line) and 8 < len(line) < 300:
                rows.append({"ch": r.chamber, "who": f"{r.first} {r.last_key}", "filed": str(r.filed)[:10], "line": line.strip()})
print("partnership-like lines:", len(rows), "members:", len({x['who'] for x in rows}))
json.dump(rows, open("out/partnership_lines.json", "w"))
