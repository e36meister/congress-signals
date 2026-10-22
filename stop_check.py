"""Is the stop rule actually helping? Checked on this account's real trades.

Owner, Oct 22: for every main-pick position the stop sold, show the price it was bought at, the price the stop
sold it at, the price at the end of its planned holding period (as if it had been held), and how much the stop
saved or cost. Covers every past stop sale in the Alpaca history, then each new one.

While the stop is off, positions that WOULD have hit it are tracked the same way (marked "would have sold"),
so the check keeps measuring both directions.

Automatic switch (owner chose "switch automatically + notify"): once at least MIN_DONE stop sales have reached
the end of their planned hold, the stop is turned off if holding them would clearly have done better (average
per-sale difference above zero with sureness >= SURE), and turned back on if, while off, the stop clearly would
have helped. Pooled evidence has to swing from one side to the other, so it can't flip back and forth.
The weekly backtest check leaves the setting alone while real trades decided it (policy["price_exit_lock"]).
"""
import datetime as dt
import math
import re

import numpy as np
import requests

PREFIX, PARK_PREFIX, SLEEVE_PREFIX = "cs-", "cs-park-", "cs-sm-"
DEFAULT_RULE = "stop15"          # the rule watched while the stop is off
MIN_DONE = 10                    # finished stop sales needed before switching
SURE = 2.0                       # t-stat needed (same bar as the other automatic switches)
BARS = "https://data.alpaca.markets/v2/stocks/bars"


def _pct(rule):
    m = re.search(r"(\d+)$", rule or "")
    return int(m.group(1)) / 100 if m else None


def _hold(cid, default=60):
    m = re.search(r"-h(\d+)$", cid or "")
    return int(m.group(1)) if m else int(default)


def _orders(api, days=1000):
    """Every filled order in the account's history (newest pages first)."""
    seen, out, until = set(), [], None
    after = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for _ in range(60):
        params = {"status": "closed", "limit": 500, "direction": "desc", "after": after}
        if until:
            params["until"] = until
        page = api.get("/v2/orders", **params)
        if not page:
            break
        for o in page:
            if o["id"] not in seen:
                seen.add(o["id"])
                out.append(o)
        if len(page) < 500:
            break
        until = page[-1]["submitted_at"]
    return [o for o in out if o.get("filled_at") and float(o.get("filled_qty") or 0) > 0]


