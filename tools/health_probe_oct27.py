"""Read-only probe (Oct 27): after the Oct 26 House-votes fix, daily runs show no House refusal but also no
"Votes: N new roll calls" line. Is the House really quiet (recess), or are House votes silently missing?

Prints the newest saved House roll call per year, then asks clerk.house.gov for the next few roll numbers
(one request at a time, 2 s apart). Saves nothing.
"""
import os, sys, time
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from congress_signals import engine as E

v = pd.read_pickle("data/cache/votes.pkl")
print(f"votes on file: {len(v):,}")
for ch in ("H", "S"):
    keys = [k for k in v if k.startswith(ch + "|")]
    print(f"{ch}: {len(keys):,} roll calls")
for y in (2024, 2025, 2026):
    ns = sorted(int(k.split("|")[2]) for k in v if k.startswith(f"H|{y}|"))
    if not ns:
        print(f"House {y}: none saved")
        continue
    last = ns[-1]
    print(f"House {y}: {len(ns)} saved, highest roll {last}, date {v[f'H|{y}|{last}']['date']}")
    gaps = sorted(set(range(1, last + 1)) - set(ns))
    print(f"   gaps below highest: {len(gaps)} {gaps[:15]}")
    if y == 2026:
        for n in range(last + 1, last + 4):
            u = f"https://clerk.house.gov/evs/{y}/roll{n:03d}.xml"
            try:
                r = requests.get(u, headers=E.UA, timeout=30)
                print(f"   {r.status_code} {len(r.content):>8,} B rollcall={b'<rollcall-vote' in r.content} {u}")
                print(f"      body: {r.text[:120]!r}  type={r.headers.get('content-type')}")
            except Exception as e:
                print(f"   EXC {type(e).__name__} {u}")
            time.sleep(2)
for u in ("https://clerk.house.gov/evs/2026/index.asp", "https://clerk.house.gov/evs/2026/roll314.xml"):
    try:
        r = requests.get(u, headers=E.UA, timeout=30)
        import re as _re
        print(f"{u}: {r.status_code} {len(r.content):,} B; roll links: {_re.findall(r'roll(\\d{3})', r.text)[:6]}")
    except Exception as e:
        print(f"{u}: EXC {type(e).__name__}")
    time.sleep(2)
try:
    r = requests.get("https://clerk.house.gov/Votes", headers=E.UA, timeout=30)
    import re
    rolls = re.findall(r"Roll Call\s*(\d+)", r.text)[:5]
    print(f"clerk.house.gov/Votes: {r.status_code}, newest roll numbers on page: {rolls}")
except Exception as e:
    print(f"clerk.house.gov/Votes: EXC {type(e).__name__}")
