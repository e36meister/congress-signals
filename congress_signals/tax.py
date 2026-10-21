"""Taxes for the trading account (owner, Oct 21): every sale's gain or loss the way Form 8949 and Schedule D want
it, wash sales, short vs long term, dividends and interest, and a list of what to file. Federal only: Florida has
no personal income tax.

Rules used (US federal, individual, cash account):
  - Lots are matched first in, first out (FIFO), Alpaca's default, so the numbers line up with its 1099-B.
  - Long term = held more than one year (sold after the 1-year anniversary of the purchase date); otherwise short.
  - Wash sale: a sale at a loss with the same stock bought within 30 days before or after it. That share of the
    loss is disallowed (code W, adjustment in column g) and added to the cost of the replacement shares, whose
    holding period then starts from the original purchase.
  - Short sales: gain or loss when the short is covered, dated by the cover; treated as short term.
  - Alpaca reports cost basis to the IRS for these stocks, so sales go in box A (short term) or box D (long term).
This is a record-keeping aid, not tax advice; the broker's 1099 is the official document to compare against.
Nothing here logs amounts (the repo and its run logs are public)."""
import datetime as dt
from collections import defaultdict

EPS = 1e-9


def _d(s):
    return dt.date.fromisoformat(str(s)[:10])


def _long_term(acq, sold):
    a = _d(acq)
    try:
        anniv = a.replace(year=a.year + 1)
    except ValueError:              # Feb 29
        anniv = a.replace(year=a.year + 1, day=28)
    return _d(sold) > anniv


def realize(fills):
    """fills: [{"date": "YYYY-MM-DD" (trade date, New York), "t": ticker, "side": "buy"|"sell"|"sell_short",
    "qty": float > 0, "px": float, "id": str}] in any order. Returns (sales, open_lots)."""
    fills = sorted(fills, key=lambda f: (f["date"], f.get("time") or "", f.get("id") or ""))
    for i, f in enumerate(fills):
        f["_i"] = i
    by_t = defaultdict(list)
    for f in fills:
        by_t[f["t"]].append(f)
    sales, open_lots = [], []
    for t, fs in by_t.items():
        lots = []           # long lots: {"qty", "cps" (cost per share incl. wash add-ons), "acq", "fill"}
        shorts = []         # open shorts: {"qty", "pps" (proceeds per share), "open"}
        repl_left = {f["_i"]: f["qty"] for f in fs if f["side"] == "buy"}   # shares still able to be wash replacements
        pending = defaultdict(list)      # buy fill index -> [(qty, add per share, acquired date)] from earlier wash sales
        for f in fs:
            q, px, day = float(f["qty"]), float(f["px"]), f["date"]
            if f["side"] == "buy":
                # cover open shorts first
                while q > EPS and shorts:
                    s = shorts[0]
                    k = min(q, s["qty"])
                    sales.append({"t": t, "qty": k, "acq": s["open"], "sold": day, "proceeds": k * s["pps"],
                                  "cost": k * px, "adj": 0.0, "code": "", "term": "short", "short_sale": True})
                    s["qty"] -= k
                    q -= k
                    if s["qty"] <= EPS:
                        shorts.pop(0)
                if q <= EPS:
                    continue
                # a buy that replaces shares sold at a loss earlier gets that loss added to its cost, in pieces
                left = q
                for (wq, add, acq) in pending.pop(f["_i"], []):
                    k = min(wq, left)
                    if k <= EPS:
                        continue
                    lots.append({"qty": k, "cps": px + add, "acq": acq, "fill": f["_i"]})
                    left -= k
                if left > EPS:
                    lots.append({"qty": left, "cps": px, "acq": day, "fill": f["_i"]})
                continue
            # sell / sell_short
            while q > EPS and lots and f["side"] == "sell":
                lot = lots[0]
                k = min(q, lot["qty"])
                proceeds, cost = k * px, k * lot["cps"]
                rec = {"t": t, "qty": k, "acq": lot["acq"], "sold": day, "proceeds": proceeds, "cost": cost,
                       "adj": 0.0, "code": "", "term": "long" if _long_term(lot["acq"], day) else "short",
                       "short_sale": False}
                lot["qty"] -= k
                q -= k
                if lot["qty"] <= EPS:
                    lots.pop(0)
                loss = cost - proceeds
                if loss > EPS:
                    _wash(rec, loss, k, day, fs, f, lots, repl_left, pending, lot)
                sales.append(rec)
            if q > EPS:      # nothing (or not enough) held: a short sale
                shorts.append({"qty": q, "pps": px, "open": day})
        for lot in lots:
            open_lots.append({"t": t, "qty": lot["qty"], "cps": lot["cps"], "acq": lot["acq"]})
        for s in shorts:
            open_lots.append({"t": t, "qty": -s["qty"], "cps": s["pps"], "acq": s["open"]})
    sales.sort(key=lambda r: (r["sold"], r["t"]))
    return sales, open_lots


