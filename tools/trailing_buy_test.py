"""Read-only pick-then-test (owner, Nov 5): instead of buying a pick at the first open after it's disclosed, wait
while the price falls and buy once it bounces a set amount off its lowest price since the open ("trailing buy").

Picks: the tool's own (main picks by the backtest rule + the small-company portfolio's buys), bought from 2016 on
(minute prices start then), held to the same sell day as now. Prices: Alpaca 1-minute bars (split-adjusted), regular hours only.

Variants (32): bounce 0.5/1/2/3% off the low | max wait 1/2/3/5 trading days (not bounced by then: buy at that
day's close) | "rising: buy now" gate on/off (on: if the first open is at or above the previous close, buy at the
open like today; off: always trail). A bar whose high reaches the trigger fills at the trigger, or at the bar's
open if it opened above it (gaps fill at the open, never better than real).

Judged like every other idea: choose the variant on 2016-2021 only, test it on 2022-now; plus walk-forward (each
year uses the variant that was best on earlier years). Prints aggregates only; writes out/trailing_buy_test.json."""
import os, sys, json, time, requests, numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
pol = E.load_adaptive(cfg)["policy"]
cost = cfg.get("COST_BPS", 0) / 1e4
buys = sc[(sc["tx_type"] == "buy") & sc["entry_px"].notna()]
main, _ = E._select(buys, "score", cfg["PICK_PERCENTILE"], cfg["PICKS_PER_WEEK"],
                    per_member=cfg.get("MAX_PICKS_PER_MEMBER_WEEK"), skip_late=cfg.get("SKIP_LATE_FILINGS"),
                    net_sell=cfg.get("NET_SELL_FILTER"))
small = E.small_cap_rows(sc, bool(pol.get("sleeve_coverage")))
parts = []
for name, rows, hold in (("main", main, int(pol.get("hold_other", 60))), ("small", small, int(pol.get("hold_sleeve", 250)))):
    rows = rows.copy()
    fr = E.forward_returns(rows, px, hold, cost=cost)
    rows = rows.drop(columns=[c for c in fr.columns if c in rows.columns]).join(fr)
    parts.append(rows.assign(src=name, hold=hold))
P = pd.concat(parts)
P = P[P["closed"] & P["ret"].notna() & (P["entry_date"] >= "2016-01-04")].copy()
P = P.drop_duplicates(["ticker", "entry_date"]).reset_index(drop=True)
print(f"Picks to replay: {len(P)} ({(P.src == 'main').sum()} main, {(P.src == 'small').sum()} small-company), "
      f"bought {P.entry_date.min():%Y-%m-%d} to {P.entry_date.max():%Y-%m-%d}")

