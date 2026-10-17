"""Read-only audit of every signal: does the information behind it actually come back, in every year?
For each f_ column (buys and sells): trades flagged per filing year, the last time it fired, and warnings for
signals that never fire, stopped firing, dropped sharply in the last 12 months, or have empty years in the middle.
Then every cached data file by year, so a gap can be traced to its source."""
import sys, os, json, glob, re
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import congress_signals.engine as E

D = "data"
sc = pd.read_pickle(f"{D}/cache/scored.pkl")
sc["fd"] = pd.to_datetime(sc["filed_date"])
sc["yr"] = sc["fd"].dt.year
today = pd.Timestamp.today().normalize()
YRS = list(range(2014, today.year + 1))
print(f"Trades scored: {len(sc):,} (buys {int((sc.tx_type == 'buy').sum()):,}, sells {int((sc.tx_type == 'sell').sum()):,}); "
      f"last filing {sc['fd'].max().date()}")
print("Trades per filing year: " + " ".join(f"{y}:{(sc.yr == y).sum()}" for y in YRS))

bw = E.DEFAULT_CONFIG["BUY_WEIGHTS"]
sw = E.DEFAULT_CONFIG["SHORT_WEIGHTS"]
trial = E.TRIAL_SIGNALS
fcols = sorted(c for c in sc.columns if c.startswith("f_") and pd.api.types.is_numeric_dtype(sc[c]))
warn = []
rows = []
for c in fcols:
    k = c[2:]
    v = sc[c].astype(float)
    on = v.fillna(0) != 0
    binary = set(np.unique(v.dropna().values)).issubset({0.0, 1.0})
    used = ("buy " if bw.get(k) else "") + ("sell " if sw.get(k) else "") + ("trial" if k in trial else "")
    per = [int((on & (sc.yr == y)).sum()) for y in YRS]
    tot = int(on.sum())
    last = sc.loc[on, "fd"].max() if tot else None
    nan_share = float(v.isna().mean())
    rows.append((c, used.strip() or "-", tot, per, last, nan_share, binary))
    # warnings
    if tot == 0:
        warn.append(f"NEVER FIRES: {c} [{used.strip() or 'not in score'}]")
        continue
    m12 = int((on & (sc.fd > today - pd.DateOffset(months=12))).sum())
    n12 = int((sc.fd > today - pd.DateOffset(months=12)).sum())
    prior = (sc.fd > today - pd.DateOffset(months=48)) & (sc.fd <= today - pd.DateOffset(months=12))
    mp, np_ = int((on & prior).sum()), int(prior.sum())
    rate_now = m12 / max(n12, 1)
    rate_before = mp / max(np_, 1)
    if mp >= 20 and rate_now < 0.25 * rate_before:
        warn.append(f"DROPPED: {c} fired on {rate_before * 100:.1f}% of trades in the 3 years before, "
                    f"{rate_now * 100:.1f}% in the last 12 months [{used.strip() or 'not in score'}]")
    if last is not None:
        days = (today - last).days
        exp = rate_before * n12 / 12 * (days / 30.4)          # how many it would have fired since then at the old rate
        if exp >= 8 and days > 60:
            warn.append(f"STOPPED: {c} last fired {last.date()} ({days} days ago; ~{exp:.0f} expected since) "
                        f"[{used.strip() or 'not in score'}]")
    nz = [i for i, x in enumerate(per) if x > 0]
    if nz:
        inside = per[nz[0]:nz[-1] + 1]
        holes = [YRS[nz[0] + i] for i, x in enumerate(inside) if x == 0]
        big = max(per)
        if holes and big >= 30:
            warn.append(f"EMPTY YEARS: {c} has no trades in {holes} but up to {big}/yr otherwise "
                        f"[{used.strip() or 'not in score'}]")
        first_y = YRS[nz[0]]
        if first_y >= 2019 and big >= 30:
            warn.append(f"STARTS LATE: {c} first fires in {first_y} (data may only start then) "
                        f"[{used.strip() or 'not in score'}]")
    if nan_share > 0.5:
        warn.append(f"MOSTLY BLANK: {c} is blank (no value) on {nan_share * 100:.0f}% of trades")

print(f"\n=== Warnings ({len(warn)}) ===")
for w in warn:
    print("  " + w)

print("\n=== Every signal: trades flagged per filing year ===")
print(f"{'signal':<30}{'used in':<16}" + "".join(f"{str(y)[2:]:>6}" for y in YRS) + f"{'total':>8}  last fired")
for c, used, tot, per, last, nan_share, binary in rows:
    print(f"{c[:29]:<30}{used[:15]:<16}" + "".join(f"{x:>6}" for x in per) + f"{tot:>8}  "
          f"{last.date() if last is not None else '-'}" + ("" if binary else "  (scale)"))

# score weights that point at a column the engine never makes
print("\n=== Weights with no column behind them ===")
missing = [k for k in list(bw) + list(sw) + list(trial) if f"f_{k}" not in sc.columns]
print("  " + (", ".join(sorted(set(missing))) or "none"))

print("\n=== Cached data by year (to trace a gap to its source) ===")
for p in sorted(glob.glob(f"{D}/cache/*.pkl")):
    try:
        df = pd.read_pickle(p)
    except Exception as e:
        print(f"  {os.path.basename(p)}: unreadable ({type(e).__name__})")
        continue
    if not isinstance(df, pd.DataFrame):
        print(f"  {os.path.basename(p)}: {type(df).__name__} of {len(df) if hasattr(df, '__len__') else '?'}")
        continue
    dcol = next((c for c in df.columns if re.search(r"(date|filed|posted|when|day|time)", str(c), re.I)
                 and not re.search(r"lag|days$", str(c), re.I)), None)
    if dcol is None:
        print(f"  {os.path.basename(p)}: {len(df):,} rows (no date column)")
        continue
    y = pd.to_datetime(df[dcol], errors="coerce").dt.year
    cnt = y.value_counts().sort_index()
    span = " ".join(f"{int(k)}:{int(v)}" for k, v in cnt.items() if 2012 <= k <= today.year)
    print(f"  {os.path.basename(p)} ({len(df):,} rows, by {dcol}): {span}")
for p in sorted(glob.glob(f"{D}/cache/*.json")):
    try:
        j = json.load(open(p))
        n = len(j) if hasattr(j, "__len__") else 1
    except Exception as e:
        n = f"unreadable ({type(e).__name__})"
    sz = os.path.getsize(p)
    print(f"  {os.path.basename(p)}: {n} entries, {sz / 1e6:.1f} MB")
for p in sorted(glob.glob(f"{D}/state/collection_check_*.json")):
    j = json.load(open(p))
    print(f"\n{os.path.basename(p)} ({j.get('at')}): problems {j.get('problems')}; failures {j.get('failures')[:20]}")
