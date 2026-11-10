"""Read-only: paper account at a glance for a status check in chat. Percentages and tickers only, no dollar amounts
(the run log is public). Last saved close report (account vs S&P, 70/30 S&P 100 mix, per version), open positions'
gain %, today's orders, and today's Buy list tickers. Nothing is placed, cancelled or saved."""
import sys, json, datetime as dt
sys.path.insert(0, ".")
import broker, cloud_store


def r2(k):
    raw = cloud_store.get(k, log=lambda m: None)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def pct(x):
    return "n/a" if x is None else f"{x:+.2f}%"


d = r2("dashboard_data.json") or {}
cr = d.get("close_report") or {}
print("== Last close report", cr.get("date"), "| start", cr.get("start"))
print("account since start", pct(cr.get("since_start")), "| S&P same flows", pct(cr.get("spy_since_start")))
m = cr.get("sp100_mix") or {}
if m:
    print(f"70/30 mix since {m.get('start')}: mix {pct(m.get('mix'))} | account {pct(m.get('account'))} | "
          f"S&P {pct(m.get('spy'))} | {m.get('trading_days')} trading days")
if cr.get("sp100_mix_error"):
    print("mix error:", cr["sp100_mix_error"])
for v in cr.get("versions") or []:
    if isinstance(v, dict):
        print("version", {k: v.get(k) for k in v if k not in ("put_in", "value", "cost", "dollars", "trades")})
for s in cr.get("sleeves") or []:
    f = lambda x: None if x is None else x * 100          # sleeves are saved as fractions
    print("sleeve", s.get("name"), s.get("positions"), "pos | ret", pct(f(s.get("ret"))), "| S&P", pct(f(s.get("spy"))),
          "| avg days", s.get("avg_days"))

api = broker.Alpaca(broker.settings(60))
print("== Open positions (gain % since bought; today %)")
for p in sorted(api.get("/v2/positions"), key=lambda p: -float(p.get("unrealized_plpc") or 0)):
    print(f"{p['symbol']:6} {float(p.get('unrealized_plpc') or 0) * 100:+7.2f}%  today {float(p.get('change_today') or 0) * 100:+6.2f}%")
a = api.get("/v2/account")
eq, last = float(a["equity"]), float(a["last_equity"])
print("account today", pct((eq / last - 1) * 100 if last else None), "| cash share", f"{float(a['cash']) / eq * 100:.1f}%")
after = dt.date.today().isoformat() + "T00:00:00Z"
print("== Orders today")
for o in api.get("/v2/orders", status="all", limit=100, direction="asc", after=after):
    cid = o.get("client_order_id") or ""
    kind = "tool" if cid.startswith("cs-") else "button" if cid.startswith("tap-") else "other"
    print(o["symbol"], o["side"], o["status"], kind, (o.get("filled_at") or "")[11:16])
buys = [r.get("ticker") or r.get("Ticker") for r in (d.get("buy") or d.get("buys") or []) if isinstance(r, dict)]
print("== Buy list:", ", ".join(t for t in buys if t) or "(none or different key)", "| keys:", ",".join(sorted(d)[:40]))
