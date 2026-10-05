"""After each market close: how the paper account is doing, compared with the S&P 500 and with what the
backtest says to expect. Written into the dashboard's data (My portfolio tab) and emailed if Gmail is set up.
Read-only: never trades."""
import os, json, io, datetime as dt
import numpy as np
import requests
import broker
import quick_check as Q
import congress_signals.engine as E

HORIZON_DAYS = {"1 week": 5, "1 month": 20, "3 months": 60, "6 months": 125, "1 year": 250}


def _spy_closes(api, start):
    r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/bars", headers=api.h, timeout=30,
                     params={"timeframe": "1Day", "start": start, "feed": "iex", "limit": 1000, "adjustment": "all"})
    bars = r.json().get("bars") or []
    return {b["t"][:10]: float(b["c"]) for b in bars}


def _spy_now(api):
    try:
        r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/trades/latest", headers=api.h,
                         params={"feed": "iex"}, timeout=30)
        return float(r.json()["trade"]["p"])
    except Exception:
        return None


def _expected(horizons, group, days):
    """What the backtest's average trade in this group did vs the S&P after about `days` trading days."""
    rows = [h for h in horizons or [] if h.get("Group") == group and h.get("Holding period") in HORIZON_DAYS]
    if not rows or not days:
        return None
    best = min(rows, key=lambda h: abs(HORIZON_DAYS[h["Holding period"]] - days))
    scale = min(1.0, days / HORIZON_DAYS[best["Holding period"]])      # early days: a share of the full-period average
    f = lambda v: None if v is None else float(v) * scale
    return {"avg": f(best.get("Avg vs SPY")), "low": f(best.get("Range low")), "high": f(best.get("Range high")),
            "period": best["Holding period"]}


