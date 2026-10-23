"""One-off: the stop sale that filled at -12.9% (Oct 19). Prints ticker, prices and timing only (no shares or dollars)."""
import os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import broker, stop_check
api = broker.Alpaca(broker.settings(60))
orders = stop_check._orders(api)
for l in stop_check._lots(orders):
    if l["sold"] == "2026-10-19" and l.get("sell_px") and 0.86 < l["sell_px"] / l["buy_px"] < 0.88:
        s = l["sym"]
        sells = [o for o in orders if o["symbol"] == s and o["side"] == "sell"]
        print("TICKER", s, "| bought", l["bought"], round(l["buy_px"], 2), "| stop line", round(l["buy_px"] * 0.85, 2))
        for o in sells:
            print("SELL submitted", o["submitted_at"], "filled", o["filled_at"], "at", o["filled_avg_price"], "type", o.get("type"))
        for feed in ("sip", "iex"):
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=api.h, timeout=30,
                             params={"symbols": s, "timeframe": "1Day", "start": "2026-10-14", "end": "2026-10-21", "adjustment": "raw", "feed": feed})
            if r.status_code == 200:
                for b in (r.json().get("bars") or {}).get(s, []):
                    print("BAR", b["t"][:10], "open", b["o"], "high", b["h"], "low", b["l"], "close", b["c"])
                break
        r = requests.get("https://data.alpaca.markets/v1/corporate-actions", headers=api.h, timeout=20,
                         params={"symbols": s, "types": "cash_dividend", "start": l["bought"], "limit": 50})
        print("DIVIDENDS", [(d.get("ex_date"), d.get("rate")) for d in ((r.json().get("corporate_actions") or {}).get("cash_dividends") or [])] if r.status_code == 200 else r.status_code)
