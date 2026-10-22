"""Why it moved (owner, Oct 22): for every stock held (yours and the tool's) and every BUY candidate, when it moves
3%+ against the S&P in a day or 7%+ over 5 trading days, gather why and explain it in two or three lines:
  * the move itself vs the S&P,
  * whether similar companies (same industry, Yahoo) and the sector fund moved the same way (sector-wide vs this stock),
  * earnings reported in the last few days, analyst rating / price-target changes, company SEC filings (8-K),
  * the latest headlines (Alpaca's news feed; Google News as the backup source),
  * member trades of the stock in the last 30 days.
Saved to Cloudflare (movers/latest.json, shown in the app) and ONE combined phone alert per check for the stocks newly
flagged. Runs from the 15-minute filings check, at most every 30 minutes, on weekdays 9:45 AM-6 PM ET.
Every source failure is logged (Data health picks it up); nothing is silently skipped."""
import os, re, json, html, time, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
import requests

DAY_REL, FIVE_REL = 3.0, 7.0            # % vs the S&P that triggers a check
STATE_KEY, OUT_KEY = "movers/state.json", "movers/latest.json"
DATA = "https://data.alpaca.markets"
SECTOR_ETF = {"Technology": "XLK", "Industrials": "XLI", "Financial Services": "XLF", "Healthcare": "XLV",
              "Energy": "XLE", "Utilities": "XLU", "Basic Materials": "XLB", "Consumer Cyclical": "XLY",
              "Consumer Defensive": "XLP", "Real Estate": "XLRE", "Communication Services": "XLC"}
SECTOR_NAME = {"XLK": "Tech", "XLI": "Industrials", "XLF": "Financials", "XLV": "Healthcare", "XLE": "Energy",
               "XLU": "Utilities", "XLB": "Materials", "XLY": "Consumer", "XLP": "Consumer staples", "XLRE": "Real estate",
               "XLC": "Communications"}
ITEMS = {"1.01": "material agreement", "1.02": "agreement ended", "1.03": "bankruptcy", "2.01": "acquisition or sale",
         "2.02": "earnings results", "2.03": "new debt", "2.05": "restructuring", "2.06": "impairment",
         "3.01": "listing notice", "3.02": "share sale", "4.01": "auditor change", "4.02": "restatement",
         "5.01": "change in control", "5.02": "executive or director change", "5.07": "shareholder vote",
         "7.01": "investor disclosure", "8.01": "other event"}
PARK = {"SPY", "VOO", "IVV", "SGOV", "BIL", "SHV"}
UA_WEB = {"User-Agent": "Mozilla/5.0 (compatible; CapitolCapital research)"}


def _et_now():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return dt.datetime.utcnow() - dt.timedelta(hours=4)


def due(state, now=None):
    now = now or _et_now()
    if now.weekday() >= 5 or not (dt.time(9, 45) <= now.time() <= dt.time(18, 0)):
        return False
    last = state.get("last")
    return not last or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last)).total_seconds() > 29 * 60


def _pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def _fmt(v, d=1):
    return "–" if v is None else f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v):.{d}f}%"


# ------------------------------------------------------------------------------------------------ prices
def moves(api_h, syms, log):
    """{sym: {"day", "d5", "px", "date"}} from Alpaca (snapshots for today, daily bars for 5 days)."""
    out = {}
    syms = sorted(set(syms))
    for i in range(0, len(syms), 100):
        chunk = [s.replace("-", ".") for s in syms[i:i + 100]]
        try:
            r = requests.get(f"{DATA}/v2/stocks/snapshots", headers=api_h, timeout=30,
                             params={"symbols": ",".join(chunk), "feed": "iex"})
            r.raise_for_status()
            snaps = r.json() or {}
        except Exception as e:
            log(f"Why it moved: price snapshots failed ({type(e).__name__}: {e})")
            snaps = {}
        start = (dt.date.today() - dt.timedelta(days=14)).isoformat()
        bars = {}
        try:
            tok = None
            while True:
                p = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start, "feed": "iex",
                     "adjustment": "all", "limit": 10000}
                if tok:
                    p["page_token"] = tok
                r = requests.get(f"{DATA}/v2/stocks/bars", headers=api_h, params=p, timeout=30)
                r.raise_for_status()
                js = r.json() or {}
                for s, bl in (js.get("bars") or {}).items():
                    bars.setdefault(s, []).extend(bl)
                tok = js.get("next_page_token")
                if not tok:
                    break
        except Exception as e:
            log(f"Why it moved: daily bars failed ({type(e).__name__}: {e})")
        for s in chunk:
            sn = snaps.get(s) or {}
            px = (sn.get("latestTrade") or {}).get("p") or (sn.get("dailyBar") or {}).get("c")
            db, pb = sn.get("dailyBar") or {}, sn.get("prevDailyBar") or {}
            day_date = str(db.get("t") or "")[:10]
            day = _pct(px, pb.get("c"))
            closes = [b["c"] for b in sorted(bars.get(s) or [], key=lambda b: b["t"]) if str(b["t"])[:10] < day_date]
            d5 = _pct(px, closes[-5]) if len(closes) >= 5 else None
            out[s.replace(".", "-")] = {"day": day, "d5": d5, "px": px, "date": day_date}
    return out


