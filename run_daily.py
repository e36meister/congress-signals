"""Daily run on GitHub: collect new filings, score, backtest, build the watchlist,
and update dashboard_data.json in your Google Drive so the dashboard refreshes."""
import os, json, datetime as dt, traceback
import congress_signals.engine as E
import broker

env = os.environ.get
cfg = {**E.DEFAULT_CONFIG,
       "DATA_DIR": "./data",
       "SEC_USER_AGENT": env("SEC_USER_AGENT", ""),
       "CONGRESS_API_KEY": env("CONGRESS_API_KEY", ""),
       "LDA_API_KEY": env("LDA_API_KEY", ""),
       "QUIVER_API_KEY": env("QUIVER_API_KEY", ""),
       "FMP_API_KEY": env("FMP_API_KEY", ""),
       "TIINGO_API_KEY": env("TIINGO_API_KEY", ""),
       # true / false forces tuned weights on or off; unset lets the weekly check decide
       "USE_TUNED_WEIGHTS": {"true": True, "false": False}.get((env("USE_TUNED_WEIGHTS") or "").strip().lower(), "auto"),
       "TIME_BUDGET_MIN": int(env("TIME_BUDGET_MIN", "320"))}
os.makedirs(cfg["DATA_DIR"], exist_ok=True)


