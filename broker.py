"""Alpaca connection: follows the watchlist's BUY picks in your Alpaca account and reports holdings to the dashboard.

Paper (fake money) is the default. Real money needs TRADING_MODE=live, the live keys, AND AUTO_TRADE_LIVE=true.
Only positions this tool opened (order IDs starting with "cs-") are ever sold by it; anything you buy yourself
is left alone. Nothing about balances or positions is written to the run log, because the repo is public.
"""
import os, re, math, datetime as dt
import numpy as np
import pandas as pd
import requests

env = os.environ.get
BASE = {"paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}
PREFIX = "cs-"
PARK_PREFIX = PREFIX + "park-"          # unused tool money parked in an S&P 500 fund, like the backtest
PARK_CHOICES = ("SPY", "VOO", "IVV")     # first one you don't hold yourself


def settings(hold_days):
    mode = (env("TRADING_MODE") or "paper").strip().lower()
    mode = mode if mode in BASE else "paper"
    up = mode.upper()
    key, secret = env(f"ALPACA_{up}_KEY_ID"), env(f"ALPACA_{up}_SECRET_KEY")
    auto_default = "true" if mode == "paper" else "false"
    auto = (env("AUTO_TRADE_LIVE" if mode == "live" else "AUTO_TRADE") or auto_default).strip().lower() == "true"
    num = lambda k, d: float(env(k) or d)
    return {"mode": mode, "key": key, "secret": secret, "auto": auto,
            # unset = size like the backtest: the account split evenly across the picks a holding period needs
            "dollars": float(env("DOLLARS_PER_TRADE")) if env("DOLLARS_PER_TRADE") else None,
            "max_positions": int(float(env("MAX_POSITIONS"))) if env("MAX_POSITIONS") else None,
            "max_invested": num("MAX_INVESTED_PCT", 90) / 100, "hold_days": int(hold_days)}


class Alpaca:
    def __init__(self, s):
        self.base = BASE[s["mode"]]
        self.h = {"APCA-API-KEY-ID": s["key"], "APCA-API-SECRET-KEY": s["secret"]}

    def get(self, path, **params):
        r = requests.get(self.base + path, headers=self.h, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path, body):
        return requests.post(self.base + path, headers=self.h, json=body, timeout=30)

    def delete(self, path):
        return requests.delete(self.base + path, headers=self.h, timeout=30)


def to_alpaca(t):
    return t.replace("-", ".")


def from_alpaca(s):
    return s.replace(".", "-")


def _bot_buys(api, any_buyer=False, owner=False):
    """First filled buy per symbol that this tool placed (or anyone, with any_buyer; or you, with owner:
    the Buy button or Alpaca itself), from Alpaca's order history."""
    out, after = {}, (dt.datetime.utcnow() - dt.timedelta(days=500)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = None
    for _ in range(20):
        params = {"status": "closed", "limit": 500, "direction": "desc", "after": after}
        if until:
            params["until"] = until
        page = api.get("/v2/orders", **params)
        if not page:
            break
        for o in page:
            mine = (o.get("client_order_id") or "").startswith(PREFIX)
            if (any_buyer or (not mine if owner else mine)) and o.get("side") == "buy" and o.get("filled_at"):
                sym = o["symbol"]
                if sym not in out or o["filled_at"] < out[sym]["filled_at"]:
                    out[sym] = o
        if len(page) < 500:
            break
        until = page[-1]["submitted_at"]
    return out


NEAR_END_DAYS = 5          # a position this close to its sell date is re-checked against the current hold every run


def current_hold(order, sym, pol, cfg):
    """The holding period the weekly check currently supports for this position's group: the small-company
    portfolio, or main picks of small / other companies (size at the filing, from the run's scores)."""
    cid = order.get("client_order_id") or ""
    if not pol or cid.startswith(PARK_PREFIX):
        return None
    if cid.startswith(PREFIX + "sm-"):
        v = pol.get("hold_sleeve")
    else:
        cap = (cfg.get("_caps") or {}).get(from_alpaca(sym))
        v = pol.get("hold_small") if (cap is not None and cap < 2e9) else pol.get("hold_other")
    try:
        return int(v) if v else None
    except (TypeError, ValueError):
        return None


def _hold_of(order, default):
    m = re.search(r"-h(\d+)$", order.get("client_order_id") or "")
    return int(m.group(1)) if m else int(default)


SLEEVE_MAX_CAP = 2e9
MISFIT_MARGIN = 1.10      # sell only when the estimated size is at least 10% over the line


def sleeve_misfits(bot, caps):
    """Small-company-portfolio positions whose company was clearly $2B or more when the member bought."""
    out = set()
    for sym, o in bot.items():
        if not (o.get("client_order_id") or "").startswith(PREFIX + "sm-"):
            continue
        c = caps.get(sym)
        if c is not None and pd.notna(c) and float(c) >= SLEEVE_MAX_CAP * MISFIT_MARGIN:
            out.add(sym)
    return out


def recheck_queued_sells(caps, log, policy=None):
    """Cancel a waiting cleanup sell whose position no longer counts as a misfit (its size estimate moved
    back under the margin). Only touches sell orders the cleanup itself placed; never your orders.
    Skipped while a small-company price rule is on, since its sales look the same."""
    if (policy or {}).get("sleeve_price_exit", "none") != "none" or (policy or {}).get("sleeve_extend", "none") != "none":
        return
    s = settings(60)
    if not (s["key"] and s["secret"]):
        return
    api = Alpaca(s)
    bot = _bot_buys(api)
    keep = sleeve_misfits(bot, caps)
    n = 0
    for o in api.get("/v2/orders", status="open", limit=500):
        sym = o["symbol"]
        if (o.get("side") == "sell" and sym in bot and sym not in keep
                and (bot[sym].get("client_order_id") or "").startswith(PREFIX + "sm-")
                and sym in caps and not (o.get("client_order_id") or "").startswith(PREFIX)):
            held = int(np.busday_count(dt.date.fromisoformat(bot[sym]["filled_at"][:10]), dt.date.today()))
            if held < _hold_of(bot[sym], 250):       # not a normal end-of-hold sale
                if api.delete(f"/v2/orders/{o['id']}").status_code in (200, 204):
                    n += 1
    if n:
        log(f"Broker: cancelled {n} cleanup sell order(s) that no longer apply")


def sync(cfg, buys, new, last_prices, log, trade=True):
    """Sell bot positions past the holding period, buy new BUY picks within the limits, and return a
    snapshot of the account for the dashboard (or None when no keys are set)."""
    s = settings(cfg.get("HOLD_DAYS", 60))
    pol = cfg.get("_hold_policy") or {"hold_small": s["hold_days"], "hold_other": s["hold_days"]}
    if not (s["key"] and s["secret"]):
        log(f"Broker: no Alpaca {s['mode']} keys set; skipping")
        return None
    api = Alpaca(s)
    try:
        acct = api.get("/v2/account")
        clock = api.get("/v2/clock")
    except Exception as e:
        log(f"Broker: couldn't reach Alpaca ({type(e).__name__}); trying again next run")
        return None
    today = dt.date.today()
    positions = {p["symbol"]: p for p in api.get("/v2/positions")}
    open_orders = api.get("/v2/orders", status="open", limit=500)
    pending = {o["symbol"] for o in open_orders}
    bot = _bot_buys(api)
    actions, sold = [], set()
    # symbols you (or the Buy button) also bought: selling would close your shares too, so the tool leaves them
    # stocks you bought yourself, from the whole order history (the last 500 orders alone would forget
    # your older buys once the tool has traded a lot, and then a stock you both own could be sold)
    others = set(_bot_buys(api, owner=True))

    is_park = lambda sym: (bot.get(sym, {}).get("client_order_id") or "").startswith(PARK_PREFIX)
    park = next((x for x in PARK_CHOICES if x not in others), None)

    # price rules the weekly check can turn on, set separately for main picks and the small-company portfolio
    is_sm = lambda o: (o.get("client_order_id") or "").startswith(PREFIX + "sm-")
    rule_pe = lambda o: pol.get("sleeve_price_exit" if is_sm(o) else "price_exit", "none")
    rule_ext = lambda o: pol.get("sleeve_extend" if is_sm(o) else "extend", "none")
    pct = lambda r: int(re.search(r"(\d+)$", r).group(1)) / 100 if re.search(r"(\d+)$", r or "") else 0.0
    hist = cfg.get("_price_hist")                            # daily closes since a date, from the full update

    def high_since(sym, bought, now_px):
        try:
            h = hist(from_alpaca(sym), bought) if hist else None
        except Exception:
            h = None
        if h is None or not len(h):
            return None
        return max(float(np.nanmax(h)), now_px)

    # 1. sell what this tool bought once the holding period (trading days) is over
    for sym, o in (bot.items() if trade else []):
        if sym not in positions or sym in pending or sym in others or is_park(sym):
            continue
        bought = dt.date.fromisoformat(o["filled_at"][:10])
        held = int(np.busday_count(bought, today))
        hold = orig = _hold_of(o, s["hold_days"])
        # near the end of its hold (or past it), checked every run: the holding period the weekly check now
        # supports for this group wins over the one it was bought under (longer: keep it; shorter: sell)
        now_hold = current_hold(o, sym, pol, cfg)
        if now_hold and now_hold != orig and held >= min(orig, now_hold) - NEAR_END_DAYS:
            hold = now_hold
            if now_hold > orig and held >= orig - NEAR_END_DAYS and sym not in cfg.setdefault("_extended_logged", set()):
                cfg["_extended_logged"].add(sym)
                actions.append({"side": "hold", "symbol": sym, "ok": True,
                                "why": f"holding {now_hold} trading days instead of {orig}: the weekly check now favors the longer hold"})
        ext = rule_ext(o)
        if ext != "none" and hold <= held < 2 * hold:
            # keep holding while it's in profit and within X% of its high since buying
            now_px = float(positions[sym].get("current_price") or 0)
            entry = float(positions[sym].get("avg_entry_price") or 0)
            hi = high_since(sym, o["filled_at"][:10], now_px)
            if hi is None or (now_px > entry and now_px >= hi * (1 - pct(ext))):
                continue
        if held >= hold:
            r = api.delete(f"/v2/positions/{sym}")
            actions.append({"side": "sell", "symbol": sym, "ok": r.status_code in (200, 207),
                            "why": f"held {held} trading days" + (f" (the weekly check moved this group to {hold})" if hold != orig else "")})
            sold.add(sym)

    # 1a. the weekly check can turn on "sell when a member who bought discloses a sale" (main picks only;
    #     the small-company portfolio keeps its own fixed hold). Only sales disclosed since we bought count.
    fn = cfg.get("_member_sales")
    if trade and pol.get("exit_on_member_sell") and fn:
        for sym, o in bot.items():
            if sym not in positions or sym in pending or sym in sold or sym in others or is_park(sym):
                continue
            if (o.get("client_order_id") or "").startswith(PREFIX + "sm-"):
                continue
            bought = o["filled_at"][:10]
            try:
                hits = [x for x in fn(from_alpaca(sym), bought) if x["d"] >= bought]
            except Exception:
                hits = []
            if hits:
                r = api.delete(f"/v2/positions/{sym}")
                actions.append({"side": "sell", "symbol": sym, "ok": r.status_code in (200, 207),
                                "why": "a member who bought it disclosed a sale"})
                sold.add(sym)

    # 1a2. stop rules: sell a position that fell X% below its buy price, or X% from its high since buying
    if trade:
        for sym, o in bot.items():
            if sym not in positions or sym in pending or sym in sold or sym in others or is_park(sym):
                continue
            pe = rule_pe(o)
            if pe == "none":
                continue
            now_px = float(positions[sym].get("current_price") or 0)
            entry = float(positions[sym].get("avg_entry_price") or 0)
            if not (now_px > 0 and entry > 0):
                continue
            if pe.startswith("trail"):
                ref = high_since(sym, o["filled_at"][:10], now_px)
                why = f"fell {pct(pe)*100:.0f}% from its high since buying"
            else:
                ref, why = entry, f"fell {pct(pe)*100:.0f}% below the buy price"
            if ref and now_px <= ref * (1 - pct(pe)):
                r = api.delete(f"/v2/positions/{sym}")
                actions.append({"side": "sell", "symbol": sym, "ok": r.status_code in (200, 207), "why": why})
                sold.add(sym)

    # 1b. keep the paper test clean: sell small-company-portfolio positions that clearly don't fit its rules
    #     (anything bought under the earlier bug). Company size is an estimate that drifts a little from day
    #     to day, so only clearly larger companies are sold; one near the $2B line is left alone.
    if trade and buys is not None and len(buys) and "market_cap" in buys:
        caps = dict(zip(buys["ticker"].map(to_alpaca), buys["market_cap"]))
        for sym in sleeve_misfits(bot, caps):
            if sym not in positions or sym in pending or sym in sold or sym in others:
                continue
            r = api.delete(f"/v2/positions/{sym}")
            actions.append({"side": "sell", "symbol": sym, "ok": r.status_code in (200, 207),
                            "why": "didn't fit the small-company rules"})
            positions.pop(sym, None)
            sold.add(sym)                            # don't buy it back in the same run

    # 2. buy new picks: the main strategy (BUY picks) and the separate small-company portfolio
    if s["auto"] and trade:
        sleeve_pct = max(0.0, min(0.9, float(env("SMALL_SLEEVE_PCT") or 30) / 100))
        is_sleeve = lambda sym: (bot.get(sym, {}).get("client_order_id") or "").startswith(PREFIX + "sm-")
        val = lambda sym: float(positions[sym].get("market_value") or 0)
        # the tool's budget leaves out positions you bought yourself (they aren't the tool's money to plan with)
        mine = [p for p in positions if p in bot and p not in others]
        yours = sum(val(p) for p in positions if p not in mine)
        equity = max(0.0, float(acct.get("equity") or 0) - yours)
        parked = sum(val(p) for p in mine if is_park(p))
        mine = [p for p in mine if not is_park(p)]
        inv_main = sum(val(p) for p in mine if not is_sleeve(p))
        inv_sleeve = sum(val(p) for p in mine if is_sleeve(p))
        inv0 = inv_main + inv_sleeve
        per_week = float(cfg.get("PICKS_PER_WEEK", 5))
        plan = max(5, int(per_week * int(pol["hold_other"]) / 5))     # positions open at once in the backtest
        main_cap = s["max_invested"] * (1 - sleeve_pct) * equity
        dollars = s["dollars"] or main_cap / plan
        slots = (s["max_positions"] or plan) - len([p for p in mine if not is_sleeve(p)]) - len(pending)

        def place(t, amount, cid):
            sym = to_alpaca(t)
            price = last_prices.get(t)
            if sym in positions or sym in pending or sym in sold or not price or not math.isfinite(price) or price <= 0:
                return None
            try:
                asset = api.get(f"/v2/assets/{sym}")
                if not asset.get("tradable"):
                    return None
            except Exception:
                return None
            body = {"symbol": sym, "side": "buy", "type": "market", "time_in_force": "day", "client_order_id": cid}
            if asset.get("fractionable"):
                body["notional"] = f"{amount:.2f}"
            else:
                qty = int(amount // price)
                if qty < 1:
                    return None
                body["qty"] = str(qty)
            r = api.post("/v2/orders", body)
            if r.status_code in (200, 201):
                pending.add(sym)
                return True
            return False

        live = cfg.get("_live_picks")
        if live is not None:
            # the backtest's own rule on the newest filings, bought as soon as they're seen (stale picks skipped)
            rows, order, first_time = live, list(live["ticker"]) if len(live) else [], False
        else:
            rows = buys[buys["action"] == "BUY"] if buys is not None and len(buys) else buys
            new_buys = set(new.loc[new["action"] == "BUY", "ticker"]) if new is not None and len(new) else set()
            first_time = not any(not is_sleeve(x) for x in bot)   # first run: start from the whole current BUY list
            order = [t for t in rows["ticker"] if first_time or t in new_buys] if rows is not None and len(rows) else []
        if rows is not None and len(rows):
            for t in order:
                if slots <= 0 or inv_main + dollars * 0.5 > main_cap:
                    break
                row = rows[rows["ticker"] == t].iloc[0]
                small = float(row.get("f_small_cap", 0) or 0) >= 1
                hold = int(pol["hold_small"] if small else pol["hold_other"])
                k = float(row.get("size_mult", 1.0) or 1.0) if pol.get("sizing", "equal") != "equal" else 1.0
                amt = dollars * k                        # bigger position for a stronger pick, when sizing is on
                if inv_main + amt > main_cap:
                    amt = main_cap - inv_main
                if amt < 1:
                    break
                ok = place(t, amt, f"{PREFIX}{t}-{today:%Y%m%d}-h{hold}")
                if ok is None:
                    continue
                actions.append({"side": "buy", "symbol": to_alpaca(t), "ok": ok, "hold": hold,
                                "why": "new BUY pick" if not first_time else "current BUY pick (first run)"})
                if ok:
                    slots -= 1
                    inv_main += amt

        # small-company portfolio: every timely purchase of a company under $2B, once per stock
        fresh_sm = cfg.get("_small_fresh")
        if sleeve_pct > 0 and fresh_sm is not None:
            sm = fresh_sm              # the backtest's small-company rule on filings recent enough to match its timing
        elif sleeve_pct > 0 and buys is not None and len(buys) and "f_small_cap" in buys:
            sm = buys[(buys["f_small_cap"].fillna(0) >= 1) & (buys.get("lag_days", pd.Series(0, index=buys.index)).fillna(999) <= 45)]
            if pol.get("sleeve_coverage") and "f_no_coverage" in sm:     # set by the weekly check
                sm = sm[sm["f_no_coverage"].fillna(0) == 0]
        else:
            sm = None
        if sm is not None and len(sm):
            sleeve_cap = sleeve_pct * 0.95 * equity
            sm_slots = int(cfg.get("_small_slots") or 25)
            amount = sleeve_cap / max(sm_slots, 1)
            hold = int(pol.get("hold_sleeve", 250))
            # the backtest's rule already spaces repeat buys of a stock; the old list-based path buys each stock once
            ever = set() if fresh_sm is not None else {sym for sym in bot if is_sleeve(sym)}
            for t in sm["ticker"]:
                if inv_sleeve + amount > sleeve_cap:
                    break
                if to_alpaca(t) in ever:
                    continue
                ok = place(t, amount, f"{PREFIX}sm-{t}-{today:%Y%m%d}-h{hold}")
                if ok is None:
                    continue
                actions.append({"side": "buy", "symbol": to_alpaca(t), "ok": ok, "hold": hold, "why": "small-company portfolio"})
                if ok:
                    inv_sleeve += amount

        # 3. park the tool's unused money in an S&P 500 fund (the backtest assumes idle money sits in SPY),
        #    keeping a little cash for your own Buy-button orders. Sold down as picks need the money.
        if park and park not in pending and env("PARK_IDLE", "true").lower() != "false":
            spent = inv_main + inv_sleeve - inv0
            open_buys = sum(float(o.get("notional") or 0) for o in open_orders if o.get("side") == "buy")
            buffer = 0.03 * equity
            target = max(0.0, equity - inv_main - inv_sleeve - open_buys - buffer)
            diff = target - parked
            cash_free = float(acct.get("cash") or 0) - spent - open_buys - 0.02 * float(acct.get("equity") or 0)
            body = None
            if diff > max(250.0, 0.005 * equity) and cash_free > 250:
                body = {"symbol": park, "side": "buy", "notional": f"{min(diff, cash_free):.2f}"}
            elif diff < -max(250.0, 0.005 * equity) and parked > 0:
                body = {"symbol": park, "side": "sell", "notional": f"{min(-diff, parked):.2f}"}
            if body:
                body.update({"type": "market", "time_in_force": "day",
                             "client_order_id": f"{PARK_PREFIX}{body['side']}-{dt.datetime.now():%Y%m%d%H%M%S}"})
                r = api.post("/v2/orders", body)
                actions.append({"side": body["side"], "symbol": park, "ok": r.status_code in (200, 201),
                                "why": f"parking unused money in the S&P 500 (${float(body['notional']):,.0f})"})
    if trade:
        log(f"Broker ({s['mode']}): {sum(a['ok'] for a in actions if a['side'] == 'buy')} buy order(s), "
            f"{sum(a['ok'] for a in actions if a['side'] == 'sell')} sell order(s)"
            + ("" if s["auto"] else "; automatic buying is off"))
    else:
        log(f"Broker ({s['mode']}): connected, portfolio refreshed")

    # 3. snapshot for the dashboard (written to your private Drive, never to the log)
    try:
        positions = api.get("/v2/positions")
        acct = api.get("/v2/account")
        recent = api.get("/v2/orders", status="all", limit=40, direction="desc")
    except Exception:
        positions, recent = list(positions.values()), []
    bot = _bot_buys(api) if actions else bot
    try:
        first = _bot_buys(api, any_buyer=True)
    except Exception:
        first = {}
    pos = []
    try:
        yours_first = _bot_buys(api, owner=True)
    except Exception:
        yours_first = {}
    for p in positions:
        # a stock you also bought counts as yours (the tool never sells it), dated from your first buy
        o = None if p["symbol"] in yours_first else bot.get(p["symbol"])
        f0 = o or yours_first.get(p["symbol"]) or first.get(p["symbol"])
        bought = f0["filled_at"][:10] if f0 else None
        pos.append({"t": from_alpaca(p["symbol"]), "qty": float(p["qty"]), "avg": float(p["avg_entry_price"]),
                    "px": float(p.get("current_price") or 0), "mv": float(p.get("market_value") or 0),
                    "pl": float(p.get("unrealized_pl") or 0), "plpc": float(p.get("unrealized_plpc") or 0),
                    "day": float(p.get("change_today") or 0), "bot": bool(o), "bought": bought,
                    "sleeve": ("small" if o and (o.get("client_order_id") or "").startswith(PREFIX + "sm-")
                               else "park" if o and (o.get("client_order_id") or "").startswith(PARK_PREFIX) else None),
                    "hold": (None if (o.get("client_order_id") or "").startswith(PARK_PREFIX) else _hold_of(o, s["hold_days"])) if o else None,
                    "held": int(np.busday_count(dt.date.fromisoformat(bought), today)) if bought else None})
    eq, last = float(acct.get("equity") or 0), float(acct.get("last_equity") or 0)
    try:    # the standard amount per main pick (before sizing), for the dashboard's suggested amounts
        sp_ = max(0.0, min(0.9, float(env("SMALL_SLEEVE_PCT") or 30) / 100))
        plan_ = max(5, int(float(cfg.get("PICKS_PER_WEEK", 5)) * int(pol["hold_other"]) / 5))
        # the tool's budget: account value minus positions you bought yourself (same as when it places orders)
        yours_ = sum(float(p.get("market_value") or 0) for p in positions
                     if p["symbol"] not in bot or p["symbol"] in others)
        per_pick = s["dollars"] or s["max_invested"] * (1 - sp_) * max(0.0, eq - yours_) / plan_
    except Exception:
        per_pick = None
    return {"mode": s["mode"], "auto": s["auto"], "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "market_open": bool(clock.get("is_open")), "next_open": clock.get("next_open"),
            "equity": eq, "cash": float(acct.get("cash") or 0), "day_pl": eq - last if last else None,
            "limits": {"dollars": s["dollars"], "max_positions": s["max_positions"], "max_invested_pct": s["max_invested"] * 100,
                       "hold_days": int(pol["hold_other"]), "hold_small": int(pol["hold_small"]),
                       "per_week": float(cfg.get("PICKS_PER_WEEK", 5)), "from_policy": bool(cfg.get("_hold_policy")),
                       "small_pct": float(env("SMALL_SLEEVE_PCT") or 30), "hold_sleeve": int(pol.get("hold_sleeve", 250)),
                       "sleeve_coverage": bool(pol.get("sleeve_coverage")),
                       "exit_on_member_sell": bool(pol.get("exit_on_member_sell")),
                       "price_exit": pol.get("price_exit", "none"), "extend": pol.get("extend", "none"),
                       "sleeve_price_exit": pol.get("sleeve_price_exit", "none"),
                       "sleeve_extend": pol.get("sleeve_extend", "none"),
                       "per_pick": per_pick, "sizing": pol.get("sizing", "equal")},
            "positions": sorted(pos, key=lambda x: -x["mv"]),
            "orders": [{"t": from_alpaca(o["symbol"]), "side": o["side"], "qty": o.get("qty"), "status": o.get("status", ""),
                        "filled_px": o.get("filled_avg_price"), "at": o.get("filled_at") or o.get("submitted_at") or "",
                        "bot": (o.get("client_order_id") or "").startswith(PREFIX),
                        "by": "tool" if (o.get("client_order_id") or "").startswith(PREFIX)
                        or (o["side"] == "sell" and o["symbol"] in bot and not o.get("client_order_id", "").startswith("tap-")
                            and o["symbol"] not in others)
                        else ("tap" if (o.get("client_order_id") or "").startswith("tap-") else "you")} for o in recent],
            "this_run": actions}


def place_tap_order(ticker, dollars, req_id, log):
    """One order you approved on the dashboard. Returns (ok, short_reason)."""
    s = settings(60)
    if not (s["key"] and s["secret"]):
        return False, "no-keys"
    cap = float(env("MAX_TAP_DOLLARS") or 25000)
    if not (1 <= dollars <= cap):
        return False, "amount-over-limit"
    api = Alpaca(s)
    sym = to_alpaca(ticker)
    try:
        asset = api.get(f"/v2/assets/{sym}")
        acct = api.get("/v2/account")
    except Exception:
        return False, "not-found"
    if not asset.get("tradable"):
        return False, "not-tradable"
    if dollars > float(acct.get("buying_power") or 0):
        return False, "not-enough-cash"
    try:    # not enough cash: sell some of the tool's parked S&P fund first, never your own shares
        cash = float(acct.get("cash") or 0)
        if dollars > cash:
            bot = _bot_buys(api)
            pk = next((x for x, o in bot.items() if (o.get("client_order_id") or "").startswith(PARK_PREFIX)), None)
            pos = {p["symbol"]: p for p in api.get("/v2/positions")}
            if pk and pk in pos:
                amt = min(float(pos[pk].get("market_value") or 0), dollars - cash + 50)
                if amt > 1:
                    api.post("/v2/orders", {"symbol": pk, "side": "sell", "notional": f"{amt:.2f}", "type": "market",
                                            "time_in_force": "day", "client_order_id": f"{PARK_PREFIX}tap-{req_id}"[:48]})
    except Exception:
        pass
    body = {"symbol": sym, "side": "buy", "type": "market", "time_in_force": "day",
            "client_order_id": f"tap-{ticker}-{req_id}"[:48]}
    if asset.get("fractionable"):
        body["notional"] = f"{dollars:.2f}"
    else:
        try:
            r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/trades/latest", headers=api.h,
                             params={"feed": "iex"}, timeout=30)
            price = float(r.json()["trade"]["p"])
        except Exception:
            return False, "no-price"
        qty = int(dollars // price)
        if qty < 1:
            return False, "amount-too-small"
        body["qty"] = str(qty)
    r = api.post("/v2/orders", body)
    if r.status_code in (200, 201):
        return True, "placed"
    if r.status_code == 422 and "client_order_id" in (r.text or ""):
        return True, "placed"                    # already placed on an earlier pass
    return False, f"rejected-{r.status_code}"
