"""Member pages: one file per member of Congress with every disclosed trade, how each did against the S&P 500
(3 months and 1 year after the trade date), and everything public tied to that trade's company in the
3 months before and after it (committee hearings and closed briefings, votes, markups, contracts, federal rules,
company announcements, lobbying, company insiders' trades, floor speeches, other members' trades, posts).

The app filters the timeline with sliders (default: 45 days before the trade), so the files hold +/- 92 days.
Uploaded to Cloudflare R2 as members/index.json and members/<id>.json; unchanged files aren't re-sent."""
import os, re, json, hashlib, datetime as dt
import numpy as np
import pandas as pd

WINDOW = 92            # days kept on each side of a trade (the app's sliders go up to 13 weeks = 91 days)
HORIZONS = {"3m": 63, "1y": 252}     # trading days
# what the tool had flagged on a trade, for the trade's Context view (generic scoring inputs left out)
SIG_LABELS = {"committee": "their committee oversees the industry", "contracts": "company has big federal contracts",
              "lobbying": "company lobbies their committees", "grants": "company got federal grants or loans",
              "grant_after_trade": "a grant or loan came after the trade", "campaign_vendor": "their campaign paid the company",
              "spouse_employer": "spouse works for the company", "spouse_insider": "spouse is a company insider",
              "relative_tie": "a relative is a company insider", "family_possible": "possible family tie",
              "paid_travel": "company paid for travel", "outside_position": "holds a position with the company",
              "disclosure_tie": "company named in their yearly disclosure", "already_owned": "already owned it",
              "insider_buying": "company insiders were buying", "insider_selling": "company insiders were selling",
              "unusual_volume": "unusual trading volume before the trade", "reg_action": "federal rule naming the company after",
              "witness_after": "company testified to their committee after", "markup_after": "their committee marked up an industry bill after",
              "major_8k_after": "major company announcement after", "bill_advanced": "a bill they back advanced after",
              "closed_briefing": "closed committee briefing before", "testified": "company testified to their committee",
              "home_state": "company based in their state", "contract_in_state": "company has contracts in their state",
              "speech": "named the company in a floor speech", "pre_event": "traded before a company event",
              "option": "used options", "against_street": "bought against Wall Street ratings", "after_downgrade": "bought after a downgrade",
              "no_coverage": "no analyst covers the company", "activist_13d": "an activist investor took a stake",
              "new_5pct": "a new 5% holder", "funds_adding": "funds were adding the stock", "short_heavy": "heavy short selling",
              "post_named_before": "named the company in a post before", "post_sector_burst": "posting burst about the industry",
              "post_crowd": "3+ members posted about the company", "post_tone_clash": "post tone clashes with the trade",
              "buddy": "a frequent trading partner made the same trade", "revolving_door": "ex-staff lobbyist for the company",
              "employee_donations": "company employees donated to them", "vote_sector": "voted on an industry bill near the trade",
              "bills": "sponsored a bill in the industry", "first_time": "first time trading this stock",
              "defense_company": "defense company", "small_cap": "company under $2B", "full_sale": "sold the whole position"}
SECTOR_ALL = "*"
MAX_EVENTS = 6000      # per member file (keeps the busiest traders' pages under ~1 MB)


def _d(x):
    try:
        return pd.Timestamp(x).strftime("%Y-%m-%d") if pd.notna(x) else None
    except Exception:
        return None


def _money(v):
    v = float(v or 0)
    return f"${v/1e9:.1f}B" if v >= 1e9 else f"${v/1e6:.1f}M" if v >= 1e6 else f"${v/1e3:.0f}K"


def member_key(bioguide, name):
    if isinstance(bioguide, str) and re.fullmatch(r"[A-Z]\d{6}", bioguide):
        return bioguide
    return "n-" + re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:40]