def _bars(api, syms, start, adj):
    """Daily closes {symbol: {YYYY-MM-DD: close}}."""
    out = {}
    end = (dt.datetime.utcnow() - dt.timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
    syms = sorted(set(syms))
    for i in range(0, len(syms), 100):
        for feed in ("sip", "iex"):
            tok, got, ok = None, {}, True
            for _ in range(200):
                p = {"symbols": ",".join(syms[i:i + 100]), "timeframe": "1Day", "start": start, "end": end,
                     "adjustment": adj, "feed": feed, "limit": 10000}
                if tok:
                    p["page_token"] = tok
                r = requests.get(BARS, headers=api.h, params=p, timeout=60)
                if r.status_code != 200:
                    ok = False
                    break
                js = r.json()
                for s, bars in (js.get("bars") or {}).items():
                    for b in bars:
                        got.setdefault(s, {})[b["t"][:10]] = float(b["c"])
                tok = js.get("next_page_token")
                if not tok:
                    break
            if ok:
                out.update(got)
                break
    return out


def _at(series, day):
    """Close on `day`, or the last close before it."""
    if not series:
        return None, None
    ks = [k for k in series if k <= day]
    if not ks:
        return None, None
    k = max(ks)
    return k, series[k]


def _lots(orders):
    """The tool's main-pick positions, one per buy: bought (date, price, shares, planned hold) and, once sold,
    the sale (date, average price). Stocks you also bought yourself are left out (the tool never sells those)."""
    by = {}
    for o in sorted(orders, key=lambda o: o["filled_at"]):
        by.setdefault(o["symbol"], []).append(o)
    lots = []
    for sym, os_ in by.items():
        cids = [(o.get("client_order_id") or "") for o in os_ if o["side"] == "buy"]
        if any(not c.startswith(PREFIX) for c in cids):
            continue                            # you (or the Buy button) bought it too
        cur = None
        for o in os_:
            cid = o.get("client_order_id") or ""
            q, px = float(o["filled_qty"]), float(o.get("filled_avg_price") or 0)
            if o["side"] == "buy":
                if cid.startswith((PARK_PREFIX, SLEEVE_PREFIX)) or cur is not None:
                    continue
                cur = {"sym": sym, "bought": o["filled_at"][:10], "buy_px": px, "qty": q, "hold": _hold(cid),
                       "sold_q": 0.0, "sold_val": 0.0, "sold": None}
            elif cur is not None:
                take = min(q, cur["qty"] - cur["sold_q"])
                cur["sold_q"] += take
                cur["sold_val"] += take * px
                cur["sold"] = cur["sold"] or o["filled_at"][:10]
                if cur["sold_q"] >= cur["qty"] * 0.999:
                    cur["sell_px"] = cur["sold_val"] / cur["sold_q"]
                    lots.append(cur)
                    cur = None
        if cur is not None and not cur["sold_q"]:
            cur["sold"] = None
            lots.append(cur)                    # still held
    return lots


def sale_reasons(log=print):
    """Why the tool sold each stock, as the runs saved it for trade alerts (R2 push/why/, since Oct 8):
    {symbol: [(YYYY-MM-DD, reason), ...]}."""
    import json
    import cloud_store
    out = {}
    for k in cloud_store.list_keys("push/why/", log):
        m = re.search(r"(\d{4})(\d\d)(\d\d)\d{4}\.json$", k)
        raw = cloud_store.get(k, log) if m else None
        try:
            d = json.loads(raw) if raw else {}
        except Exception:
            continue
        for sk, why in d.items():
            sym, _, side = sk.partition("|")
            if side == "sell":
                out.setdefault(sym, []).append((f"{m.group(1)}-{m.group(2)}-{m.group(3)}", str(why)))
    return out


def _stop_reason(reasons, sym, sold):
    """True / False when a run saved why this sale happened (up to 5 days before the fill: a stop that triggers
    on Friday evening fills Monday), None when nothing was saved."""
    near = [w for d, w in reasons.get(sym.replace(".", "-"), [])
            if 0 <= (dt.date.fromisoformat(sold) - dt.date.fromisoformat(d)).days <= 5]
    if not near:
        return None
    return any(w.startswith("fell ") for w in near)


def check(api, rule, log=print, reasons=None):
    """Rows for the Account page plus the summary the automatic switch reads. `rule` is the current
    main-pick price rule ("stop15", "trail15", "none", ...). `reasons`: sale_reasons() (read here when None)."""
    on = bool(_pct(rule))
    if reasons is None:
        try:
            reasons = sale_reasons(log)
        except Exception:
            reasons = {}
    watch = rule if on else DEFAULT_RULE
    pct, trail = _pct(watch), watch.startswith("trail")
    lots = _lots(_orders(api))
    if not lots:
        return {"rule": rule, "rows": [], "summary": _summary([], rule)}
    start = min(l["bought"] for l in lots)
    syms = {l["sym"] for l in lots} | {"SPY"}
    raw, split, tot = (_bars(api, syms, start, a) for a in ("raw", "split", "all"))
    cal = sorted((raw.get("SPY") or {}).keys())          # trading days
    last_day = cal[-1] if cal else dt.date.today().isoformat()
    rows = []
    for l in lots:
        s = l["sym"]
        R, S, T = raw.get(s) or {}, split.get(s) or {}, tot.get(s) or {}
        if not R or not S or not T:
            continue
        i0 = next((i for i, d in enumerate(cal) if d >= l["bought"]), None)
        end = cal[i0 + l["hold"]] if i0 is not None and i0 + l["hold"] < len(cal) else None
        # did the stop sell it (or, with the stop off, would it have)?
        sale_day, sale_px, shadow = None, None, False
        _, sb = _at(S, l["bought"])
        _, rb = _at(R, l["bought"])
        if not (sb and rb):
            continue
        to_raw = rb / sb                                   # split-adjusted -> prices as of the buy
        stop_end = min(x for x in (l["sold"], end, last_day) if x)
        days = sorted(d for d in S if l["bought"] < d <= stop_end)
        if on and l["sold"] and (end is None or l["sold"] < end):
            why = _stop_reason(reasons, s, l["sold"])
            if why is None:          # no saved reason (sales before Oct 8): judge by price. The sale fills at the
                                     # next open, which can gap a few % past the stop line
                hi = max([l["buy_px"]] + [S[d] * to_raw for d in days if d < l["sold"]])
                ref = hi if trail else l["buy_px"]
                why = l["sell_px"] <= ref * (1 - pct) * 1.05
            if why:
                sale_day, sale_px = l["sold"], l["sell_px"]
        elif not on:
            hi = l["buy_px"]
            for d in days:
                c = S[d] * to_raw
                if c <= (hi if trail else l["buy_px"]) * (1 - pct):
                    sale_day, sale_px, shadow = d, c, True
                    break
                hi = max(hi, c)
        if not sale_day:
            continue
        qty = l["sold_q"] if not shadow and l["sold_q"] else l["qty"]
        _, rs = _at(R, sale_day)
        _, ss = _at(S, sale_day)
        _, ts = _at(T, sale_day)
        done = bool(end) and end <= last_day
        e = end if done else last_day
        de, se = _at(S, e)
        _, te = _at(T, e)
        if not (rs and ss and ts and se and te):
            continue
        end_est = end
        if not end and i0 is not None:                     # hold not over yet: the trading day it ends (weekdays)
            left = i0 + l["hold"] - (len(cal) - 1)
            end_est = str(np.busday_offset(np.datetime64(last_day), left, roll="forward"))
        end_px = se * rs / ss                              # the price then, in the shares as they were when sold
        hold_val = te * rs / ts                            # the same, plus dividends paid in between
        rows.append({"t": s.replace(".", "-"), "bought": l["bought"], "buy_px": round(l["buy_px"], 4),
                     "sold": sale_day, "sell_px": round(sale_px, 4), "qty": round(qty, 6),
                     "end": end_est, "end_px": round(end_px, 4), "px_day": de, "done": done,
                     "div": round(max(0.0, hold_val - end_px) * qty, 2),
                     "saved": round((sale_px - hold_val) * qty, 2),
                     "diff": hold_val / sale_px - 1, "would": shadow})
    rows.sort(key=lambda r: r["sold"], reverse=True)
    return {"rule": rule, "watch": watch, "rows": rows, "summary": _summary(rows, rule),
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def _summary(rows, rule):
    done = [r for r in rows if r["done"]]
    n = len(done)
    mean = sum(r["diff"] for r in done) / n if n else None
    sd = math.sqrt(sum((r["diff"] - mean) ** 2 for r in done) / (n - 1)) if n > 1 else None
    t = mean / (sd / math.sqrt(n)) if (n > 1 and sd) else None
    on = bool(_pct(rule))
    verdict = None
    if n >= MIN_DONE and t is not None:
        if on and mean > 0 and t >= SURE:
            verdict = "off"
        elif not on and mean < 0 and t <= -SURE:
            verdict = "on"
    return {"n": len(rows), "n_done": n, "saved_done": round(sum(r["saved"] for r in done), 2),
            "saved_all": round(sum(r["saved"] for r in rows), 2), "mean_diff": mean, "t": t,
            "min_done": MIN_DONE, "sure": SURE, "verdict": verdict, "on": on}


def apply(cfg, sc, E, notify, log=print):
    """Switch the main-pick stop from real results (run by the daily update). Returns the change or None."""
    if not sc:
        return None
    s = sc.get("summary") or {}
    v = s.get("verdict")
    if not v:
        return None
    a = E.load_adaptive(cfg)
    pol = a["policy"]
    cur = pol.get("price_exit", "none")
    if v == "off" and _pct(cur):
        new = "none"
        pol["price_exit_real_prev"] = cur
    elif v == "on" and not _pct(cur):
        new = pol.get("price_exit_real_prev") or DEFAULT_RULE
    else:
        return None
    pol["price_exit"] = new
    pol["price_exit_lock"] = "real trades"
    lab = E.PRICE_EXITS
    msg = (f"Holding the stocks the stop sold would have done better: {s['n_done']} finished stop sales, "
           f"{s['mean_diff'] * 100:+.1f}% each on average if held, ${-s['saved_done']:,.0f} in total (sureness {s['t']:.1f})."
           if v == "off" else
           f"With the stop off, it would have helped: {s['n_done']} stocks that fell to the stop line, "
           f"{-s['mean_diff'] * 100:+.1f}% each on average by selling there, ${s['saved_done']:,.0f} in total (sureness {-s['t']:.1f}).")
    a.setdefault("history", []).append({
        "date": dt.date.today().isoformat(), "change": E.PRICE_EXIT_LABEL + " (real trades)",
        "from": lab.get(cur, cur), "to": lab.get(new, new), "gain_per_year": None, "sureness": abs(s["t"]),
        "by": "real trades", "key": "price_exit", "note": msg})
    E.save_adaptive(cfg, a)
    title = "Stop turned off" if v == "off" else "Stop turned back on"
    log(f"Stop check: {title.lower()} from real trades ({s['n_done']} finished sales, sureness {abs(s['t']):.1f})")
    notify(title, msg)
    return {"from": cur, "to": new, "why": msg}