# ------------------------------------------------------------------------------------------------ context
def news(api_h, t, co, log, days=3):
    """Latest headlines: Alpaca's news feed, then Google News as the backup source."""
    since = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []
    try:
        r = requests.get(f"{DATA}/v1beta1/news", headers=api_h, timeout=30,
                         params={"symbols": t.replace("-", "."), "start": since, "limit": 12, "sort": "desc"})
        r.raise_for_status()
        for n in (r.json() or {}).get("news") or []:
            syms = n.get("symbols") or []
            items.append({"h": html.unescape(n.get("headline") or "").strip(), "src": n.get("source") or "", "u": n.get("url") or "",
                          "at": n.get("created_at") or "", "n_sym": len(syms)})
    except Exception as e:
        log(f"Why it moved: Alpaca news failed for {t} ({type(e).__name__}); using Google News")
    if len(items) < 2:
        q = quote(f'"{co}" stock' if co else f"{t} stock")
        try:
            r = requests.get(f"https://news.google.com/rss/search?q={q}+when:{days}d&hl=en-US&gl=US&ceid=US:en",
                             headers=UA_WEB, timeout=30)
            r.raise_for_status()
            for m in re.finditer(r"<item>(.*?)</item>", r.text, re.S):
                it = m.group(1)
                title = html.unescape(re.sub(r"<[^>]+>", "", (re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1]))
                src = html.unescape((re.search(r"<source[^>]*>(.*?)</source>", it, re.S) or [None, ""])[1])
                link = (re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1]
                pub = (re.search(r"<pubDate>(.*?)</pubDate>", it, re.S) or [None, ""])[1]
                if src and title.endswith(" - " + src):
                    title = title[: -len(src) - 3]
                try:
                    from email.utils import parsedate_to_datetime
                    pub = parsedate_to_datetime(pub).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass
                items.append({"h": title.strip(), "src": src, "u": link.strip(), "at": pub, "n_sym": 1})
        except Exception as e:
            log(f"Why it moved: Google News failed for {t} ({type(e).__name__})")
    # company-specific stories first (fewer tickers tagged), then newest
    seen, out = set(), []
    for n in sorted(items, key=lambda x: x["at"], reverse=True):
        k = re.sub(r"\W+", " ", n["h"].lower())[:60]
        if n["h"] and k not in seen:
            seen.add(k)
            out.append(n)
    out.sort(key=lambda x: (x["n_sym"] > 3, ""), reverse=False)
    return [{k: v for k, v in n.items() if k != "n_sym"} for n in out[:4]]


