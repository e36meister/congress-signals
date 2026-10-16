"""Read-only: how yearly financial disclosures look after text extraction, to check the spouse-job / travel /
position / holdings readers. Prints counts by year and chamber, and raw lines around known ties."""
import sys, re, collections
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

fd = pd.read_pickle("data/cache/annual_fd.pkl")
fd["yr"] = pd.to_datetime(fd["filed"], errors="coerce").dt.year
txt = {d: E._unz(t).lower() for d, t in zip(fd["doc"], fd["text"])}
fd["has_text"] = [bool(txt[d].strip()) for d in fd["doc"]]
fd["n_hold"] = [len(h) if isinstance(h, (set, list)) else 0 for h in fd["holdings"]]
fd["sched"] = [bool(E.FD_SCHEDULE.search(txt[d]) or re.search(r"(?m)^\s*s(?:chedule)?\s+[a-j]\s*:", txt[d])) for d in fd["doc"]]
fd["spouse_word"] = [bool(re.search(r"\bspouse\b|\bsp\b", txt[d])) for d in fd["doc"]]
print("Yearly disclosures by chamber and year: reports | with text | with any holdings | schedule headers found | 'spouse' appears")
for (ch, y), g in fd.groupby(["chamber", "yr"]):
    print(f"  {ch:<6} {int(y)}: {len(g):>4} | {g.has_text.sum():>4} | {(g.n_hold > 0).sum():>4} | {g.sched.sum():>4} | {g.spouse_word.sum():>4}")

# which kinds _fd_kinds finds, by year (any company): count reports with a 'spouse_job' line for any company name
meta = {}
try:
    import json
    meta = json.load(open("data/cache/ticker_meta.json"))
except Exception:
    pass
cks = sorted({E.company_key((m or {}).get("name")) for m in meta.values() if isinstance(m, dict)} - {""}, key=len)
cks = [c for c in cks if len(c) >= 5][:4000]
print("\nReports where a company shows up as spouse's job / travel / position, by chamber and year (sample of 4,000 names):")
cnt = collections.Counter()
for (ch, y, d) in zip(fd["chamber"], fd["yr"], fd["doc"]):
    t = txt[d]
    if not t:
        continue
    kinds = set()
    for ck in cks:
        if ck in t and E._name_hit(t, ck):
            kinds |= E._fd_kinds(t, ck)
    for k in kinds:
        cnt[(ch, y, k)] += 1
for ch in ("House", "Senate"):
    for k in ("spouse_job", "travel", "position", "gift"):
        print(f"  {ch:<6} {k:<11}" + " ".join(f"{int(y)}:{cnt.get((ch, y, k), 0)}" for y in sorted(fd['yr'].dropna().unique())))

def show(last, words, n=1):
    g = fd[(fd["last_key"] == last)].sort_values("filed")
    print(f"\n=== {last}: {len(g)} reports; years {sorted(g.yr.dropna().astype(int).unique())}")
    for _, r in g.tail(n).iterrows():
        t = txt[r["doc"]]
        lines = t.splitlines()
        print(f"--- {r['chamber']} {r['filed']} doc {r['doc']} ({len(lines)} lines, {r['n_hold']} holdings)")
        for i, ln in enumerate(lines):
            if any(w in ln for w in words):
                lo, hi = max(0, i - 2), min(len(lines), i + 3)
                print("   " + " ⏎ ".join(x.strip()[:110] for x in lines[lo:hi]))
                break
        print("   first lines: " + " ⏎ ".join(x.strip()[:80] for x in lines[:12]))

show("loeffler", ["intercontinental", "ice "])
show("delbene", ["microsoft"])
show("spanberger", ["l3harris", "harris"])
show("auchincloss", ["state street"])
show("capito", ["wells fargo"])
show("mcconnell", ["kroger", "board", "director"])
show("kelly", ["spouse"], n=1)
show("warner", ["spouse"], n=1)
