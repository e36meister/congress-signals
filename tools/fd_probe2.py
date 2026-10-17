"""Read-only: what sits in the travel (H) and positions (E) schedules of yearly disclosures, by year, and which
public companies appear there. Also how many House reports would get holdings back from their saved text."""
import sys, os, re, json, collections
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

fd = pd.read_pickle("data/cache/annual_fd.pkl")
fd["y"] = pd.to_datetime(fd["filed"], errors="coerce").dt.year
meta = json.load(open("data/cache/ticker_meta.json"))
sc = pd.read_pickle("data/cache/scored.pkl")
traded = set(sc["ticker"].dropna())
cks = {}
for t in traded:
    ck = E.company_key((meta.get(t) or {}).get("name"))
    if ck and len(ck) >= 4:
        cks[ck] = t
print("company keys of traded stocks:", len(cks))
# quick first-word prefilter
first = collections.defaultdict(list)
for ck in cks:
    first[ck.split()[0]].append(ck)

def sched_lines(t):
    out, s = collections.defaultdict(list), ""
    for ln in t.splitlines():
        m = E.FD_SCHEDULE.search(E._fd_norm(ln))
        if m:
            s = m.group(1)
            continue
        out[s].append(ln)
    return out

for ch in ("House", "Senate"):
    print(f"\n===== {ch} =====")
    for y, g in fd[fd["chamber"] == ch].groupby("y"):
        if y < 2014:
            continue
        nh = ne = 0
        hits = collections.Counter()
        samp_h, samp_e = [], []
        kinds = collections.Counter()
        for blob in g["text"]:
            t = E._unz(blob).lower()
            if ch == "House":
                S = sched_lines(t)
                H = [l for l in S.get("h", []) if l.strip() and "none disclosed" not in l]
                Ee = [l for l in S.get("e", []) if l.strip() and "none disclosed" not in l]
            else:
                H = [l for l in t.splitlines() if re.search(r"travel|trip|lodging|reimburs", l)]
                Ee = [l for l in t.splitlines() if re.search(r"position|director|board|trustee", l)]
            nh += bool(H); ne += bool(Ee)
            if len(samp_h) < 6:
                samp_h += H[:2]
            if len(samp_e) < 4:
                samp_e += Ee[:2]
            for l in H:
                for w in set(re.findall(r"[a-z0-9&]+", l)):
                    for ck in first.get(w, ()):
                        if E._name_hit(l, ck):
                            hits[cks[ck]] += 1
        # what _fd_kinds says for every traded company key in these reports (cheap: only keys present)
        print(f"{y}: {len(g)} reports | with travel lines {nh} | with position lines {ne} | public cos in travel: "
              f"{dict(hits.most_common(8))}")
        if y in (2016, 2019, 2022, 2024, 2025):
            for l in samp_h[:6]:
                print("     H:", repr(l[:150]))
            for l in samp_e[:4]:
                print("     E:", repr(l[:150]))

print("\n===== House holdings recoverable from saved text =====")
for y, g in fd[(fd["chamber"] == "House")].groupby("y"):
    emp = g[~g["holdings"].map(bool)]
    rec = sum(1 for b in emp["text"] if E._house_holdings(E._unz(b)))
    print(y, f"empty {len(emp)} -> would recover {rec}")
