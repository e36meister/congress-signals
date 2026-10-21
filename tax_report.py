"""Taxes page (More > Taxes): reads every fill, dividend, interest and fee from the Alpaca account the tool trades
(paper now; live once TRADING_MODE=live), works out each sale's gain or loss (congress_signals/tax.py) and saves,
per tax year, to Cloudflare R2 (tax/<mode>/...): the Form 8949 statement (PDF + CSV), the Schedule D totals and a
list of what to file. Runs after the market-close report. Amounts are never written to the run log (public repo)."""
import os, io, csv, json, datetime as dt
from zoneinfo import ZoneInfo
import broker
import cloud_store
from congress_signals import tax as T
from congress_signals import health

health.install("tax")
NY = ZoneInfo("America/New_York")
log = lambda m: print(dt.datetime.now(NY).strftime("%H:%M:%S"), m, flush=True)
problems, fails = [], []


def activities(api, types):
    out, tok = [], None
    for _ in range(200):
        p = {"activity_types": types, "direction": "asc", "page_size": 100}
        if tok:
            p["page_token"] = tok
        page = api.get("/v2/account/activities", **p)
        if not page:
            break
        out += page
        if len(page) < 100:
            break
        tok = page[-1]["id"]
    else:
        fails.append("Taxes: account history is longer than 20,000 entries; only the first 20,000 were read")
    return out


def ny_date(ts):
    return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(NY).date().isoformat()


def money(v):
    return f"{v:,.2f}" if v >= 0 else f"({-v:,.2f})"


