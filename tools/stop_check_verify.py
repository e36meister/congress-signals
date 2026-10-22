"""Cross-check stop_check.py against the reasons the daily runs saved for each sale (R2 push/why/*.json).
Prints counts only (the run log is public)."""
import os, sys, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import broker, stop_check, cloud_store

tok, acc = os.environ["CLOUDFLARE_API_TOKEN"], os.environ["CLOUDFLARE_ACCOUNT_ID"]
base = f"https://api.cloudflare.com/client/v4/accounts/{acc}/r2/buckets/{cloud_store.BUCKET}/objects"
keys, cursor = [], None
for _ in range(50):
    p = {"prefix": "push/why/", "per_page": 1000}
    if cursor:
        p["cursor"] = cursor
    r = requests.get(base, headers={"Authorization": f"Bearer {tok}"}, params=p, timeout=60)
    js = r.json()
    if r.status_code != 200:
        print("list failed", r.status_code, str(js)[:200]); break
    keys += [o["key"] for o in js.get("result") or []]
    cursor = (js.get("result_info") or {}).get("cursor")
    if not cursor or not js.get("result_info", {}).get("is_truncated"):
        break
print("reason files:", len(keys), "oldest:", min(keys)[9:17] if keys else None)
stops = set()
for k in keys:
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        d = json.loads(raw)
    except Exception:
        continue
    day = f"{k[9:13]}-{k[13:15]}-{k[15:17]}"
    for sk, why in d.items():
        if sk.endswith("|sell") and why.startswith("fell "):
            stops.add((sk.split("|")[0], day))
print("stop sales in reason files:", len(stops))
s = broker.settings(60)
sc = stop_check.check(broker.Alpaca(s), "stop15", print)
rows = [(r["t"], r["sold"]) for r in sc["rows"]]
print("stop sales found from orders:", len(rows))
near = lambda a, b: abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days) <= 4
both = [x for x in rows if any(x[0] == y[0] and near(x[1], y[1]) for y in stops)]
print("found in both:", len(both))
print("found from orders but no stop reason saved:", len(rows) - len(both))
print("stop reason saved but not found from orders:", sum(1 for y in stops if not any(x[0] == y[0] and near(x[1], y[1]) for x in rows)))
ok = all(r["sell_px"] < r["buy_px"] for r in sc["rows"])
print("every row sold below its buy price:", ok, "| rows with prices filled:", sum(1 for r in sc["rows"] if r["end_px"] and r["sell_px"]))

# why a saved stop reason wasn't matched (no tickers or amounts printed)
orders = stop_check._orders(broker.Alpaca(s))
lots = stop_check._lots(orders)
for sym, day in stops:
    if any(x[0] == sym and near(x[1], day) for x in rows):
        continue
    os_ = [o for o in orders if o["symbol"].replace(".", "-") == sym]
    kinds = sorted({(o["side"], (o.get("client_order_id") or "")[:6]) for o in os_})
    ls = [l for l in lots if l["sym"].replace(".", "-") == sym]
    print("missed: reason day", day, "| order kinds", kinds, "| lots", len(ls),
          [{"bought": l["bought"], "sold": l["sold"], "ratio": round(l.get("sell_px", 0) / l["buy_px"], 3) if l.get("sell_px") else None,
            "hold": l["hold"], "fully_sold": l["sold_q"] >= l["qty"] * 0.999} for l in ls],
          "| sell fills", [(o["filled_at"][:10], o["status"]) for o in os_ if o["side"] == "sell"])
