"""Alpaca connection: follows the watchlist's BUY picks in your Alpaca account and reports holdings to the dashboard.

Paper (fake money) is the default. Real money needs TRADING_MODE=live, the live keys, AND AUTO_TRADE_LIVE=true.
Only positions this tool opened (order IDs starting with "cs-") are ever sold by it; anything you buy yourself
is left alone. Nothing about balances or positions is written to the run log, because the repo is public.
"""
import os, math, datetime as dt
import numpy as np
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
            "dollars": num("DOLLARS_PER_TRADE", 5000), "max_positions": int(num("MAX_POSITIONS", 20)),
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


def _bot_buys(api):
    """First filled buy per symbol that this tool placed, from Alpaca's own order history."""
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
            if (o.get("client_order_id") or "").startswith(PREFIX) and o.get("side") == "buy" and o.get("filled_at"):
                sym = o["symbol"]
                if sym not in out or o["filled_at"] < out[sym]["filled_at"]:
                    out[sym] = o
        if len(page) < 500:
            break
        until = page[-1]["submitted_at"]
    return out


def sync(cfg, buys, new, last_prices, log, trade=True):
    """Sell bot positions past the holding period, buy new BUY picks within the limits, and return a
    snapshot of the account for the dashboard (or None when no keys are set)."""
    s = settings(cfg.get("HOLD_DAYS", 60))
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
    actions = []

    # 1. sell what this tool bought once the holding period (trading days) is over
    for sym, o in (bot.items() if trade else []):
        if sym not in positions or sym in pending:
            continue
        bought = dt.date.fromisoformat(o["filled_at"][:10])
        held = int(np.busday_count(bought, today))
        if held >= s["hold_days"]:
            r = api.delete(f"/v2/positions/{sym}")
            actions.append({"side": "sell", "symbol": sym, "ok": r.status_code in (200, 207),
                            "why": f"held {held} trading days"})

    # 2. buy new BUY picks
    if s["auto"] and trade:
        equity = float(acct.get("equity") or 0)
        invested = sum(float(p.get("market_value") or 0) for p in positions.values())
        slots = s["max_positions"] - len(positions) - len(pending)
        rows = buys[buys["action"] == "BUY"] if buys is not None and len(buys) else buys
        if rows is not None and len(rows):
            new_buys = set(new.loc[new["action"] == "BUY", "ticker"]) if new is not None and len(new) else set()
            first_time = not bot          # first run with keys: start from the whole current BUY list
            cands = [t for t in rows["ticker"] if first_time or t in new_buys]
            for t in cands:
                sym = to_alpaca(t)
                if slots <= 0 or invested + s["dollars"] > s["max_invested"] * equity:
                    break
                if sym in positions or sym in pending:
                    continue
                price = last_prices.get(t)
                if not price or not math.isfinite(price) or price <= 0:
                    continue
                qty = int(s["dollars"] // price)
                if qty < 1:
                    continue
                try:
                    asset = api.get(f"/v2/assets/{sym}")
                    if not asset.get("tradable"):
                        continue
                except Exception:
                    continue
                cid = f"{PREFIX}{t}-{today:%Y%m%d}"      # same pick on the same day can't be ordered twice
                r = api.post("/v2/orders", {"symbol": sym, "qty": str(qty), "side": "buy", "type": "market",
                                            "time_in_force": "day", "client_order_id": cid})
                ok = r.status_code in (200, 201)
                actions.append({"side": "buy", "symbol": sym, "ok": ok, "qty": qty,
                                "why": "new BUY pick" if not first_time else "current BUY pick (first run)"})
                if ok:
                    slots -= 1
                    invested += qty * price
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
    pos = []
    for p in positions:
        o = bot.get(p["symbol"])
        bought = o["filled_at"][:10] if o else None
        pos.append({"t": from_alpaca(p["symbol"]), "qty": float(p["qty"]), "avg": float(p["avg_entry_price"]),
                    "px": float(p.get("current_price") or 0), "mv": float(p.get("market_value") or 0),
                    "pl": float(p.get("unrealized_pl") or 0), "plpc": float(p.get("unrealized_plpc") or 0),
                    "day": float(p.get("change_today") or 0), "bot": bool(o), "bought": bought,
                    "held": int(np.busday_count(dt.date.fromisoformat(bought), today)) if bought else None})
    eq, last = float(acct.get("equity") or 0), float(acct.get("last_equity") or 0)
    return {"mode": s["mode"], "auto": s["auto"], "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "market_open": bool(clock.get("is_open")), "next_open": clock.get("next_open"),
            "equity": eq, "cash": float(acct.get("cash") or 0), "day_pl": eq - last if last else None,
            "limits": {"dollars": s["dollars"], "max_positions": s["max_positions"], "max_invested_pct": s["max_invested"] * 100,
                       "hold_days": s["hold_days"]},
            "positions": sorted(pos, key=lambda x: -x["mv"]),
            "orders": [{"t": from_alpaca(o["symbol"]), "side": o["side"], "qty": o.get("qty"), "status": o["status"],
                        "filled_px": o.get("filled_avg_price"), "at": (o.get("filled_at") or o.get("submitted_at") or "")[:16],
                        "bot": (o.get("client_order_id") or "").startswith(PREFIX)} for o in recent],
            "this_run": actions}