# ---------------------------------------------------------------------------
# returns after each trade
# ---------------------------------------------------------------------------
def trade_returns(tr, px):
    """Return and return vs SPY from the first close on/after the trade date, 63 and 252 trading days later."""
    out = {f"r{k}": np.full(len(tr), np.nan) for k in HORIZONS} | {f"x{k}": np.full(len(tr), np.nan) for k in HORIZONS}
    if px is None or not len(px) or "SPY" not in px:
        return out
    pxf = px.ffill()
    arr = pxf.to_numpy(dtype=float)
    col = {c: i for i, c in enumerate(pxf.columns)}
    idx = pxf.index.values
    spy = arr[:, col["SPY"]]
    td = pd.to_datetime(tr["trade_date"].where(tr["trade_date"].notna(), tr["filed_date"])).values
    pos = np.searchsorted(idx, td, side="left")
    n = len(idx)
    for i, (t, p) in enumerate(zip(tr["ticker"].values, pos)):
        c = col.get(t)
        if c is None or p >= n:
            continue
        p0 = arr[p, c]
        if not np.isfinite(p0) or p0 <= 0:
            continue
        for k, h in HORIZONS.items():
            if p + h < n:
                p1, s0, s1 = arr[p + h, c], spy[p], spy[p + h]
                if np.isfinite(p1) and np.isfinite(s0) and np.isfinite(s1) and s0 > 0:
                    out[f"r{k}"][i] = p1 / p0 - 1
                    out[f"x{k}"][i] = (p1 / p0) - (s1 / s0)
    return out


# ---------------------------------------------------------------------------
# events tied to a company (ticker) or to a member
# ---------------------------------------------------------------------------
def company_events(data, tickers, company_key):
    """{ticker: [[date, kind, text, url], ...]} for the tickers members traded."""
    from congress_signals.engine import MAJOR_8K
    tickers = set(tickers)
    ev = {t: [] for t in tickers}
    meta = data.get("meta") or {}
    add = lambda t, d, k, x, u="": ev[t].append([d, k, x, u]) if t in ev and d else None
    for t, v in (data.get("k8") or {}).items():
        if t not in ev:
            continue
        for d, items in v.get("k", []):
            its = [MAJOR_8K[x.strip()] for x in str(items).split(",") if x.strip() in MAJOR_8K]
            if its:
                add(t, _d(d), "8k", "Company announced " + ", ".join(dict.fromkeys(its)) + " (8-K)")
    for t, v in (data.get("fedreg") or {}).items():
        for doc in v.get("docs", []) if t in ev else []:
            d, typ, ag, title = (list(doc) + [None] * 4)[:4]
            add(t, _d(d), "fr", f"Federal {str(typ or 'document').lower()} naming the company" + (f" ({ag})" if ag else "")
                + (f": {title}" if title else ""))
    con = data.get("contracts")
    if con is not None and len(con):
        c = con[con["ticker"].isin(tickers)].copy()
        c["action_date"] = pd.to_datetime(c["action_date"], errors="coerce")
        g = c.dropna(subset=["action_date"]).groupby(["ticker", "action_date"]).agg(
            amount=("amount", "sum"), agency=("agency", "first"), st=("pop_state", "first")).reset_index()
        for r in g[g["amount"] >= 1e6].itertuples():
            add(r.ticker, _d(r.action_date), "ct", f"Federal contract {_money(r.amount)}" + (f" from {r.agency}" if r.agency else "")
                + (f", work in {r.st}" if isinstance(r.st, str) and r.st else ""))
    for t, v in (data.get("events") or {}).items():
        for kind, d in v.get("events", []) if t in ev else []:
            add(t, _d(d), "ern" if kind == "earnings" else "fda", "Earnings report" if kind == "earnings" else "FDA approval")
    lb = data.get("lobbying")
    if lb is not None and len(lb):
        for r in lb[lb["ticker"].isin(tickers)].itertuples():
            iss = ", ".join(r.issues) if isinstance(r.issues, (list, tuple)) else str(r.issues or "")
            iss = iss[:140]
            who = r.lobbyists if isinstance(r.lobbyists, str) else ", ".join(r.lobbyists[:4]) if isinstance(r.lobbyists, (list, tuple)) else ""
            add(r.ticker, _d(r.posted), "lb", f"Lobbying filing {_money(r.amount)}" + (f" on {iss}" if iss else "")
                + (f" · lobbyists: {who[:120]}" if who else ""))
    ins = data.get("insiders")
    if ins is not None and len(ins):
        i2 = ins[ins["ticker"].isin(tickers)].copy()
        i2["filed"] = pd.to_datetime(i2["filed"], errors="coerce")
        g = i2.dropna(subset=["filed"]).groupby(["ticker", "filed", "code"]).agg(value=("value", "sum"), n=("owner", "nunique")).reset_index()
        for r in g[g["value"] >= 1e5].itertuples():
            add(r.ticker, _d(r.filed), "ins", f"Company insider{'s' if r.n > 1 else ''} {'bought' if r.code == 'P' else 'sold'} "
                f"{_money(r.value)} of stock (filed)")
    # companies that testified at any hearing (the member's own committees are marked per member later)
    mt = data.get("meetings")
    if mt is not None and len(mt):
        keys = {t: company_key((meta.get(t) or {}).get("name")) for t in tickers}
        keys = {t: k for t, k in keys.items() if k and len(k) >= 4}
        by_key = {}
        for t, k in keys.items():
            by_key.setdefault(k, []).append(t)
        for r in mt.itertuples():
            for o in r.orgs:
                for k, ts in by_key.items():
                    if k in o:
                        for t in ts:
                            ev[t].append([_d(r.date), "wit", f"Company testified: {r.title[:150]}", "", sorted(r.cids)])
    return ev


