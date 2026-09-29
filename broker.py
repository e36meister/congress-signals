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


def _bot_buys(api, any_buyer=False):
    """First filled buy per symbol that this tool placed (or anyone, with any_buyer), from Alpaca's order history."""
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
            if (any_buyer or (o.get("client_order_id") or "").startswith(PREFIX)) and o.get("side") == "buy" and o.get("filled_at"):
                sym = o["symbol"]
                if sym not in out or o["filled_at"] < out[sym]["filled_at"]:
                    out[sym] = o
        if len(page) < 500:
            break
        until = page[-1]["submitted_at"]
    return out


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


def recheck_queued_sells(caps, log):
    """Cancel a waiting cleanup sell whose position no longer counts as a misfit (its size estimate moved
    back under the margin). Only touches sell orders the cleanup itself placed; never your orders."""
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
    others = {o["symbol"] for o in api.get("/v2/orders", status="all", limit=500, direction="desc")
              if o.get("side") == "buy" and float(o.get("filled_qty") or 0) > 0
              and not (o.get("client_order_id") or "").startswith(PREFIX)}

    # 1. sell what this tool bought once the holding period (trading days) is over
    for sym, o in (bot.items() if trade else []):
        if sym not in positions or sym in pending or sym in others:
            continue
        bought = dt.date.fromisoformat(o["filled_at"][:10])
        held = int(np.busday_count(bought, today))
        if held >= _hold_of(o, s["hold_days"]):             # each position keeps the hold it was bought under
            r = api.delete(f"/v2/positions/{sym}")
            actions.append({"side": "sell", "symbol": sym, "ok": r.status_code in (200, 207),
                            "why": f"held {held} trading days"})
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
        equity = float(acct.get("equity") or 0)
        sleeve_pct = max(0.0, min(0.9, float(env("SMALL_SLEEVE_PCT") or 30) / 100))
        is_sleeve = lambda sym: (bot.get(sym, {}).get("client_order_id") or "").startswith(PREFIX + "sm-")
        val = lambda sym: float(positions[sym].get("market_value") or 0)
        inv_main = sum(val(p) for p in positions if not is_sleeve(p))
        inv_sleeve = sum(val(p) for p in positions if is_sleeve(p))
        per_week = float(cfg.get("PICKS_PER_WEEK", 5))
        plan = max(5, int(per_week * int(pol["hold_other"]) / 5))     # positions open at once in the backtest
        main_cap = s["max_invested"] * (1 - sleeve_pct) * equity
        dollars = s["dollars"] or main_cap / plan
        slots = (s["max_positions"] or plan) - len([p for p in positions if not is_sleeve(p)]) - len(pending)

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

        rows = buys[buys["action"] == "BUY"] if buys is not None and len(buys) else buys
        if rows is not None and len(rows):
            new_buys = set(new.loc[new["action"] == "BUY", "ticker"]) if new is not None and len(new) else set()
            first_time = not any(not is_sleeve(x) for x in bot)   # first run: start from the whole current BUY list
            for t in [t for t in rows["ticker"] if first_time or t in new_buys]:
                if slots <= 0 or inv_main + dollars > main_cap:
                    break
                row = rows[rows["ticker"] == t].iloc[0]
                small = float(row.get("f_small_cap", 0) or 0) >= 1
                hold = int(pol["hold_small"] if small else pol["hold_other"])
                ok = place(t, dollars, f"{PREFIX}{t}-{today:%Y%m%d}-h{hold}")
                if ok is None:
                    continue
                actions.append({"side": "buy", "symbol": to_alpaca(t), "ok": ok, "hold": hold,
                                "why": "new BUY pick" if not first_time else "current BUY pick (first run)"})
                if ok:
                    slots -= 1
                    inv_main += dollars

        # small-company portfolio: every timely purchase of a company under $2B, once per stock
        if sleeve_pct > 0 and buys is not None and len(buys) and "f_small_cap" in buys:
            sm = buys[(buys["f_small_cap"].fillna(0) >= 1) & (buys.get("lag_days", pd.Series(0, index=buys.index)).fillna(999) <= 45)]
            sleeve_cap = sleeve_pct * 0.95 * equity
            sm_slots = int(cfg.get("_small_slots") or 25)
            amount = sleeve_cap / max(sm_slots, 1)
            hold = int(pol.get("hold_sleeve", 250))
            ever = {sym for sym in bot if is_sleeve(sym)}
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
    for p in positions:
        o = bot.get(p["symbol"])
        f0 = o or first.get(p["symbol"])
        bought = f0["filled_at"][:10] if f0 else None
        pos.append({"t": from_alpaca(p["symbol"]), "qty": float(p["qty"]), "avg": float(p["avg_entry_price"]),
                    "px": float(p.get("current_price") or 0), "mv": float(p.get("market_value") or 0),
                    "pl": float(p.get("unrealized_pl") or 0), "plpc": float(p.get("unrealized_plpc") or 0),
                    "day": float(p.get("change_today") or 0), "bot": bool(o), "bought": bought,
                    "sleeve": "small" if o and (o.get("client_order_id") or "").startswith(PREFIX + "sm-") else None,
                    "hold": _hold_of(o, s["hold_days"]) if o else None,
                    "held": int(np.busday_count(dt.date.fromisoformat(bought), today)) if bought else None})
    eq, last = float(acct.get("equity") or 0), float(acct.get("last_equity") or 0)
    return {"mode": s["mode"], "auto": s["auto"], "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "market_open": bool(clock.get("is_open")), "next_open": clock.get("next_open"),
            "equity": eq, "cash": float(acct.get("cash") or 0), "day_pl": eq - last if last else None,
            "limits": {"dollars": s["dollars"], "max_positions": s["max_positions"], "max_invested_pct": s["max_invested"] * 100,
                       "hold_days": int(pol["hold_other"]), "hold_small": int(pol["hold_small"]),
                       "per_week": float(cfg.get("PICKS_PER_WEEK", 5)), "from_policy": bool(cfg.get("_hold_policy")),
                       "small_pct": float(env("SMALL_SLEEVE_PCT") or 30), "hold_sleeve": int(pol.get("hold_sleeve", 250))},
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
