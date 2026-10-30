"""Your own buys follow the tool's sell rules (owner, Oct 30).

Positions you open yourself (Buy button or straight in Alpaca) from MANUAL_FROM on are checked every daily run against
the same reasons the tool sells its own picks for:
  - "member_sold": a member whose purchase disclosure matches your buy has disclosed a sale
  - "avoid":       a new sale filing makes the stock an avoid (scores in the avoid range of all sale signals)
  - "hold":        past the holding period (counted from your buy) and behind the S&P since you bought, or a year old
  - "score":       it was a pick when you bought, and the member purchase behind it no longer clears the buy bar
When one fires you get a phone alert and an email. Unless you tap Keep in the app, the next morning's run sells the
position at the open. Keep holds it until a different reason comes up. Positions you opened before MANUAL_FROM keep
the old behavior (the tool never sells them); all your trades show a small "manual" tag in the app.

State lives in private storage (Cloudflare R2): manual/state.json (written here), manual/keep.json (the app's Keep
button). Nothing about the account goes to the run log beyond counts, because the repo is public."""
import json, datetime as dt
import numpy as np

import cloud_store

MANUAL_FROM = "2026-10-30T19:45:00Z"          # owner, Oct 30 3:48 PM ET: only new buys from now
STATE_KEY, KEEP_KEY = "manual/state.json", "manual/keep.json"
PREFIX, PARK_PREFIX = "cs-", "cs-park-"
LABEL = {"member_sold": "a member whose purchase matches yours sold",
         "avoid": "it became an avoid",
         "hold": "its hold is over and it's behind the S&P",
         "score": "the purchase behind it no longer scores as a pick"}


def _load(key):
    b = cloud_store.get(key)
    try:
        return json.loads(b) if b else {}
    except Exception:
        return {}


