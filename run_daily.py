"""Daily run on GitHub: collect new filings, score, backtest, build the watchlist,
and update dashboard_data.json in your Google Drive so the dashboard refreshes."""
import os, json, datetime as dt, traceback
import congress_signals.engine as E
import broker
import cloud_store

env = os.environ.get
cfg = {**E.DEFAULT_CONFIG,
       "DATA_DIR": "./data",
       "SEC_USER_AGENT": env("SEC_USER_AGENT", ""),
       "CONGRESS_API_KEY": env("CONGRESS_API_KEY", ""),
       "LDA_API_KEY": env("LDA_API_KEY", ""),
       "QUIVER_API_KEY": env("QUIVER_API_KEY", ""),
       "FMP_API_KEY": env("FMP_API_KEY", ""),
       "OCR_SPACE_API_KEY": env("OCR_SPACE_API_KEY", ""),
       "GOOGLE_VISION_API_KEY": env("GOOGLE_VISION_API_KEY", ""),
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
    # keyed by the member's Congress ID when known (names are now spelled one way, so old name keys can differ)
    key = lambda o, w: f"{o['t']}|{w.get('b') or w['n']}|{w['ty']}|{w['td']}"
    if seen and "__v2__" not in seen:
        # one time: trades already emailed under the old name keys (same stock, type and trade date) stay seen
        old = {(k.split("|")[0], k.split("|")[-2], k.split("|")[-1]) for k in seen if k.count("|") >= 3}
        for o in items:
            for w in o["who"]:
                if (o["t"], w["ty"], w["td"]) in old:
                    seen.add(key(o, w))
        seen.add("__v2__")
        json.dump(sorted(seen), open(path, "w"))
    new = [o for o in items if any(key(o, w) not in seen for w in o["who"])]
    try:      # phone alert, once per flagged trade (tracked apart from the email so a failed email doesn't repeat it)
        ppath = os.path.join(cfg["DATA_DIR"], "state", "worth_pushed.json")
        pushed = set(json.load(open(ppath))) if os.path.exists(ppath) else None
        if pushed is None:                       # first time: don't alert on everything already emailed
            pushed = set(seen) | {key(o, w) for o in items for w in o["who"] if key(o, w) in seen}
        fresh = [o for o in items if any(key(o, w) not in pushed for w in o["who"])]
        if fresh:
            ts = ", ".join(o["t"] for o in fresh[:6]) + ("…" if len(fresh) > 6 else "")
            first = fresh[0]
            body = (f"{first['t']}: " + "; ".join(first.get("why") or [])[:180]) if len(fresh) == 1 else \
                   f"{len(fresh)} recent trades with a rare red flag. Not buy signals on their own."
            if cloud_store.notify(f"Worth a look: {ts}", body, "worth", "/#today", E.log):
                for o in fresh:
                    for w in o["who"]:
                        pushed.add(key(o, w))
        os.makedirs(os.path.dirname(ppath), exist_ok=True)
        json.dump(sorted(pushed), open(ppath, "w"))
    except Exception as e:
        E.log(f"Worth a look: phone alert skipped ({type(e).__name__})")
    if new:
        rows = "".join(f"<tr><td><b>{html.escape(o['t'])}</b><br><span style='color:#666'>{html.escape(o.get('co') or '')}</span></td>"
                       f"<td>{'<br>'.join(html.escape(w['n']) + (' bought ' if w['ty'] == 'buy' else ' sold ') + w['td'] for w in o['who'])}</td>"
                       f"<td style='font-size:13px'>{html.escape('; '.join(o['why']))}</td></tr>" for o in new)
        body = (f"<p>{len(new)} recently disclosed trade(s) with a rare red flag. These aren't buy signals on their own; "
                f"they're worth a look.</p><table border=1 cellpadding=6 style='border-collapse:collapse;font-family:Arial'>"
                f"<tr><th>Stock</th><th>Members</th><th>Why</th></tr>{rows}</table><p><a href='{DASHBOARD_URL}'>Open Capitol Capital</a></p>")
        phones = cloud_store.has_phones()       # phone alerts cover these; email only when no phone has alerts on
        if phones:
            E.log("Email: worth-a-look skipped (phone alerts are on)")
        if phones or _send_email("Worth a look: " + ", ".join(o["t"] for o in new), body, f"Email: sent {len(new)} worth-a-look item(s)"):
            for o in new:
                for w in o["who"]:
                    seen.add(key(o, w))
            seen.add("__v2__")
            json.dump(sorted(seen), open(path, "w"))
    elif not os.path.exists(path):
        json.dump(["__v2__"], open(path, "w"))


def send_weekly_summary():
    """Once a week (the first run each Monday from 6 AM New York time, and once right after this was added):
    paper account, new picks, trades the tool made, what the weekly check changed, backtest headline, data progress."""
    import html
    path = os.path.join(cfg["DATA_DIR"], "state", "weekly_email.json")
    st = json.load(open(path)) if os.path.exists(path) else {}
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("America/New_York"))
    week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
    if st.get("week") == week or (now.weekday() == 0 and now.hour < 6):
        return              # once a week: the first run from Monday 6 AM on (a later day if Monday's runs failed)
    d = json.load(open(os.path.join(cfg["DATA_DIR"], "dashboard_data.json")))
    esc = lambda x: html.escape(str(x if x is not None else ""))
    pc = lambda x, n=1: "–" if x is None else f"{x * 100:+.{n}f}%"
    since = now.date() - dt.timedelta(days=7)
    parts = []
    # paper account
    ch = d.get("close_history") or []
    if ch:
        last = ch[-1]
        wk = next((r for r in reversed(ch) if r.get("date") and r["date"] <= since.isoformat()), ch[0])
        wchg = (last["equity"] / wk["equity"] - 1) if wk.get("equity") and last.get("equity") else None
        f2 = lambda x: "–" if x is None else f"{x:+.2f}%"
        parts.append(f"<h3>Paper account</h3><p>${(last.get('equity') or 0):,.0f} · this week {pc(wchg)} · since start "
                     f"{f2(last.get('since_start'))} vs the S&amp;P's {f2(last.get('spy_since_start'))}</p>")
    # trades the tool made
    orders = [o for o in ((d.get("portfolio") or {}).get("orders") or [])
              if o.get("by") == "tool" and o.get("status") == "filled" and str(o.get("at", ""))[:10] >= since.isoformat()]
    if orders:
        park = ("SPY", "VOO", "IVV")
        lst = lambda side, pk: ", ".join(sorted({esc(o["t"]) for o in orders if o["side"] == side and (o["t"] in park) == pk}))
        lines = [f"Bought: {lst('buy', False)}" if lst("buy", False) else "",
                 f"Sold: {lst('sell', False)}" if lst("sell", False) else "",
                 "Moved unused money into the S&amp;P 500 fund" if lst("buy", True) else "",
                 "Took money out of the S&amp;P 500 fund for new picks" if lst("sell", True) else ""]
        parts.append("<h3>Trades the tool made</h3><p>" + "<br>".join(x for x in lines if x) + "</p>")
    # new buy picks this week
    w = d.get("watchlist") or {}
    picks = [r for r in (w.get("buys") or []) if r.get("action") == "BUY" and str(r.get("d") or "")[:10] >= since.isoformat()]
    if picks:
        parts.append("<h3>New buy picks</h3><table border=1 cellpadding=5 style='border-collapse:collapse'>"
                     "<tr><th>Stock</th><th>Member</th><th>Main reasons</th></tr>" + "".join(
                         f"<tr><td><b>{esc(r['t'])}</b><br><span style='color:#666'>{esc(r.get('co'))}</span></td><td>{esc(r.get('m'))}</td>"
                         f"<td style='font-size:13px'>{esc('; '.join(str(r.get('why') or '').split('; ')[:3]))}</td></tr>" for r in picks[:15]) + "</table>")
    else:
        top = [r for r in (w.get("buys") or []) if r.get("action") == "BUY"][:5]
        parts.append("<h3>New buy picks</h3><p>None this week." + (" Current top picks: " + ", ".join(
            f"<b>{esc(r['t'])}</b> ({esc(r.get('co'))})" for r in top) if top else "") + "</p>")
    # weekly check
    a = d.get("adaptive") or {}
    ch_ = [h for h in a.get("history") or [] if str(h.get("date", "")) >= since.isoformat()]
    parts.append("<h3>Weekly check</h3><p>" + ("<br>".join(f"{esc(h['change'])}: {esc(h.get('from'))} → {esc(h.get('to'))}" for h in ch_)
                                              if ch_ else "No settings changed.") + "</p>")
    # backtest headline
    def vs(block, name):
        for r in (block or {}).get("perf") or []:
            if r.get("name") == name:
                return r.get("Per year vs S&P 500"), r.get("Likely range low"), r.get("Likely range high")
        return None, None, None
    m_, ml, mh = vs(d.get("backtest"), "Long picks")
    s_, sl, sh = vs(d.get("small"), "Small-company picks")
    parts.append(f"<h3>Backtest since 2014, per year vs the S&amp;P 500</h3><p>Main picks {pc(m_)} (likely {pc(ml)} to {pc(mh)})"
                 f"<br>Small-company portfolio {pc(s_)} (likely {pc(sl)} to {pc(sh)})</p>")
    # data progress
    g = d.get("data_progress") or {}
    if g:
        hs, ss, hr = g.get("house_scans") or {}, g.get("senate_scans") or {}, g.get("house_reread") or {}
        parts.append(f"<h3>Data</h3><p>House reports left to re-read: {esc(hr.get('left'))}<br>"
                     f"Scanned reports left: House {esc(hs.get('left'))}, Senate {esc(ss.get('left'))} "
                     f"({esc(hs.get('rows'))} + {esc(ss.get('rows'))} trades read from scans so far)</p>")
    body = ("<div style='font-family:Arial;max-width:640px'>" + "".join(parts)
            + f"<p><a href='{DASHBOARD_URL}'>Open Capitol Capital</a></p></div>")
    if _send_email(f"Capitol Capital weekly: {now:%b %d}", body, "Email: sent the weekly summary"):
        st["week"] = week
        json.dump(st, open(path, "w"))


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
    # a one-off check the owner asked for at a set time (force_checks.json in the repo)
    forced = None
    try:
        fc = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "force_checks.json")))
        at = fc.get("weekly_at")
        if at and dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(at) and a.get("forced_done") != at:
            forced, due = at, True
            E.log(f"Adjustments: running the weekly check now (asked for {at})")
    except FileNotFoundError:
        pass
    except Exception as e:
        E.log(f"Adjustments: forced-check file unreadable ({type(e).__name__})")
    if due and not E.out_of_time(cfg, 90):
        try:
            E.evaluate_adjustments(scored, px, cfg, wf=(bt or {}).get("walk_forward") if isinstance(bt, dict) else None)
            if forced:
                a2 = E.load_adaptive(cfg)
                a2["forced_done"] = forced
                E.save_adaptive(cfg, a2)
        except Exception as e:
            E.log(f"Adjustments: weekly check skipped ({e})")
    try:
        E.check_sizing_once(scored, px, cfg)
    except Exception as e:
        E.log(f"Adjustments: sizing check skipped ({e})")
    try:
        E.check_trial_signals_once(scored, px, cfg)
    except Exception as e:
        E.log(f"Adjustments: new-signal check skipped ({e})")
    try:
        E.check_exit_rule_once(scored, px, cfg)
    except Exception as e:
        E.log(f"Adjustments: sell-when-member-sells check skipped ({e})")
    try:        # one email when a full House re-read has finished
        rp = os.path.join(cfg["DATA_DIR"], "state", "house_reread.json")
        rr = json.load(open(rp)) if os.path.exists(rp) else {"version": E.HOUSE_PARSER_VERSION}
        bl = os.path.join(cfg["DATA_DIR"], "cache", "house_backlog.json")
        if not rr.get("notified") and os.path.exists(bl) and E.house_backlog(cfg) == 0:
            hx = scored[scored["chamber"] == "House"] if "chamber" in scored else scored.iloc[0:0]
            yrs = hx.groupby(E.pd.to_datetime(hx["filed_date"]).dt.year).size().to_dict() if len(hx) else {}
            body = (f"<p>All House trade reports have been read again with the fixed reader.</p>"
                    f"<p>House trades on file: {len(hx):,} (before the fix: {rr.get('rows_before') or 'n/a'} raw rows).</p>"
                    "<p>By filing year: " + ", ".join(f"{y}: {n:,}" for y, n in sorted(yrs.items())) + "</p>"
                    "<p>The filter test starts in the next update; the weekly check will redo everything on the full data.</p>")
            if _send_email("Capitol Capital: House trades re-read", body, "Email: sent the House re-read notice"):
                rr["notified"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                json.dump(rr, open(rp, "w"))
    except Exception as e:
        E.log(f"House re-read notice: skipped ({type(e).__name__})")
    if not E.out_of_time(cfg, 80):
        try:
            ft = E.load_filter_state(cfg)
            if E.house_backlog(cfg) > 100:
                E.log(f"Filter test: waiting until House reports are re-read ({E.house_backlog(cfg)} left)")
            elif not ft.get("complete"):
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
        try:       # the backtest's pick rule on the newest filings: bought as soon as seen, stale picks skipped
            live_p, small_f = E.live_picks(scored, px, cfg), E.fresh_small_rows(scored, cfg)
            E.log(f"Live picks: {len(live_p)} fresh main pick(s), {len(small_f)} fresh small-company buy(s)")
        except Exception as e:
            live_p = small_f = None
            E.log(f"Live picks: fell back to the BUY list ({type(e).__name__}: {e})")
        snap = broker.sync(dict(cfg, _hold_policy=E.load_adaptive(cfg)["policy"], _small_slots=sm.get("slots"),
                                _live_picks=live_p, _small_fresh=small_f,
                                _caps=scored.sort_values("filed_date").drop_duplicates("ticker", keep="last")
                                      .set_index("ticker")["market_cap"].dropna().to_dict() if "market_cap" in scored else {},
                                _member_sales=msales, _price_hist=phist), buys, new, last, E.log)
        path = os.path.join(cfg["DATA_DIR"], "dashboard_data.json")
        d = json.load(open(path))
        if snap is not None:
            d["portfolio"] = snap
            cloud_store.save_reasons(snap.get("this_run"), E.log)       # phone trade alerts say why
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
    cloud_store.put("dashboard_data.json", os.path.join(cfg["DATA_DIR"], "dashboard_data.json"), E.log)   # phone app
    try:
        send_weekly_summary()
    except Exception as e:
        E.log(f"Weekly summary: skipped ({type(e).__name__}: {e})")
    try:      # price charts with member trades for the tickers on Today, Defense and My portfolio
        d = json.load(open(os.path.join(cfg["DATA_DIR"], "dashboard_data.json")))
        w = d.get("watchlist") or {}
        ts = {r.get("t") for r in (w.get("buys") or []) + (w.get("sells") or [])}
        ts |= {r.get("t") for r in ((d.get("defense") or {}).get("longs") or [])}
        ts |= {p.get("t") for p in ((d.get("portfolio") or {}).get("positions") or [])}
        charts_path = E.export_ticker_charts(cfg, scored, px, ts)
        upload_to_drive(charts_path, "ticker_charts.json")
        cloud_store.put("ticker_charts.json", charts_path, E.log)
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
