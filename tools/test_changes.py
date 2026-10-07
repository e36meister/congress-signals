"""Read-only check of the Oct 7 changes on the saved data (no trading, nothing saved back). Prints aggregates only."""
import sys, os, time, json, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", ""), "TIME_BUDGET_MIN": 0}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
px = px[[c for c in px.columns if c in set(sc["ticker"]) | set(E.BENCHMARK_ETFS) | {"SPY"}]]
print("scored", sc.shape, "prices", px.shape, px.index.max().date())

# 1. without opening prices, the new forward_returns must equal the old close-to-close numbers
pol = E.load_adaptive(cfg)["policy"]
b = sc[sc["tx_type"] == "buy"].head(5000)
fr = E.forward_returns(b, px, int(pol["hold_other"]))
same = np.nanmax(np.abs(fr["entry_px"].values - b["entry_px"].values)) if len(b) else 0
print(f"1. close-based entry prices unchanged: max diff {same:.6f}")

# 2. live picks == the backtest's picks for past days
bt_picks, _ = E._select(sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()], "score", cfg["PICK_PERCENTILE"],
                        cfg["PICKS_PER_WEEK"], per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"),
                        skip_late=cfg.get("SKIP_LATE_FILINGS"), net_sell=cfg.get("NET_SELL_FILTER"))
ok = bad = 0
for d in pd.bdate_range("2025-06-02", "2025-09-30", freq="7B"):
    lp = E.live_picks(sc, px, cfg, grace_days=0, today=d)
    exp = set(bt_picks.loc[pd.to_datetime(bt_picks["entry_date"]) >= d, "ticker"])
    got = set(lp["ticker"])
    ok += got == exp
    bad += got != exp
print(f"2. live picks match the backtest's picks on {ok} of {ok + bad} sample days")
lp = E.live_picks(sc, px, cfg)
sf = E.fresh_small_rows(sc, cfg)
print(f"   today: {len(lp)} fresh main pick(s) {list(lp['ticker'])}, {len(sf)} fresh small-company buy(s)")
w = sc[(sc["tx_type"] == "buy") & (sc["filed_date"] >= pd.Timestamp.today() - pd.Timedelta(days=30))]
print(f"   (filings in the last 30 days: {len(w)})")

# 3. company size at the time from SEC share counts
t0 = time.time()
shares = E.collect_share_counts(cfg)
print(f"3. share counts: {len(shares)} companies in {time.time() - t0:.0f}s")

# 4. opening prices + dividends/splits for every ticker (same download the full update will do)
t0 = time.time()
E._OPEN_BUF.clear(); E._ACT_BUF.clear()
start = (pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
cols = list(px.columns)
for i in range(0, len(cols), 100):
    E._yf_close(cols[i:i + 100], start, tries=2)
    time.sleep(1)
E._save_opens(cfg, px)
E._save_actions(cfg)
print(f"4. opens and dividends/splits downloaded in {time.time() - t0:.0f}s")
acts = E.load_actions(cfg)

# 5. company size: old (today's shares) vs new (shares then)
data = {"meta": json.load(open("data/cache/ticker_meta.json")) if os.path.exists("data/cache/ticker_meta.json") else {},
        "shares": shares, "actions": acts}
meta = data["meta"]
sub = sc[(sc["tx_type"] == "buy")].copy()
E._CAP_CACHE.clear()
good = {}
for t in set(sub["ticker"]):
    if t in shares and t in px.columns:
        c_sec = E.cap_at_time(t, px.index[-1], px[t], shares, acts, lag_days=0)
        c_y = (meta.get(t) or {}).get("cap")
        good[t] = c_sec is not None and (not c_y or 1 / 3 < c_sec / float(c_y) < 3)
new = np.array([E.cap_at_time(t, d, px[t], shares, acts) if good.get(t) else np.nan for t, d in zip(sub["ticker"], sub["filed_date"])])
old = sub["market_cap"].values
m = np.isfinite(new) & np.isfinite(old)
print(f"5. size from SEC shares then: {np.isfinite(new).mean() * 100:.0f}% of purchases; matched companies {sum(good.values())}")
r = new[m] / old[m]
print(f"   new/old ratio: median {np.median(r):.3f}, 10th-90th pct {np.percentile(r, 10):.2f}-{np.percentile(r, 90):.2f}")
so, sn = old < 2e9, np.where(np.isfinite(new), new < 2e9, old < 2e9)
print(f"   'under $2B' purchases: old {np.nansum(so)}, new {np.nansum(sn)}; changed class {int(np.sum(so != sn))}")

# 6. headline backtests: old timing (closes) vs new (opens), and the small-company portfolio with the new sizes
def headline(scored, label):
    bt = E.run_backtest(scored, px, cfg)
    p = bt["perf"] if isinstance(bt, dict) else None
    lp_ = p["Long picks"] if p is not None else None
    cp = p["Copy every purchase"] if p is not None else None
    print(f"   {label}: main picks {lp_['Per year vs S&P 500'] * 100:+.2f}%/yr vs S&P "
          f"(range {lp_['Likely range low'] * 100:+.1f} to {lp_['Likely range high'] * 100:+.1f}); "
          f"copy every purchase {cp['Per year vs S&P 500'] * 100:+.2f}%/yr")
print("6. backtests")
E._OPEN_REG.clear()
sc_close = E.apply_hold_policy(sc, px, cfg, pol)
headline(sc_close, "closes (old timing)")
E.register_opens(cfg, px)
sc_open = E.apply_hold_policy(sc, px, cfg, pol)
headline(sc_open, "opens (new timing)")
s2 = sc_open.copy()
nm = pd.Series(np.nan, index=s2.index)
nm.loc[sub.index] = np.where(np.isfinite(new), new, old)
s2.loc[nm.notna(), "market_cap"] = nm[nm.notna()]
s2["f_small_cap"] = np.where(s2["market_cap"].isna(), 0.0, np.where(s2["market_cap"] < 2e9, 1.0, np.where(s2["market_cap"] < 1e10, 0.5, 0.0)))
for lab, s_ in (("old sizes", sc_open), ("SEC sizes then", s2)):
    r_ = E.small_cap_backtest(s_, px, cfg)
    if r_:
        p = r_["perf"]["Small-company picks"]
        print(f"   small-company portfolio, {lab}: {p['Per year vs S&P 500'] * 100:+.2f}%/yr vs S&P "
              f"(range {p['Likely range low'] * 100:+.1f} to {p['Likely range high'] * 100:+.1f}), {len(r_['picks'])} picks")

# 7. portfolio level: the hold-longer options, earlier years vs the last 3 (what the weekly check will weigh)
print("7. hold-longer options (main picks, opens timing, current settings otherwise)")
idx_cut = px.index[-1] - pd.Timedelta(days=3 * 365)
for ext in ("none", "ride10", "ahead", "ahead_daily"):
    p2 = dict(pol, extend=ext)
    s_ = E.apply_hold_policy(sc, px, cfg, p2)
    bt = E.run_backtest(s_, px, cfg)
    d = bt["curves"]["Long picks"].pct_change().fillna(0)
    spy = bt["curves"]["S&P 500 (SPY)"].pct_change().fillna(0)
    def ann(m):
        x = (d[m] - spy[m])
        return x.mean() * 252 * 100
    early, late = d.index < idx_cut, d.index >= idx_cut
    print(f"   {ext:12s}: all {ann(d.index == d.index):+.2f}%/yr vs S&P | before the last 3 yrs {ann(early):+.2f} | last 3 yrs {ann(late):+.2f}")