def _wash(rec, loss, k, day, fs, sale_fill, lots, repl_left, pending, sold_lot):
    """Disallow the loss on shares that have replacements bought within 30 days before or after the sale."""
    lo, hi = _d(day) - dt.timedelta(days=30), _d(day) + dt.timedelta(days=30)
    need, per_share = k, loss / k
    for b in fs:
        if need <= EPS:
            break
        if b["side"] != "buy" or b["_i"] == sold_lot["fill"] or repl_left.get(b["_i"], 0) <= EPS:
            continue
        bd = _d(b["date"])
        if not (lo <= bd <= hi):
            continue
        if b["_i"] < sale_fill["_i"]:
            # bought before the sale: only shares still held count; adjust those open lots now
            held = [l for l in lots if l["fill"] == b["_i"]]
            avail = min(repl_left[b["_i"]], sum(l["qty"] for l in held))
            if avail <= EPS:
                continue
            use = min(need, avail)
            rest = use
            for l in held:
                if rest <= EPS:
                    break
                part = min(rest, l["qty"])
                if part < l["qty"] - EPS:           # split the lot so only the replacement part gets the add-on
                    lots.insert(lots.index(l) + 1, {**l, "qty": l["qty"] - part})
                    l["qty"] = part
                l["cps"] += per_share
                l["acq"] = rec["acq"]
                rest -= part
        else:
            use = min(need, repl_left[b["_i"]])
            pending[b["_i"]].append((use, per_share, rec["acq"]))
        repl_left[b["_i"]] -= use
        need -= use
    disallowed = (k - need) * per_share
    if disallowed > EPS:
        rec["adj"] = disallowed
        rec["code"] = "W"


def summarize(sales, year, dividends=None, interest=0.0, fees=0.0, estimated_dividends=None):
    """Totals for one tax year (by the sale date) and what to file."""
    ys = [s for s in sales if s["sold"][:4] == str(year)]
    tot = {}
    for term in ("short", "long"):
        g = [s for s in ys if s["term"] == term]
        p, c, a = sum(s["proceeds"] for s in g), sum(s["cost"] for s in g), sum(s["adj"] for s in g)
        tot[term] = {"n": len(g), "proceeds": round(p, 2), "cost": round(c, 2), "adj": round(a, 2),
                     "gain": round(p - c + a, 2)}
    div = round(float(dividends or 0), 2)
    net = round(tot["short"]["gain"] + tot["long"]["gain"], 2)
    todo = []
    if ys:
        todo.append({"form": "Form 8949", "why": f"{len(ys)} sale(s): box A (short term) and box D (long term), "
                     "basis reported to the IRS. You can attach the statement instead of copying each sale.",
                     "file": "form8949"})
        todo.append({"form": "Schedule D (Form 1040)", "why": "Totals from Form 8949: line 1b (box A, short term) and line 8b "
                     "(box D, long term)" + ("; a net loss offsets up to $3,000 of other income, the rest carries forward"
                                      if net < 0 else ""), "file": "summary"})
    if div > 0 or interest > 0:
        todo.append({"form": "Form 1040 lines 2b/3a-3b" + (" + Schedule B" if div + interest > 1500 else ""),
                     "why": "Dividends and interest from the 1099-DIV / 1099-INT"
                     + (" (over $1,500, so Schedule B too)" if div + interest > 1500 else ""), "file": "summary"})
    if net > 0:
        todo.append({"form": "Form 1040-ES (estimated tax)", "why": "With a large gain and no tax withheld, the IRS "
                     "expects quarterly payments (Apr 15, Jun 15, Sep 15, Jan 15) to avoid an underpayment penalty. "
                     "Usually safe if this year's payments + withholding cover last year's total tax.", "file": ""})
    todo.append({"form": "Florida", "why": "No state income tax return needed for investment income.", "file": ""})
    wash = [s for s in ys if s["code"] == "W"]
    return {"year": int(year), "short": tot["short"], "long": tot["long"], "net": net,
            "wash": {"n": len(wash), "disallowed": round(sum(s["adj"] for s in wash), 2)},
            "dividends": div, "dividends_estimate": estimated_dividends, "interest": round(float(interest or 0), 2),
            "fees": round(float(fees or 0), 2), "todo": todo}


def form8949_rows(sales, year):
    out = []
    for s in sales:
        if s["sold"][:4] != str(year):
            continue
        qty = s["qty"]
        q = f"{qty:,.0f}" if abs(qty - round(qty)) < 1e-6 else f"{qty:,.4f}".rstrip("0")
        out.append({"box": ("A" if s["term"] == "short" else "D"), "term": s["term"],
                    "desc": f"{q} sh {s['t']}" + (" (short sale)" if s.get("short_sale") else ""),
                    "acq": s["acq"], "sold": s["sold"], "proceeds": round(s["proceeds"], 2), "cost": round(s["cost"], 2),
                    "code": s["code"], "adj": round(s["adj"], 2), "gain": round(s["proceeds"] - s["cost"] + s["adj"], 2)})
    return out