def main():
    s = broker.settings(60)
    if not (s["key"] and s["secret"]):
        E.log("Close report: no Alpaca keys; skipping")
        return
    api = broker.Alpaca(s)
    clock = api.get("/v2/clock")
    if clock.get("is_open"):
        E.log("Close report: market still open; skipping")
        return
    drive, folder = Q._drive()
    if drive is None:
        return
    q = f"name = 'dashboard_data.json' and '{folder}' in parents and trashed = false"
    found = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    if not found:
        return
    fid = found[0]["id"]
    data = json.loads(drive.files().get_media(fileId=fid, supportsAllDrives=True).execute())
    today = dt.date.today().isoformat()
    forced = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if (data.get("close_report") or {}).get("date") == today and not forced:
        E.log("Close report: already done today")
        return

    # account value by day (Alpaca's own history) against the S&P 500 over the same days
    hist = api.get("/v2/account/portfolio/history", period="3M", timeframe="1D")
    days = [dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat() for t in hist.get("timestamp") or []]
    eq = [float(x) if x is not None else None for x in hist.get("equity") or []]
    pts = [(d, e) for d, e in zip(days, eq) if e and d < today]
    # Alpaca's daily bars run a day or two behind the close; the equity recorded at each close is exact,
    # so it replaces Alpaca's value for every day we have it
    rec = {h["date"]: float(h["equity"]) for h in data.get("close_history", []) if h.get("equity")}
    if rec:
        first_rec = min(rec)
        pts = [(d, e) for d, e in pts if d < first_rec][:1] + sorted((d, e) for d, e in rec.items() if d < today)
    acct_now = float(api.get("/v2/account").get("equity") or 0)
    if acct_now:
        pts.append((today, acct_now))        # the daily history adds today's bar only later in the evening
    start_i = next((i for i in range(1, len(pts)) if pts[i][1] != pts[0][1]), 0)   # first day money actually moved
    pts = pts[max(0, start_i - 1):]
    spy = _spy_closes(api, (dt.date.fromisoformat(pts[0][0]) - dt.timedelta(days=7)).isoformat()) if pts else {}
    spy_last = _spy_now(api)
    if spy_last:
        spy[today] = spy_last
    series = {"dates": [], "account": [], "spy": []}
    if pts:
        e0 = pts[0][1]
        before = [k for k in spy if k <= pts[0][0]]           # the last S&P close on or before the start (weekends)
        s0 = spy[max(before)] if before else next((spy[d] for d, _ in pts if d in spy), None)
        last_s = s0
        for d, e in pts:
            last_s = spy.get(d, last_s)
            series["dates"].append(d)
            series["account"].append(round((e / e0 - 1) * 100, 3))
            series["spy"].append(round((last_s / s0 - 1) * 100, 3) if s0 and last_s else None)

    # each strategy's open positions: return since bought vs the S&P over the same days, vs the backtest
    snap = broker.sync({"HOLD_DAYS": 60, "_hold_policy": (data.get("adaptive") or {}).get("policy")},
                       None, None, {}, E.log, trade=False)
    spy_all = _spy_closes(api, min([p["bought"] for p in snap["positions"] if p.get("bought")] or [today]))
    spy_now = spy_last or (spy_all[max(spy_all)] if spy_all else None)
    horizons = (data.get("backtest") or {}).get("horizons")
    groups = {"main": ("Main picks", "Top-scored purchases"), "small": ("Small-company portfolio", "Small companies"),
              "yours": ("Your own buys", "All purchases")}
    sleeves = []
    for key, (label, hgroup) in groups.items():
        ps = [p for p in snap["positions"] if (p.get("sleeve") == "small" if key == "small"
                                                else (p.get("bot") and p.get("sleeve") not in ("small", "park")) if key == "main"
                                                else not p.get("bot"))]
        rows = []
        for p in ps:
            b = p.get("bought")
            s_then = spy_all.get(b) if b else None
            if s_then is None and b:
                prior = [d for d in spy_all if d <= b]
                s_then = spy_all[max(prior)] if prior else None
            spy_ret = (spy_now / s_then - 1) if (spy_now and s_then) else None
            rows.append((p["plpc"], spy_ret, p.get("held") or 0))
        if not rows:
            continue
        r = float(np.mean([x[0] for x in rows]))
        sp = [x[1] for x in rows if x[1] is not None]
        spr = float(np.mean(sp)) if sp else None
        held = int(round(np.mean([x[2] for x in rows])))
        sleeves.append({"name": label, "positions": len(rows), "avg_days": held, "ret": r, "spy": spr,
                        "diff": (r - spr) if spr is not None else None, "expected": _expected(horizons, hgroup, held)})

    acct = series["account"][-1] if series["account"] else 0.0
    spyc = series["spy"][-1] if series["spy"] else None
    report = {"date": today, "equity": snap["equity"], "day_pl": snap.get("day_pl"), "since_start": acct,
              "spy_since_start": spyc, "start": series["dates"][0] if series["dates"] else today,
              "series": series, "sleeves": sleeves}
    data["close_report"] = report
    data["portfolio"] = {**snap, "limits": (data.get("portfolio") or {}).get("limits") or snap["limits"]}
    hist_list = [h for h in data.get("close_history", []) if h.get("date") != today]
    hist_list.append({k: report[k] for k in ("date", "equity", "day_pl", "since_start", "spy_since_start")})
    data["close_history"] = hist_list[-120:]
    from googleapiclient.http import MediaIoBaseUpload
    drive.files().update(fileId=fid, media_body=MediaIoBaseUpload(io.BytesIO(json.dumps(data).encode()),
                         mimetype="application/json"), supportsAllDrives=True).execute()
    E.log("Close report: saved to the dashboard")
    email(report, snap["mode"], snap, data)


LUCK_TE, LUCK_Z = 0.15, 1.96          # same 95% luck range as the dashboard chart


def _fmt_money(v, sign=False):
    if v is None:
        return "–"
    s = f"${abs(v):,.0f}"
    return (("+" if v >= 0 else "-") + s) if sign else (("-" if v < 0 else "") + s)


