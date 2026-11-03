"""Weekly sector sweep (owner, Nov 3: test every signal inside each sector, find new sector pairs on its own).

Once a week every buy signal is tested inside each sector (tools/signal_group_audit.py's method: flagged vs
unflagged buys of the same sector filed the same month, at 60/125/250 trading days, earlier years vs the fixed
holdout, Newey-West t, bars and a luck count from placebo signals with matched firing rates clumped by member and
month). A pair that passes is added as a switched-off TRIAL signal ("<signal>__<sector>"). It can only be switched
on after it has also worked on trades filed AFTER the day it was found (engine.new_data_gate), because it was
chosen looking at every year. Pairs that are never switched on are dropped after 26 weeks. Results (hits vs luck)
are saved to data/state/sector_sweep.json and reported.
"""
import datetime as dt
import json
import os
import re
import time
import numpy as np
import pandas as pd

STATE = "sector_sweep.json"
EVERY_DAYS = 6
DROP_WEEKS = 26
H = (60, 125, 250)
MIN_BUYS, MIN_FLAG, NPL = 400, 40, 3


def _path(cfg):
    p = os.path.join(cfg["DATA_DIR"], "state", STATE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _load(cfg):
    try:
        return json.load(open(_path(cfg)))
    except Exception:
        return {}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def load_auto(E, cfg):
    """Register the pairs found by earlier sweeps as trial signals (call before scoring)."""
    st = _load(cfg)
    n = 0
    for name, d in (st.get("found") or {}).items():
        thr = float(d.get("thr", 0.0))
        E.SECTOR_VARIANTS[name] = (d["base"], (d["sector"],), (lambda v, t=thr: v >= t) if thr > 0 else (lambda v: v > 0))
        E.TRIAL_SIGNALS[name] = 0.3 * float(d["sign"])
        E.TRIAL_LABELS[name] = (f"Signal ({d['sector']} only, found by the weekly sector check {d['found']}): "
                                + E.TRIAL_LABELS.get(d["base"][2:], d["base"][2:]).replace("Signal: ", "")
                                + ("" if d["sign"] > 0 else ", scored lower"))
        E.SIGNAL_FOUND[name] = d["found"]
        n += 1
    return n


def _nw_t(k, lags):
    k = pd.Series(k).dropna().values.astype(float)
    n = len(k)
    if n < 6:
        return np.nan
    e = k - k.mean()
    v = e @ e / n
    for L in range(1, min(lags, n - 1) + 1):
        v += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return float(k.mean() / np.sqrt(v / n)) if v > 0 else np.nan


def run(E, scored, px, cfg, log=print, force=False):
    """The weekly sweep. Returns a summary dict (or None when it isn't due)."""
    st = _load(cfg)
    now = dt.datetime.now(dt.timezone.utc)
    last = st.get("last_run")
    if not force and last and (now - dt.datetime.fromisoformat(last)).days < EVERY_DAYS:
        return None
    t0 = time.time()
    bw, _ = E.active_weights(cfg)
    b = scored[(scored["tx_type"] == "buy") & scored["entry_px"].notna() & (scored["lag_days"].fillna(999) <= 45)]
    b = b.reset_index(drop=True)
    m = pd.to_datetime(b["filed_date"]).dt.to_period("M").astype(str).values
    sec = b["sector"].fillna("?").astype(str).values
    hold = getattr(E, "HOLDOUT_START", "2023-11-01")[:7]
    early, recent = m < hold, m >= hold
    X = {}
    for h in H:
        fr = E.forward_returns(b, px, h)
        X[h] = np.where(fr["closed"].values, np.clip(fr["excess"].values.astype(float), -1, 3), np.nan)
    base_sigs = sorted(k for k in list(cfg["BUY_WEIGHTS"]) + list(E.TRIAL_SIGNALS)
                       if f"f_{k}" in b and "__" not in k and k not in E.SECTOR_VARIANTS
                       and pd.api.types.is_numeric_dtype(b[f"f_{k}"]))
    flags, thr = {}, {}
    for k in base_sigs:
        v = b[f"f_{k}"].astype(float).fillna(0).values
        binary = set(np.unique(v)).issubset({0.0, 1.0})
        if binary or (v > 0).sum() <= 50:
            flags[k], thr[k] = v > 0, 0.0
        else:
            t = float(np.quantile(v[v > 0], 0.8))
            flags[k], thr[k] = (v >= t) & (v > 0), t
    who = ((b["bioguide"] if "bioguide" in b else b["ticker"]).astype(object).fillna("?").astype(str) + "|" + m).values
    uniq, inv = np.unique(who, return_inverse=True)

    def lift(idx, f, h, part=None):
        x = X[h][idx]
        ok = ~np.isnan(x) if part is None else (~np.isnan(x) & part[idx])
        ff = f[idx][ok]
        if ff.sum() < MIN_FLAG or (~ff).sum() < MIN_FLAG:
            return None, None
        d = pd.DataFrame({"x": x[ok], "p": m[idx][ok], "f": ff})
        base = d[~d["f"]].groupby("p")["x"].mean()
        y = d[d["f"]].join(base.rename("b"), on="p").dropna(subset=["b"])
        if len(y) < MIN_FLAG:
            return None, None
        dd = y["x"] - y["b"]
        return float(dd.mean()), _nw_t(dd.groupby(y["p"]).mean().sort_index(), int(np.ceil(h / 21)))

    def stats(idx, f):
        r = {}
        for h in H:
            r[f"l{h}"], r[f"t{h}"] = lift(idx, f, h)
            r[f"e{h}"], _ = lift(idx, f, h, early)
            r[f"r{h}"], _ = lift(idx, f, h, recent)
        return r

    def hit(r, bar, sgn):
        for s in (1, -1):
            hs = [h for h in H if r[f"t{h}"] is not None and np.isfinite(r[f"t{h}"]) and r[f"t{h}"] * sgn * s >= bar[h]]
            if len(hs) >= 2 and all((r[f"e{h}"] or 0) * sgn * s > 0 and (r[f"r{h}"] or 0) * sgn * s > 0 for h in hs[:2]):
                return s
        return 0

    groups = [g for g, n in pd.Series(sec).value_counts().items() if n >= MIN_BUYS and g not in ("?", "", "NONE", "ETF")]
    gidx = {g: np.flatnonzero(sec == g) for g in groups}
    rng = np.random.default_rng(int(now.strftime("%Y%W")))
    pl = []
    for _ in range(NPL):
        for g in groups:
            for k in base_sigs:
                fk = flags[k][gidx[g]]
                if fk.sum() < MIN_FLAG:
                    continue
                pl.append(stats(gidx[g], (rng.random(len(uniq)) < max(0.01, float(fk.mean())))[inv]))
    PT = {h: np.array([abs(p[f"t{h}"]) for p in pl if p[f"t{h}"] is not None and np.isfinite(p[f"t{h}"])]) for h in H}
    bar = {h: max(1.65, float(np.quantile(PT[h], 0.95))) if len(PT[h]) else 9.0 for h in H}
    luck_rate = (sum(1 for p in pl if hit(p, bar, 1) != 0) / len(pl)) if pl else 0.0
    found = dict(st.get("found") or {})
    hits, tested = [], 0
    today = dt.date.today().isoformat()
    existing = {(E.SECTOR_VARIANTS[v][0], s) for v in E.SECTOR_VARIANTS for s in E.SECTOR_VARIANTS[v][1]}
    for g in groups:
        for k in base_sigs:
            if flags[k][gidx[g]].sum() < MIN_FLAG:
                continue
            tested += 1
            sgn = float(np.sign(bw.get(k) or E.TRIAL_SIGNALS.get(k, 0) or (cfg["BUY_WEIGHTS"].get(k) or 1)))
            r = stats(gidx[g], flags[k])
            hv = hit(r, bar, sgn)
            if not hv:
                continue
            sign = sgn * hv                          # the direction it actually worked in
            name = f"{k}__{_slug(g)}"
            hits.append({"name": name, "signal": k, "sector": g, "sign": sign,
                         **{f"l{h}": r[f"l{h}"] for h in H}, **{f"t{h}": r[f"t{h}"] for h in H}})
            if name in found:
                found[name]["last_pass"] = today
            elif (f"f_{k}", g) not in existing:
                found[name] = {"base": f"f_{k}", "sector": g, "sign": sign, "thr": thr[k], "found": today,
                               "last_pass": today}
    # drop pairs never switched on that haven't passed for DROP_WEEKS
    on = set((E.load_adaptive(cfg)["policy"] or {}).get("signals_on", []))
    dropped = [n for n, d in found.items() if n not in on and
               (dt.date.today() - dt.date.fromisoformat(d.get("last_pass", d["found"]))).days > DROP_WEEKS * 7]
    for n in dropped:
        found.pop(n, None)
    summary = {"at": now.isoformat(timespec="seconds"), "groups": len(groups), "tested": tested, "hits": hits,
               "luck": round(luck_rate * tested, 1), "bars": bar, "added": [h["name"] for h in hits if found.get(h["name"], {}).get("found") == today
                                                                               and h["name"] not in (st.get("found") or {})],
               "dropped": dropped, "minutes": round((time.time() - t0) / 60, 1)}
    st.update({"last_run": summary["at"], "found": found, "last": summary,
               "history": (st.get("history") or [])[-51:] + [{k: summary[k] for k in ("at", "tested", "luck") } | {"hits": len(hits)}]})
    json.dump(st, open(_path(cfg), "w"), indent=1, default=float)
    log(f"Sector sweep: {len(hits)} signal-sector hits in {tested} tests across {len(groups)} sectors (luck alone gives "
        f"about {summary['luck']}); new trial signals {summary['added'] or 'none'}; dropped {dropped or 'none'}; "
        f"{summary['minutes']} min")
    return summary
