"""Family accounts (owner, Nov 2 2026): the tool trades each person's own real-money Alpaca account the same way it
trades the owner's, sized from that account's balance, and saves a snapshot of it for their app.

The account list and each person's keys are kept in R2 by the app's server (cloud/accounts.js). The keys are
encrypted there with AES-GCM, with a key made from the ACCOUNTS_KEY secret (same rule as the server). Nothing about an account (name, email, balances, positions) is ever written to the run log, which
can be public: accounts are numbered in the log, and the account's private id is only used in R2 file names.
"""
import base64, hashlib, json, os, datetime as dt

import cloud_store

IDX = "accounts/index.json"


def _unb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _aes_key():
    src = os.environ.get("ACCOUNTS_KEY") or ""
    return hashlib.sha256(("cc-accounts-v1|" + src).encode()).digest() if len(src) >= 32 else None


def _keys(acct_id, log):
    raw = cloud_store.get(f"accounts/{acct_id}/keys.json", log=lambda m: None)
    if not raw:
        return None
    k = _aes_key()
    if not k:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        box = json.loads(raw)
        pt = AESGCM(k).decrypt(_unb64u(box["iv"]), _unb64u(box["ct"]), f"acct:{acct_id}".encode())
        js = json.loads(pt)
        return js if js.get("key") and js.get("secret") else None
    except Exception:
        return None


def load(log):
    """Accounts the tool trades this run: turned on and with keys saved. [] when there are none (or no Cloudflare)."""
    raw = cloud_store.get(IDX, log=lambda m: None)
    if not raw:
        return []
    try:
        idx = json.loads(raw)
    except Exception:
        log("Family accounts: the account list couldn't be read")
        return []
    out, missing = [], 0
    for a in idx if isinstance(idx, list) else []:
        if a.get("status") == "disabled" or not a.get("keys_set_at"):
            continue
        k = _keys(a.get("id", ""), log)
        if not k:
            missing += 1
            continue
        out.append({"id": a["id"], "auto": a.get("auto", True) is not False, "key": k["key"], "secret": k["secret"]})
    if missing:
        log(f"Family accounts: {missing} account(s) have keys that couldn't be read (ACCOUNTS_KEY missing or changed? "
            "reset their keys on the Accounts page); they're skipped")
    return out


def _member_sold(snap, msales):
    out = {}
    if not msales:
        return out
    for p in (snap or {}).get("positions") or []:
        if p.get("bought"):
            try:
                s_ = msales(p["t"], p["bought"])
            except Exception:
                s_ = None
            if s_:
                out[p["t"]] = {"bought": p["bought"], "sales": s_}
    return out


def run(cfg, buys, new, last_prices, log, trade, msales=None, close_report=None):
    """broker.sync for every family account; saves each snapshot for their app. Returns {id: snapshot}.
    One account failing never stops the others (or the owner's run)."""
    import broker
    accts = load(log)
    snaps = {}
    for i, a in enumerate(accts, 1):
        try:
            with broker.use_account(a):
                snap = broker.sync(dict(cfg, _viewer=True), buys, new, last_prices,
                                   lambda m, i=i: log(f"Family account {i}: {m}"), trade=trade)
        except Exception as e:
            snap = None
            log(f"Family account {i}: failed ({type(e).__name__}); the others go on")
        if snap is None:
            _save(a["id"], None, log, problem="Couldn't reach this Alpaca account on the last update.")
            continue
        snaps[a["id"]] = snap
        _save(a["id"], snap, log, member_sold=_member_sold(snap, msales),
              close_report=(close_report or {}).get(a["id"]))
    if accts:
        log(f"Family accounts: {len(snaps)} of {len(accts)} refreshed" + ("" if trade else " (no trading this step)"))
    return snaps