def upload_to_drive(local_path, title, mimetype="application/json"):
    sa = env("GDRIVE_SERVICE_ACCOUNT_JSON")
    folder = env("GDRIVE_FOLDER_ID")
    if not sa or not folder:
        E.log("Drive upload skipped: GDRIVE_SERVICE_ACCOUNT_JSON / GDRIVE_FOLDER_ID not set")
        return
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa), scopes=["https://www.googleapis.com/auth/drive"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    q = f"name = '{title}' and '{folder}' in parents and trashed = false"
    found = drive.files().list(q=q, fields="files(id, modifiedTime)", supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute().get("files", [])
    if found and title == "dashboard_data.json":
        # keep what the market-close report wrote to Drive since this run started
        try:
            remote = json.loads(drive.files().get_media(fileId=found[0]["id"], supportsAllDrives=True).execute())
            local = json.load(open(local_path))
            for k in ("close_report", "close_history"):
                if k in remote:
                    local[k] = remote[k]
            json.dump(local, open(local_path, "w"))
        except Exception as e:
            E.log(f"Drive: couldn't merge the close report ({type(e).__name__})")
    media = MediaFileUpload(local_path, mimetype=mimetype, resumable=False)
    if found:
        drive.files().update(fileId=found[0]["id"], media_body=media, supportsAllDrives=True).execute()
        E.log(f"Drive: updated {title}")
    else:
        drive.files().create(body={"name": title, "parents": [folder]}, media_body=media,
                             supportsAllDrives=True).execute()
        E.log(f"Drive: created {title}")


DASHBOARD_URL = "https://claude.ai/artifact/Hkj8H6ZduZXcuvtgSaByru"


def _send_email(subject, body_html, log_ok):
    addr, pw = env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD")
    to = env("ALERT_TO") or addr
    if not (addr and pw):
        return False
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject[:140], addr, to
    msg.attach(MIMEText(body_html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(addr, pw.replace(" ", ""))
            s.send_message(msg)
        E.log(log_ok)
        return True
    except Exception as e:
        E.log(f"Email: sending failed ({type(e).__name__}); check the Gmail app password secret")
        return False


def send_worth_alerts(items):
    """Email red-flag trades not seen before (Worth a look), once each."""
    import html
    path = os.path.join(cfg["DATA_DIR"], "state", "worth_seen.json")
    seen = set(json.load(open(path))) if os.path.exists(path) else set()
    key = lambda o, w: f"{o['t']}|{w['n']}|{w['ty']}|{w['td']}"
    new = [o for o in items if any(key(o, w) not in seen for w in o["who"])]
    if new:
        rows = "".join(f"<tr><td><b>{html.escape(o['t'])}</b><br><span style='color:#666'>{html.escape(o.get('co') or '')}</span></td>"
                       f"<td>{'<br>'.join(html.escape(w['n']) + (' bought ' if w['ty'] == 'buy' else ' sold ') + w['td'] for w in o['who'])}</td>"
                       f"<td style='font-size:13px'>{html.escape('; '.join(o['why']))}</td></tr>" for o in new)
        body = (f"<p>{len(new)} recently disclosed trade(s) with a rare red flag. These aren't buy signals on their own; "
                f"they're worth a look.</p><table border=1 cellpadding=6 style='border-collapse:collapse;font-family:Arial'>"
                f"<tr><th>Stock</th><th>Members</th><th>Why</th></tr>{rows}</table><p><a href='{DASHBOARD_URL}'>Open Capitol Capital</a></p>")
        if _send_email("Worth a look: " + ", ".join(o["t"] for o in new), body, f"Email: sent {len(new)} worth-a-look item(s)"):
            for o in new:
                for w in o["who"]:
                    seen.add(key(o, w))
            json.dump(sorted(seen), open(path, "w"))
    elif not os.path.exists(path):
        json.dump([], open(path, "w"))


def send_alerts(buys, sells, new):
    """Email new BUY / avoid signals (defense picks flagged) when Gmail secrets are set."""
    addr, pw = env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD")
    to = env("ALERT_TO") or addr
    if not (addr and pw):
        E.log("Email alerts: not set up (add GMAIL_ADDRESS and GMAIL_APP_PASSWORD secrets)")
        return
    if new is None or new.empty:
        E.log("Email alerts: no new signals today")
        return
    import smtplib, html
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    allrows = __import__("pandas").concat([buys, sells]) if len(sells) else buys
    info = {r.ticker: r for r in allrows.itertuples()} if len(allrows) else {}
    rows = []
    for n in new.itertuples():
        r = info.get(n.ticker)
        company = html.escape(str(getattr(r, "company", "") or "")) if r is not None else ""
        defense = bool(getattr(r, "is_defense", False)) if r is not None else False
        rows.append(f"<tr><td><b>{n.action}</b></td><td><b>{n.ticker}</b>{' 🛡 defense' if defense else ''}<br>"
                    f"<span style='color:#666'>{company}</span></td><td>{html.escape(str(n.members))}</td>"
                    f"<td style='font-size:13px'>{html.escape(str(n.why))}</td></tr>")
    body = (f"<p>{len(new)} new signal(s) from today's congressional trade filings.</p>"
            f"<table border=1 cellpadding=6 style='border-collapse:collapse;font-family:Arial'>"
            f"<tr><th>Action</th><th>Stock</th><th>Members</th><th>Why</th></tr>{''.join(rows)}</table>"
            f"<p><a href='{DASHBOARD_URL}'>Open Capitol Capital</a></p>")
    msg = MIMEMultipart("alternative")
    tick = ", ".join(f"{a} {t}" for a, t in zip(new["action"], new["ticker"]))
    msg["Subject"] = f"Congress trade signals: {tick}"[:140]
    msg["From"], msg["To"] = addr, to
    msg.attach(MIMEText(body, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(addr, pw.replace(" ", ""))
            s.send_message(msg)
        E.log(f"Email alerts: sent {len(new)} new signal(s) to {to}")
    except Exception as e:
        E.log(f"Email alerts: sending failed ({e}); check the Gmail app password secret")


def main():
    try:
        scored, px = E.prepare(cfg)
    except RuntimeError as e:
        E.log(f"Stopped early: {e}")
        return
    try:     # foreign vs US purchases (country lookups fill in over a few runs)
        cs = E.load_countries(cfg, list(scored["ticker"].unique()), priority=scored["ticker"].value_counts().to_dict())
        fr = E.foreign_report(scored, cs, cfg)
    except Exception as e:
        fr = None
        E.log(f"Foreign companies: report skipped ({type(e).__name__}: {e})")
    bt = E.run_backtest(scored, px, cfg)
    E.backtest_report(bt, cfg)
    E.export_dashboard(cfg, bt=bt)
    try:
        rp = E.export_research(scored, px, cfg)
        if rp:
            upload_to_drive(rp, "research_buys.csv.gz", mimetype="application/gzip")
    except Exception as e:
        E.log(f"Research export skipped ({type(e).__name__}: {e})")
    tuned_path = os.path.join(cfg["DATA_DIR"], "state", "tuned_weights.json")
    now = dt.datetime.utcnow()
    if (now.weekday() == 6 and now.hour < 16) or not os.path.exists(tuned_path):   # retune Sunday mornings
        try:
            tuned = E.tune_weights(scored, px, cfg)
            E.decide_tuned(cfg, tuned)
            E.export_dashboard(cfg, tuned=tuned)
        except Exception as e:
            E.log(f"Tuning skipped: {e}")
    a = E.load_adaptive(cfg)
    due = not a.get("evaluated_at") or (dt.datetime.now(dt.timezone.utc)
                                        - dt.datetime.fromisoformat(a["evaluated_at"])).days >= 7
    if due and not E.out_of_time(cfg, 90):
        try:
            E.evaluate_adjustments(scored, px, cfg, wf=(bt or {}).get("walk_forward") if isinstance(bt, dict) else None)
        except Exception as e:
            E.log(f"Adjustments: weekly check skipped ({e})")
    try:
        E.check_sizing_once(scored, px, cfg)
    except Exception as e:
        E.log(f"Adjustments: sizing check skipped ({e})")
    try:
        E.check_exit_rule_once(scored, px, cfg)
    except Exception as e:
        E.log(f"Adjustments: sell-when-member-sells check skipped ({e})")
    if not E.out_of_time(cfg, 80):
        try:
            ft = E.load_filter_state(cfg)
            if not ft.get("complete"):
                E.filter_rule_test(scored, px, cfg)
        except Exception as e:
            E.log(f"Filter test: skipped ({type(e).__name__}: {e})")
    if not E.out_of_time(cfg, 75):
        try:
            E.check_price_rules_once(scored, px, cfg)
        except Exception as e:
            E.log(f"Adjustments: price-rule check skipped ({type(e).__name__}: {e})")
    buys, sells = E.build_watchlist(scored, px, cfg)
    new = E.diff_alerts(buys, sells, cfg)
    try:
        E.archive_analysts(cfg, [buys, sells])
    except Exception as e:
        E.log(f"Analyst archive: skipped ({e})")
    E.watchlist_report(buys, sells, new, cfg)
    E.export_dashboard(cfg, buys=buys, sells=sells, new=new)
    try:     # recently traded stocks with unusual trading volume (Today tab)
        path = os.path.join(cfg["DATA_DIR"], "dashboard_data.json")
        d = json.load(open(path))
        d["unusual"] = E.unusual_activity(scored, px, cfg)
        if fr is not None:
            d["foreign"] = fr
        d["worth"] = E.worth_a_look(scored, cfg)
        w = d.get("watchlist") or {}
        hear = E.upcoming_hearings(cfg, (w.get("buys") or []) + (w.get("sells") or []))
        for r in (w.get("buys") or []) + (w.get("sells") or []):
            if r.get("t") in hear:
                r["hear"] = hear[r["t"]]
        json.dump(d, open(path, "w"), default=str)
    except Exception as e:
        E.log(f"Unusual activity: skipped ({type(e).__name__}: {e})")
    send_alerts(buys, sells, new)
    try:
        send_worth_alerts(json.load(open(os.path.join(cfg["DATA_DIR"], "dashboard_data.json"))).get("worth") or [])
    except Exception as e:
        E.log(f"Worth a look: email skipped ({type(e).__name__})")
    try:
        last = px.ffill().iloc[-1].to_dict() if px is not None and len(px) else {}
        sm = (bt.get("small") or {}) if isinstance(bt, dict) else {}
        mt = E.member_trades(scored)
        msales = lambda t, bought: E.member_sales(mt, t, bought)
        pxf = px.ffill() if px is not None else None
        phist = lambda t, since: pxf.loc[E.pd.Timestamp(since):, t].dropna().values if pxf is not None and t in pxf else None
        snap = broker.sync(dict(cfg, _hold_policy=E.load_adaptive(cfg)["policy"], _small_slots=sm.get("slots"),
                                _member_sales=msales, _price_hist=phist), buys, new, last, E.log)
        path = os.path.join(cfg["DATA_DIR"], "dashboard_data.json")
        d = json.load(open(path))
        if snap is not None:
            d["portfolio"] = snap
        # members who bought a stock you hold and have since disclosed a sale (shown on My portfolio)
        ms = {}
        for p in (d.get("portfolio") or {}).get("positions") or []:
            if p.get("bought"):
                s_ = msales(p["t"], p["bought"])
                if s_:
                    ms[p["t"]] = {"bought": p["bought"], "sales": s_}
        d["member_sold"] = ms
        json.dump(d, open(path, "w"))
    except Exception as e:
        E.log(f"Broker: skipped this run ({type(e).__name__})")
    upload_to_drive(os.path.join(cfg["DATA_DIR"], "dashboard_data.json"), "dashboard_data.json")
    try:      # price charts with member trades for the tickers on Today, Defense and My portfolio
        d = json.load(open(os.path.join(cfg["DATA_DIR"], "dashboard_data.json")))
        w = d.get("watchlist") or {}
        ts = {r.get("t") for r in (w.get("buys") or []) + (w.get("sells") or [])}
        ts |= {r.get("t") for r in ((d.get("defense") or {}).get("longs") or [])}
        ts |= {p.get("t") for p in ((d.get("portfolio") or {}).get("positions") or [])}
        upload_to_drive(E.export_ticker_charts(cfg, scored, px, ts), "ticker_charts.json")
    except Exception as e:
        E.log(f"Ticker charts: skipped ({type(e).__name__}: {e})")
    E.log(f"Finished. {len(new)} new signal(s).")


def flag_unfinished():
    """Tells the workflow to start another run right away when this one ran out of time."""
    path = os.path.join(cfg["DATA_DIR"], "state", "needs_more_runs")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if E.HIT_TIME_LIMIT:
        open(path, "w").write(dt.datetime.utcnow().isoformat())
        E.log("Data collection isn't finished; the next run will start right after this one.")
    elif os.path.exists(path):
        os.remove(path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
    finally:
        flag_unfinished()
