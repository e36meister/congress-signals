"""Daily run on GitHub: collect new filings, score, backtest, build the watchlist,
and update dashboard_data.json in your Google Drive so the dashboard refreshes."""
import os, json, datetime as dt, traceback
import congress_signals.engine as E

env = os.environ.get
cfg = {**E.DEFAULT_CONFIG,
       "DATA_DIR": "./data",
       "SEC_USER_AGENT": env("SEC_USER_AGENT", ""),
       "CONGRESS_API_KEY": env("CONGRESS_API_KEY", ""),
       "LDA_API_KEY": env("LDA_API_KEY", ""),
       "QUIVER_API_KEY": env("QUIVER_API_KEY", ""),
       "FMP_API_KEY": env("FMP_API_KEY", ""),
       "TIINGO_API_KEY": env("TIINGO_API_KEY", ""),
       "USE_TUNED_WEIGHTS": env("USE_TUNED_WEIGHTS", "false").lower() == "true",
       "TIME_BUDGET_MIN": int(env("TIME_BUDGET_MIN", "320"))}
os.makedirs(cfg["DATA_DIR"], exist_ok=True)


def upload_to_drive(local_path, title):
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
    media = MediaFileUpload(local_path, mimetype="application/json", resumable=False)
    if found:
        drive.files().update(fileId=found[0]["id"], media_body=media, supportsAllDrives=True).execute()
        E.log(f"Drive: updated {title}")
    else:
        drive.files().create(body={"name": title, "parents": [folder]}, media_body=media,
                             supportsAllDrives=True).execute()
        E.log(f"Drive: created {title}")


DASHBOARD_URL = "https://claude.ai/artifact/Hkj8H6ZduZXcuvtgSaByru"


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
            f"<p><a href='{DASHBOARD_URL}'>Open the Capitol Trades Desk dashboard</a></p>")
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
    bt = E.run_backtest(scored, px, cfg)
    E.backtest_report(bt, cfg)
    E.export_dashboard(cfg, bt=bt)
    tuned_path = os.path.join(cfg["DATA_DIR"], "state", "tuned_weights.json")
    if dt.date.today().weekday() == 6 or not os.path.exists(tuned_path):     # retune on Sundays
        try:
            tuned = E.tune_weights(scored, px, cfg)
            E.export_dashboard(cfg, tuned=tuned)
        except Exception as e:
            E.log(f"Tuning skipped: {e}")
    buys, sells = E.build_watchlist(scored, px, cfg)
    new = E.diff_alerts(buys, sells, cfg)
    E.watchlist_report(buys, sells, new, cfg)
    E.export_dashboard(cfg, buys=buys, sells=sells, new=new)
    send_alerts(buys, sells, new)
    upload_to_drive(os.path.join(cfg["DATA_DIR"], "dashboard_data.json"), "dashboard_data.json")
    E.log(f"Finished. {len(new)} new signal(s).")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
