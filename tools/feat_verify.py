"""Read-only check that the faster scoring gives exactly the same results as before (tools/engine_ref.py is the
scoring code as it was). Runs the normal data loading, then scores every trade with both and compares every column.
Prints only timings and difference counts (no account data). Nothing is saved back."""
import sys, os, time, copy
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import run_daily as R
import congress_signals.engine as E
import engine_ref as REF


class Stop(Exception):
    pass


CAP = {}
orig = E.compute_features


def capture(tx, px, data, cfg):
    CAP.update(tx=tx, px=px, data=data, cfg=cfg)
    raise Stop()


E.compute_features = capture
_rd = lambda name: (lambda cfg: pd.read_pickle(E._p(cfg, "cache", name)) if os.path.exists(E._p(cfg, "cache", name)) else pd.DataFrame())
E.collect_house_paper = _rd("house_paper_tx.pkl")
E.collect_senate_paper = _rd("senate_paper_tx.pkl")
try:
    E.prepare(R.cfg)
except Stop:
    pass
tx, px, data, cfg = CAP["tx"], CAP["px"], CAP["data"], CAP["cfg"]
print(f"inputs: {len(tx)} trades, {px.shape[1]} price columns")

REF.register_opens(cfg, px)
d_ref = copy.deepcopy(data)
t0 = time.time()
a = REF.compute_features(tx.copy(), px, d_ref, cfg)
t_ref = time.time() - t0
t0 = time.time()
b = orig(tx.copy(), px, data, cfg)
t_new = time.time() - t0
print(f"TIME before {t_ref / 60:.1f} min, after {t_new / 60:.1f} min")

bad = 0
cols = sorted(set(a.columns) | set(b.columns))
for c in cols:
    if c not in a or c not in b:
        print(f"COLUMN only in {'before' if c in a else 'after'}: {c}")
        bad += 1
        continue
    x, y = a[c], b[c]
    if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y) and not pd.api.types.is_bool_dtype(x):
        xv, yv = x.astype(float).values, y.astype(float).values
        same = np.isclose(xv, yv, rtol=1e-9, atol=1e-9, equal_nan=True)
        if c.startswith("f_"):
            same &= (np.isnan(xv) == np.isnan(yv)) & ((xv == yv) | np.isnan(xv))
    else:
        xs, ys = x.astype(str).values, y.astype(str).values
        same = xs == ys
    n_bad = int((~same).sum())
    if n_bad:
        bad += 1
        i = int(np.nonzero(~same)[0][0])
        print(f"DIFF {c}: {n_bad} rows differ, e.g. row {i}: before {x.iloc[i]!r} after {y.iloc[i]!r}")
sa, sb = REF.apply_scores(a, cfg), E.apply_scores(b, cfg)
ds = int((~np.isclose(sa["score"].astype(float), sb["score"].astype(float), rtol=0, atol=1e-12, equal_nan=True)).sum())
print(f"SCORES: {ds} trades with a different score")
print(f"RESULT: {'IDENTICAL' if bad == 0 and ds == 0 else 'DIFFERENT'} ({len(cols)} columns checked)")
