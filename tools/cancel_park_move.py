"""Owner, Oct 22: cancel the queued sale that would move parked SPY to SGOV at a loss (the move now waits until the
fund is back to its cost). Cancels only open orders whose id starts with cs-park-move-; touches nothing else."""
import sys, requests
sys.path.insert(0, ".")
import broker
s = broker.settings(60)
api = broker.Alpaca(s)
for o in api.get("/v2/orders", status="open", limit=200):
    if (o.get("client_order_id") or "").startswith(broker.PARK_PREFIX + "move-"):
        r = api.delete(f"/v2/orders/{o['id']}")
        print("cancel", o["symbol"], o["side"], o.get("qty"), o["client_order_id"], "->", r.status_code)
left = [o for o in api.get("/v2/orders", status="open", limit=200) if (o.get("client_order_id") or "").startswith(broker.PARK_PREFIX + "move-")]
print("parking move orders still open:", len(left))