def statement_pdf(mode, year, rows, summ):
    from fpdf import FPDF
    pdf = FPDF(orientation="L", unit="pt", format="Letter")
    pdf.set_auto_page_break(True, 36)
    cols = [("(a) Description", 170), ("(b) Acquired", 70), ("(c) Sold", 70), ("(d) Proceeds", 85), ("(e) Cost basis", 85),
            ("(f) Code", 45), ("(g) Adjustment", 80), ("(h) Gain or (loss)", 90)]
    for box, title in (("A", "Part I - Short-term (held one year or less). Box A: basis reported to the IRS"),
                       ("D", "Part II - Long-term (held more than one year). Box D: basis reported to the IRS")):
        part = [r for r in rows if r["box"] == box]
        if not part:
            continue
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 18, f"Form 8949 statement - tax year {year}" + ("  (PAPER ACCOUNT - PRACTICE, NOT FOR FILING)" if mode != "live" else ""), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 13, "Sales of stock through Alpaca Securities LLC. Lots matched first in, first out; wash sales (code W) per IRC 1091.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 16, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 8)
        for h, w in cols:
            pdf.cell(w, 14, h, border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for r in part:
            vals = [r["desc"], r["acq"], r["sold"], money(r["proceeds"]), money(r["cost"]), r["code"],
                    money(r["adj"]) if r["adj"] else "", money(r["gain"])]
            for (h, w), v in zip(cols, vals):
                pdf.cell(w, 13, str(v), border=1, align="R" if h[1] in "degh" else "L")
            pdf.ln()
        t = summ["short" if box == "A" else "long"]
        pdf.set_font("Helvetica", "B", 8)
        for (h, w), v in zip(cols, ["Totals", "", "", money(t["proceeds"]), money(t["cost"]), "", money(t["adj"]) if t["adj"] else "", money(t["gain"])]):
            pdf.cell(w, 14, str(v), border=1, align="R" if h[1] in "degh" else "L")
        pdf.ln()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 18, f"Schedule D totals and filing checklist - {year}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    s, l = summ["short"], summ["long"]
    for line in (f"Schedule D line 1b (box A): proceeds {money(s['proceeds'])}, cost {money(s['cost'])}, adjustments {money(s['adj'])}, gain {money(s['gain'])}",
                 f"Schedule D line 8b (box D): proceeds {money(l['proceeds'])}, cost {money(l['cost'])}, adjustments {money(l['adj'])}, gain {money(l['gain'])}",
                 f"Net capital gain or (loss): {money(summ['net'])}",
                 f"Wash sales: {summ['wash']['n']}, loss disallowed {money(summ['wash']['disallowed'])} (added to the replacement shares' cost)",
                 f"Dividends received: {money(summ['dividends'])}   Interest: {money(summ['interest'])}   Regulatory fees: {money(summ['fees'])}",
                 ""):
        pdf.multi_cell(0, 14, line, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 14, "What to file", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for x in summ["todo"]:
        pdf.multi_cell(0, 14, f"- {x['form']}: {x['why']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 12, "Compare these totals with the Form 1099 Alpaca sends by mid-February; the 1099 is the official record. "
                          "This is a record-keeping aid, not tax advice.")
    return bytes(pdf.output())


def main():
    s = broker.settings(0)
    mode = s["mode"]
    if not (s["key"] and s["secret"]):
        problems.append(f"Taxes: no Alpaca {mode} keys set, so the Taxes page can't be updated")
        return
    api = broker.Alpaca(s)
    raw = activities(api, "FILL")
    fills = [{"date": ny_date(a["transaction_time"]), "time": a["transaction_time"], "t": broker.from_alpaca(a["symbol"]),
              "side": a["side"], "qty": float(a["qty"]), "px": float(a["price"]), "id": a["id"]}
             for a in raw if a.get("symbol") and float(a.get("qty") or 0) > 0]
    other = activities(api, "DIV,DIVCGL,DIVCGS,DIVFT,DIVNRA,DIVROC,DIVTXEX,INT,FEE")
    sales, open_lots = T.realize(fills)
    est = None
    if mode != "live":
        try:
            est = broker.dividend_estimate(api)
        except Exception as e:
            fails.append(f"Taxes: dividend estimate failed ({type(e).__name__})")
    years = sorted({f["date"][:4] for f in fills} | {ny_date(a.get("date") or a.get("transaction_time"))[:4] for a in other
                                                     if a.get("date") or a.get("transaction_time")}
                   | {str(dt.date.today().year)})
    index = {"mode": mode, "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "years": {},
             "open_lots": len(open_lots), "fills": len(fills)}
    for y in years:
        yo = [a for a in other if (ny_date(a.get("date") or a.get("transaction_time")) if (a.get("date") or a.get("transaction_time")) else "")[:4] == y]
        amt = lambda types: sum(float(a.get("net_amount") or 0) for a in yo if str(a.get("activity_type")) in types)
        div = amt(("DIV", "DIVCGL", "DIVCGS", "DIVFT", "DIVNRA", "DIVROC", "DIVTXEX"))
        summ = T.summarize(sales, y, dividends=div, interest=amt(("INT",)), fees=-amt(("FEE",)),
                           estimated_dividends=(est or {}).get("by_year", {}).get(y) if est else None)
        rows = T.form8949_rows(sales, y)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Box", "Term", "(a) Description", "(b) Date acquired", "(c) Date sold", "(d) Proceeds", "(e) Cost basis",
                    "(f) Code", "(g) Adjustment", "(h) Gain or loss"])
        for r in rows:
            w.writerow([r["box"], r["term"], r["desc"], r["acq"], r["sold"], f"{r['proceeds']:.2f}", f"{r['cost']:.2f}",
                        r["code"], f"{r['adj']:.2f}" if r["adj"] else "", f"{r['gain']:.2f}"])
        base = f"tax/{mode}/{y}"
        ok = cloud_store.put(f"{base}/form8949.csv", buf.getvalue(), log, content_type="text/csv")
        ok &= cloud_store.put(f"{base}/sales.json", json.dumps({"rows": rows}), log)
        try:
            ok &= cloud_store.put(f"{base}/statement.pdf", statement_pdf(mode, y, rows, summ), log, content_type="application/pdf")
            summ["files"] = ["statement.pdf", "form8949.csv"]
        except Exception as e:
            fails.append(f"Taxes: the {y} PDF statement failed ({type(e).__name__}: {e})")
            summ["files"] = ["form8949.csv"]
        if not ok:
            fails.append(f"Taxes: the {y} files didn't all upload to Cloudflare")
        index["years"][y] = summ
    if not cloud_store.put(f"tax/{mode}/index.json", json.dumps(index), log):
        problems.append("Taxes: couldn't save the Taxes page to Cloudflare")
    log(f"Taxes: {mode} account, {len(fills)} fills, {len(sales)} sales over {len(years)} year(s) saved")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        problems.append(f"Taxes: the update failed ({type(e).__name__}: {e})")
        raise
    finally:
        health.finish("tax", problems, fails, log)
