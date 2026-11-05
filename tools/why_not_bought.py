"""Read-only (owner, Nov 5): why a stock on Today's Buy candidates wasn't bought. For each ticker in PROBE_TICKERS
(default MSFT,APP,FITB,VZ) prints: its newest buy filings (filed/entry dates, score, lag, members), the bar it had to
clear on its filing day, where the tool's own pick rule (_select, the live rule) stopped it, how many picks its
filing week had already used, and any paper-account orders/position. Aggregates and tickers only."""
import os, sys, json, requests, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

T = [t.strip().upper() for t in (os.environ.get("PROBE_TICKERS") or "MSFT,APP,FITB,VZ").split(",") if t.strip()]
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
pct, per_week, per_mem = cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"], cfg.get("MAX_PICKS_PER_MEMBER_WEEK")
today = pd.Timestamp(pd.Timestamp.now(tz="America/New_York").date())
BD = pd.offsets.BDay
print(f"rule: top {100 - pct}% of earlier scores, {per_week}/week, {per_mem}/member/week, skip late "
      f"{cfg.get('SKIP_LATE_FILINGS')}, net-sell filter {cfg.get('NET_SELL_FILTER')}; buy window = first entry "
      f"no more than 2 trading days ago (on/after {(today - BD(2)):%Y-%m-%d})")

# the live rule exactly as live_picks runs it, kept whole so the weekly slots and holds are visible
b = sc[sc["tx_type"] == "buy"].copy()
last_px = px.index.max()
newish = b["entry_date"].isna() & (b["filed_date"] >= last_px - pd.Timedelta(days=7))
b.loc[newish, "entry_date"] = b.loc[newish, "filed_date"] + BD(1)
if newish.any():
    hold = E.row_holds(b.loc[newish], E.load_adaptive(cfg)["policy"])
    b.loc[newish, "exit_date"] = [d + BD(int(h)) for d, h in zip(b.loc[newish, "entry_date"], hold)]
b = b[b["entry_px"].notna() | newish]
picks, held = E._select(b, "score", pct, per_week, per_member=per_mem, skip_late=cfg.get("SKIP_LATE_FILINGS"),
                        net_sell=cfg.get("NET_SELL_FILTER"))
picks["week"] = pd.to_datetime(picks["filed_date"]).dt.to_period("W-FRI")
recent_wk = picks[pd.to_datetime(picks["filed_date"]) >= today - pd.Timedelta(days=45)]
print("\nPicks the rule made in the last 45 days (filed, ticker, score, entry):")
for _, r in recent_wk.sort_values("filed_date").iterrows():
    print(f"  {r['filed_date']:%Y-%m-%d} {r['ticker']:6s} {r['score']:.1f} entry {pd.Timestamp(r['entry_date']):%Y-%m-%d}")

allb = b.sort_values("filed_date")
hist_all = sc.loc[sc["tx_type"] == "buy", "score"].values
for t in T:
    print(f"\n===== {t}")
    rows = allb[(allb["ticker"] == t) & (allb["filed_date"] >= today - pd.Timedelta(days=60))]
    if not len(rows):
        print("  no buy filings in the last 60 days in the saved data")
    for _, r in rows.iterrows():
        earlier = allb.loc[allb["filed_date"] < r["filed_date"].normalize(), "score"].values
        bar = np.percentile(earlier, pct) if len(earlier) >= 100 else np.inf
        wk = r["filed_date"].to_period("W-FRI")
        used = picks[(picks["week"] == wk) & (pd.to_datetime(picks["filed_date"]) <= r["filed_date"])]
        mk = r["who"] if "who" in r and isinstance(r["who"], str) else r["member"]
        mem_used = int((used.get("who", used["member"]) == mk).sum()) if len(used) else 0
        why = []
        if r["score"] < bar: why.append(f"score {r['score']:.1f} under that day's bar {bar:.1f}")
        if cfg.get("SKIP_LATE_FILINGS") and (r.get("lag_days") or 0) > 45: why.append(f"filed {r['lag_days']:.0f} days after the trade (late)")
        if len(used) >= per_week: why.append(f"week's {per_week} picks already used ({', '.join(used['ticker'])})")
        if per_mem and mem_used >= per_mem: why.append(f"member already had {per_mem} picks that week")
        h = held.get(t)
        if t in set(picks["ticker"]):
            last_pick = picks[picks["ticker"] == t].iloc[-1]
            why.append(f"rule picked {t} on {last_pick['filed_date']:%Y-%m-%d} (hold to {pd.Timestamp(last_pick['exit_date']):%Y-%m-%d})")
        ent = pd.Timestamp(r["entry_date"]) if pd.notna(r["entry_date"]) else None
        if ent is not None and ent < today - BD(2): why.append(f"first entry {ent:%Y-%m-%d} is past the 2-day buy window")
        print(f"  filed {r['filed_date']:%Y-%m-%d} traded {pd.Timestamp(r['trade_date']):%Y-%m-%d} lag {r.get('lag_days')} "
              f"score {r['score']:.1f} (all-time pct {(hist_all < r['score']).mean() * 100:.0f}) sellers {r.get('n_sellers')} buyers {r.get('n_buyers')}")
        print("    -> " + ("; ".join(why) if why else "nothing in the rule blocks it"))

print("\n== paper account")
H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
r = requests.get("https://paper-api.alpaca.markets/v2/orders", headers=H, timeout=30,
                 params={"status": "all", "limit": 500, "direction": "desc", "symbols": ",".join(T), "after": "2026-08-01T00:00:00Z"})
for o in r.json() if r.ok else []:
    print("ORDER", o["symbol"], o.get("submitted_at", "")[:16], o.get("side"), o.get("status"), "cid", (o.get("client_order_id") or "")[:20])
pos = requests.get("https://paper-api.alpaca.markets/v2/positions", headers=H, timeout=30)
print("HELD", [p["symbol"] for p in pos.json()] if pos.ok else pos.status_code)
