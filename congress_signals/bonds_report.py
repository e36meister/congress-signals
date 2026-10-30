"""Builds bonds.json for the app (owner, Oct 29): the moving-to-safety gauge, its groups, flagged bond trades and
each member's bond trades. Published to Cloudflare R2 by run_daily; read by the app's Bonds view."""
import json
import datetime as dt
import numpy as np
import pandas as pd
import congress_signals.engine as E
import congress_signals.gauge as G
import congress_signals.other_assets as OA
import congress_signals.bond_links as B


def _f(x, nd=3):
    try:
        x = float(x)
        return None if np.isnan(x) else round(x, nd)
    except Exception:
        return None


def market_test(series, spy):
    """Does the tilt known at a month's end line up with the S&P 500 over the next 1 and 3 months?"""
    if spy is None or not len(series):
        return {"verdict": "not tested (no S&P prices)"}
    me = spy.resample("ME").last()
    s = series.copy()
    s.index = s.index + pd.offsets.MonthEnd(0)
    j = s.join(pd.DataFrame({"r1": me.shift(-1) / me - 1, "r3": me.shift(-3) / me - 1}), how="inner").dropna(subset=["tilt", "r1"])
    out = {"months": int(len(j))}
    for h, step in (("r1", 1), ("r3", 3)):
        x = j[["tilt", h]].dropna()
        if len(x) < 24:
            continue
        c = x["tilt"].corr(x[h])
        n = len(x) / step
        out[h] = {"corr": _f(c), "t": _f(c * np.sqrt(max(n - 2, 1) / max(1e-9, 1 - c * c)), 2),
                  "top_fifth": _f(x[x["tilt"] > x["tilt"].quantile(0.8)][h].mean(), 4),
                  "bottom_fifth": _f(x[x["tilt"] < x["tilt"].quantile(0.2)][h].mean(), 4)}
    ts = [abs(out[h]["t"]) for h in ("r1", "r3") if h in out and out[h]["t"] is not None]
    out["verdict"] = ("no clear link to the market yet" if not ts or max(ts) < 2.5
                      else "possible link to the market (needs to hold up as more bond history fills in)")
    return out


def build(cfg, scored, log=print):
    other = OA.load(cfg)
    d = G.trades(scored, other)
    series = G.tilt_series(d)
    try:
        px = pd.read_pickle(E._p(cfg, "cache", "prices.pkl"))
        spy = px["SPY"].dropna().sort_index() if "SPY" in px else None
        if spy is not None:
            spy.index = pd.to_datetime(spy.index)
    except Exception:
        spy = None
    ev, names = E.trading_pairs(scored)
    groups = G.groups(d, G.partner_groups(ev, names))
    cut = d["filed_date"].max() - pd.Timedelta(days=90) if len(d) else None
    grows = []
    for name, g in groups.items():
        g3 = g[g["filed_date"] > cut] if cut is not None else g.iloc[0:0]
        if g3["who"].nunique() < 3:
            continue
        gross = g3["usd"].sum()
        tilt = (g3.loc[g3.side == "safety", "signed"].sum() - g3.loc[g3.side == "stock", "signed"].sum()) / gross if gross else None
        kind = name.split(":")[0] if ":" in name else ("party" if name in ("Democrats", "Republicans", "Independents")
                                                       else "chamber" if name in ("House", "Senate") else "all")
        grows.append({"name": name.split(": ", 1)[-1], "kind": kind.lower(), "tilt": _f(tilt), "members": int(g3["who"].nunique()),
                      "safety_trades": int((g3.side == "safety").sum()), "usd": _f(gross, 0),
                      "who": sorted(set(g3["who"].dropna().astype(str)))[:40]})
    grows.sort(key=lambda r: (r["kind"] != "all", -(abs(r["tilt"] or 0))))
    # bond trades, flagged ones first
    try:
        meta = json.load(open(E._p(cfg, "cache", "ticker_meta.json")))
        cnames = {E.company_key((v or {}).get("name") or ""): t for t, v in meta.items() if (v or {}).get("name")}
    except Exception:
        cnames = {}
    links = B.link_all(other, scored, cnames)
    bonds = []
    if len(links):
        links = links.sort_values("trade_date", ascending=False)
        for _, r in links.iterrows():
            bonds.append({"who": r.get("who"), "member": r["member"], "chamber": r["chamber"],
                          "date": r["trade_date"].strftime("%Y-%m-%d") if pd.notna(r["trade_date"]) else None,
                          "filed": str(r.get("filed_date") or "")[:10], "tx": r.get("tx_raw"), "lo": _f(r.get("amt_lo"), 0),
                          "hi": _f(r.get("amt_hi"), 0), "owner": r.get("owner"), "asset": r.get("asset"), "kind": r["kind"],
                          "state": r.get("muni_state"), "sector": r.get("sector"), "flag": r.get("flag"),
                          "why": r.get("why") or None, "ticker": r.get("ticker")})
    s36 = series.tail(36)
    out = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "gauge": {"series": [{"month": m.strftime("%Y-%m"), "tilt": _f(r.tilt), "members": int(r.members),
                                 "toward_safety": int(r.toward_safety), "toward_stocks": int(r.toward_stocks),
                                 "net_safety": _f(r.net_safety, 0), "net_stock": _f(r.net_stock, 0),
                                 "safety_trades": int(r.safety_trades)} for m, r in s36.iterrows()],
                     "test": market_test(series, spy),
                     "bond_history_from": str(pd.to_datetime(other["trade_date"], errors="coerce").min())[:10] if len(other) else None},
           "groups": grows[:60],
           "bonds": bonds[:600],
           "counts": {k: int(v) for k, v in (other["kind"].value_counts().items() if len(other) else [])}}
    log(f"Bonds: {len(other):,} bond and other non-stock trades on file; {sum(1 for b in bonds if b['flag'] == 'strong')} "
        f"flagged; gauge {len(series)} months; market test: {out['gauge']['test'].get('verdict')}")
    return out
