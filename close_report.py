"""After each market close: how the paper account is doing, compared with the S&P 500 and with what the
backtest says to expect. Written into the dashboard's data (My portfolio tab) and emailed if Gmail is set up.
Read-only: never trades."""
import os, json, io, datetime as dt
import numpy as np
import requests
import broker
import quick_check as Q
import congress_signals.engine as E

HORIZON_DAYS = {"1 week": 5, "1 month": 20, "3 months": 60, "6 months": 125, "1 year": 250}


def _spy_closes(api, start):
    r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/bars", headers=api.h, timeout=30,
                     params={"timeframe": "1Day", "start": start, "feed": "iex", "limit": 1000, "adjustment": "all"})
    bars = r.json().get("bars") or []
    return {b["t"][:10]: float(b["c"]) for b in bars}


def _closes(api, sym, start, adjustment):
    r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/bars", headers=api.h, timeout=30,
                     params={"timeframe": "1Day", "start": start, "feed": "iex", "limit": 1000, "adjustment": adjustment})
    r.raise_for_status()
    return {b["t"][:10]: float(b["c"]) for b in r.json().get("bars") or []}


def _on_or_before(series, d):
    prior = [k for k in series if k <= d]
    return series[max(prior)] if prior else None


def _park_fills(api):
    """Every filled order the tool placed to park money in an S&P fund (oldest first)."""
    out, until = [], None
    after = (dt.datetime.utcnow() - dt.timedelta(days=500)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for _ in range(20):
        params = {"status": "closed", "limit": 500, "direction": "desc", "after": after}
        if until:
            params["until"] = until
        page = api.get("/v2/orders", **params)
        if not page:
            break
        for o in page:
            if ((o.get("client_order_id") or "").startswith(broker.PARK_PREFIX) and o["symbol"] in broker.PARK_SP500
                    and float(o.get("filled_qty") or 0)):
                out.append((o["filled_at"][:10], o["symbol"], float(o["filled_qty"]) * (1 if o["side"] == "buy" else -1),
                            float(o["filled_avg_price"])))
        if len(page) < 500:
            break
        until = page[-1]["submitted_at"]
    return sorted(out)


def park_adjustment(api, days):
    """Owner, Oct 30 2026: parking was always meant to be a T-bill fund, so the stats count every past S&P parking
    trade as if the same dollars had gone into SGOV on the same days. Returns {day: dollars to add to the account's
    value} (0 before any S&P parking). Same dollars in and out on the same days, so cash is identical; the only
    difference is what the parked shares were worth (and the S&P fund's dividends, which SGOV's price already
    includes). Raises if a price is missing, so a broken adjustment is reported instead of quietly skipped."""
    fills = _park_fills(api)
    if not fills or not days:
        return {d: 0.0 for d in days}
    start = (dt.date.fromisoformat(fills[0][0]) - dt.timedelta(days=7)).isoformat()
    sgov = _closes(api, "SGOV", start, "all")                         # dividends folded into the price
    raw = {sym: _closes(api, sym, start, "raw") for sym in {f[1] for f in fills}}
    divs = []
    try:
        tok = None
        for _ in range(50):            # every page (Nov 3 review: only the first 100 were read)
            page = api.get("/v2/account/activities/DIV", direction="asc", after=start, page_size=100,
                           **({"page_token": tok} if tok else {}))
            for a in page or []:
                if a.get("symbol") in raw:
                    divs.append((str(a.get("date") or a.get("transaction_time") or "")[:10], float(a.get("net_amount") or 0)))
            if not page or len(page) < 100:
                break
            tok = page[-1].get("id")
    except Exception as e:
        E.log(f"Close report: S&P-parking dividends not read ({type(e).__name__}); couldn't be read; the re-count may be off by a few dollars")
    out, i = {}, 0
    qty, units = {}, 0.0
    for d in sorted(days):
        while i < len(fills) and fills[i][0] <= d:
            fd, sym, q, px = fills[i]
            g = _on_or_before(sgov, fd)
            if not g:
                raise ValueError(f"no SGOV price for {fd}")
            qty[sym] = qty.get(sym, 0.0) + q
            units += q * px / g                     # the same dollars bought/sold in SGOV
            i += 1
        held = 0.0
        for sym, q in qty.items():
            if abs(q) > 1e-9:
                p = _on_or_before(raw[sym], d)
                if not p:
                    raise ValueError(f"no {sym} price for {d}")
                held += q * p
        g = _on_or_before(sgov, d)
        if units and not g:
            raise ValueError(f"no SGOV price for {d}")
        out[d] = units * (g or 0) - held - sum(a for dd, a in divs if dd <= d)
    return out


def _cash_flows(api, start):
    """Money you put in or took out after the start (deposits, withdrawals, transfers), {day: dollars}."""
    out = {}
    for kind in ("CSD", "CSW", "JNLC"):
        try:
            for a in api.get(f"/v2/account/activities/{kind}", direction="asc", after=start, page_size=100):
                d = str(a.get("date") or a.get("transaction_time") or "")[:10]
                if d > start:
                    out[d] = out.get(d, 0.0) + float(a.get("net_amount") or 0)
        except Exception as e:
            E.log(f"Close report: {kind} transfers couldn't be read ({type(e).__name__}); the shadow S&P assumes none")
    return out


def _spy_now(api):
    try:
        r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/trades/latest", headers=api.h,
                         params={"feed": "iex"}, timeout=30)
        return float(r.json()["trade"]["p"])
    except Exception:
        return None


