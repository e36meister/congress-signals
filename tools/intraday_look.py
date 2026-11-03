"""Read-only: one stock's 15-minute bars (with pre- and after-hours) between two times, from Alpaca's data API.
Usage: intraday_look.py NVDA 2026-10-29T13:00:00-04:00 2026-10-30T20:00:00-04:00"""
import os, sys, requests

t, start, end = sys.argv[1].upper(), sys.argv[2], sys.argv[3]
h = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""),
     "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
url = f"https://data.alpaca.markets/v2/stocks/{t}/bars"
p = {"timeframe": "15Min", "start": start, "end": end, "limit": 10000, "feed": "iex", "adjustment": "raw"}
r = requests.get(url, headers=h, params=p, timeout=60)
print("status", r.status_code)
bars = (r.json() or {}).get("bars") or []
print(f"{len(bars)} bars")
for b in bars:
    print(b["t"], f"o {b['o']:.2f} h {b['h']:.2f} l {b['l']:.2f} c {b['c']:.2f} v {b['v']}")
