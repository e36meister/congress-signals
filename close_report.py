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
                                                else (p.get("bot") and p.get("sleeve") != "small") if key == "main"
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
    email(report, snap["mode"])


def email(rep, mode):
    addr, pw = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (addr and pw):
        return
    import smtplib
    from email.mime.text import MIMEText
    pct = lambda v: "–" if v is None else f"{v:+.2f}%"
    lines = [f"<p><b>{'Paper' if mode == 'paper' else 'Real-money'} account at the close:</b> ${rep['equity']:,.0f} "
             f"({'+' if (rep['day_pl'] or 0) >= 0 else '-'}${abs(rep['day_pl'] or 0):,.0f} today)</p>",
             f"<p>Since {rep['start']}: account {pct(rep['since_start'])}, S&amp;P 500 {pct(rep['spy_since_start'])}</p><ul>"]
    for s_ in rep["sleeves"]:
        e = s_.get("expected") or {}
        exp = "" if e.get("avg") is None else f"; backtest expects about {e['avg']*100:+.1f}% vs S&amp;P by now"
        lines.append(f"<li>{s_['name']}: {s_['positions']} positions, {s_['ret']*100:+.1f}% "
                     f"vs S&amp;P {pct(None if s_['spy'] is None else s_['spy']*100)} over the same days{exp}</li>")
    lines.append(f"</ul><p><a href='https://claude.ai/artifact/Hkj8H6ZduZXcuvtgSaByru'>Open Capitol Capital</a></p>")
    msg = MIMEText("".join(lines), "html")
    msg["Subject"] = f"Capitol Capital close: {pct(rep['since_start'])} vs S&P {pct(rep['spy_since_start'])}"
    msg["From"], msg["To"] = addr, os.environ.get("ALERT_TO") or addr
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(addr, pw.replace(" ", ""))
            srv.send_message(msg)
    except Exception as e:
        E.log(f"Close report: email failed ({type(e).__name__})")


if __name__ == "__main__":
    main()
