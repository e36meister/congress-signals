"""Read-only: do purchases right after a member's strongly positive post about the company beat similar purchases?
And where do the post signals stand in the per-trade test and the score today."""
import sys, json, os
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E
import congress_signals.social as S
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "social"}
sc = pd.read_pickle("data/cache/scored.pkl")
px = pd.read_pickle("data/cache/prices.pkl")
pol = E.load_adaptive(cfg)["policy"]
print("signals switched on:", pol.get("signals_on"), "| sizing:", pol.get("sizing"))
print("score weight social_post:", cfg["BUY_WEIGHTS"].get("social_post"))
idx = S.load_index(cfg) or {}
men = idx.get("mentions") or {}
print("members with dated company mentions:", len(men))
bio = sc["bioguide"] if "bioguide" in sc else pd.Series(None, index=sc.index)
td = pd.to_datetime(sc["trade_date"].where(sc["trade_date"].notna(), sc["filed_date"]))
flags = {k: np.zeros(len(sc)) for k in ("strong_pos30", "pos30", "strong_neg30", "named30", "named90")}
for i, (b, t, d) in enumerate(zip(bio, sc["ticker"], td)):
    if not isinstance(b, str) or pd.isna(d):
        continue
    ms = [m for m in ((men.get(b) or {}).get(t) or []) if not m[2]]
    if not ms:
        continue
    ds, lo30, lo90 = d.strftime("%Y-%m-%d"), (d - pd.Timedelta(days=30)).strftime("%Y-%m-%d"), (d - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    n30 = [m for m in ms if lo30 <= m[0] < ds]
    n90 = [m for m in ms if lo90 <= m[0] < ds]
    if n30:
        flags["named30"][i] = 1
        tones = [m[1] for m in n30 if m[1] is not None]
        if tones and max(tones) >= 0.6: flags["strong_pos30"][i] = 1
        if tones and max(tones) >= 0.3: flags["pos30"][i] = 1
        if tones and min(tones) <= -0.6: flags["strong_neg30"][i] = 1
    if n90:
        flags["named90"][i] = 1
for k, v in flags.items():
    sc["f_x_" + k] = v
b = sc[sc["tx_type"] == "buy"]
print("buys:", len(b), {k: int((b['f_x_' + k] > 0).sum()) for k in flags})
cfg2 = dict(cfg)
for hold in (None, 250):
    res = E.rare_signal_test(sc, px, cfg2, hold=hold)
    print(f"\n=== hold {res.get('hold')} days: flagged purchase vs similar purchases (same month, same industry) ===")
    f = lambda v, p=True: "–" if v is None else (f"{v * 100:+.2f}%" if p else f"{v:.1f}")
    for r in res.get("rows", []):
        if "post" in r["signal"] or r["signal"].startswith("x_") or r["signal"] == "social_post":
            print(f"{'PASS ' if r['passes'] else '     '}{r['signal']:<22} n {r['n']:>5}  lift {f(r['lift'])} (t {f(r['t'], False)})  "
                  f"earlier {f(r['lift_early'])} (t {f(r['t_early'], False)}, n {r['n_early']})  last3y {f(r['lift_recent'])} (n {r['n_recent']})")
# share of all purchases on each, so a 'rare' miss is visible
for c in ["f_social_post", "f_post_named_before", "f_post_tone_clash", "f_post_crowd", "f_post_sector_burst"] + ["f_x_" + k for k in flags]:
    if c in b:
        print(c, f"on {100 * (b[c] > 0).mean():.1f}% of buys")
