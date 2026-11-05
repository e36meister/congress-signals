"""Read-only (owner, Nov 5): today's move of the paper account vs SPY, Nov 4 vs Nov 5, and the positions that moved
most today. Percentages only (public log): no dollar balances."""
import os, requests
H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
B = "https://paper-api.alpaca.markets"
a = requests.get(f"{B}/v2/account", headers=H, timeout=30).json()
eq, last = float(a["equity"]), float(a["last_equity"])
h = requests.get(f"{B}/v2/account/portfolio/history", headers=H, params={"period": "1W", "timeframe": "1D"}, timeout=30).json()
import datetime as dt
pts = [(dt.datetime.utcfromtimestamp(t).date().isoformat(), e) for t, e in zip(h.get("timestamp") or [], h.get("equity") or []) if e]
print("account by day (% change):", [(d, f"{(e / p - 1) * 100:+.2f}%") for (d, e), (_, p) in zip(pts[1:], pts[:-1])])
print(f"account today so far: {(eq / last - 1) * 100:+.2f}%")
s = requests.get("https://data.alpaca.markets/v2/stocks/snapshots", headers=H, params={"symbols": "SPY,OEF,RSP,IWM,QQQ", "feed": "iex"}, timeout=30).json()
for k, v in s.items():
    print(f"{k}: today {(v['latestTrade']['p'] / v['prevDailyBar']['c'] - 1) * 100:+.2f}%  yesterday {(v['prevDailyBar']['c'] / v['prevDailyBar']['o'] - 1) * 100:+.2f}% (open to close)")
pos = requests.get(f"{B}/v2/positions", headers=H, timeout=30).json()
mv = sum(abs(float(p["market_value"])) for p in pos)
rows = sorted(((p["symbol"], float(p["change_today"]) * 100, abs(float(p["market_value"])) / eq * 100) for p in pos), key=lambda x: x[1])
print(f"positions: {len(pos)}, invested {mv / eq * 100:.0f}% of the account")
print("worst today:", [(t, f"{c:+.1f}%", f"{w:.1f}% of acct") for t, c, w in rows[:8]])
print("best today:", [(t, f"{c:+.1f}%", f"{w:.1f}% of acct") for t, c, w in rows[-5:][::-1]])