def yahoo(t, log):
    """Industry, peers, analyst changes in the last 5 days, and earnings reported in the last 3 days."""
    import yfinance as yf
    out = {"industry": None, "sector": None, "peers": [], "analysts": [], "earnings": None, "co": None}
    tk = yf.Ticker(t)
    try:
        info = tk.info or {}
        out.update(industry=info.get("industry"), sector=info.get("sector"), co=info.get("shortName") or info.get("longName"))
        key = info.get("industryKey")
        if key:
            try:
                tc = yf.Industry(key).top_companies
                if tc is not None and len(tc):
                    out["peers"] = [str(s).upper() for s in list(tc.index)[:12] if str(s).upper() != t.replace("-", ".")][:8]
            except Exception as e:
                log(f"Why it moved: Yahoo industry peers failed for {t} ({type(e).__name__})")
    except Exception as e:
        log(f"Why it moved: Yahoo company info failed for {t} ({type(e).__name__})")
    try:
        ud = tk.upgrades_downgrades
        if ud is not None and len(ud):
            ud = ud.reset_index()
            dc = next((c for c in ud.columns if "date" in str(c).lower()), ud.columns[0])
            cut = dt.datetime.utcnow() - dt.timedelta(days=5)
            for r in ud.to_dict("records"):
                when = r.get(dc)
                try:
                    when = when.to_pydatetime().replace(tzinfo=None)
                except Exception:
                    continue
                if when < cut:
                    continue
                act = {"up": "upgraded to", "down": "downgraded to", "init": "started at", "reit": "reiterated",
                       "main": "kept"}.get(str(r.get("Action") or ""), str(r.get("Action") or ""))
                pt, ppt = r.get("currentPriceTarget"), r.get("priorPriceTarget")
                pts = ""
                try:
                    if pt and float(pt) > 0:
                        pts = f", target ${float(ppt):,.0f} → ${float(pt):,.0f}" if ppt and float(ppt) > 0 and float(ppt) != float(pt) else f", target ${float(pt):,.0f}"
                except Exception:
                    pass
                out["analysts"].append({"d": when.strftime("%Y-%m-%d"), "x": f"{r.get('Firm')} {act} {r.get('ToGrade')}{pts}"})
            out["analysts"].sort(key=lambda a: a["d"], reverse=True)
            out["analysts"] = out["analysts"][:5]
    except Exception as e:
        log(f"Why it moved: Yahoo analyst changes failed for {t} ({type(e).__name__})")
    try:
        ed = tk.earnings_dates
        if ed is not None and len(ed):
            now = dt.datetime.now(dt.timezone.utc)
            for when, r in ed.iterrows():
                w = when.to_pydatetime()
                if w.tzinfo is None:
                    w = w.replace(tzinfo=dt.timezone.utc)
                if now - dt.timedelta(days=4) <= w <= now and r.get("Reported EPS") == r.get("Reported EPS"):
                    est, rep = r.get("EPS Estimate"), r.get("Reported EPS")
                    out["earnings"] = {"d": w.strftime("%Y-%m-%d"), "est": None if est != est else float(est), "rep": float(rep)}
                    break
    except Exception as e:
        log(f"Why it moved: Yahoo earnings dates failed for {t} ({type(e).__name__})")
    return out


_CIK = {}


