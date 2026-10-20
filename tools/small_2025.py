"""Read-only: when in 2025 the small-company picks pulled ahead of the S&P 500 (from the app's data)."""
import sys, json
sys.path.insert(0, ".")
import cloud_store
import pandas as pd
d = json.loads(cloud_store.get("dashboard_data.json", print) or b"{}")
sm = d.get("small") or {}
c = sm.get("curve") or {}
print("series:", list((c.get("series") or {}).keys()))
df = pd.DataFrame(c["series"], index=pd.to_datetime(c["dates"]))
pk, sp = "Small-company picks", "S&P 500 (SPY)"
g = (1 + df / 100)            # growth of $1 (series are cumulative % gains)
y = g[(g.index >= "2024-12-31") & (g.index <= "2025-12-31")]
rel = (y[pk] / y[pk].iloc[0]) / (y[sp] / y[sp].iloc[0]) - 1
print("2025: picks", f"{y[pk].iloc[-1] / y[pk].iloc[0] - 1:+.1%}", "S&P", f"{y[sp].iloc[-1] / y[sp].iloc[0] - 1:+.1%}")
m = rel.resample("ME").last()
print("lead over S&P since Jan 1, month end:", {k.strftime("%b"): f"{v:+.1%}" for k, v in m.items()})
# biggest climb in the lead: lowest point before the highest point
hi = rel.idxmax(); lo = rel[:hi].idxmin()
print(f"biggest run: {lo.date()} ({rel[lo]:+.1%}) -> {hi.date()} ({rel[hi]:+.1%}); gain in lead {(1 + rel[hi]) / (1 + rel[lo]) - 1:+.1%}")
pg = y[pk][lo:hi]; sg = y[sp][lo:hi]
print(f"over that run: picks {pg.iloc[-1] / pg.iloc[0] - 1:+.1%}, S&P {sg.iloc[-1] / sg.iloc[0] - 1:+.1%}")
r30 = (y[pk].pct_change(21) - y[sp].pct_change(21)).dropna()
print("best 21-day stretches (picks minus S&P):", {k.strftime("%b %d"): f"{v:+.1%}" for k, v in r30.nlargest(5).items()})
yrs = sm.get("years") or []
print("year table 2025:", [r for r in yrs if str(r.get("year")) == "2025"])
