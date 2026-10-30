"""Moving-to-safety gauge (owner, Oct 29): is Congress moving money from stocks into bonds and Treasuries, or the
other way? Overall, by party, chamber, committee and trading-partner group.

Trades on the "safety" side: municipal bonds, Treasuries, corporate bonds (from other_assets) and bond/T-bill
funds traded like stocks (SGOV, BIL, TLT, AGG, MUB, ...). Everything else in the stock table is the "stock" side;
private funds and other assets are left out. Dollars are the middle of each reported range.

A reading is dated by when the trades became public (filing date), so a past reading only uses what was known
then. tilt = (net into safety - net into stocks) / (all safety + all stock dollars), between -1 and +1:
+ means moving toward safety, - toward stocks.
"""
import re
import numpy as np
import pandas as pd

BOND_FUNDS = {"SGOV", "BIL", "SHV", "SHY", "IEF", "TLT", "GOVT", "VGSH", "VGIT", "VGLT", "SCHO", "SCHR", "SPTS",
              "BSV", "BIV", "BLV", "BND", "AGG", "SCHZ", "IUSB", "MUB", "VTEB", "SUB", "SHM", "TFI", "HYD", "TIP",
              "VTIP", "SCHP", "STIP", "LQD", "VCIT", "VCSH", "IGSB", "JPST", "MINT", "NEAR", "FLOT", "USFR", "TFLO",
              "BOXX", "ICSH", "GBIL", "CLTL", "TBIL", "XBIL", "EDV", "ZROZ", "SPTL", "SPTI", "FBND", "BOND", "PIMIX"}
SAFETY_KINDS = {"muni", "treasury", "corp_bond"}
TOP_BRACKET = 75_000_000.0


def _mid(lo, hi):
    lo = pd.to_numeric(lo, errors="coerce")
    hi = pd.to_numeric(hi, errors="coerce")
    m = (lo + hi.fillna(lo)) / 2
    return m.clip(upper=TOP_BRACKET)


def _sign(tx_type):
    t = str(tx_type or "").lower()
    if t.startswith(("p", "buy")):
        return 1
    if t.startswith(("s", "sale", "sell")):
        return -1
    return 0


def trades(scored, other):
    """One table of both sides: who, member, chamber, party, committees, side, signed dollars, trade/known dates."""
    keep = ["who", "member", "chamber", "party", "committees", "trade_date", "filed_date", "amt_lo", "amt_hi", "tx_type", "ticker"]
    s = scored[[c for c in keep if c in scored]].copy()
    s["side"] = np.where(s["ticker"].astype(str).str.upper().isin(BOND_FUNDS), "safety", "stock")
    s["sign"] = s["tx_type"].map(_sign)
    s["what"] = s["ticker"]
    parts = [s]
    if other is not None and len(other):
        o = other[other["kind"].isin(SAFETY_KINDS)].copy()
        o["trade_date"] = pd.to_datetime(o["trade_date"], errors="coerce")
        o["filed_date"] = pd.to_datetime(o["filed_date"], errors="coerce")
        o["sign"] = o["tx_raw"].map(_sign)
        o["side"], o["what"] = "safety", o["asset"]
        # who / party / committees from the same member's stock trades (nearest one before the bond trade)
        ref = s.dropna(subset=["trade_date"]).sort_values("trade_date")
        ref = ref.assign(_k=ref["chamber"].astype(str) + "|" + ref["member"].map(_last))
        o["_k"] = o["chamber"].astype(str) + "|" + o["member"].map(_last)
        rows = []
        by = {k: g for k, g in ref.groupby("_k")}
        for i, r in o.iterrows():
            g = by.get(r["_k"])
            if g is None or pd.isna(r["trade_date"]):
                rows.append((None, None, ""))
                continue
            j = max(0, int(np.searchsorted(g["trade_date"].values, np.datetime64(r["trade_date"]), side="right")) - 1)
            x = g.iloc[j]
            rows.append((x.get("who"), x.get("party"), x.get("committees") or ""))
        o["who"], o["party"], o["committees"] = [a for a, _, _ in rows], [b for _, b, _ in rows], [c for _, _, c in rows]
        o["who"] = o["who"].fillna(o["_k"])
        parts.append(o[[c for c in s.columns if c in o.columns]])
    d = pd.concat(parts, ignore_index=True)
    d["trade_date"] = pd.to_datetime(d["trade_date"], errors="coerce")
    d["filed_date"] = pd.to_datetime(d["filed_date"], errors="coerce")
    d["usd"] = _mid(d["amt_lo"], d["amt_hi"])
    d = d[(d["sign"] != 0) & d["usd"].notna() & d["filed_date"].notna()]
    d["signed"] = d["sign"] * d["usd"]
    return d


