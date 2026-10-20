"""Read-only: for chosen members' small-company buys in a window, everything known around each trade: what the
tool recorded, the member's own posts before it, the company's SEC filings (8-K items) and earnings dates around it,
and how the stock had moved before and after."""
import sys, os, json, time
import numpy as np, pandas as pd, requests
sys.path.insert(0, ".")
import congress_signals.engine as E
import congress_signals.social as S
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "social", "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", "")}
WHO = os.environ.get("WHO", "Wasserman Schultz|Gottheimer").split("|")
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
pol = E.load_adaptive(cfg)["policy"]
rows = E.small_cap_rows(sc, coverage=bool(pol.get("sleeve_coverage")), hold=int(pol.get("hold_sleeve", 250)))
rows = rows[rows["member"].astype(str).str.contains("|".join(WHO))]
rows = rows[(pd.to_datetime(rows["filed_date"]) >= "2024-09-01") & (pd.to_datetime(rows["filed_date"]) <= "2025-10-24")]
idx = S.load_index(cfg) or {}
men, secs = idx.get("mentions") or {}, idx.get("sectors") or {}
rp = idx.get("recent_posts") or {}
hdr = E._sec_headers(cfg)
tick_cik = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in requests.get("https://www.sec.gov/files/company_tickers.json", headers=hdr, timeout=60).json().values()}
ev = json.load(open("data/cache/company_events.json")) if os.path.exists("data/cache/company_events.json") else {}
ITEMS = {"1.01": "material agreement", "1.02": "agreement ended", "2.01": "acquisition/sale done", "2.02": "earnings results",
         "2.03": "new debt", "2.05": "restructuring", "3.02": "share sale", "5.02": "executive/director change",
         "5.07": "shareholder vote", "7.01": "Reg FD disclosure", "8.01": "other event", "9.01": "exhibits", "1.03": "bankruptcy",
         "3.01": "listing notice", "4.02": "restatement", "5.03": "bylaws change", "2.06": "impairment"}
fcols = [c for c in sc.columns if c.startswith("f_")]
lab = {**{k: v.replace("Signal: ", "") for k, v in E.TRIAL_LABELS.items()}}
for _, r in rows.sort_values(["member", "trade_date"]).iterrows():
    t = r["ticker"]; td = pd.Timestamp(r["trade_date"]); fd = pd.Timestamp(r["filed_date"])
    print("\n" + "=" * 100)
    print(f"{r['member']} — {t} {r.get('company')} · {r.get('sector')} / {r.get('industry')} · cap ${float(r.get('market_cap') or 0) / 1e6:,.0f}M")
    print(f"  traded {td.date()} · filed {fd.date()} · owner {r.get('owner') or 'self'} · ${float(r.get('amt_lo') or 0):,.0f}-{float(r.get('amt_hi') or 0):,.0f}")
    if t in px.columns:
        p = px[t].dropna()
        def ch(a, b):
            s_ = p.loc[a:b]
            return f"{s_.iloc[-1] / s_.iloc[0] - 1:+.1%}" if len(s_) > 1 else "–"
        print(f"  stock: 30 days before {ch(td - pd.Timedelta(days=30), td)} · trade→filing {ch(td, fd)} · 3 months after filing {ch(fd, fd + pd.Timedelta(days=91))} · 6 months {ch(fd, fd + pd.Timedelta(days=182))}")
    print(f"  tool's reasons: {r.get('why') or '–'}")
    on = [c for c in fcols if pd.notna(r.get(c)) and float(r.get(c) or 0) != 0]
    print("  signals on:", ", ".join(c[2:] for c in on) or "none")
    for nc in ("spouse_note", "relative_note", "family_note", "post_note", "volume_note", "event_note", "buddy_note", "bill_adv_note"):
        if r.get(nc):
            print(f"  {nc}: {r.get(nc)}")
    # the member's posts before the trade
    b = r.get("bioguide")
    ms = ((men.get(b) or {}).get(t) or []) if isinstance(b, str) else []
    near = [m for m in ms if (td - pd.Timedelta(days=180)).strftime("%Y-%m-%d") <= m[0] <= fd.strftime("%Y-%m-%d")]
    print(f"  member's posts naming the company (180 days before → filing): {len(near)}" + (f" {near[:5]}" if near else ""))
    sec = r.get("sector")
    arr = (secs.get(b) or {}).get(sec) if isinstance(b, str) and sec else None
    if arr is not None and len(arr):
        x = np.datetime64(td)
        n30 = int(np.searchsorted(arr, x) - np.searchsorted(arr, x - np.timedelta64(30, "D")))
        n365 = int(np.searchsorted(arr, x - np.timedelta64(30, "D")) - np.searchsorted(arr, x - np.timedelta64(395, "D")))
        print(f"  posts about {sec}: {n30} in the 30 days before (usual month ~{n365 / 12.2:.1f})")
    for p_ in (rp.get(b) or []) if isinstance(b, str) else []:
        if (td - pd.Timedelta(days=60)).strftime("%Y-%m-%d") <= p_["d"] <= fd.strftime("%Y-%m-%d") and (t in (p_.get("t") or []) or sec in (p_.get("s") or [])):
            print(f"    post {p_['d']} [{p_.get('src')}]: {str(p_.get('x') or '')[:160]}  {p_.get('u') or ''}")
    # SEC filings around the trade
    cik = tick_cik.get(t)
    if cik and hdr:
        try:
            js = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=hdr, timeout=30).json()
            f_ = js.get("filings", {}).get("recent", {})
            got = []
            for form, d, items in zip(f_.get("form", []), f_.get("filingDate", []), f_.get("items", [])):
                dd = pd.Timestamp(d)
                if form in ("8-K", "8-K/A", "SC 13D", "SC 13G", "4") and td - pd.Timedelta(days=45) <= dd <= td + pd.Timedelta(days=60):
                    desc = ", ".join(ITEMS.get(i.strip(), i.strip()) for i in str(items).split(",") if i.strip() and i.strip() != "9.01")
                    rel = (dd - td).days
                    got.append(f"{form} {d} ({'+' if rel >= 0 else ''}{rel}d){': ' + desc if desc else ''}")
            print("  SEC filings 45 days before → 60 after the trade:" + ("".join("\n    " + g for g in got[::-1] if not g.startswith("4 ")) or " none"))
            n4 = sum(1 for g in got if g.startswith("4 "))
            if n4:
                print(f"    + {n4} insider (Form 4) filings in that span")
            time.sleep(0.2)
        except Exception as e:
            print("  SEC filings: failed", type(e).__name__)
    earn = sorted(d for k, d in (ev.get(t) or {}).get("events", []) if k == "earnings")
    nearE = [d for d in earn if (td - pd.Timedelta(days=45)).strftime("%Y-%m-%d") <= d <= (td + pd.Timedelta(days=60)).strftime("%Y-%m-%d")]
    print(f"  earnings dates near the trade: {nearE or '–'}")
