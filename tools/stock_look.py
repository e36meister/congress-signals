"""Read-only: everything on file about one stock for the owner (default ETN): the owner's fills and position,
member trades of it, analyst rating changes and price targets around the buy, earnings dates, recent price moves,
and recent headlines. Usage via research.yml: script = "stock_look.py" (ticker from STOCK env or default)."""
import sys, os, json, datetime as dt
sys.path.insert(0, ".")
import cloud_store
T = os.environ.get("STOCK", "ETN").upper()

def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try: return json.loads(raw) if raw else None
    except Exception: return None

d = r2("dashboard_data.json") or {}
P = d.get("portfolio") or {}
print(f"=== {T}: your account")
fills = (P.get("fills") or {}).get(T) or []
for f in fills: print("  fill", f)
for o in P.get("orders") or []:
    if o.get("t") == T: print("  order", {k: o.get(k) for k in ("at", "side", "qty", "filled_px", "status", "by")})
for x in P.get("positions") or []:
    if x.get("t") == T: print("  position", {k: x.get(k) for k in ("qty", "avg", "px", "mv", "pl", "plpc", "bought", "held", "bot")})
first_buy = min([f[0] for f in fills if f[1] == "buy"] or [None]) if fills else None
print("  first buy:", first_buy)
ms = (d.get("member_sold") or {}).get(T)
if ms: print("  member_sold:", ms)
w = d.get("watchlist") or {}
for r in (w.get("buys") or []) + (w.get("sells") or []):
    if r.get("t") == T: print("  watchlist row:", json.dumps(r, default=str)[:1500])
for r in d.get("worth") or []:
    if r.get("t") == T: print("  worth a look:", json.dumps(r, default=str)[:800])

ch = r2("ticker_charts.json") or {}
tr = (ch.get("trades") or {}).get(T) or []
print(f"\n=== member trades of {T} on file: {len(tr)}")
for x in sorted(tr, key=lambda x: x.get("td") or "")[-25:]:
    print("  ", {k: x.get(k) for k in ("n", "ty", "td", "fd", "amt", "px", "sg") if x.get(k) is not None})

print(f"\n=== analyst ratings (saved history)")
try:
    ah = json.load(open("data/cache/analyst_history.json")).get(T) or {}
    for r in sorted(ah.get("rows") or [])[-40:]: print("  ", r)
    print("  fetched", ah.get("fetched"))
except Exception as e:
    print("  saved history not available:", type(e).__name__)

import yfinance as yf
tk = yf.Ticker(T)
print("\n=== Yahoo (live)")
try:
    ud = tk.upgrades_downgrades
    if ud is not None and len(ud):
        ud = ud.reset_index(); print(ud.sort_values(ud.columns[0]).tail(30).to_string())
except Exception as e: print("  upgrades/downgrades:", type(e).__name__, e)
for name in ("recommendations", "analyst_price_targets", "earnings_dates", "calendar"):
    try:
        v = getattr(tk, name)
        print(f"\n--- {name}\n", v.head(12).to_string() if hasattr(v, "head") else v)
    except Exception as e: print(f"--- {name}: {type(e).__name__} {e}")
try:
    h = tk.history(period="4mo")[["Close", "Volume"]]
    print("\n--- daily closes (last 4 months)\n", h.tail(70).to_string())
    spy = yf.Ticker("SPY").history(period="4mo")["Close"]
    print("\n--- SPY closes\n", spy.tail(70).to_string())
except Exception as e: print("history:", type(e).__name__, e)
try:
    for n in (tk.news or [])[:15]:
        c = n.get("content") or n
        print("  news:", c.get("pubDate") or c.get("providerPublishTime"), "|", c.get("title"), "|", (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher"))
except Exception as e: print("news:", type(e).__name__, e)
