"""Read-only audit of every relationship signal: how many trades each flags per filing year (gaps = data that
stopped or never came), the source data behind it per year, and well-known public ties checked by hand."""
import sys, os, json, re, collections
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

D = "data"
sc = pd.read_pickle(f"{D}/cache/scored.pkl")
sc["yr"] = pd.to_datetime(sc["filed_date"]).dt.year
YRS = list(range(2014, pd.Timestamp.today().year + 1))
print(f"Trades scored: {len(sc):,}; members {sc['member'].nunique()}; by year: " +
      " ".join(f"{y}:{(sc.yr == y).sum()}" for y in YRS))

SIGS = ["f_spouse", "f_spouse_insider", "f_relative_tie", "f_family_possible", "f_spouse_employer", "f_paid_travel",
        "f_outside_position", "f_disclosure_tie", "f_already_owned", "f_campaign_vendor", "f_employee_donations",
        "f_donations", "f_lobbying", "f_revolving_door", "f_ex_member_lobbyist", "f_buddy", "f_leader",
        "f_committee_cluster", "f_committee", "f_subcommittee", "f_committee_leader", "f_home_state",
        "f_contract_in_state", "f_testified", "f_closed_briefing", "f_grants", "f_bills", "f_vote_sector", "f_speech",
        "f_social_post"]
print("\n=== Trades flagged per filing year (all buys and sells) ===")
print(f"{'signal':<24}" + "".join(f"{y:>6}" for y in YRS) + "   total")
for c in SIGS:
    if c not in sc:
        print(f"{c:<24} MISSING COLUMN")
        continue
    on = sc[c].fillna(0) > 0
    row = [int((on & (sc.yr == y)).sum()) for y in YRS]
    print(f"{c:<24}" + "".join(f"{v:>6}" for v in row) + f"   {int(on.sum()):>6}")

print("\n=== Source data per year ===")
try:
    fd = pd.read_pickle(f"{D}/cache/annual_fd.pkl")
    fy = pd.to_datetime(fd["filed"], errors="coerce").dt.year
    print("Yearly disclosures read: " + " ".join(f"{y}:{(fy == y).sum()}" for y in range(2013, YRS[-1] + 1))
          + f" | members {fd['who'].nunique() if 'who' in fd else '?'}")
    if "text" in fd:
        empty = sum(1 for t in fd["text"] if not E._unz(t).strip())
        print(f"  disclosures with no readable text (scans): {empty} of {len(fd)}")
except Exception as e:
    print("Yearly disclosures: unreadable", e)
try:
    lob = json.load(open(f"{D}/cache/lobbying.json"))
    ly = collections.Counter(str(r[0])[:4] for v in lob.values() for r in v.get("rows", []))
    cov = collections.Counter(str(r[0])[:4] for v in lob.values() for r in v.get("rows", []) if len(r) > 3 and r[3])
    print(f"Lobbying filings ({len(lob)} companies): " + " ".join(f"{y}:{ly.get(str(y), 0)}" for y in range(2013, YRS[-1] + 1)))
    print("  with a 'covered position' (ex-government lobbyist): " + " ".join(f"{y}:{cov.get(str(y), 0)}" for y in range(2013, YRS[-1] + 1)))
except Exception as e:
    print("Lobbying: unreadable", e)
try:
    ed = json.load(open(f"{D}/cache/employee_donations.json"))
    cy = collections.Counter(k.split("|")[1] for k in ed if "|" in k)
    print("Employee-donation campaign-cycles: " + " ".join(f"{k}:{v}" for k, v in sorted(cy.items())))
except Exception as e:
    print("Employee donations: unreadable", e)
try:
    cv = pd.read_pickle(f"{D}/cache/campaign_vendors.pkl")
    print("Campaign payments: " + " ".join(f"{y}:{n}" for y, n in pd.to_datetime(cv['date']).dt.year.value_counts().sort_index().items()))