def build_email(rep, mode, snap, data):
    """The close email: three number tiles, the luck range, today's biggest movers, each strategy vs the S&P,
    and what happens next. Inline styles only (email apps ignore style sheets)."""
    pct = lambda v, d=2: "–" if v is None else f"{v:+.{d}f}%"
    green, red, grey, ink, line = "#0B7A4B", "#B42318", "#6B778A", "#152033", "#DCE1E8"
    col = lambda v: grey if v is None or v == 0 else (green if v > 0 else red)
    eq, day = rep.get("equity") or 0, rep.get("day_pl")
    day_pct = (day / (eq - day) * 100) if (day is not None and eq and eq != day) else None
    acct, spy = rep.get("since_start"), rep.get("spy_since_start")
    gap = (acct - spy) if (acct is not None and spy is not None) else None
    start = dt.date.fromisoformat(rep["start"]) if rep.get("start") else dt.date.today()
    start_txt = f"{start:%b} {start.day}"

    def tile(big, label, sub, color):
        return (f"<td width='33%' style='padding:12px 10px;border:1px solid {line};border-radius:8px;vertical-align:top'>"
                f"<div style='font:600 22px Menlo,Consolas,monospace;color:{color}'>{big}</div>"
                f"<div style='font-size:12px;color:{grey};margin-top:2px'>{label}</div>"
                f"<div style='font-size:12px;color:{ink}'>{sub}</div></td>")
    tiles = ("<table width='100%' cellspacing='6' cellpadding='0' style='border-collapse:separate'><tr>"
             + tile(_fmt_money(eq), "account value", f"{'Paper' if mode == 'paper' else 'Real money'}", ink)
             + tile(_fmt_money(day, True), "today", pct(day_pct), col(day))
             + tile(pct(gap), "vs the S&amp;P 500", f"since {start_txt} (S&amp;P {pct(spy)})", col(gap))
             + "</tr></table>")

    # where the account sits in the 95% luck range around the S&P
    yrs = max((dt.date.fromisoformat(rep["date"]) - start).days, 0) / 365.25
    w = LUCK_Z * LUCK_TE * yrs ** 0.5 * 100
    if gap is None or w <= 0:
        luck = "Not enough history yet to place the account in the luck range."
    elif abs(gap) <= w:
        room_up, room_dn = w - gap, w + gap
        edge = f"{room_up:.1f}% below the top edge" if room_up <= room_dn else f"{room_dn:.1f}% above the bottom edge"
        luck = (f"<b>Inside the luck range, {edge}.</b> The range is ±{w:.1f}% around the S&amp;P today and widens "
                f"over time.")
    else:
        luck = (f"<b>{'Above' if gap > 0 else 'Below'} the luck range</b> by {abs(gap) - w:.1f}% "
                f"(the range is ±{w:.1f}% around the S&amp;P today).")

    # today's biggest movers among open positions
    pos = [p for p in (snap.get("positions") or []) if p.get("day") is not None and abs(float(p["day"])) >= 0.0005]
    mv = []
    for p in pos:
        d = float(p["day"])
        mv.append((p["t"], d * 100, (p.get("mv") or 0) * d / (1 + d) if d > -1 else 0))
    mv.sort(key=lambda x: x[1], reverse=True)
    up, dn = [m for m in mv if m[1] > 0][:3], [m for m in mv if m[1] < 0][-3:][::-1]

    def mrow(m, arrow, c):
        return (f"<tr><td style='padding:3px 8px 3px 0;color:{c}'>{arrow}</td><td style='padding:3px 12px 3px 0;font-weight:600'>{m[0]}</td>"
                f"<td align='right' style='padding:3px 12px 3px 0;color:{c};font-family:Menlo,Consolas,monospace'>{m[1]:+.1f}%</td>"
                f"<td align='right' style='color:{c};font-family:Menlo,Consolas,monospace'>{_fmt_money(m[2], True)}</td></tr>")
    movers = "".join(mrow(m, "▲", green) for m in up) + "".join(mrow(m, "▼", red) for m in dn)
    movers = movers or f"<tr><td style='color:{grey}'>No price changes today.</td></tr>"

    # each strategy vs the S&P since bought
    srows = ""
    for s_ in rep.get("sleeves") or []:
        diff = None if s_.get("diff") is None else s_["diff"] * 100
        e = (s_.get("expected") or {}).get("avg")
        srows += (f"<tr><td style='padding:4px 12px 4px 0'>{s_['name']} <span style='color:{grey}'>({s_['positions']})</span></td>"
                  f"<td align='right' style='padding:4px 12px 4px 0;font-family:Menlo,Consolas,monospace'>{pct(s_['ret'] * 100, 1)}</td>"
                  f"<td align='right' style='padding:4px 12px 4px 0;font-family:Menlo,Consolas,monospace;color:{col(diff)}'>{pct(diff, 1)}</td>"
                  f"<td align='right' style='font-family:Menlo,Consolas,monospace;color:{grey}'>{pct(None if e is None else e * 100, 1)}</td></tr>")
    strat = (f"<table cellspacing='0' cellpadding='0' style='font-size:14px'><tr style='color:{grey};font-size:12px'>"
             f"<td style='padding-bottom:4px'>Since bought</td><td align='right' style='padding:0 12px 4px 0'>Return</td>"
             f"<td align='right' style='padding:0 12px 4px 0'>vs S&amp;P</td><td align='right' style='padding-bottom:4px'>Backtest</td></tr>{srows}</table>") if srows else ""

    # what happens next
    soon = sorted([(p["t"], int(p["hold"]) - int(p.get("held") or 0)) for p in snap.get("positions") or []
                   if p.get("bot") and p.get("hold") is not None and int(p["hold"]) - int(p.get("held") or 0) <= 5],
                  key=lambda x: x[1])
    today_s = rep["date"]
    bought = sorted({o["t"] for o in snap.get("orders") or [] if o.get("side") == "buy" and str(o.get("at", ""))[:10] == today_s
                     and o.get("status") in ("filled", "partially_filled", "accepted", "new")})
    w_ = data.get("watchlist") or {}
    n_buy = len([r for r in w_.get("buys") or [] if r.get("action") == "BUY"])
    n_worth = len(data.get("worth") or [])
    nxt = [f"<b>Tool sells in the next 5 trading days:</b> " + (", ".join(f"{t} ({'today' if d <= 0 else f'in {d} day' + ('s' if d > 1 else '')})" for t, d in soon) or "none"),
           f"<b>Bought today:</b> " + (", ".join(bought) or "none"),
           f"<b>On the dashboard:</b> {n_buy} buy candidate{'s' if n_buy != 1 else ''}" + (f", {n_worth} worth a look" if n_worth else "")]

    h = lambda t: f"<div style='font:600 12px Arial;letter-spacing:.8px;color:{grey};text-transform:uppercase;margin:22px 0 6px'>{t}</div>"
    d0 = dt.date.fromisoformat(rep["date"])
    html = (f"<div style='font-family:Arial,Helvetica,sans-serif;color:{ink};max-width:560px;font-size:14px;line-height:1.45'>"
            f"<div style='font:600 12px Arial;letter-spacing:1px;color:{grey};text-transform:uppercase'>Capitol Capital · close, {d0:%b} {d0.day}</div>"
            f"{tiles}<p style='margin:10px 2px 0'>{luck}</p>"
            f"{h('Today’s biggest moves')}<table cellspacing='0' cellpadding='0' style='font-size:14px'>{movers}</table>"
            f"{h('Each strategy') + strat if strat else ''}"
            f"{h('Coming up')}" + "".join(f"<div style='margin:3px 0'>{x}</div>" for x in nxt) +
            f"<p style='margin-top:22px'><a href='https://claude.ai/artifact/Hkj8H6ZduZXcuvtgSaByru' style='color:#1D4F91'>Open Capitol Capital</a></p></div>")
    subject = (f"Close: {_fmt_money(day, True)} today · {pct(gap)} vs S&P" if day is not None else f"Close: {pct(gap)} vs S&P")
    return subject, html


def email(rep, mode, snap=None, data=None):
    addr, pw = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (addr and pw):
        return
    import smtplib
    from email.mime.text import MIMEText
    subject, html = build_email(rep, mode, snap or {}, data or {})
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"], msg["To"] = addr, os.environ.get("ALERT_TO") or addr
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(addr, pw.replace(" ", ""))
            srv.send_message(msg)
    except Exception as e:
        E.log(f"Close report: email failed ({type(e).__name__})")


if __name__ == "__main__":
    main()
