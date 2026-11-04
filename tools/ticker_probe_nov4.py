"""Nov 4 (read-only): why one stock shows as 'manual' and whether it's on Today. For the ticker in PROBE_T
(default FMAO) prints: its phone alerts, its paper-account orders (client id prefix = who placed it), the
manual-rules state, and every place it appears in dashboard_data.json (the section path, not the whole row)."""
import os, sys, json, requests
sys.path.insert(0, ".")
import cloud_store

T = (os.environ.get("PROBE_T") or "FMAO").upper()

print("== alerts")
raw = cloud_store.get("push/log.json", log=print)
for x in (json.loads(raw) if raw else []):
    s = json.dumps(x)
    if T in s:
        print("ALERT", json.dumps({k: str(v)[:300] for k, v in x.items() if k in ("at", "title", "body", "tag", "url", "items")}))

print("== orders")
H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
r = requests.get("https://paper-api.alpaca.markets/v2/orders", headers=H, timeout=30,
                 params={"status": "all", "limit": 500, "direction": "desc", "symbols": T, "after": "2026-09-01T00:00:00Z"})
for o in r.json() if r.ok else []:
    print("ORDER", o.get("submitted_at"), o.get("filled_at"), o.get("side"), o.get("type"), o.get("status"),
          "qty", o.get("qty"), "notional", o.get("notional"), "filled", o.get("filled_qty"), o.get("filled_avg_price"),
          "cid", o.get("client_order_id"), "ext", o.get("extended_hours"))
p = requests.get(f"https://paper-api.alpaca.markets/v2/positions/{T}", headers=H, timeout=30)
print("POSITION", p.status_code, {k: p.json().get(k) for k in ("qty", "avg_entry_price", "market_value")} if p.ok else "")

print("== manual rules state")
for key in ("manual/state.json", "manual/keep.json"):
    b = cloud_store.get(key, log=lambda m: None)
    if b:
        s = json.loads(b)
        print(key, json.dumps(s.get(T) if isinstance(s, dict) and T in s else {k: v for k, v in (s.items() if isinstance(s, dict) else []) if T in json.dumps(v)})[:800])

print("== tap requests / reasons")
for k in sorted(cloud_store.list_keys("push/why/", log=lambda m: None))[-40:]:
    b = (cloud_store.get(k, log=lambda m: None) or b"").decode("utf-8", "replace")
    if T in b:
        print(k, b[:300])
st = json.loads(cloud_store.get("manual/state.json", log=lambda m: None) or b"{}")
print("STATE keys", list(st)[:20], "| ruled has T:", T in (st.get("ruled") or []), "| pending:", (st.get("pending") or {}).get(T))

print("== dashboard_data.json")
d = json.loads(cloud_store.get("dashboard_data.json", log=print) or b"{}")
print("generated", d.get("generated") or d.get("updated") or d.get("asof"))
def walk(x, path):
    if isinstance(x, dict):
        if x.get("t") == T or x.get("ticker") == T:
            keep = {k: v for k, v in x.items() if not isinstance(v, (list, dict))}
            print("AT", path, json.dumps(keep)[:600])
            return
        for k, v in x.items():
            walk(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            if v == T:
                print("AT", f"{path}[{i}]", "(bare ticker)")
            walk(v, f"{path}[{i}]")
walk(d, "data")