def _last_trade(api, sym):
    try:
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/trades/latest", headers=api.h,
                         params={"feed": "iex"}, timeout=30)
        return float(r.json()["trade"]["p"])
    except Exception:
        return None


# Owner, Nov 5 2026: live test, no extra trades. From the Nov 5 close, track an account that keeps 70% in an S&P 100
# fund (the biggest companies) and 30% in this paper account (the picks), rebalanced to 70/30 at the first close of
# each month, next to the paper account alone and the S&P from the same day. Review: next year (date to be set).
# Rebuilt from the start every run (adjusted prices, so the fund's dividends count), so nothing drifts.
MIX_START, MIX_FUND, MIX_W = "2026-11-05", "OEF", 0.7


def sp100_mix(api, pts, flows, spy, today):
    days = [(d, e) for d, e in pts if d >= MIX_START]
    if not days or days[0][0] != MIX_START:
        raise ValueError(f"no account value on file for the start day {MIX_START}")
    fund = _closes(api, MIX_FUND, (dt.date.fromisoformat(MIX_START) - dt.timedelta(days=7)).isoformat(), "all")
    if today not in fund:                    # today's daily bar arrives later in the evening
        p = _last_trade(api, MIX_FUND)
        if p:
            fund[today] = p
    oef_part, acct_part = MIX_W, 1 - MIX_W
    acct_cum = 1.0
    f0, s0 = _on_or_before(fund, MIX_START), _on_or_before(spy, MIX_START)
    if not (f0 and s0):
        raise ValueError(f"no {MIX_FUND} or SPY price for {MIX_START}")
    out = {"dates": [MIX_START], "mix": [0.0], "account": [0.0], "spy": [0.0]}
    (prev_d, prev_e), prev_f = days[0], f0
    for d, e in days[1:]:
        f, s = _on_or_before(fund, d), _on_or_before(spy, d)
        if not (f and s and prev_e):
            raise ValueError(f"missing price for {d}")
        ra = (e - flows.get(d, 0.0)) / prev_e - 1          # the account's own return that day, new money left out
        oef_part *= f / prev_f
        acct_part *= 1 + ra
        acct_cum *= 1 + ra
        nav = oef_part + acct_part
        if d[:7] != prev_d[:7]:                             # first close of a new month: back to 70/30
            oef_part, acct_part = MIX_W * nav, (1 - MIX_W) * nav
        out["dates"].append(d)
        out["mix"].append(round((nav - 1) * 100, 3))
        out["account"].append(round((acct_cum - 1) * 100, 3))
        out["spy"].append(round((s / s0 - 1) * 100, 3))
        prev_d, prev_e, prev_f = d, e, f
    return {"start": MIX_START, "fund": MIX_FUND, "fund_pct": round(MIX_W * 100), "review": "next year (date to be set)",
            "series": out, "mix": out["mix"][-1], "account": out["account"][-1], "spy": out["spy"][-1],
            "trading_days": len(out["dates"]) - 1}


def _et(ts):
    """An Alpaca UTC time as New York (date, minutes after midnight)."""
    t = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        t = t - dt.timedelta(hours=5)
    return t.date().isoformat(), t.hour * 60 + t.minute


def _spy_at(bars, ts):
    """SPY at the moment of a fill: the open if it filled during market hours, the last close before it if it filled
    before the open (pre-market), that day's close if after hours."""
    d, m = _et(ts)
    days = sorted(bars)
    if d in bars and 570 <= m < 960:
        return bars[d]["o"]
    if d in bars and m >= 960:
        return bars[d]["c"]
    prior = [x for x in days if x < d]
    return bars[prior[-1]]["c"] if prior else None