def speech_index(data):
    """{(ticker, bioguide): [(date, url)]}: floor speeches naming the company that the member spoke in."""
    sp = data.get("speeches") or {}
    gran = sp.get("granules") or {}
    out = {}
    for t, v in (sp.get("companies") or {}).items():
        for gid in v.get("granules", []):
            g = gran.get(gid) or {}
            pkg = re.match(r"(CREC-\d{4}-\d{2}-\d{2})", gid or "")
            url = f"https://www.govinfo.gov/content/pkg/{pkg.group(1)}/html/{gid}.htm" if pkg else ""
            for b in g.get("ids") or []:
                out.setdefault((t, b), []).append((_d(g.get("date")), url))
    return out


def member_wide_events(data, bioguide, cids):
    """Closed briefings and markups of the member's committees, and the member's floor votes on bills that
    affect an industry: [[date, kind, text, url, sectors]]."""
    from congress_signals.engine import POLICY_SECTORS
    out = []
    pol = data.get("bill_policy") or {}
    mt = data.get("meetings")
    if cids and mt is not None and len(mt):
        for r in mt.itertuples():
            if not (cids & r.cids):
                continue
            if r.closed:
                out.append([_d(r.date), "cb", f"Closed briefing, their committee: {r.title[:150]}", "", [SECTOR_ALL]])
            elif "markup" in str(r.type).lower():
                secs = sorted({s for bl in (r.bills or []) for s in POLICY_SECTORS.get(pol.get(bl) or "", [])})
                if secs:
                    out.append([_d(r.date), "mk", f"Their committee marked up: {r.title[:150]}", "", secs])
    if bioguide:
        for v in (data.get("votes") or {}).values():
            if bioguide in (v.get("ids") or []):
                p = pol.get(v.get("bill")) or ""
                secs = POLICY_SECTORS.get(p, [])
                if secs:
                    out.append([_d(v.get("date")), "vt", f"Voted on {v.get('bill')} ({p})", "", list(secs)])
    return out


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def export_members(cfg, scored, px, data, log=print, put=None, photos=None, posts=None, post_profiles=None):
    """Writes data/members/index.json and one file per member; uploads changed files with put(name, path)."""
    import congress_signals.engine as E
    data = data or {}
    out_dir = os.path.join(cfg["DATA_DIR"], "members")
    os.makedirs(out_dir, exist_ok=True)
    sigc = [f"f_{k}" for k in SIG_LABELS if f"f_{k}" in scored]
    notec = [c for c in ("spouse_note", "relative_note", "family_note", "post_note", "volume_note", "event_note", "buddy_note",
                         "bill_adv_note") if c in scored]
    cols = [c for c in ("member", "bioguide", "ticker", "company", "tx_type", "trade_date", "filed_date", "owner", "amt_lo",
                        "amt_hi", "sector", "chamber", "state", "party", "cids", "option", "score") if c in scored] + sigc + notec
    tr = scored[cols].copy()
    tr = tr[tr["tx_type"].isin(["buy", "sell"])].reset_index(drop=True)
    tr["key"] = [member_key(b, m) for b, m in zip(tr.get("bioguide", [None] * len(tr)), tr["member"])]
    rets = trade_returns(tr, px)
    for k, v in rets.items():
        tr[k] = v
    tr["td"] = pd.to_datetime(tr["trade_date"].where(tr["trade_date"].notna(), tr["filed_date"]))
    tags = {}
    try:
        tags = (json.load(open(os.path.join(cfg["DATA_DIR"], "state", "member_tags.json"))) or {})
    except Exception:
        pass
    cev = company_events(data, tr["ticker"].unique(), E.company_key)
    for t in cev:
        cev[t] = sorted([e for e in cev[t] if e[0]], key=lambda e: e[0])
    sidx = speech_index(data)
    by_t = {t: g for t, g in tr[["ticker", "td", "tx_type", "member", "key"]].groupby("ticker")}
    if photos is None:
        try:
            photos = E.member_photos(cfg, [k for k in tr["key"].unique() if re.fullmatch(r"[A-Z]\d{6}", k)])
        except Exception as e:
            log(f"Member pages: photos skipped ({type(e).__name__})")
            photos = {}
    hashes_path = os.path.join(cfg["DATA_DIR"], "state", "members_hash.json")
    try:
        old_hash = json.load(open(hashes_path))
    except Exception:
        old_hash = {}
    new_hash, index, sent = {}, [], 0
    W = pd.Timedelta(days=WINDOW)
    for key, g in tr.groupby("key"):
        g = g.sort_values("td", ascending=False)
        name = str(g["member"].iloc[0])
        b = key if re.fullmatch(r"[A-Z]\d{6}", key) else None
        tag = (tags.get("bio") or {}).get(b) or (tags.get("names") or {}).get(name) or ""
        cids = {c for s in g.get("cids", pd.Series([""])).fillna("") for c in str(s).split(";") if c}
        trades = []
        sig_rows = []
        for rr in g[sigc + notec].to_dict("records") if sigc or notec else [{}] * len(g):
            sg = [SIG_LABELS[c[2:]] for c in sigc if pd.notna(rr.get(c)) and float(rr.get(c) or 0) > 0]
            sg += [str(rr.get(c)) for c in notec if rr.get(c)]
            sig_rows.append(sg[:16])
        for i, r in enumerate(g.itertuples()):
            p30 = None
            try:
                if r.ticker in px.columns and pd.notna(r.td):
                    pp = px[r.ticker].loc[r.td - pd.Timedelta(days=30):r.td].dropna()
                    p30 = round(float(pp.iloc[-1] / pp.iloc[0] - 1), 4) if len(pp) > 1 else None
            except Exception:
                p30 = None
            trades.append([i, _d(r.td), _d(r.filed_date), r.ticker, (r.company or "")[:60], r.tx_type,
                           str(r.owner or "")[:12], None if pd.isna(r.amt_lo) else int(r.amt_lo),
                           None if pd.isna(r.amt_hi) else int(r.amt_hi), r.sector or "",
                           *[None if pd.isna(getattr(r, f"x{k}")) else round(float(getattr(r, f"x{k}")), 4) for k in HORIZONS],
                           *[None if pd.isna(getattr(r, f"r{k}")) else round(float(getattr(r, f"r{k}")), 4) for k in HORIZONS],
                           p30, sig_rows[i]])
        # events within WINDOW days of one of the member's trades in that company / sector
        tdates = {t: np.sort(gg["td"].values) for t, gg in g.groupby("ticker")}
        sdates = {s: np.sort(gg["td"].values) for s, gg in g.groupby("sector") if s}
        alld = np.sort(g["td"].values)

        def near(arr, d):
            if d is None or not len(arr):
                return False
            x = np.datetime64(d)
            k = np.searchsorted(arr, x - np.timedelta64(WINDOW, "D"), side="left")
            return k < len(arr) and arr[k] <= x + np.timedelta64(WINDOW, "D")
        evs = []
        for t, arr in tdates.items():
            for e in cev.get(t, []):
                if e[1] == "wit":
                    mine = bool(cids & set(e[4]))
                    if near(arr, e[0]):
                        evs.append([e[0], "witm" if mine else "wit", e[2] + (" (their committee)" if mine else ""), t, "", ""])
                elif near(arr, e[0]):
                    evs.append([e[0], e[1], e[2], t, "", e[3]])
            for d, url in sidx.get((t, b), []) if b else []:
                if near(arr, d):
                    evs.append([d, "sp", "Spoke on the floor in a debate naming the company", t, "", url])
            # other members' trades in the same stock
            og = by_t.get(t)
            if og is not None:
                og = og[og["key"] != key]
                for r in og.itertuples():
                    d = _d(r.td)
                    if near(arr, d):
                        evs.append([d, "ot", f"{r.member} {'bought' if r.tx_type == 'buy' else 'sold'}", t, "", ""])
        for e in member_wide_events(data, b, cids):
            secs = e[4]
            ok = near(alld, e[0]) if SECTOR_ALL in secs else any(near(sdates.get(s, np.array([], dtype="datetime64[ns]")), e[0]) for s in secs)
            if ok:
                evs.append([e[0], e[1], e[2], "", "|".join(secs), e[3]])
        for p in (posts or {}).get(key, []):
            # p: [date, platform, text, tickers(list), tone, url, kind]
            if near(alld, p[0]):
                evs.append([p[0], "post", f"{p[1]}: {p[2][:280]}", ",".join(p[3] or []), "", p[5] or ""])
        evs = sorted({tuple(e) for e in evs if e[0]}, key=lambda e: e[0])
        if len(evs) > MAX_EVENTS:      # very active traders: keep company news first, then the most recent of the rest
            core = [e for e in evs if e[1] not in ("ot", "vt")]
            rest = [e for e in evs if e[1] in ("ot", "vt")]
            keep = core[-MAX_EVENTS:] + (rest[-(MAX_EVENTS - len(core)):] if len(core) < MAX_EVENTS else [])
            evs = sorted(keep, key=lambda e: e[0])
        bx = g[g["tx_type"] == "buy"]
        sx = g[g["tx_type"] == "sell"]
        st = {"nt": len(g), "buys": len(bx), "sells": len(sx), "first": _d(g["td"].min()), "last": _d(g["td"].max()),
              "bx3m": _mean(bx["x3m"]), "bx1y": _mean(bx["x1y"]), "bhit1y": _hit(bx["x1y"]),
              "sx3m": _mean(-sx["x3m"]), "sx1y": _mean(-sx["x1y"]), "tickers": int(g["ticker"].nunique())}
        doc = {"k": key, "b": b, "n": name, "tag": tag, "ch": str(g["chamber"].iloc[0]) if "chamber" in g else "",
               "photo": (photos or {}).get(b), "stats": st,
               "tcols": ["i", "td", "fd", "t", "co", "ty", "own", "lo", "hi", "sec", "x3m", "x1y", "r3m", "r1y", "p30", "sg"],
               "trades": trades, "ecols": ["d", "k", "x", "t", "sec", "u"], "events": [list(e) for e in evs],
               "posts": [p for p in (posts or {}).get(key, [])][-300:],
               "pprof": (post_profiles or {}).get(key),
               "ties": _fd_ties(g)}
        txt = json.dumps(_clean(doc), separators=(",", ":"), default=str)
        h = hashlib.md5(txt.encode()).hexdigest()
        path = os.path.join(out_dir, f"{key}.json")
        open(path, "w").write(txt)
        new_hash[key] = h
        if put and old_hash.get(key) != h:
            if put(f"members/{key}.json", path):
                sent += 1
            else:
                new_hash[key] = None
        index.append({"k": key, "n": name, "tag": tag, "ch": doc["ch"], **st})
    index.sort(key=lambda r: r["last"] or "", reverse=True)
    ipath = os.path.join(out_dir, "index.json")
    itxt = json.dumps(_clean({"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                              "members": index}), separators=(",", ":"))
    open(ipath, "w").write(itxt)
    if put:
        put("members/index.json", ipath)
    json.dump(new_hash, open(hashes_path, "w"))
    log(f"Member pages: {len(index)} members, {len(tr):,} trades; {sent} page(s) changed and uploaded "
        f"({len(itxt)/1024:,.0f} KB index)")
    return ipath


def _fd_ties(g):
    """The yearly-report lines behind this member's company ties (spouse's job, outside positions, sponsored
    travel), newest first, with a link to the report and any dollar amount on the line (owner, Oct 29)."""
    try:
        from . import engine as E
    except Exception:
        return []
    out, seen = [], set()
    for w in (g["who"].dropna().unique() if "who" in g else []):
        for e in E.FD_TIE_EV.get(w, []):
            k = (e["t"], e["kind"], e["doc"])
            if k in seen:
                continue
            seen.add(k)
            amt = re.findall(r"(?:over\s+)?\$[\d,]+(?:\s*-\s*\$[\d,]+)?", e["line"], re.I)
            out.append({**e, "amt": amt[0] if amt else None})
    out.sort(key=lambda e: e["filed"] or "", reverse=True)
    return out[:60]


def _mean(s):
    s = pd.to_numeric(s, errors="coerce").dropna()
    return round(float(s.mean()), 4) if len(s) else None


def _hit(s):
    s = pd.to_numeric(s, errors="coerce").dropna()
    return round(float((s > 0).mean()), 3) if len(s) else None


def _clean(o):
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.floating):
        v = float(o)
        return v if np.isfinite(v) else None
    if isinstance(o, np.integer):
        return int(o)
    return o
