"""Read-only: a stock's position and its orders in the last 5 days (any status), straight from Alpaca."""
import sys, datetime as dt
sys.path.insert(0, ".")
import broker
T = (sys.argv[1] if len(sys.argv) > 1 else "ACN").upper()
api = broker.Alpaca(broker.settings(60))
try:
    p = api.get(f"/v2/positions/{broker.to_alpaca(T)}")
    print("position", p.get("qty"), "available", p.get("qty_available"), "price", p.get("current_price"), "pl", p.get("unrealized_pl"))
except Exception as e:
    print("position: none", type(e).__name__)
after = (dt.datetime.utcnow() - dt.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
for o in api.get("/v2/orders", status="all", limit=100, direction="desc", after=after, symbols=broker.to_alpaca(T)):
    print("order", o["side"], o.get("qty"), o.get("notional"), o["status"], "filled", o.get("filled_qty"), "@", o.get("filled_avg_price"),
          o.get("client_order_id"), "submitted", o.get("submitted_at"), "filled_at", o.get("filled_at"), "canceled", o.get("canceled_at"))
c = api.get("/v2/clock"); print("market open", c.get("is_open"), "next open", c.get("next_open"))
