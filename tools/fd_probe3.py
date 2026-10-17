"""Read-only: the trades that got company-paid travel, the matching lines, and the most common trip sponsors."""
import sys, re, json, collections
import pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

fd = pd.read_pickle("data/cache/annual_fd.pkl")
fd["y"] = pd.to_datetime(fd["filed"], errors="coerce").dt.year
meta = json.load(open("data/cache/ticker_meta.json"))
sc = pd.read_pickle("data/cache/scored.pkl")
t = sc[sc.get("f_paid_travel", 0) > 0]
print("trades with paid travel:", len(t))
for _, r in t.iterrows():
    ck = E.company_key((meta.get(r["ticker"]) or {}).get("name"))
    print(f"  {str(r['filed_date'])[:10]} {r['member']} {r['ticker']} key={ck!r}")
print()


def sched(text):
    s, out = "", collections.defaultdict(list)
    for ln in text.splitlines():
        m = E.FD_SCHEDULE.search(E._fd_norm(ln))
        if m:
            s = m.group(1); continue
        out[s].append(ln)
    return out


DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
for era, ys in (("2016-2020", range(2016, 2021)), ("2022-2026", range(2022, 2027))):
    src = collections.Counter()
    n_trips = 0
    for blob in fd[(fd["chamber"] == "House") & fd["y"].isin(list(ys))]["text"]:
        H = sched(E._unz(blob)).get("h", [])
        for ln in H:
            m = DATE.search(ln)
            if m and m.start() > 3:
                n_trips += 1
                src[ln[:m.start()].strip().lower()] += 1
    print(f"House trips {era}: {n_trips}; top sponsors:")
    for k, v in src.most_common(45):
        print(f"   {v:4}  {k}")