def version_results(api, today, spy_now):
    """Owner, Nov 5: the tool's live results per strategy version. Each holding the tool started (buys until the
    share count is back to zero) belongs to the version in force at its first fill; its result is everything sold
    plus what's still held, against the money put in, next to SPY over the same days (fills priced at the open,
    pre-market at the prior close). Money-weighted. Dividends aren't counted (small)."""
    import cloud_store, strategy_versions as SV
    vs = SV.load(cloud_store)
    if not vs:
        return None
    orders = broker._filled_orders(api)
    held = {p["symbol"]: float(p.get("current_price") or 0) for p in api.get("/v2/positions") or []}
    start = min(o["filled_at"] for o in orders)[:10] if orders else today
    r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/bars", headers=api.h, timeout=30,
                     params={"timeframe": "1Day", "start": start, "feed": "iex", "limit": 1000, "adjustment": "all"})
    bars = {b["t"][:10]: {"o": float(b["o"]), "c": float(b["c"])} for b in r.json().get("bars") or []}
    by = {}
    for o in sorted(orders, key=lambda o: o["filled_at"]):
        by.setdefault(o["symbol"], []).append(o)
    segs = []
    for sym, os_ in by.items():
        qty, seg = 0.0, None
        for o in os_:
            q, px = float(o["filled_qty"]), float(o["filled_avg_price"])
            if o["side"] == "buy":
                if qty <= 1e-6:
                    seg = {"sym": sym, "first": o, "cost": 0.0, "proceeds": 0.0, "spy_cost": 0.0, "open": True}
                    segs.append(seg)
                qty += q
                seg["cost"] += q * px
                s0 = _spy_at(bars, o["filled_at"])
                seg["spy_cost"] += q * px / s0 if s0 else 0.0          # SPY units the same money would buy
            elif seg is not None:
                qty -= q
                seg["proceeds"] += q * px
                s1 = _spy_at(bars, o["filled_at"])
                seg["spy_sold"] = seg.get("spy_sold", 0.0) + (seg["spy_cost"] * min(1.0, q / max(qty + q, 1e-9))) * (s1 or 0)
                seg["spy_cost"] *= max(0.0, 1 - min(1.0, q / max(qty + q, 1e-9)))
                if qty <= 1e-6:
                    seg["open"], qty = False, 0.0
        if seg is not None and seg["open"]:
            seg["proceeds"] += qty * held.get(sym, 0.0)
            seg["spy_sold"] = seg.get("spy_sold", 0.0) + seg["spy_cost"] * (spy_now or 0)
            seg["spy_cost"] = 0.0
    out = []
    for v in [None] + [x["v"] for x in vs]:
        rows = [s for s in segs
                if (s["first"].get("client_order_id") or "").startswith(broker.PREFIX)
                and not (s["first"].get("client_order_id") or "").startswith(broker.PARK_PREFIX)
                and SV.version_at(vs, s["first"]["filled_at"]) == v]
        meta = next((x for x in vs if x["v"] == v), None)
        cost = sum(s["cost"] for s in rows)
        if not rows and v is None:
            continue
        ret = (sum(s["proceeds"] for s in rows) / cost - 1) if cost else None
        spy = (sum(s.get("spy_sold", 0.0) for s in rows) / cost - 1) if cost else None
        out.append({"v": v, "label": f"v{v}" if v else "Before version stamps",
                    "start": meta["start"] if meta else None, "by": meta["by"] if meta else None,
                    "what": meta["what"] if meta else ["buys before Nov 5 2026"],
                    "trades": len(rows), "open": sum(1 for s in rows if s["open"]),
                    "ret": None if ret is None else round(ret * 100, 2), "spy": None if spy is None else round(spy * 100, 2),
                    "diff": None if ret is None or spy is None else round((ret - spy) * 100, 2)})
    return out


def _expected(horizons, group, days):
    """What the backtest's average trade in this group did vs the S&P after about `days` trading days."""
    rows = [h for h in horizons or [] if h.get("Group") == group and h.get("Holding period") in HORIZON_DAYS]
    if not rows or not days:
        return None
    best = min(rows, key=lambda h: abs(HORIZON_DAYS[h["Holding period"]] - days))
    scale = min(1.0, days / HORIZON_DAYS[best["Holding period"]])      # early days: a share of the full-period average
    f = lambda v: None if v is None else float(v) * scale
    return {"avg": f(best.get("Avg vs SPY")), "low": f(best.get("Range low")), "high": f(best.get("Range high")),
            "period": best["Holding period"]}


