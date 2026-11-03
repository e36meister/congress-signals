"""Read-only: orders submitted in the last 12 hours (symbol, side, type, status, id prefix, times). No amounts or
quantities are printed (the run log is public). Nothing is placed or cancelled."""
import sys, datetime as dt
sys.path.insert(0, ".")
import broker
api = broker.Alpaca(broker.settings(60))
after = (dt.datetime.utcnow() - dt.timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
for o in api.get("/v2/orders", status="all", limit=100, direction="desc", after=after):
    cid = o.get("client_order_id") or ""
    kind = "tool" if cid.startswith("cs-") else "button" if cid.startswith("tap-") else "other (yours or a position close)"
    print(o["symbol"], o["side"], o.get("type"), o["status"], kind, cid[:18], "submitted", o.get("submitted_at"),
          "filled", o.get("filled_at"))
c = api.get("/v2/clock"); print("market open", c.get("is_open"), "next open", c.get("next_open"))
