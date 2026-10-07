"""One-off read-only check for the close report fix: the S&P price when the account's first trade filled vs the
close before. Prints only public S&P prices and the trade time (never balances or positions)."""
import datetime as dt, requests, broker
s = broker.settings(60); api = broker.Alpaca(s)
t0 = min(o["filled_at"] for o in broker._bot_buys(api, any_buyer=True).values())
t0d = dt.datetime.strptime(t0[:19], "%Y-%m-%dT%H:%M:%S")
r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/bars", headers=api.h, timeout=30,
                 params={"timeframe": "1Min", "feed": "iex", "adjustment": "all", "limit": 30,
                         "start": (t0d - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"), "end": t0d.strftime("%Y-%m-%dT%H:%M:%SZ")})
b = r.json().get("bars") or []
d = requests.get("https://data.alpaca.markets/v2/stocks/SPY/bars", headers=api.h, timeout=30,
                 params={"timeframe": "1Day", "feed": "iex", "adjustment": "all", "start": "2026-09-20", "end": "2026-10-07"}).json().get("bars") or []
print("first fill (UTC):", t0)
print("SPY minute bar at first fill:", b[-1] if b else None)
print("SPY daily closes:", [(x["t"][:10], x["c"]) for x in d])
owner = broker._bot_buys(api, owner=True)
print("stocks you bought yourself (symbols only):", sorted(owner))