H = {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET_KEY", "")}
S = requests.Session()
FEED = ["sip"]


def bars(sym, start, end):
    out, tok = [], None
    for _ in range(6):
        p = {"timeframe": "1Min", "start": start, "end": end, "limit": 10000, "adjustment": "split", "feed": FEED[0]}
        if tok:
            p["page_token"] = tok
        for a in range(5):
            r = S.get(f"https://data.alpaca.markets/v2/stocks/{sym}/bars", headers=H, params=p, timeout=60)
            if r.status_code == 429:
                time.sleep(3 * (a + 1)); continue
            break
        if r.status_code == 403 and FEED[0] == "sip":
            FEED[0] = "iex"; print("SIP history refused; using the IEX feed"); return bars(sym, start, end)
        if not r.ok:
            return None
        js = r.json()
        out += js.get("bars") or []
        tok = js.get("next_page_token")
        if not tok:
            break
    return out


BOUNCE, WAIT, GATE = (0.005, 0.01, 0.02, 0.03), (1, 2, 3, 5), (True, False)
VARS = [(b, w, g) for b in BOUNCE for w in WAIT for g in GATE]
res, miss = [], {"no data": 0, "day mismatch": 0}
t0 = time.time()
for i, r in P.iterrows():
    sym, d0 = r["ticker"].replace("-", "."), pd.Timestamp(r["entry_date"])
    b = bars(sym, (d0 - pd.Timedelta(days=5)).strftime("%Y-%m-%d"), (d0 + pd.Timedelta(days=10)).strftime("%Y-%m-%d"))
    if not b:
        miss["no data"] += 1; continue
    df = pd.DataFrame(b)
    df["ts"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = df[(df.ts.dt.time >= pd.Timestamp("09:30").time()) & (df.ts.dt.time < pd.Timestamp("16:00").time())]
    df["day"] = df.ts.dt.normalize().dt.tz_localize(None)
    days = sorted(df.day.unique())
    prev = [d for d in days if d < d0]
    after = [d for d in days if d >= d0]
    if not after or after[0] != d0 or not prev:
        miss["day mismatch"] += 1; continue
    open0 = float(df[df.day == d0].iloc[0]["o"])
    prevclose = float(df[df.day == prev[-1]].iloc[-1]["c"])
    seq = {d: df[df.day == d][["o", "h", "l", "c"]].values for d in after[:5]}
    row = {"y": d0.year, "q": str(d0.to_period("Q")), "src": r["src"], "base": float(r["ret"]), "gap_down": open0 < prevclose}
    for (bb, w, g) in VARS:
        if g and open0 >= prevclose:
            fill, waited = open0, 0
        else:
            low, fill, waited = open0, None, 0
            for k, d in enumerate(after[:w]):
                for o, h, l, c in seq[d]:
                    trig = low * (1 + bb)
                    if h >= trig:
                        fill, waited = max(trig, o), k
                        break
                    low = min(low, l)
                if fill is not None:
                    break
            if fill is None:                      # never bounced in time: buy at the close of the last wait day
                last = seq[after[min(w, len(after)) - 1]]
                fill, waited = float(last[-1][3]), min(w, len(after)) - 1
        v = (1 + row["base"] + 2 * cost) * open0 / fill - 1 - 2 * cost
        row[f"{bb}|{w}|{int(g)}"] = (v - row["base"], fill > open0 * 1.0001, waited)
    res.append(row)
    if len(res) % 250 == 0:
        print(f"  replayed {len(res)} picks ({(time.time() - t0) / 60:.0f} min)")
print(f"Replayed {len(res)} of {len(P)} picks (skipped: {miss}); feed {FEED[0]}")

D = pd.DataFrame(res)


def summ(d, k):
    x = d[k].map(lambda z: z[0])
    if len(x) < 20:
        return None
    qm = x.groupby(d["q"]).mean()
    se = qm.std() / np.sqrt(len(qm)) if len(qm) > 2 else np.nan
    return {"n": int(len(x)), "gain": float(x.mean()), "t": float(x.mean() / se) if se else None,
            "median": float(x.median()), "trim": float(x.clip(x.quantile(0.01), x.quantile(0.99)).mean()), "max": float(x.max()),
            "better": float((x > 1e-9).mean()), "paid_more": float(d[k].map(lambda z: z[1]).mean()),
            "days": float(d[k].map(lambda z: z[2]).mean())}


keys = [f"{b}|{w}|{int(g)}" for b, w, g in VARS]
lab = lambda k: (lambda b, w, g: f"bounce {float(b) * 100:g}%, wait {w}d, {'buy now if rising' if g == '1' else 'always trail'}")(*k.split("|"))
train, test = D[D.y < 2022], D[D.y >= 2022]
table = {k: {"train": summ(train, k), "test": summ(test, k), "all": summ(D, k)} for k in keys}
best = max(keys, key=lambda k: (table[k]["train"] or {}).get("gain", -9))
print("\nAll variants (gain per trade vs buying at the open, percentage points; t uses quarter clusters):")
for k in sorted(keys, key=lambda k: -(table[k]["train"] or {}).get("gain", -9)):
    a, b_ = table[k]["train"] or {}, table[k]["test"] or {}
    print(f"  {lab(k):45s} 2016-21 {a.get('gain', np.nan) * 100:+.2f} (t {a.get('t') or np.nan:.1f}) | "
          f"2022-now {b_.get('gain', np.nan) * 100:+.2f} (t {b_.get('t') or np.nan:.1f}, median {b_.get('median', np.nan) * 100:+.2f}, "
          f"1%-trimmed {b_.get('trim', np.nan) * 100:+.2f}, biggest {b_.get('max', np.nan) * 100:+.0f}) | paid more than the open "
          f"{(b_.get('paid_more') or 0) * 100:.0f}% | avg wait {b_.get('days') or 0:.1f}d")
tb = table[best]["test"] or {}
print(f"\nChosen on 2016-2021: {lab(best)}  ->  2022-now {tb.get('gain', np.nan) * 100:+.2f} pts a trade "
      f"(t {tb.get('t') or np.nan:.1f}, n {tb.get('n')}), better than the open on {(tb.get('better') or 0) * 100:.0f}% of picks, "
      f"paid more on {(tb.get('paid_more') or 0) * 100:.0f}%")
x = test[best].map(lambda z: z[0])
print("  by year:", {int(y): f"{g.mean() * 100:+.2f}" for y, g in x.groupby(test["y"])})
print("  by portfolio:", {s: f"{g.mean() * 100:+.2f} (n {len(g)})" for s, g in x.groupby(test["src"])})
print("  gap-down opens only:", f"{x[test['gap_down']].mean() * 100:+.2f} (n {int(test['gap_down'].sum())})")
wf, picks_wf = [], {}
for y in range(2019, int(D.y.max()) + 1):
    past, cur = D[D.y < y], D[D.y == y]
    if not len(cur):
        continue
    k = max(keys, key=lambda k: (summ(past, k) or {}).get("gain", -9))
    picks_wf[y] = lab(k)
    wf.append(cur.assign(g=cur[k].map(lambda z: z[0]))[["q", "y", "g"]])
W = pd.concat(wf)
qm = W.groupby("q")["g"].mean()
wt = W["g"].mean() / (qm.std() / np.sqrt(len(qm)))
print(f"\nWalk forward 2019-now: {W['g'].mean() * 100:+.2f} pts a trade (t {wt:.1f}, n {len(W)}); chosen each year: {picks_wf}")
os.makedirs("out", exist_ok=True)
json.dump({"n": len(D), "skipped": miss, "feed": FEED[0], "best": best, "best_label": lab(best),
           "table": table, "walk_forward": {"gain": float(W["g"].mean()), "t": float(wt), "n": int(len(W)), "chosen": picks_wf}},
          open("out/trailing_buy_test.json", "w"), indent=1, default=str)
