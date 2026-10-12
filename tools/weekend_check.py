"""Read-only: does Alpaca's 'today' change on a weekend equal Friday's change? Prints only percent changes."""
import os, requests
h = {"APCA-API-KEY-ID": os.environ["ALPACA_PAPER_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["ALPACA_PAPER_SECRET_KEY"]}
b = "https://paper-api.alpaca.markets"
a = requests.get(b + "/v2/account", headers=h, timeout=30).json()
c = requests.get(b + "/v2/clock", headers=h, timeout=30).json()
eq, le = float(a["equity"]), float(a["last_equity"])
print("clock:", c.get("timestamp"), "open:", c.get("is_open"), "next_open:", c.get("next_open"))
print(f"equity vs last_equity: {(eq / le - 1) * 100:+.3f}%")
ph = requests.get(b + "/v2/account/portfolio/history", headers=h, params={"period": "1W", "timeframe": "1D"}, timeout=30).json()
from datetime import datetime, timezone
for t, e in zip(ph.get("timestamp", []), ph.get("equity", [])):
    print(datetime.fromtimestamp(t, timezone.utc).date(), "rel to now:", f"{(e / eq - 1) * 100:+.3f}%" if e else None)