def filings(t, log, days=5):
    ua = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        log("Why it moved: SEC filings skipped (SEC_USER_AGENT secret not set for this job)")
        return []
    h = {"User-Agent": ua}
    try:
        if not _CIK:
            r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=h, timeout=30)
            r.raise_for_status()
            _CIK.update({str(v["ticker"]).upper().replace(".", "-"): int(v["cik_str"]) for v in r.json().values()})
        cik = _CIK.get(t)
        if not cik:
            return []
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=h, timeout=30)
        r.raise_for_status()
        rec = (r.json().get("filings") or {}).get("recent") or {}
    except Exception as e:
        log(f"Why it moved: SEC filings failed for {t} ({type(e).__name__})")
        return []
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    for i, form in enumerate(rec.get("form") or []):
        d = rec["filingDate"][i]
        if d < cut or form not in ("8-K", "8-K/A", "SC 13D", "SCHEDULE 13D", "10-Q", "10-K"):
            continue
        items = [ITEMS.get(x.strip(), x.strip()) for x in str((rec.get("items") or [""] * (i + 1))[i]).split(",")
                 if x.strip() and x.strip() != "9.01"]
        acc = str(rec["accessionNumber"][i]).replace("-", "")
        out.append({"d": d, "x": f"{form}{': ' + ', '.join(items) if items else ''}",
                    "u": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{rec['primaryDocument'][i]}"})
    return out[:4]


# ------------------------------------------------------------------------------------------------ explain
def explain(t, m, spy, ctx, peer_moves, etf, members, kind):
    lines = []
    if kind == "day":
        rel = (m["day"] or 0) - (spy["day"] or 0)
        lines.append(f"{'Up' if m['day'] > 0 else 'Down'} {_fmt(abs(m['day']))[1:]} today (S&P {_fmt(spy['day'])}).")
        mv = m["day"]
    else:
        rel = (m["d5"] or 0) - (spy["d5"] or 0)
        lines.append(f"{'Up' if m['d5'] > 0 else 'Down'} {_fmt(abs(m['d5']))[1:]} over 5 days (S&P {_fmt(spy['d5'])}).")
        mv = m["d5"]
    e = ctx.get("earnings")
    if e:
        beat = "" if e["est"] is None else (" beat" if e["rep"] > e["est"] else " missed" if e["rep"] < e["est"] else " matched")
        lines.append(f"Earnings {e['d']}: ${e['rep']:.2f} a share{beat}" + (f" (expected ${e['est']:.2f})." if e["est"] is not None else "."))
    pm = [(p, v) for p, v in peer_moves if v is not None]
    avg = sum(v for _, v in pm) / len(pm) if pm else None
    etf_txt = f"{SECTOR_NAME.get(etf[0], etf[0])} fund {_fmt(etf[1])}" if etf and etf[1] is not None else ""
    if avg is not None:
        top = sorted(pm, key=lambda x: x[1] if mv > 0 else -x[1], reverse=True)[:3]
        peers_txt = ", ".join(f"{p} {_fmt(v)}" for p, v in top)
        same = (avg * mv > 0) and abs(avg) >= max(1.5, 0.4 * abs(mv))
        if same:
            lines.append(f"Sector-wide: similar companies moved too ({peers_txt}; average {_fmt(avg)}"
                         + (f"; {etf_txt}" if etf_txt else "") + ").")
        else:
            lines.append(f"Mostly this stock: similar companies averaged {_fmt(avg)} ({peers_txt})"
                         + (f"; {etf_txt}" if etf_txt else "") + ".")
    elif etf_txt:
        lines.append(f"{etf_txt}.")
    for a in ctx.get("analysts")[:2]:
        lines.append(f"Analyst {a['d'][5:]}: {a['x']}.")
    for f in ctx.get("filings")[:2]:
        lines.append(f"Company filing {f['d'][5:]}: {f['x']}.")
    if ctx.get("news"):
        n = ctx["news"][0]
        lines.append(f"Headline: “{n['h'][:160]}” ({n['src']}).")
    for x in members[:2]:
        lines.append(f"Member: {x}.")
    if not (e or ctx.get("analysts") or ctx.get("filings") or ctx.get("news")):
        lines.append("No company news, filings, earnings or analyst changes found in the last few days.")
    return lines, rel


def run(api_h, holdings, candidates, info, member_trades, log, dry=False, force=()):
    """holdings / candidates: ticker lists. info: {t: {"co","sec"}} from the app data. member_trades: {t: [trade]}.
    Returns the saved result. dry=True: no alert, nothing saved. force: tickers to explain regardless of the move."""
    import cloud_store
    state = {}
    try:
        state = json.loads(cloud_store.get(STATE_KEY, log=lambda m: None) or b"{}")
    except Exception:
        state = {}
    want = [t for t in dict.fromkeys(list(holdings) + list(candidates)) if t and t not in PARK]
    if not want:
        return None
    mv = moves(api_h, want + ["SPY"], log)
    spy = mv.get("SPY") or {}
    if spy.get("day") is None:
        log("Why it moved: no S&P price; check failed this run")
        return None
    flagged = []
    for t in want:
        m = mv.get(t)
        if not m or m.get("day") is None:
            continue
        kind = None
        if m and m.get("day") is not None and abs(m["day"] - spy["day"]) >= DAY_REL:
            kind = "day"
        elif m and m.get("d5") is not None and spy.get("d5") is not None and abs(m["d5"] - spy["d5"]) >= FIVE_REL:
            kind = "5d"
        if kind or t in force:
            flagged.append((t, kind or "day"))
    log(f"Why it moved: {len(want)} stocks checked, {len(flagged)} moved enough to explain"
        + (f" ({', '.join(t for t, _ in flagged)})" if flagged else ""))

    def gather(tk):
        t, kind = tk
        y = yahoo(t, log)
        co = (info.get(t) or {}).get("co") or y.get("co")
        y["news"] = news(api_h, t, co, log)
        y["filings"] = filings(t, log)
        return t, kind, y

    with ThreadPoolExecutor(max_workers=4) as ex:
        got = list(ex.map(gather, flagged))
    peers_all = sorted({p.replace(".", "-") for _, _, y in got for p in y.get("peers") or []} |
                       {SECTOR_ETF[y["sector"]] for _, _, y in got if y.get("sector") in SECTOR_ETF})
    pmv = moves(api_h, peers_all, log) if peers_all else {}
    day_key = spy.get("date") or dt.date.today().isoformat()
    try:
        old = json.loads(cloud_store.get(OUT_KEY, log=lambda m: None) or b"{}")
    except Exception:
        old = {}
    items = {k: v for k, v in (old.get("items") or {}).items()
             if (dt.date.today() - dt.date.fromisoformat(v.get("date", "2000-01-01"))).days <= 7}
    seen = state.get("seen") or {}
    new = []
    for t, kind, y in got:
        m = mv.get(t) or {}
        f = "day" if kind == "day" else "d5"
        pm = [(p.replace(".", "-"), (pmv.get(p.replace(".", "-")) or {}).get(f)) for p in y.get("peers") or []]
        etf = SECTOR_ETF.get(y.get("sector"))
        etf_mv = (etf, (pmv.get(etf) or {}).get(f)) if etf else None
        cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        mem = []
        for x in sorted(member_trades.get(t) or [], key=lambda x: x.get("fd") or x.get("td") or "", reverse=True):
            d = x.get("fd") or x.get("td") or ""
            if d >= cutoff:
                mem.append(f"{x.get('n')} {'bought' if x.get('ty') == 'buy' else 'sold'} (traded {x.get('td')}, disclosed {x.get('fd') or '?'})")
        lines, rel = explain(t, m, spy, y, pm, etf_mv, mem, kind)
        key = f"{t}|{day_key}|{kind}"
        rec = {"t": t, "co": (info.get(t) or {}).get("co") or y.get("co"), "date": day_key, "kind": kind,
               "move": m.get("day") if kind == "day" else m.get("d5"), "spy": spy.get("day") if kind == "day" else spy.get("d5"),
               "rel": rel, "held": t in holdings, "cand": t in candidates, "lines": lines, "news": y.get("news") or [],
               "analysts": y.get("analysts") or [], "filings": y.get("filings") or [], "earnings": y.get("earnings"),
               "peers": [[p, v] for p, v in pm if v is not None], "etf": list(etf_mv) if etf_mv else None,
               "industry": y.get("industry"), "members": mem,
               "first": (items.get(t) or {}).get("first") if (items.get(t) or {}).get("date") == day_key else None,
               "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        rec["first"] = rec["first"] or rec["at"]
        items[t] = rec
        if key not in seen and t not in force:
            new.append(rec)
            seen[key] = rec["at"]
    res = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "items": items}
    if dry:
        return res
    cloud_store.put(OUT_KEY, json.dumps(res, default=str), log=lambda m: None)
    state = {"last": res["updated"], "seen": {k: v for k, v in seen.items()
                                              if (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(v)).days < 10}}
    cloud_store.put(STATE_KEY, json.dumps(state), log=lambda m: None)
    if new:
        new.sort(key=lambda r: -abs(r["rel"] or 0))
        head = ", ".join(f"{r['t']} {_fmt(r['move'])}" for r in new[:5])
        title = f"{len(new)} stock{'s' if len(new) > 1 else ''} moved: {head}" if len(new) > 1 else \
            f"{new[0]['t']} {_fmt(new[0]['move'])} {'today' if new[0]['kind'] == 'day' else 'over 5 days'}"
        body = "\n".join(f"{r['t']}: " + " ".join(r["lines"][1:2] or r["lines"][:1]) for r in new[:5])
        cloud_store.notify(title[:120], body, tag="movers", url="/#movers" if len(new) > 1 else f"/#movers/{new[0]['t']}",
                           items=[{"l": f"{r['t']} {_fmt(r['move'])}: " + " ".join(r["lines"][1:2]), "u": f"/#movers/{r['t']}"} for r in new[:20]],
                           log=log)
    log(f"Why it moved: {len(items)} explanation(s) on file, {len(new)} new" + (" (alert sent)" if new else ""))
    return res