def all_orders(api, cache=None):
    """Every filled order in the account's history (pages of 500, newest first)."""
    if cache is not None and "_orders" in cache:
        return cache["_orders"]
    out, seen, until = [], set(), None
    after = (dt.datetime.utcnow() - dt.timedelta(days=5 * 365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for _ in range(60):
        p = {"status": "closed", "limit": 500, "direction": "desc", "after": after}
        if until:
            p["until"] = until
        page = api.get("/v2/orders", **p)
        if not page:
            break
        for o in page:
            if o["id"] not in seen:
                seen.add(o["id"])
                out.append(o)
        if len(page) < 500:
            break
        until = page[-1]["submitted_at"]
    out = [o for o in out if o.get("filled_at") and float(o.get("filled_qty") or 0) > 0]
    if cache is not None:
        cache["_orders"] = out
    return out


def holdings(orders):
    """Per symbol: the date the current holding began, and whether you placed a buy in it on/after MANUAL_FROM.
    `orders`: filled orders (any order, oldest first not required)."""
    by = {}
    for o in orders:
        if not o.get("filled_at") or not float(o.get("filled_qty") or 0):
            continue
        by.setdefault(o["symbol"], []).append(o)
    out = {}
    for sym, os_ in by.items():
        os_.sort(key=lambda o: o["filled_at"])
        qty, start, mine, any_mine = 0.0, None, False, False
        for o in os_:
            q = float(o["filled_qty"])
            cid = o.get("client_order_id") or ""
            if o["side"] == "buy":
                if qty <= 1e-6:
                    start, mine, any_mine = o["filled_at"], False, False
                qty += q
                if not cid.startswith(PREFIX):
                    any_mine = True
                    if o["filled_at"] >= MANUAL_FROM:
                        mine = True
            else:
                qty -= q
                if qty <= 1e-6:
                    qty, start, mine, any_mine = 0.0, None, False, False
        if qty > 1e-6 and start:
            out[sym] = {"start": start[:10], "ruled": mine, "manual": any_mine}
    return out


def state_for_app():
    st = _load(STATE_KEY)
    return {"pending": st.get("pending") or {}, "keep": _load(KEEP_KEY), "ruled": st.get("ruled") or [],
            "updated": st.get("updated")}


def _next_open_text(clock):
    try:
        return dt.datetime.fromisoformat(clock["next_open"].replace("Z", "+00:00")).strftime("%a %b %-d")
    except Exception:
        return "the next open"


def review(api, cfg, positions, orders, clock, log, trade=True):
    """Check your ruled positions; alert on new reasons, sell ones flagged on an earlier day that you didn't keep.
    Returns (actions, alerts, state) where alerts are dicts for the email; state is what the app shows."""
    hold = holdings(orders)
    ruled = {s: h for s, h in hold.items() if h["ruled"] and s in positions}
    st = _load(STATE_KEY)
    pend, base = st.get("pending") or {}, st.get("base") or {}
    keep = _load(KEEP_KEY)
    keep0 = json.dumps(keep, sort_keys=True)
    for d in (pend, base, keep):            # sold or no longer ruled: forget it
        for s in [s for s in d if s not in ruled]:
            d.pop(s, None)
    actions, alerts = [], []
    ev = cfg.get("_manual_eval")
    if ruled and ev is None:
        log("Manual positions: sell checks failed to run (no scores this run); nothing was sold or flagged")
        return actions, alerts, {"pending": pend, "keep": keep, "ruled": sorted(ruled)}
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    for sym, h in ruled.items():
        try:
            reasons, was_pick = ev(sym, h["start"], base.get(sym, {}).get("was_pick"))
        except Exception as e:
            log(f"Manual positions: check failed for one position ({type(e).__name__}: {str(e)[:80]})")
            continue
        base.setdefault(sym, {"start": h["start"], "was_pick": was_pick})
        if not reasons:
            pend.pop(sym, None)
            keep.pop(sym, None)
            continue
        key = ",".join(sorted({r["k"] for r in reasons}))
        if (keep.get(sym) or {}).get("key") == key:
            continue                                            # you chose to keep it for these reasons
        p = pend.get(sym)
        if p and p.get("key") == key and p.get("day", today) < today:
            if not trade:
                continue
            r = api.delete(f"/v2/positions/{sym}")
            ok = r.status_code in (200, 207)
            why = "you didn't tap Keep: " + "; ".join(x["t"] for x in reasons)
            actions.append({"side": "sell", "symbol": sym, "ok": ok, "why": why})
            if ok:
                pend.pop(sym, None)
                alerts.append({"sym": sym, "sold": True, "reasons": reasons})
            else:
                log(f"Manual positions: a sell order failed (HTTP {r.status_code}); tries again next run")
            continue
        if not p or p.get("key") != key:
            pend[sym] = {"key": key, "day": today, "reasons": reasons, "start": h["start"],
                         "sell_on": _next_open_text(clock)}
            alerts.append({"sym": sym, "sold": False, "reasons": reasons, "sell_on": pend[sym]["sell_on"]})
    state = {"pending": pend, "keep": keep, "base": base, "ruled": sorted(ruled),
             "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    if trade:
        cloud_store.put(STATE_KEY, json.dumps(state, default=str), log=lambda m: None)
        if json.dumps(keep, sort_keys=True) != keep0:      # only when this run dropped a keep (the app writes it too)
            cloud_store.put(KEEP_KEY, json.dumps(keep), log=lambda m: None)
        for a in alerts:
            t = a["sym"].replace(".", "-")
            txt = "; ".join(x["t"] for x in a["reasons"])
            if a["sold"]:
                cloud_store.notify(f"Sold your {t} at the open", f"Reason: {txt}. You didn't tap Keep.", tag=f"manual-{t}",
                                   url=f"/#port/{t}", log=log)
            else:
                cloud_store.notify(f"Sell {t} at the open {a['sell_on']}?", f"{txt[:300]}. Tap Keep in the app to hold it.",
                                   tag=f"manual-{t}", url=f"/#port/{t}", log=log)
    log(f"Manual positions: {len(ruled)} under the tool's rules, {len(pend)} flagged to sell, "
        f"{sum(1 for a in actions if a['ok'])} sold this run")
    return actions, alerts, {k: v for k, v in state.items() if k != "base"}


def make_eval(E, scored, px, cfg):
    """The reasons for one position, from this run's scores and prices: (ticker_alpaca, start YYYY-MM-DD,
    was_pick or None) -> (reasons, was_pick)."""
    import pandas as pd
    pol = E.load_adaptive(cfg)["policy"]
    mt = E.member_trades(scored)
    buys = scored[(scored["tx_type"] == "buy") & scored["ticker"].notna()]
    sells = scored[(scored["tx_type"] == "sell") & scored["ticker"].notna()]
    bar = float(np.percentile(buys["score"].dropna(), cfg["PICK_PERCENTILE"])) if len(buys) else np.inf
    srt = np.sort(sells["short_score"].dropna().values) if len(sells) else np.array([])
    pxf = px.ffill()
    caps = scored.sort_values("filed_date").drop_duplicates("ticker", keep="last").set_index("ticker")["market_cap"] \
        if "market_cap" in scored else pd.Series(dtype=float)
    ahead_rules = ("ahead", "ahead_daily", "ahead_daily_bill")
    today = pd.Timestamp.today().normalize()

    def f(sym, start, was_pick):
        t = sym.replace(".", "-")
        s0 = pd.Timestamp(start)
        out = []
        # the member purchase(s) behind your buy: the stock's purchase disclosures filed in the 45 days up to it
        mine = buys[(buys["ticker"] == t) & (buys["filed_date"] <= s0) & (buys["filed_date"] > s0 - pd.Timedelta(days=45))]
        best = float(mine["score"].max()) if len(mine) else None
        if was_pick is None:
            was_pick = best is not None and best >= bar
        # 1. a member whose purchase matches yours disclosed a sale after you bought
        for x in E.member_sales(mt, t, s0, after=s0):
            out.append({"k": "member_sold", "t": f"{x['n']} sold (disclosed {x['d']})"})
            break
        # 2. became an avoid: a sale filing after your buy, scoring in the avoid range of all sale signals
        new_s = sells[(sells["ticker"] == t) & (sells["filed_date"] > s0)]
        if len(new_s) and len(srt):
            top = float(new_s["short_score"].max())
            prc = float(np.searchsorted(srt, top, side="left") / len(srt) * 100)
            if prc >= cfg["AVOID_PERCENTILE"]:
                out.append({"k": "avoid", "t": f"a new sale filing makes it an avoid (sale signal in the top {max(1, round(100 - prc))}%)"})
        # 3. holding period, counted from your buy; kept while ahead of the S&P (up to a year) like the tool's picks
        held = int(np.busday_count(s0.date(), today.date()))
        cap = caps.get(t)
        hold = int(pol["hold_small"] if (cap is not None and cap == cap and cap < 2e9) else pol["hold_other"])
        if held >= hold:
            ext = pol.get("extend", "none")
            if held >= 250:
                out.append({"k": "hold", "t": f"held {held} trading days: past the 1-year limit"})
            elif ext in ahead_rules and t in pxf and "SPY" in pxf:
                p = pxf.loc[s0:, t].dropna()
                q = pxf.loc[s0:, "SPY"].dropna()
                if len(p) > 1 and len(q) > 1:
                    gap = p.iloc[-1] / p.iloc[0] - q.iloc[-1] / q.iloc[0]
                    if not gap > 0:
                        out.append({"k": "hold", "t": f"past its {hold}-day hold and {gap * 100:+.1f} points behind the S&P since you bought"})
            elif ext not in ahead_rules:
                out.append({"k": "hold", "t": f"past its {hold}-day hold"})
        # 4. it was a pick when you bought and no longer clears the buy bar
        if was_pick and best is not None and best < bar:
            out.append({"k": "score", "t": f"the member purchase behind it now scores {best:.0f}, under the buy bar ({bar:.0f})"})
        return out, was_pick
    return f