def _last(name):
    w = re.sub(r"[^a-z ]", "", str(name or "").lower()).split()
    w = [x for x in w if x not in ("jr", "sr", "ii", "iii", "iv")]
    return w[-1] if w else ""


def tilt_series(d, freq="MS", by="filed_date"):
    """Monthly reading: tilt, dollars each way, members on each side (by their own net tilt that month)."""
    if not len(d):
        return pd.DataFrame()
    x = d.assign(month=d[by].dt.to_period("M").dt.to_timestamp())
    out = []
    for mo, g in x.groupby("month"):
        net_safe = g.loc[g["side"] == "safety", "signed"].sum()
        net_stock = g.loc[g["side"] == "stock", "signed"].sum()
        gross = g["usd"].sum()
        per = g.groupby(["who", "side"])["signed"].sum().unstack(fill_value=0)
        ms = per.get("safety", pd.Series(0, index=per.index)) - per.get("stock", pd.Series(0, index=per.index))
        out.append({"month": mo, "tilt": (net_safe - net_stock) / gross if gross else np.nan,
                    "net_safety": net_safe, "net_stock": net_stock, "gross": gross,
                    "members": int(per.shape[0]), "toward_safety": int((ms > 0).sum()), "toward_stocks": int((ms < 0).sum()),
                    "safety_trades": int((g["side"] == "safety").sum())})
    return pd.DataFrame(out).set_index("month")


def groups(d, pairs=None):
    """{group name: rows} for party, chamber, each committee and each trading-partner group."""
    gs = {"All of Congress": d}
    for p, g in d.groupby(d["party"].fillna("?")):
        gs[{"D": "Democrats", "R": "Republicans", "I": "Independents"}.get(p, p)] = g
    for c, g in d.groupby("chamber"):
        gs[str(c)] = g
    comm = d.assign(c=d["committees"].fillna("").astype(str).str.split(r";\s*")).explode("c")
    comm = comm[comm["c"].astype(str).str.len() > 2]
    for c, g in comm.groupby("c"):
        if g["who"].nunique() >= 5:
            gs["Committee: " + c] = g
    for name, members in (pairs or {}).items():
        g = d[d["who"].isin(members)]
        if len(g):
            gs["Partners: " + name] = g
    return gs


def partner_groups(ev, names, min_shared=3):
    """Trading-partner groups: members linked by pairs with min_shared+ same trades within a week (connected sets)."""
    if ev is None or not len(ev):
        return {}
    cnt = ev.groupby("pair")["ticker"].nunique()
    edges = [p for p, n in cnt.items() if n >= min_shared]
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in edges:
        parent[find(a)] = find(b)
    sets = {}
    for a, b in edges:
        for x in (a, b):
            sets.setdefault(find(x), set()).add(x)
    out = {}
    for s in sorted(sets.values(), key=len, reverse=True):
        lead = sorted(s, key=lambda w: names.get(w, w))
        label = ", ".join(names.get(w, w) for w in lead[:3]) + (f" +{len(s) - 3}" if len(s) > 3 else "")
        out[label] = s
    return out
