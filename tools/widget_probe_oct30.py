"""Read-only (Oct 30): why the close report says the account is about -48% since start. Prints the stored report,
the cash transfers, the S&P parking fills and the re-count. Changes nothing."""
import sys, json
sys.path.insert(0, ".")
import broker, cloud_store, close_report as C
s = broker.settings(60); api = broker.Alpaca(s)
raw = cloud_store.get("dashboard_data.json") if hasattr(cloud_store, "get") else None
d = json.loads(raw) if raw else {}
cr = d.get("close_report") or {}
print("report", {k: cr.get(k) for k in ("date", "equity", "since_start", "spy_since_start", "start", "park_adjust",
      "park_adjust_error", "counted_equity", "shadow_spy", "vs_spy_dollars", "put_in")})
se = cr.get("series") or {}
for row in list(zip(se.get("dates", []), se.get("account", []), se.get("spy", [])))[:6] + [("...",)] + list(zip(se.get("dates", []), se.get("account", []), se.get("spy", [])))[-6:]:
    print("series", row)
print("history", [(h.get("date"), h.get("equity"), h.get("since_start")) for h in (d.get("close_history") or [])[:5]])
start = cr.get("start") or "2026-08-01"
for kind in ("CSD", "CSW", "JNLC", "JNLS", "TRANS"):
    try:
        acts = api.get(f"/v2/account/activities/{kind}", direction="asc", page_size=100)
        print(kind, [(a.get("date") or a.get("transaction_time"), a.get("net_amount"), a.get("description")) for a in acts][:20])
    except Exception as e:
        print(kind, "err", e)
print("flows used", C._cash_flows(api, start))
print("park fills", C._park_fills(api))
h = api.get("/v2/account/portfolio/history", period="3M", timeframe="1D")
print("alpaca equity", list(zip(h.get("timestamp", [])[:3], h.get("equity", [])[:3])), "... last", h.get("equity", [])[-3:])
