"""Read-only: shape of Alpaca's corporate-actions data (public market data only)."""
import os, json, requests
h = {"APCA-API-KEY-ID": os.environ["ALPACA_PAPER_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["ALPACA_PAPER_SECRET_KEY"]}
syms = "AAPL,MSFT,CVX,XOM,JPM,KO,PG,NVDA,BRK.B,T,VZ,PFE,O,MO,ABBV"
r = requests.get("https://data.alpaca.markets/v1/corporate-actions", headers=h, timeout=60,
                 params={"symbols": syms, "types": "cash_dividend,forward_split,reverse_split", "start": "2026-06-01",
                         "limit": 1000})
print(r.status_code)
js = r.json()
print("keys:", list(js.keys()))
ca = js.get("corporate_actions") or {}
for k, v in ca.items():
    print(k, len(v), json.dumps(v[:2])[:600])
print("next:", js.get("next_page_token"))
big = ",".join(["AAPL"] * 1) + "," + ",".join(f"T{i}" for i in range(600))
r2 = requests.get("https://data.alpaca.markets/v1/corporate-actions", headers=h, timeout=60,
                  params={"symbols": big, "types": "cash_dividend", "start": "2026-09-01", "limit": 1000})
print("600 symbols:", r2.status_code, r2.text[:200])
