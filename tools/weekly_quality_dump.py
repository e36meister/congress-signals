"""Read-only input for the weekly quality report (owner, Nov 5 2026). Prints, from the app's Cloudflare data:
- suggestions the weekly check / stop check made but did NOT apply (waiting for the owner's OK)
- the weekly check's own result table (best options it tested) and when it last ran
- the live test (70% S&P 100 fund + 30% picks), the account alone and the S&P since Nov 5
- the tool's buys by strategy version, and the version log
- this week's tool buys and sells (tickers only), the backtest headline
Percentages and tickers only: no dollar balances (the run log may be public)."""
import sys, json, datetime as dt
sys.path.insert(0, ".")
import cloud_store

pc = lambda v, k=100: "–" if v is None else f"{float(v) * k:+.2f}%"
d = cloud_store.read_dashboard()
sugg = json.loads(cloud_store.get("strategy/suggestions.json", log=print) or b"[]")
vers = json.loads(cloud_store.get("strategy/versions.json", log=print) or b"[]")
a = d.get("adaptive") or {}
week_ago = (dt.date.today() - dt.timedelta(days=7)).isoformat()

print("== SUGGESTIONS (pending, not applied)")
for x in [s for s in sugg if s.get("status") == "pending"]:
    print(f"  [{x.get('date')}] {x.get('source')}: {x.get('setting')} ({x.get('key')}): {x.get('from')} -> {x.get('to')} | "
          f"gain {pc(x.get('gain_per_year'))}/yr vs current, sureness {x.get('sureness')} | note: {str(x.get('note') or '')[:200]}")
if not any(s.get("status") == "pending" for s in sugg):
    print("  none")

print(f"\n== WEEKLY CHECK (last ran {a.get('evaluated_at')})")
for r in (a.get("evaluation") or [])[:40]:
    print("  ", {k: r.get(k) for k in ("Setting", "Option", "Per year vs S&P", "Gain vs current", "Sureness", "Verdict")})
print("  history this week:", [h for h in a.get("history") or [] if str(h.get("date", "")) >= week_ago])

c = d.get("close_report") or {}
m = c.get("sp100_mix") or {}
print(f"\n== LIVE TEST since {m.get('start')} ({m.get('trading_days')} trading days); close report {c.get('date')}")
print(f"  70% S&P 100 + 30% picks {pc(m.get('mix'), 1)} | account alone {pc(m.get('account'), 1)} | S&P {pc(m.get('spy'), 1)}"
      f" | error: {c.get('sp100_mix_error')}")
print(f"  whole paper account since {c.get('start')}: {pc(c.get('since_start'), 1)} vs S&P {pc(c.get('spy_since_start'), 1)}")

print("\n== BY STRATEGY VERSION (tool's buys, vs the same money in the S&P)")
for v in c.get("versions") or []:
    print(f"  {v.get('label')}: {v.get('trades')} buys ({v.get('open')} open), return {pc(v.get('ret'), 1)}, S&P {pc(v.get('spy'), 1)}, "
          f"difference {pc(v.get('diff'), 1)}")
print("  error:", c.get("versions_error"))
print("  version log:", [{k: x.get(k) for k in ("v", "start", "by", "what")} for x in vers])

p = d.get("portfolio") or {}
wk = [o for o in p.get("orders") or [] if str(o.get("at", ""))[:10] >= week_ago and o.get("status") in ("filled", "partially_filled")]
print("\n== THIS WEEK'S TRADES (tickers)")
print("  tool buys:", sorted({o["t"] for o in wk if o.get("by") == "tool" and o.get("side") == "buy" and o["t"] != "SGOV"}))
print("  tool sells:", sorted({o["t"] for o in wk if o.get("by") == "tool" and o.get("side") == "sell" and o["t"] != "SGOV"}))
print("  your buys/sells:", sorted({(o["t"], o["side"]) for o in wk if o.get("by") in ("you", "tap")}))
print("  open positions:", len([x for x in p.get("positions") or [] if x.get("t") != "SGOV"]))
for sl in c.get("sleeves") or []:
    print(f"  {sl.get('name')}: {sl.get('positions')} open, since bought {pc(sl.get('ret'))} vs S&P {pc(sl.get('spy'))}")

print("\n== BACKTEST HEADLINE (per year vs the S&P)")
for sec in ("walk_forward", "backtest", "small"):
    for r in (d.get(sec) or {}).get("perf") or []:
        if r.get("Per year vs S&P 500") is not None:
            print(f"  [{sec}] {r.get('name')}: {pc(r.get('Per year vs S&P 500'))} (likely {pc(r.get('Likely range low'))} to "
                  f"{pc(r.get('Likely range high'))})")
