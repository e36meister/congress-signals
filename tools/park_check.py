"""Read-only: the tool's parked fund positions and any open parking orders."""
import sys
sys.path.insert(0, ".")
import broker
s = broker.settings(60)
api = broker.Alpaca(s)
print("mode", s["mode"])
for p in api.get("/v2/positions"):
    if p["symbol"] in broker.PARK_ALL:
        print("position", p["symbol"], "qty", p["qty"], "avg", p["avg_entry_price"], "price", p["current_price"], "pl", p["unrealized_pl"])
for o in api.get("/v2/orders", status="open", limit=200):
    if o["symbol"] in broker.PARK_ALL or (o.get("client_order_id") or "").startswith(broker.PARK_PREFIX):
        print("open order", o["id"], o["symbol"], o["side"], o.get("qty"), o.get("notional"), o["status"], o.get("client_order_id"), o.get("submitted_at"))
for o in api.get("/v2/orders", status="closed", limit=30, direction="desc"):
    if (o.get("client_order_id") or "").startswith(broker.PARK_PREFIX):
        print("recent", o["symbol"], o["side"], o.get("filled_qty"), o.get("filled_avg_price"), o["status"], o.get("client_order_id"), o.get("filled_at"))