def _save(acct_id, snap, log, member_sold=None, close_report=None, problem=None):
    key = f"accounts/{acct_id}/portfolio.json"
    old = {}
    try:
        raw = cloud_store.get(key, log=lambda m: None)
        old = json.loads(raw) if raw else {}
    except Exception:
        old = {}
    doc = {"portfolio": snap if snap is not None else old.get("portfolio"),
           "member_sold": member_sold if snap is not None else old.get("member_sold") or {},
           "close_report": close_report or old.get("close_report"),
           "close_history": old.get("close_history") or [],
           "problem": problem,
           "saved": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    cloud_store.put(key, json.dumps(doc, default=str), log=lambda m: None)
    return doc


def close_all(log, send_email=None, notify=None):
    """After the market close: a short report for each family account (their value, the day's change, since they
    started vs the S&P 500), kept on their Account page, sent to their phones and emailed to them."""
    import broker
    accts = load(log)
    if not accts:
        return
    raw_idx = cloud_store.get(IDX, log=lambda m: None)
    emails = {a.get("id"): a.get("email") for a in (json.loads(raw_idx) if raw_idx else [])}
    today = dt.date.today().isoformat()
    for i, a in enumerate(accts, 1):
        try:
            with broker.use_account(a):
                snap = broker.sync({"HOLD_DAYS": 60, "_viewer": True}, None, None, {},
                                   lambda m, i=i: None, trade=False)
                api = broker.Alpaca(broker.settings(60))
                spy = _spy_close(api)
            if snap is None:
                continue
            key = f"accounts/{a['id']}/portfolio.json"
            raw = cloud_store.get(key, log=lambda m: None)
            doc = json.loads(raw) if raw else {}
            hist = [h for h in (doc.get("close_history") or []) if h.get("date") != today]
            hist.append({"date": today, "equity": snap["equity"], "spy": spy})
            hist = [h for h in hist if (h.get("equity") or 0) > 0][-800:]
            rep = _since_start(hist, _flows(api, hist[0]["date"]) if hist else {})
            rep.update({"date": today, "equity": snap["equity"], "day_pl": snap.get("day_pl"),
                        "positions": len([p for p in snap.get("positions") or [] if p.get("sleeve") != "park"])})
            doc.update({"portfolio": snap, "close_report": rep, "close_history": hist, "problem": None,
                        "saved": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            cloud_store.put(key, json.dumps(doc, default=str), log=lambda m: None)
            line = _close_line(rep)
            prefs = _prefs(a["id"])
            if notify and prefs["push"]:
                notify(a["id"], "Market close", line)
            if send_email and prefs["email"] and emails.get(a["id"]):
                send_email(emails[a["id"]], f"Capitol Capital: market close {dt.date.today():%b %-d}", _close_html(rep, snap))
        except Exception as e:
            log(f"Family account {i}: close report failed ({type(e).__name__})")
    log(f"Family accounts: close reports done for {len(accts)} account(s)")


def _prefs(acct_id):
    """Whether they want the daily close by push and by email (More → Notifications in their app). Defaults: push on,
    email off (they're asked about email after a few days of using the app)."""
    try:
        raw = cloud_store.get(f"accounts/{acct_id}/prefs.json", log=lambda m: None)
        p = json.loads(raw) if raw else {}
    except Exception:
        p = {}
    pick = lambda side: (p.get(side) or {}).get("close")
    return {"push": pick("push") is not False, "email": pick("email") is True}


def _flows(api, start):
    """Money they put in or took out after `start` (deposits, withdrawals, transfers), {day: dollars}."""
    out = {}
    for kind in ("CSD", "CSW", "JNLC"):
        try:
            for a in api.get(f"/v2/account/activities/{kind}", direction="asc", after=start, page_size=100):
                d = str(a.get("date") or a.get("transaction_time") or "")[:10]
                if d > start:
                    out[d] = out.get(d, 0.0) + float(a.get("net_amount") or 0)
        except Exception:
            pass
    return out


def _since_start(hist, flows):
    """Their gain since the first close, not counting money put in or taken out: in dollars, and as a percent
    chained day by day (each day's change with that day's deposits taken out first). Each close also gets its
    running index ("idx", 1.0 at the start) for the widget's little chart. The S&P 500 over the same days is kept
    for the "ahead of the S&P since start" box on their Account page."""
    if not hist:
        return {"since_start": None, "spy_since_start": None, "gain": None, "net_in": 0.0, "start": None}
    idx, prev = 1.0, hist[0]
    hist[0]["idx"] = 1.0
    for h in hist[1:]:
        f = sum(v for d, v in flows.items() if prev["date"] < d <= h["date"])
        if prev["equity"]:
            idx *= (h["equity"] - f) / prev["equity"]
        h["idx"] = round(idx, 6)
        prev = h
    net_in = sum(v for d, v in flows.items() if d > hist[0]["date"])
    last, s0 = hist[-1], hist[0].get("spy")
    return {"start": hist[0]["date"], "since_start": idx - 1, "gain": last["equity"] - hist[0]["equity"] - net_in, "net_in": net_in,
            "spy_since_start": (last["spy"] / s0 - 1) if s0 and last.get("spy") else None}


def _spy_close(api):
    try:
        import requests
        js = requests.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot", params={"feed": "iex"},
                          headers=api.h, timeout=30).json()
        return float((js.get("dailyBar") or {}).get("c") or (js.get("latestTrade") or {}).get("p"))
    except Exception:
        return None


def _money(v):
    return "–" if v is None else ("-" if v < 0 else "") + "$" + f"{abs(v):,.0f}"


def _pct(v):
    return "–" if v is None else f"{v * 100:+.2f}%"


def _close_line(rep):
    s = f"Account {_money(rep['equity'])}"
    if rep.get("day_pl") is not None:
        s += f", {'+' if rep['day_pl'] >= 0 else ''}{_money(rep['day_pl'])} today"
    if rep.get("gain") is not None and rep.get("since_start") is not None:
        d0 = dt.date.fromisoformat(rep["start"])
        s += f". Since {d0:%b} {d0.day}: {'+' if rep['gain'] >= 0 else ''}{_money(rep['gain'])} ({_pct(rep['since_start'])})"
    return s + "."


def _close_html(rep, snap):
    import html as H
    rows = "".join(f"<tr><td style='padding:4px 10px 4px 0'>{H.escape(p['t'])}</td><td style='padding:4px 10px;text-align:right'>{_money(p.get('mv'))}</td>"
                   f"<td style='padding:4px 0;text-align:right;color:{'#0B7A4B' if (p.get('pl') or 0) >= 0 else '#B42318'}'>{_pct(p.get('plpc'))}</td></tr>"
                   for p in (snap.get("positions") or []) if p.get("sleeve") != "park")
    from congress_signals import mail
    return (f"<div style='font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#152033;max-width:520px'>"
            f"<h2 style='font-weight:600;margin:0 0 8px'>Market close</h2><p>{H.escape(_close_line(rep))}</p>"
            + (f"<p style='color:#46536A;font-size:13px'>For reference, the S&amp;P 500 over the same time: {_pct(rep['spy_since_start'])}. "
               "Deposits and withdrawals aren't counted as gains.</p>" if rep.get("spy_since_start") is not None else "")
            + (f"<table style='border-collapse:collapse;font-size:14px'><tr><th align=left>Stock</th><th align=right style='padding:0 10px'>Value</th><th align=right>Gain</th></tr>{rows}</table>" if rows else "")
            + f"<p style='margin-top:18px'><a href='{H.escape(mail.app_url('/#port'))}'>Open the app</a></p></div>")
