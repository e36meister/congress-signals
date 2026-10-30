"""Oct 30 (read-only, owner): Senate roll calls are blocked on senate.gov (HTTP 403 since Oct 26). Which other public
sources have them, and how current are they? Compares with the newest Senate vote already saved."""
import os, io, sys, requests
import pandas as pd
sys.path.insert(0, ".")
UA = {"User-Agent": "CapitolCapital research (github.com/e36meister)"}

try:
    v = pd.read_pickle("data/cache/votes.pkl")
    s = {k: x for k, x in v.items() if k.startswith("S|")}
    last = max(s, key=lambda k: (s[k]["date"], k))
    print(f"saved: {len(s):,} Senate roll calls; newest {last} on {s[last]['date']}")
except Exception as e:
    print("saved votes: can't read", e)

# 1) Voteview (UCLA): CSVs of every roll call with each member's vote (bioguide IDs in the members file)
for kind in ("rollcalls", "votes", "members"):
    try:
        r = requests.get(f"https://voteview.com/static/data/out/{kind}/S119_{kind}.csv", headers=UA, timeout=120)
        print(f"voteview {kind}: HTTP {r.status_code}, {len(r.content):,} bytes")
        if kind == "rollcalls" and r.ok:
            rc = pd.read_csv(io.BytesIO(r.content))
            print("  columns:", list(rc.columns))
            print("  newest:", rc.sort_values("date").tail(3)[["session", "clerk_rollnumber", "rollnumber", "date", "bill_number", "vote_question"]].to_string())
    except Exception as e:
        print(f"voteview {kind}: failed {e}")

# 2) congress.gov API (House votes are there; is the Senate?)
key = (os.environ.get("CONGRESS_API_KEY") or "").strip()
for path in ("senate-vote/119/2", "senate-vote", "house-vote/119/2"):
    try:
        r = requests.get(f"https://api.congress.gov/v3/{path}", params={"api_key": key, "format": "json", "limit": 2}, timeout=30)
        print(f"congress.gov {path}: HTTP {r.status_code} {r.text[:200]!r}")
    except Exception as e:
        print(f"congress.gov {path}: failed {e}")

# 3) GovTrack
for u in ("https://www.govtrack.us/api/v2/vote?chamber=senate&order_by=-created&limit=2",
          "https://www.govtrack.us/congress/votes/119-2026/s292/export/csv"):
    try:
        r = requests.get(u, headers=UA, timeout=30)
        print(f"govtrack {u.split('.us')[1][:40]}: HTTP {r.status_code} {r.text[:300]!r}")
    except Exception as e:
        print(f"govtrack: failed {e}")

# 4) senate.gov right now, once
try:
    r = requests.get("https://www.senate.gov/legislative/LIS/roll_call_votes/vote1192/vote_119_2_00292.xml", headers=UA, timeout=30)
    print(f"senate.gov vote 119-2-292: HTTP {r.status_code}")
except Exception as e:
    print("senate.gov: failed", e)