def main():
    s = broker.settings(60)
    if not (s["key"] and s["secret"]):
        E.log("Close report: no Alpaca keys; skipping")
        return
    api = broker.Alpaca(s)
    clock = api.get("/v2/clock")
    if clock.get("is_open"):
        E.log("Close report: market still open; skipping")
        return
    import cloud_store
    # Nov 5 (owner): Google Drive is retired; the app's Cloudflare copy is the only dashboard_data.json. A read that
    # fails raises (the job's crash check reports it) instead of building a report on an empty file.
    try:
        data = cloud_store.read_dashboard()
    except cloud_store.ReadError as e:
        E.log(f"Close report: failed: couldn't read the dashboard from Cloudflare ({e}); the next run tries again")
        raise
    today = dt.date.today().isoformat()
    if os.environ.get("CLOSE_EMAIL_ONLY") == "1":        # re-send the last close email; nothing else changes
        rep = data.get("close_report")
        if not rep:
            E.log("Close report: no close report on file to re-send")
            return
        email(rep, (data.get("portfolio") or {}).get("mode", "paper"), data.get("portfolio") or {}, data)
        return
    forced = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if (data.get("close_report") or {}).get("date") == today and not forced:
        E.log("Close report: already done today")
        return

    # account value by day (Alpaca's own history) against the S&P 500 over the same days
    hist = api.get("/v2/account/portfolio/history", period="3M", timeframe="1D")
    days = [dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat() for t in hist.get("timestamp") or []]
    eq = [float(x) if x is not None else None for x in hist.get("equity") or []]
    pts = [(d, e) for d, e in zip(days, eq) if e and d < today]
    # Alpaca's daily bars run a day or two behind the close; the equity recorded at each close is exact,
    # so it replaces Alpaca's value for every day we have it
    rec = {h["date"]: float(h["equity"]) for h in data.get("close_history", []) if h.get("equity")}
    if rec:
        first_rec = min(rec)
        pts = [(d, e) for d, e in pts if d < first_rec][:1] + sorted((d, e) for d, e in rec.items() if d < today)
    acct_now = float(api.get("/v2/account").get("equity") or 0)
    if acct_now:
        pts.append((today, acct_now))        # the daily history adds today's bar only later in the evening
    start_i = next((i for i in range(1, len(pts)) if pts[i][1] != pts[0][1]), 0)   # first day money actually moved
    pts = pts[max(0, start_i - 1):]
    spy = _spy_closes(api, (dt.date.fromisoformat(pts[0][0]) - dt.timedelta(days=7)).isoformat()) if pts else {}
    spy_last = _spy_now(api)
    if spy_last:
        spy[today] = spy_last
    series = {"dates": [], "account": [], "spy": []}
    # past S&P parking re-counted as SGOV (see park_adjustment); if that breaks, the raw numbers are used and
    # Data health says so
    adj, adj_err = {}, None
    try:
        adj = park_adjustment(api, [d for d, _ in pts])
    except Exception as e:
        adj_err = f"{type(e).__name__}: {str(e)[:120]}"
        E.log(f"Close report: re-counting the old S&P parking as SGOV FAILED ({adj_err}); "
              "today's numbers include the S&P parking as it actually happened")
    pts = [(d, e + adj.get(d, 0.0)) for d, e in pts]
    shadow_now = None
    mix, mix_err = None, None
    if pts:
        e0 = pts[0][1]
        before = [k for k in spy if k <= pts[0][0]]           # the last S&P close on or before the start (weekends)
        s0 = spy[max(before)] if before else next((spy[d] for d, _ in pts if d in spy), None)
        # the account sat in cash until its first trade, so the S&P is measured from that moment, not from
        # the close before the start (otherwise its moves while the account held cash count against it)
        t0 = None
        try:
            t0 = min(o["filled_at"] for o in broker._bot_buys(api, any_buyer=True, history=True).values())
            if t0[:10] > pts[0][0]:
                t0d = dt.datetime.strptime(t0[:19], "%Y-%m-%dT%H:%M:%S")      # Alpaca times are UTC
                r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/bars", headers=api.h, timeout=30,
                                 params={"timeframe": "1Min", "feed": "iex", "adjustment": "all", "limit": 30,
                                         "start": (t0d - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "end": t0d.strftime("%Y-%m-%dT%H:%M:%SZ")})
                bars = r.json().get("bars") or []
                if bars:
                    s0 = float(bars[-1]["c"])
                else:
                    t0 = None
            else:
                t0 = None
        except Exception:
            t0 = None
        # Shadow S&P account (owner, Oct 30 2026): the same money on the same days, all of it in SPY with dividends
        # reinvested (Alpaca's "all"-adjusted prices). Every deposit or withdrawal goes into / comes out of the
        # shadow at that day's SPY price. Both sides are measured against the money put in.
        # A transfer only counts as new money if it's after the first trade AND the account's value actually jumped
        # by about that much that day (Oct 30: Alpaca logs the paper account's opening $100,000 as a transfer on
        # Sep 28, after the account already showed it, which made the money put in look like $200,000).
        flows, eq_by = {}, dict(pts)
        first_trade = t0[:10] if t0 else pts[0][0]
        for fd, amt in _cash_flows(api, pts[0][0]).items():
            prev = [e for d, e in pts if d < fd]
            jump = (eq_by.get(fd, prev[-1] if prev else 0) - prev[-1]) if prev else 0
            if fd <= first_trade:
                E.log(f"Close report: transfer of ${amt:,.0f} on {fd} not counted (the account's opening money)")
            elif abs(jump - amt) > 0.5 * abs(amt):
                E.log(f"Close report: transfer of ${amt:,.0f} on {fd} not counted: the account's value moved ${jump:,.0f} "
                      "that day, so it isn't new money (e.g. the paper account's opening balance)")
            else:
                flows[fd] = amt
        units = e0 / s0 if s0 else 0.0
        put_in, last_s = e0, s0
        for d, e in pts:
            last_s = s0 if (t0 and d < t0[:10]) else spy.get(d, last_s)
            if flows.get(d) and last_s:
                units += flows[d] / last_s
                put_in += flows[d]
            shadow = units * last_s if last_s else None
            series["dates"].append(d)
            series["account"].append(round((e / put_in - 1) * 100, 3))
            series["spy"].append(round((shadow / put_in - 1) * 100, 3) if shadow else None)
        shadow_now = units * last_s if last_s else None
        if today >= MIX_START:
            try:
                mix = sp100_mix(api, pts, flows, spy, today)
            except Exception as e:
                mix_err = f"{type(e).__name__}: {str(e)[:120]}"
                E.log(f"Close report: the S&P 100 + picks live test FAILED ({mix_err}); the next close tries again")

    # each strategy's open positions: return since bought vs the S&P over the same days, vs the backtest
    snap = broker.sync({"HOLD_DAYS": 60, "_hold_policy": (data.get("adaptive") or {}).get("policy")},
                       None, None, {}, E.log, trade=False)
    spy_all = _spy_closes(api, min([p["bought"] for p in snap["positions"] if p.get("bought")] or [today]))
    spy_now = spy_last or (spy_all[max(spy_all)] if spy_all else None)
    horizons = (data.get("backtest") or {}).get("horizons")
    groups = {"main": ("Main picks", "Top-scored purchases"), "small": ("Small-company portfolio", "Small companies"),
              "yours": ("Your own buys", "All purchases")}
    sleeves = []
    for key, (label, hgroup) in groups.items():
        ps = [p for p in snap["positions"] if (p.get("sleeve") == "small" if key == "small"
                                                else (p.get("bot") and p.get("sleeve") not in ("small", "park")) if key == "main"
                                                else not p.get("bot"))]
        rows = []
        for p in ps:
            b = p.get("bought")
            s_then = spy_all.get(b) if b else None
            if s_then is None and b:
                prior = [d for d in spy_all if d <= b]
                s_then = spy_all[max(prior)] if prior else None
            spy_ret = (spy_now / s_then - 1) if (spy_now and s_then) else None
            cost = float(p.get("mv") or 0) / (1 + float(p["plpc"])) if float(p["plpc"]) > -1 else 0.0
            rows.append((p["plpc"], spy_ret, p.get("held") or 0, max(cost, 0.0)))
        if not rows:
            continue
        # weighted by the money put into each position (Nov 3 review: a simple average let a $50 position count as
        # much as a $5,000 one); dates come from each position's current holding (broker._segments)
        wts = np.array([x[3] for x in rows]) if sum(x[3] for x in rows) > 0 else np.ones(len(rows))
        r = float(np.average([x[0] for x in rows], weights=wts))
        spi = [(x[1], w_) for x, w_ in zip(rows, wts) if x[1] is not None]
        spr = float(np.average([a for a, _ in spi], weights=[b for _, b in spi])) if spi and sum(b for _, b in spi) > 0 else None
        held = int(round(np.average([x[2] for x in rows], weights=wts)))
        sleeves.append({"name": label, "positions": len(rows), "avg_days": held, "ret": r, "spy": spr,
                        "diff": (r - spr) if spr is not None else None, "expected": _expected(horizons, hgroup, held)})

    versions, ver_err = None, None
    try:
        versions = version_results(api, today, spy_now)
    except Exception as e:
        ver_err = f"{type(e).__name__}: {str(e)[:120]}"
        E.log(f"Close report: results per strategy version FAILED ({ver_err}); the next close tries again")
    acct = series["account"][-1] if series["account"] else 0.0
    spyc = series["spy"][-1] if series["spy"] else None
    report = {"date": today, "equity": snap["equity"], "day_pl": snap.get("day_pl"), "since_start": acct,
              "spy_since_start": spyc, "start": series["dates"][0] if series["dates"] else today,
              "series": series, "sleeves": sleeves,
              # account value as counted (real value + the SGOV re-count of old S&P parking), the shadow S&P
              # account's value, and the money put in (what both percentages are measured against)
              "park_adjust": round(adj.get(today, 0.0), 2) if adj else 0.0, "park_adjust_error": adj_err,
              "counted_equity": pts[-1][1] if pts else None, "shadow_spy": shadow_now,
              "vs_spy_dollars": (pts[-1][1] - shadow_now) if (pts and shadow_now) else None,
              "put_in": (pts[-1][1] / (1 + series["account"][-1] / 100)) if pts and series["account"] else None,
              "sp100_mix": mix if mix is not None else (data.get("close_report") or {}).get("sp100_mix"),
              "sp100_mix_error": mix_err,
              "versions": versions if versions is not None else (data.get("close_report") or {}).get("versions"),
              "versions_error": ver_err}
    data["close_report"] = report
    data["portfolio"] = {**snap, "limits": (data.get("portfolio") or {}).get("limits") or snap["limits"]}
    hist_list = [h for h in data.get("close_history", []) if h.get("date") != today]
    hist_list.append({k: report[k] for k in ("date", "equity", "day_pl", "since_start", "spy_since_start")})
    # keep past days on the same S&P starting point as today's series
    by_day = dict(zip(series["dates"], series["spy"]))
    acct_day = dict(zip(series["dates"], series["account"]))     # same SGOV re-count and money-put-in basis
    for h in hist_list:
        if by_day.get(h.get("date")) is not None:
            h["spy_since_start"] = by_day[h["date"]]
        if acct_day.get(h.get("date")) is not None:
            h["since_start"] = acct_day[h["date"]]
    data["close_history"] = hist_list[-2000:]          # keep the start: "since start" must not roll forward
    # the steps above take a minute or more; if the quick check saved the file meanwhile, keep its other parts and
    # change only the close report's own three
    mine = {k: data[k] for k in ("close_report", "close_history", "portfolio")}
    try:
        fresh = cloud_store.read_dashboard()
        fresh.update(mine)
        data = fresh
    except cloud_store.ReadError as e:
        E.log(f"Close report: couldn't re-read the dashboard before saving ({e}); saving the copy read at the start")
    if cloud_store.save_dashboard(data, E.log):
        E.log("Close report: saved to the dashboard")
    pushed = False
    try:      # phone alert: same headline as the email
        subject = build_email(report, snap["mode"], snap, data)[0]
        a, s = report.get("since_start"), report.get("spy_since_start")
        body = f"Account ${(report.get('equity') or 0):,.0f}" + (f" · since start {a:+.2f}% vs S&P {s:+.2f}%" if a is not None and s is not None else "")
        pushed = cloud_store.has_phones() and cloud_store.notify(subject, body, "close", "/#port", E.log)
    except Exception as e:
        E.log(f"Close report: phone alert skipped ({type(e).__name__})")
    if pushed:
        E.log("Close report: sent to your phone")
    email(report, snap["mode"], snap, data)        # owner, Oct 30: emailed every day too, phone alerts or not


