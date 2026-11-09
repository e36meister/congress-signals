"""Read-only: why the tool hasn't bought fresh picks since v1 (Nov 5 close). Replays broker.sync's buy checks
against Alpaca as it is now. Prints tickers, counts and percents only (the run log is public). Nothing is placed."""
import sys, json, datetime as dt
sys.path.insert(0, ".")
import broker, cloud_store

s = broker.settings(60)
api = broker.Alpaca(s)
acct = api.get("/v2/account")
positions = {p["symbol"]: p for p in api.get("/v2/positions")}
open_orders = api.get("/v2/orders", status="open", limit=500)
filled = broker._filled_orders(api)
bot = broker._bot_buys(api, orders=filled)
others = set(broker._bot_buys(api, owner=True, orders=filled))
P = broker.PREFIX
is_park = lambda x: (bot.get(x, {}).get("client_order_id") or "").startswith(broker.PARK_PREFIX)
is_sl = lambda x: (bot.get(x, {}).get("client_order_id") or "").startswith(P + "sm-")
val = lambda x: float(positions[x].get("market_value") or 0)

mine = [x for x in positions if x in bot and x not in others]
yours = sum(val(x) for x in positions if x not in mine)
eq_all = float(acct.get("equity") or 0)
equity = max(0.0, eq_all - yours)
parked = sum(val(x) for x in mine if is_park(x))
picks = [x for x in mine if not is_park(x)]
inv_main = sum(val(x) for x in picks if not is_sl(x))
inv_sl = sum(val(x) for x in picks if is_sl(x))
sleeve_pct = 0.30
main_cap = s["max_invested"] * (1 - sleeve_pct) * equity
sl_cap = s["max_invested"] * sleeve_pct * equity
plan = max(5, int(5 * 60 / 5))
dollars = main_cap / plan * s.get("size_k", 1.0)
pc = lambda a, b: f"{100 * a / b:.1f}%" if b else "–"

print("== BUDGET (shares of the whole account)")
print("  your own positions:", pc(yours, eq_all), "| tool's money:", pc(equity, eq_all))
print("  tool main picks:", len([x for x in picks if not is_sl(x)]), "positions =", pc(inv_main, equity), "of tool money; main cap", pc(main_cap, equity))
print("  tool small-company:", len([x for x in picks if is_sl(x)]), "positions =", pc(inv_sl, equity), "; small cap", pc(sl_cap, equity))
print("  parked:", pc(parked, equity), "| one main pick slot ≈", pc(dollars, equity))
print("  main loop stops when main + half a slot > cap:", inv_main + dollars * 0.5 > main_cap)
print("  main slots left:", plan - len([x for x in picks if not is_sl(x)]))

d = json.loads(cloud_store.get("dashboard_data.json", log=lambda m: None) or "{}")
w = d.get("watchlist") or {}
print("\n== FRESH PICKS vs ACCOUNT")
for t in ["FMAO", "NVDA", "BHE"]:
    a = broker.to_alpaca(t)
    who = ("tool" if a in mine else "yours" if a in positions else "not held")
    pend = [o.get("side") + ":" + (o.get("client_order_id") or "")[:10] for o in open_orders if o["symbol"] == a]
    print(f"  {t}: held {who}; open orders {pend}")

since = (dt.datetime.utcnow() - dt.timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
print("\n== ORDERS SINCE", since[:10], "(no amounts)")
for o in api.get("/v2/orders", status="all", limit=200, direction="asc", after=since):
    cid = o.get("client_order_id") or ""
    kind = "tool-park" if cid.startswith(broker.PARK_PREFIX) else "tool-small" if cid.startswith(P + "sm-") else \
        "tool" if cid.startswith(P) else "button" if cid.startswith("tap-") else "you/other"
    print(" ", o["symbol"], o["side"], o["status"], kind, cid[:26], o.get("submitted_at", "")[:16])

sb = (d.get("broker") or d.get("paper") or {})
print("\n== LAST RUN'S ACTIONS (this_run)")
for k in ("broker", "paper", "account"):
    x = d.get(k)
    if isinstance(x, dict) and "this_run" in x:
        for a in x["this_run"]:
            print(" ", json.dumps(a, default=str)[:300])
print("  dashboard keys:", sorted(d)[:50])

# BHE detail (Nov 9 follow-up): times and types only, no amounts
print("\n== BHE ORDERS (times, types; no amounts)")
for o in api.get("/v2/orders", status="all", limit=50, direction="asc", after=since, symbols="BHE"):
    print(" ", o["side"], o.get("type"), "ext" if o.get("extended_hours") else "reg", "limit" if o.get("limit_price") else "",
          o["status"], (o.get("client_order_id") or "")[:30], "| sub", (o.get("submitted_at") or "")[:19],
          "| cancel", (o.get("canceled_at") or "")[:19], "| expire", (o.get("expired_at") or "")[:19],
          "| replaced_by", bool(o.get("replaced_by")), "| fail", (o.get("failed_at") or "")[:19])
for k in ("ext/intents.json", "health/app.json"):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        j = json.loads(raw) if raw else None
    except Exception:
        j = None
    if k == "health/app.json" and isinstance(j, dict):
        j = {s: {f: v.get(f) for f in ("text", "open", "first", "last", "n", "fixed")} for s, v in j.items() if s in ("buy", "fills")}
    if k == "ext/intents.json" and isinstance(j, dict):
        j = {"updated": j.get("updated"), "intents": [{f: v.get(f) for f in ("cid", "sym", "side", "ext", "wait_open", "created", "at")}
                                                       for v in (j.get("intents") or {}).values()]}
    print(f"\n== {k}:", json.dumps(j, default=str)[:2500])
