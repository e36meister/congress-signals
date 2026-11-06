"""Read-only (owner, Nov 6): latest price and moves for a few tickers vs SPY: today, since Nov 4 close, and since a
given buy price/date. Prices and percentages only."""
import os, requests
H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
T = (os.environ.get("PROBE_TICKERS") or "APP,VZ,FITB,MSFT,SPY").split(",")
s = requests.get("https://data.alpaca.markets/v2/stocks/snapshots", headers=H, params={"symbols": ",".join(T), "feed": "iex"}, timeout=30).json()
def open_on(sym, day):
    b = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/bars", headers=H, timeout=30,
                     params={"timeframe": "1Day", "start": day, "end": day, "feed": "sip", "adjustment": "all"}).json().get("bars") or []
    return b[0]["o"] if b else None
buy = {"APP": "2026-10-14", "VZ": "2026-10-14", "FITB": "2026-10-19", "MSFT": "2026-10-14"}
so = open_on("SPY", "2026-10-14")
for t in T:
    v = s.get(t) or {}
    p = v.get("latestTrade", {}).get("p")
    pc = v.get("prevDailyBar", {}).get("c")
    line = f"{t}: now {p}  today {(p / pc - 1) * 100:+.2f}% (prev close {pc})"
    if t in buy:
        o = open_on(t, buy[t])
        sp = open_on("SPY", buy[t])
        spy_now = s["SPY"]["latestTrade"]["p"]
        line += f" | since {buy[t]} open {o}: {(p / o - 1) * 100:+.1f}% vs SPY {(spy_now / sp - 1) * 100:+.1f}%"
    print(line, "| at", v.get("latestTrade", {}).get("t"))