except Exception as e:
    print("Campaign vendors: unreadable", e)
for nm in ("spouses.json", "relatives.json"):
    try:
        j = json.load(open(f"{D}/cache/{nm}"))
        rows = j.get("rows", {})
        print(f"{nm}: {len(rows)} members, fetched {j.get('fetched')}")
    except Exception as e:
        print(nm, "unreadable", e)
for nm in ("spouse_insiders.pkl", "relative_insiders.pkl"):
    p = f"{D}/cache/{nm}"
    if os.path.exists(p):
        d = pd.read_pickle(p)
        print(f"{nm}: {len(d)} filings by name matches; years " +
              " ".join(f"{y}:{n}" for y, n in pd.to_datetime(d['filed']).dt.year.value_counts().sort_index().items()))
    q = f"{D}/cache/{nm.split('_')[0]}_insider_quarters.json"
    if os.path.exists(q):
        qs = json.load(open(q))
        print(f"  quarters done: {len(qs)}; latest {sorted(qs)[-3:] if qs else None}")

print("\n=== Known public ties: is the member's trade in that company flagged? ===")
KNOWN = [("McConnell", ["KR", "CHPT", "MBLY", "WFC", "VMC", "IR", "NWSA", "HYLN"], "wife Elaine Chao (director)"),
         ("McCormick", ["XOM", "META"], "wife Dina Powell (director)"),
         ("Pelosi", None, "husband Paul Pelosi"), ("DelBene", ["MSFT"], "husband ex-Microsoft exec"),
         ("Loeffler", ["ICE"], "husband ICE CEO"), ("Manchin", ["MVBF", "MYL", "VTRS"], "wife MVB director, daughter Mylan CEO"),
         ("Auchincloss", ["STT", "WAT", "BMY"], "wife at State Street; mother director"),
         ("Jacobs", ["QCOM", "DBX"], "grandfather/uncle Qualcomm"), ("Ricketts", ["AMTD", "SCHW"], "family TD Ameritrade"),
         ("Delaney", ["STBA", "BGRY", "RAAC"], "husband director"), ("Spanberger", ["LHX"], "spouse job (disclosure)")]
cols = [c for c in ("f_spouse_insider", "f_relative_tie", "f_family_possible", "f_spouse_employer", "f_disclosure_tie") if c in sc]
for last, ticks, why in KNOWN:
    m = sc[sc["member"].astype(str).str.contains(last, case=False)]
    if ticks:
        m = m[m["ticker"].isin(ticks)]
    if not len(m):
        print(f"{last:<12} ({why}): no trades in those stocks on file")
        continue
    flagged = m[(m[cols].fillna(0) > 0).any(axis=1)]
    print(f"{last:<12} ({why}): {len(m)} trades in {sorted(m['ticker'].unique())[:8]}; flagged {len(flagged)} "
          f"[{', '.join(c[2:] for c in cols if (m[c].fillna(0) > 0).any())}]")
    for _, r in m.sort_values("filed_date").tail(4).iterrows():
        print(f"      {pd.Timestamp(r['filed_date']).date()} {r['tx_type']} {r['ticker']} "
              + " ".join(f"{c[2:]}={r[c]:.0f}" for c in cols))

ties = json.load(open(f"{D}/state/spouse_ties.json")) if os.path.exists(f"{D}/state/spouse_ties.json") else []
print(f"\nFamily ties table: {len(ties)} rows; by confidence " + str(collections.Counter(t.get('Confidence') for t in ties)))
traded = set(zip(sc["member"].astype(str), sc["ticker"]))
hit = [t for t in ties if any(str(t.get("Member", "")).split()[-1] in m and f"({tk})" in str(t.get("Company")) for m, tk in traded)]
print(f"  ties where the member actually traded that company: {len(hit)}")
for t in hit:
    print(f"   {t.get('Member')} | {t.get('Spouse')} | {t.get('Company')} | {t.get('Confidence')}")
