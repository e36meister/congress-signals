"""Read-only follow-ups to the signal audit: the suspicious gaps, traced to their data."""
import sys, os, json, re, collections
import numpy as np, pandas as pd, requests
sys.path.insert(0, ".")
import congress_signals.engine as E

D = "data"
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": D, "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", "")}
sc = pd.read_pickle(f"{D}/cache/scored.pkl")
sc["fd"] = pd.to_datetime(sc["filed_date"])
sc["yr"] = sc["fd"].dt.year

print("=== 1. SEC insider data (quarterly files) ===")
q = json.load(open(f"{D}/cache/insider_quarters.json"))
print("quarters marked final:", sorted(q)[-8:], f"({len(q)})")
ins = pd.read_pickle(f"{D}/cache/insiders.pkl")
ins["q"] = pd.to_datetime(ins["filed"]).dt.to_period("Q")
print("insider trades by filing quarter (last 10):", ins.groupby("q").size().tail(10).to_dict())
hdr = E._sec_headers(cfg)
for k in ("2026q1", "2026q2", "2026q3", "2025q4"):
    try:
        r = requests.head(f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{k}_form345.zip",
                          headers=hdr, timeout=60, allow_redirects=True)
        print(f"  SEC file {k}: HTTP {r.status_code}, {r.headers.get('Content-Length')} bytes")
    except Exception as e:
        print(f"  SEC file {k}: {type(e).__name__}")

print("\n=== 2. Options trades by year ===")
for nm in ("house_tx", "senate_tx"):
    p = f"{D}/cache/{nm}.pkl"
    if not os.path.exists(p):
        continue
    t = pd.read_pickle(p)
    t["yr"] = pd.to_datetime(t.get("filed_date", t.get("trade_date")), errors="coerce").dt.year
    at = t["asset_type"] if "asset_type" in t else pd.Series("", index=t.index)
    opt = t["option"].notna() if "option" in t else pd.Series(False, index=t.index)
    txt = (t.get("tx_raw", "").astype(str) + " " + t.get("note", "").astype(str) + " " + t.get("name", "").astype(str)).str.lower()
    words = txt.str.contains(r"\b(?:call|put)s?\b|strike|option", regex=True)
    g = pd.DataFrame({"yr": t["yr"], "OP tag": at.astype(str).eq("OP"), "option set": opt, "words, no option": words & ~opt})
    print(nm, "\n", g.groupby("yr")[["OP tag", "option set", "words, no option"]].sum().astype(int).T.to_string())
    ex = t[words & ~opt].tail(5)
    for _, r in ex.iterrows():
        print("   example missed:", {k: str(r.get(k))[:80] for k in ("filed_date", "ticker", "asset_type", "tx_raw", "note", "name")})
print("scored f_option by year:", sc.groupby("yr")["f_option"].sum().astype(int).to_dict())
print("scored asset types:", sc["asset_type"].value_counts().to_dict() if "asset_type" in sc else "no column")

print("\n=== 3. Lobbying filings by year ===")
lob = json.load(open(f"{D}/cache/lobbying.json"))
ly = collections.Counter()
for v in lob.values():
    for r in (v.get("rows") or []) if isinstance(v, dict) else []:
        ly[str(r[0])[:4]] += 1
print("filings by year:", dict(sorted(ly.items())))
print("companies:", len(lob), "| with no filings:", sum(1 for v in lob.values() if not (isinstance(v, dict) and v.get("rows"))))
sample = next(iter(lob.values()))
print("one entry's keys:", list(sample)[:10] if isinstance(sample, dict) else type(sample))
print("scored f_lobbying>0 by year:", sc.groupby("yr")["f_lobbying"].apply(lambda s: int((s > 0).sum())).to_dict())

print("\n=== 4. Senate scanned reports not matched (2025-2026) ===")
p = f"{D}/cache/senate_paper_unmatched.pkl"
if os.path.exists(p):
    u = pd.read_pickle(p)
    u["yr"] = pd.to_datetime(u["trade_date"], errors="coerce").dt.year
    r = u[u["yr"] >= 2025]
    print(len(r), "rows; columns:", list(u.columns))
    for c in [c for c in ("name", "asset", "member", "reason", "why") if c in r]:
        print(f"  most common {c}:", r[c].astype(str).str[:60].value_counts().head(12).to_dict())
    print(r.head(12).to_string()[:3000])

print("\n=== 5. Post signals: member id coverage and mention matches ===")
print("trades with a member id (bioguide):", f"{sc['bioguide'].notna().mean() * 100:.1f}%" if "bioguide" in sc else "NO bioguide column")
try:
    import congress_signals.social as S
    idx = S.load_index({"SOCIAL_DIR": "social"})
    men = (idx or {}).get("mentions") or {}
    print("index keys:", list((idx or {}).keys()), "| members with company mentions:", len(men))
    k0 = next(iter(men)) if men else None
    print("mention format example:", k0, str(men.get(k0))[:300] if k0 else None)
    yrs = collections.Counter()
    for b, v in men.items():
        for item in (v.items() if isinstance(v, dict) else v):
            ds = item[1] if isinstance(v, dict) else [item]
            for d in (ds if isinstance(ds, list) else [ds]):
                yrs[str(d[0] if isinstance(d, (list, tuple)) else d)[:4]] += 1
    print("dated mentions by year:", dict(sorted(yrs.items())))
except Exception as e:
    print("social index unreadable:", type(e).__name__, e)
print("post signals by year:", {c: sc.groupby("yr")[c].sum().astype(int).to_dict() for c in ("f_post_named_before", "f_social_post") if c in sc})
print("social/complete exists:", os.path.exists("social/complete"))

print("\n=== 6. Committee leaders by snapshot ===")
try:
    import glob
    for f in sorted(glob.glob(f"{D}/cache/committee_snapshots/*_v2.json"))[:0]:
        pass
    idx_ = json.load(open(f"{D}/cache/committee_history_index.json"))
    for key in sorted(idx_)[::4]:
        sha = (idx_[key] or {}).get("sha")
        p = f"{D}/cache/committee_snapshots/{sha}_v2.json"
        if sha and os.path.exists(p):
            snap = json.load(open(p))
            recs = [r for v in snap.values() for r in v]
            print(f"  {key}: members {len(recs)}, with a chair/ranking seat {sum(1 for r in recs if r.get('lead'))}")
        else:
            print(f"  {key}: no snapshot")
except Exception as e:
    print("committee snapshots unreadable:", type(e).__name__, e)