LUCK_TE, LUCK_Z = 0.15, 1.96          # same 95% luck range as the dashboard chart


def _fmt_money(v, sign=False):
    if v is None:
        return "–"
    s = f"${abs(v):,.0f}"
    return (("+" if v >= 0 else "-") + s) if sign else (("-" if v < 0 else "") + s)


def build_email(rep, mode, snap, data, chart=False):
    """The close email: three number tiles, the luck range, today's biggest movers, each strategy vs the S&P,
    and what happens next. Inline styles only (email apps ignore style sheets)."""
    pct = lambda v, d=2: "–" if v is None else f"{v:+.{d}f}%"
    green, red, grey, ink, line = "#0B7A4B", "#B42318", "#6B778A", "#152033", "#DCE1E8"
    col = lambda v: grey if v is None or v == 0 else (green if v > 0 else red)
    eq, day = rep.get("equity") or 0, rep.get("day_pl")
    day_pct = (day / (eq - day) * 100) if (day is not None and eq and eq != day) else None
    acct, spy = rep.get("since_start"), rep.get("spy_since_start")
    gap = (acct - spy) if (acct is not None and spy is not None) else None
    start = dt.date.fromisoformat(rep["start"]) if rep.get("start") else dt.date.today()
    start_txt = f"{start:%b} {start.day}"

    def tile(big, label, sub, color):
        return (f"<td width='33%' style='padding:12px 10px;border:1px solid {line};border-radius:8px;vertical-align:top'>"
                f"<div style='font:600 22px Menlo,Consolas,monospace;color:{color}'>{big}</div>"
                f"<div style='font-size:12px;color:{grey};margin-top:2px'>{label}</div>"
                f"<div style='font-size:12px;color:{ink}'>{sub}</div></td>")
    tiles = ("<table width='100%' cellspacing='6' cellpadding='0' style='border-collapse:separate'><tr>"
             + tile(_fmt_money(eq), "account value", f"{'Paper' if mode == 'paper' else 'Real money'}", ink)
             + tile(_fmt_money(day, True), "today", pct(day_pct), col(day))
             + tile(pct(gap), "vs the S&amp;P 500", f"since {start_txt} (S&amp;P {pct(spy)}"
                    + (f", {_fmt_money(rep['vs_spy_dollars'], True)}" if rep.get("vs_spy_dollars") is not None else "") + ")", col(gap))
             + "</tr></table>")

    # where the account sits in the 95% luck range around the S&P
    yrs = max((dt.date.fromisoformat(rep["date"]) - start).days, 0) / 365.25
    w = LUCK_Z * LUCK_TE * yrs ** 0.5 * 100
    if gap is None or w <= 0:
        luck = "Not enough history yet to place the account in the luck range."
    elif abs(gap) <= w:
        room_up, room_dn = w - gap, w + gap
        edge = f"{room_up:.1f}% below the top edge" if room_up <= room_dn else f"{room_dn:.1f}% above the bottom edge"
        luck = (f"<b>Inside the luck range, {edge}.</b> The range is ±{w:.1f}% around the S&amp;P today and widens "
                f"over time.")
    else:
        luck = (f"<b>{'Above' if gap > 0 else 'Below'} the luck range</b> by {abs(gap) - w:.1f}% "
                f"(the range is ±{w:.1f}% around the S&amp;P today).")

    # today's biggest movers among open positions
    pos = [p for p in (snap.get("positions") or []) if p.get("day") is not None and abs(float(p["day"])) >= 0.0005]
    mv = []
    for p in pos:
        d = float(p["day"])
        mv.append((p["t"], d * 100, (p.get("mv") or 0) * d / (1 + d) if d > -1 else 0))
    mv.sort(key=lambda x: x[1], reverse=True)
    up, dn = [m for m in mv if m[1] > 0][:3], [m for m in mv if m[1] < 0][-3:][::-1]

    def mrow(m, arrow, c):
        return (f"<tr><td style='padding:3px 8px 3px 0;color:{c}'>{arrow}</td><td style='padding:3px 12px 3px 0;font-weight:600'>{m[0]}</td>"
                f"<td align='right' style='padding:3px 12px 3px 0;color:{c};font-family:Menlo,Consolas,monospace'>{m[1]:+.1f}%</td>"
                f"<td align='right' style='color:{c};font-family:Menlo,Consolas,monospace'>{_fmt_money(m[2], True)}</td></tr>")
    movers = "".join(mrow(m, "▲", green) for m in up) + "".join(mrow(m, "▼", red) for m in dn)
    movers = movers or f"<tr><td style='color:{grey}'>No price changes today.</td></tr>"

    # each strategy vs the S&P since bought
    srows = ""
    for s_ in rep.get("sleeves") or []:
        diff = None if s_.get("diff") is None else s_["diff"] * 100
        e = (s_.get("expected") or {}).get("avg")
        srows += (f"<tr><td style='padding:4px 12px 4px 0'>{s_['name']} <span style='color:{grey}'>({s_['positions']})</span></td>"
                  f"<td align='right' style='padding:4px 12px 4px 0;font-family:Menlo,Consolas,monospace'>{pct(s_['ret'] * 100, 1)}</td>"
                  f"<td align='right' style='padding:4px 12px 4px 0;font-family:Menlo,Consolas,monospace;color:{col(diff)}'>{pct(diff, 1)}</td>"
                  f"<td align='right' style='font-family:Menlo,Consolas,monospace;color:{grey}'>{pct(None if e is None else e * 100, 1)}</td></tr>")
    strat = (f"<table cellspacing='0' cellpadding='0' style='font-size:14px'><tr style='color:{grey};font-size:12px'>"
             f"<td style='padding-bottom:4px'>Since bought</td><td align='right' style='padding:0 12px 4px 0'>Return</td>"
             f"<td align='right' style='padding:0 12px 4px 0'>vs S&amp;P</td><td align='right' style='padding-bottom:4px'>Backtest</td></tr>{srows}</table>") if srows else ""

    # what happens next
    soon = sorted([(p["t"], int(p["hold"]) - int(p.get("held") or 0)) for p in snap.get("positions") or []
                   if p.get("bot") and p.get("hold") is not None and int(p["hold"]) - int(p.get("held") or 0) <= 5],
                  key=lambda x: x[1])
    today_s = rep["date"]
    bought = sorted({o["t"] for o in snap.get("orders") or [] if o.get("side") == "buy" and str(o.get("at", ""))[:10] == today_s
                     and o.get("status") in ("filled", "partially_filled", "accepted", "new")})
    w_ = data.get("watchlist") or {}
    n_buy = len([r for r in w_.get("buys") or [] if r.get("action") == "BUY"])
    n_worth = len(data.get("worth") or [])
    nxt = [f"<b>Tool sells in the next 5 trading days:</b> " + (", ".join(f"{t} ({'today' if d <= 0 else f'in {d} day' + ('s' if d > 1 else '')})" for t, d in soon) or "none"),
           f"<b>Bought today:</b> " + (", ".join(bought) or "none"),
           f"<b>On the dashboard:</b> {n_buy} buy candidate{'s' if n_buy != 1 else ''}" + (f", {n_worth} worth a look" if n_worth else "")]

    # live test (owner, Nov 5): 70% S&P 100 fund + 30% this account, vs the account alone and the S&P
    mx = rep.get("sp100_mix")
    live_test = ""
    if mx and mx.get("trading_days"):
        ms = dt.date.fromisoformat(mx["start"])
        live_test = (f"<table cellspacing='0' cellpadding='0' style='font-size:14px'>"
                     + "".join(f"<tr><td style='padding:3px 12px 3px 0'>{lab}</td><td align='right' style='font-family:Menlo,Consolas,monospace;color:{col(v)}'>{pct(v)}</td></tr>"
                               for lab, v in ((f"{mx['fund_pct']}% S&amp;P 100 fund + {100 - mx['fund_pct']}% picks", mx["mix"]),
                                              ("Paper account alone", mx["account"]), ("S&amp;P 500", mx["spy"])))
                     + f"</table><div style='font-size:12px;color:{grey};margin-top:4px'>Since {ms:%b} {ms.day}, {ms.year} · "
                     f"{mx['trading_days']} trading day{'s' if mx['trading_days'] != 1 else ''} · tracked, not traded · review {mx['review']}</div>")
    elif rep.get("sp100_mix_error"):
        live_test = f"<div style='color:{red}'>The live test couldn't be updated today ({rep['sp100_mix_error']}).</div>"

    vrows = ""
    for v in rep.get("versions") or []:
        vrows += (f"<tr><td style='padding:3px 12px 3px 0'>{v['label']}</td>"
                  f"<td align='right' style='padding:3px 12px 3px 0;color:{grey}'>{v['trades']}</td>"
                  f"<td align='right' style='padding:3px 12px 3px 0;font-family:Menlo,Consolas,monospace'>{pct(v['ret'], 1)}</td>"
                  f"<td align='right' style='font-family:Menlo,Consolas,monospace;color:{col(v['diff'])}'>{pct(v['diff'], 1)}</td></tr>")
    ver_tbl = (f"<table cellspacing='0' cellpadding='0' style='font-size:14px'><tr style='color:{grey};font-size:12px'>"
               f"<td style='padding-bottom:4px'>Version</td><td align='right' style='padding:0 12px 4px 0'>Buys</td>"
               f"<td align='right' style='padding:0 12px 4px 0'>Return</td><td align='right' style='padding-bottom:4px'>vs S&amp;P</td></tr>{vrows}</table>") if vrows else ""

    from congress_signals import mail
    chart_link, has_chart = mail.app_url("/#port"), chart
    h = lambda t: f"<div style='font:600 12px Arial;letter-spacing:.8px;color:{grey};text-transform:uppercase;margin:22px 0 6px'>{t}</div>"
    d0 = dt.date.fromisoformat(rep["date"])
    html = (f"<div style='font-family:Arial,Helvetica,sans-serif;color:{ink};max-width:560px;font-size:14px;line-height:1.45'>"
            f"<div style='font:600 12px Arial;letter-spacing:1px;color:{grey};text-transform:uppercase'>Capitol Capital · close, {d0:%b} {d0.day}</div>"
            f"{tiles}"
            + (f"<a href='{chart_link}' style='display:block;margin:14px 0 2px;text-decoration:none'>"
               f"<img src='cid:chart' width='560' alt='Your account vs the S&amp;P 500 since {start_txt}' "
               f"style='display:block;width:100%;max-width:560px;height:auto;border:1px solid {line};border-radius:8px'></a>"
               f"<div style='font-size:12px;color:{grey};margin:0 2px'>Tap the chart to open it in the app.</div>" if has_chart else "")
            + f"<p style='margin:10px 2px 0'>{luck}</p>"
            f"{h('Today’s biggest moves')}<table cellspacing='0' cellpadding='0' style='font-size:14px'>{movers}</table>"
            f"{h('Each strategy') + strat if strat else ''}"
            f"{h('Live test: S&amp;P 100 fund + picks') + live_test if live_test else ''}"
            f"{h('Tool’s buys by strategy version') + ver_tbl if ver_tbl else ''}"
            f"{h('Coming up')}" + "".join(f"<div style='margin:3px 0'>{x}</div>" for x in nxt) +
            f"<p style='font-size:12px;color:{grey};margin:16px 2px 0'>“Since {start_txt}” is the whole account, including "
            f"your own buys and Buy/Sell-button trades, not only the tool’s. The tool’s buys usually fill in the evening or "
            f"at the next open after a filing is seen; the backtest buys at the next open, so live entry prices and hold "
            f"lengths can differ from it by a day.</p>" +
            f"<p style='margin-top:22px'><a href='{chart_link}' style='display:inline-block;background:#1D4F91;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600'>Open Capitol Capital</a></p></div>")
    subject = (f"Close: {_fmt_money(day, True)} today · {pct(gap)} vs S&P" if day is not None else f"Close: {pct(gap)} vs S&P")
    return subject, html


def email(rep, mode, snap=None, data=None):
    from congress_signals import mail
    se, png = rep.get("series") or {}, None
    try:
        png = mail.chart_png(se.get("dates") or [], se.get("account") or [], se.get("spy") or [])
    except Exception as e:
        E.log(f"Close report: email chart failed ({type(e).__name__}: {str(e)[:100]}); sent without it")
    subject, html = build_email(rep, mode, snap or {}, data or {}, chart=bool(png))
    mail.send(subject, html, E.log, {"chart": png} if png else None, ok_msg="Close report: emailed")


def family_close():
    """Each family account's own short close report (their phones, their email, their Account page)."""
    if os.environ.get("CLOSE_EMAIL_ONLY") == "1":
        return
    try:
        import cloud_store
        from congress_signals import accounts, mail
        accounts.close_all(E.log,
                           send_email=lambda to, subj, html: mail.send(subj, html, lambda m: None, to=to),
                           notify=lambda acct, title, body: cloud_store.notify(title, body, "close", "/#port", lambda m: None, account=acct))
    except Exception as e:
        E.log(f"Family accounts: close reports skipped ({type(e).__name__})")


if __name__ == "__main__":
    try:
        main()
    finally:
        family_close()
