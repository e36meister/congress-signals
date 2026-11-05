"""Hourly check for new House and Senate trade filings. Takes a minute or two.
When it finds filings it hasn't seen before, it starts the full update (daily.yml) right away,
which scores them, updates the dashboard and emails any new BUY or avoid signals."""
import os, json, re, datetime as dt
import requests
import congress_signals.engine as E

STATE = "quick_state/seen.json"
LOOKBACK_DAYS = 10


def house_ids():
    today = dt.date.today()
    years = {today.year, (today - dt.timedelta(days=LOOKBACK_DAYS)).year}
    ids = set()
    for y in sorted(years):
        try:
            df = E.house_index(y)
        except Exception as e:
            E.log(f"Quick check: House {y} index failed ({e})")
            continue
        df["FilingDate"] = E.pd.to_datetime(df["FilingDate"], errors="coerce")
        recent = df[df["FilingDate"] >= E.pd.Timestamp(today - dt.timedelta(days=LOOKBACK_DAYS))]
        ids |= {f"H{d}" for d in recent["DocID"].astype(str)}
    return ids


def senate_ids():
    try:
        s = E._senate_session()
        start = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%m/%d/%Y 00:00:00")
        rows = E.senate_list(s, {"start": "0", "length": "100", "report_types": "[11]", "filer_types": "[]",
                                 "submitted_start_date": start, "submitted_end_date": "", "candidate_state": "",
                                 "senator_state": "", "office_id": "", "first_name": "", "last_name": ""})
    except Exception as e:
        E.log(f"Quick check: Senate listing failed ({e})")
        return fmp_senate_ids()
    out = set()
    for row in rows:
        m = re.search(r'href="([^"]+)"', row[3])
        if m:
            out.add("S" + m.group(1))
    return out


FMP_EVERY_MIN = 15          # FMP free plan: 250 requests a day; every 15 min is under 100


def fmp_senate_ids():
    """Backup when the Senate site refuses: new Senate trades from FMP (owner, Oct 27). Each new trade's key is an ID,
    so a new filing starts the full update the same way. Read at most every 15 minutes."""
    key = os.environ.get("FMP_API_KEY")
    if not key:
        E.log("Quick check: no Senate backup (FMP_API_KEY isn't set)")
        return set()
    stamp = os.path.join(os.path.dirname(STATE), "fmp_at.txt")
    try:
        last = dt.datetime.fromisoformat(open(stamp).read().strip())
        if (dt.datetime.now(dt.timezone.utc) - last).total_seconds() < (FMP_EVERY_MIN - 1) * 60:
            prev = os.path.join(os.path.dirname(STATE), "fmp_ids.json")
            return set(json.load(open(prev))) if os.path.exists(prev) else set()
    except Exception:
        pass
    saved, new, err = E.fmp_senate_poll(key, E.log)
    open(stamp, "w").write(dt.datetime.now(dt.timezone.utc).isoformat())
    if err:
        E.log(f"Quick check: FMP Senate backup failed ({err})")
        return set()
    cutoff = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    ids = {"F" + k for k, d in saved.items() if str(d.get("disclosureDate") or "") >= cutoff}
    json.dump(sorted(ids), open(os.path.join(os.path.dirname(STATE), "fmp_ids.json"), "w"))
    E.log(f"Quick check: Senate via FMP backup: {len(new)} new trade(s) ({len(ids)} filed in the last {LOOKBACK_DAYS} days)")
    return ids


def start_full_update():
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        E.log("Quick check: no GitHub token; can't start the full update")
        return False
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/dispatches",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                      json={"ref": os.environ.get("GITHUB_REF_NAME", "main"), "inputs": {"chain": "0"}}, timeout=30)
    E.log(f"Quick check: started the full update (HTTP {r.status_code})")
    return r.status_code == 204


# Nov 5 (owner): nothing uses Google Drive any more (the app reads Cloudflare; the old claude.ai dashboard page and
# its Drive Buy-button order files are retired; the app's own Buy button goes through the Worker). The app's copy
# of dashboard_data.json in Cloudflare is now the only one: Drive's passing errors failed this step 3 times Nov 4-5.


def refresh_portfolio():
    """Read-only: update the dashboard's My portfolio tab from Alpaca. Never trades."""
    import broker
    import cloud_store
    snap = broker.sync({"HOLD_DAYS": int(os.environ.get("HOLD_DAYS", "60"))}, None, None, {}, E.log, trade=False)
    if snap is None:
        return
    data = cloud_store.read_dashboard()         # ReadError -> "portfolio refresh failed (...)"; nothing saved
    try:                                        # undo a waiting cleanup sale that no longer applies
        w = data.get("watchlist") or {}
        caps = {broker.to_alpaca(r["t"]): r.get("cap") for r in (w.get("buys") or []) + (w.get("sells") or []) if r.get("t")}
        if caps:
            broker.recheck_queued_sells(caps, E.log, policy=(data.get("adaptive") or {}).get("policy"))
            snap = broker.sync({"HOLD_DAYS": int(os.environ.get("HOLD_DAYS", "60"))}, None, None, {}, E.log, trade=False) or snap
    except Exception as e:
        E.log(f"Quick check: sell recheck skipped ({type(e).__name__})")
    old = data.get("portfolio") or {}
    if old.get("limits", {}).get("from_policy"):
        snap["limits"] = old["limits"]          # the full update knows the current holding periods; keep them
    data["portfolio"] = snap
    sc_new = None
    # Stop check on the Account page: redone when it's over an hour old or a sale filled since, so a stop sale
    # shows up the same day. The automatic stop switch only runs in the daily update.
    try:
        sc = data.get("stop_check") or {}
        upd = sc.get("updated") or ""
        sold_since = any(o.get("side") == "sell" and (o.get("at") or "") > upd for o in snap.get("orders") or [])
        old_ = not upd or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(upd)).total_seconds() > 3600
        if old_ or sold_since:
            import stop_check
            s_ = broker.settings(int(os.environ.get("HOLD_DAYS", "60")))
            rule = ((data.get("adaptive") or {}).get("policy") or {}).get("price_exit") \
                or (snap.get("limits") or {}).get("price_exit") or "none"
            new_sc = stop_check.check(broker.Alpaca(s_), rule, E.log)
            if sc.get("switched"):
                new_sc["switched"] = sc["switched"]
            data["stop_check"] = sc_new = new_sc
            E.log(f"Quick check: stop check updated ({new_sc['summary']['n']} stop sale(s), "
                  f"{new_sc['summary']['n_done']} past their planned hold)")
    except Exception as e:
        E.log(f"Quick check: stop check failed ({type(e).__name__}: {str(e)[:120]})")
    # Nov 3 review: the syncs and the stop check above take a while; if the full update or the close report wrote
    # the file meanwhile, writing our older copy back would undo them. Re-read it and change only our two parts.
    mine = {"portfolio": data.get("portfolio"), **({"stop_check": sc_new} if sc_new is not None else {})}
    try:
        fresh = cloud_store.read_dashboard()
        fresh.update(mine)
        data = fresh
    except cloud_store.ReadError as e:
        E.log(f"Quick check: couldn't re-read the dashboard before saving ({e}); saving the copy read at the start")
    if cloud_store.save_dashboard(data, E.log):
        E.log("Quick check: portfolio refreshed on the dashboard")


def why_it_moved(force=(), dry=False):
    """Explain big moves in what you hold and the BUY candidates (congress_signals/movers.py); every 15 min at most."""
    import broker, cloud_store
    from congress_signals import movers as M
    if not force and not dry:
        try:
            st = json.loads(cloud_store.get(M.STATE_KEY, log=lambda m: None) or b"{}")
        except Exception:
            st = {}
        if not M.due(st):
            return None
    s = broker.settings(60)
    if not (s["key"] and s["secret"]):
        E.log("Why it moved: no Alpaca keys; can't check prices")
        return None
    h = broker.Alpaca(s).h
    data = json.loads(cloud_store.get("dashboard_data.json", E.log) or b"{}")
    held = [p.get("t") for p in ((data.get("portfolio") or {}).get("positions") or []) if p.get("t")]
    w = data.get("watchlist") or {}
    cands = [r.get("t") for r in w.get("buys") or [] if r.get("action") == "BUY" and r.get("t")]
    info = {r["t"]: {"co": r.get("co"), "sec": r.get("sec")} for r in (w.get("buys") or []) + (w.get("sells") or []) if r.get("t")}
    try:
        trades = (json.loads(cloud_store.get("ticker_charts.json", E.log) or b"{}").get("trades") or {})
    except Exception as e:
        E.log(f"Why it moved: member trades not loaded ({type(e).__name__})")
        trades = {}
    return M.run(h, held, cands, info, trades, E.log, dry=dry, force=tuple(force))


def main():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    seen = set(json.load(open(STATE))) if os.path.exists(STATE) else None
    now = house_ids() | senate_ids()
    if not now:
        E.log("Quick check: couldn't read either filing list; trying again next hour")
        return
    if seen is None:
        E.log(f"Quick check: first run, noted {len(now)} recent filings")
        json.dump(sorted(now), open(STATE, "w"))
        return
    new = now - seen
    if new:
        E.log(f"Quick check: {len(new)} new filing(s): {', '.join(sorted(new)[:10])}")
        if start_full_update():
            seen |= new
    else:
        E.log("Quick check: nothing new")
    # keep only IDs still inside the lookback window so the file stays small
    # (a filing whose update couldn't be started stays unseen, so the next hour tries again)
    json.dump(sorted(seen & now), open(STATE, "w"))


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            refresh_portfolio()
        except Exception as e:
            E.log(f"Quick check: portfolio refresh failed ({type(e).__name__}: {str(e)[:150]})")
        try:
            why_it_moved()
        except Exception as e:
            E.log(f"Why it moved: failed ({type(e).__name__}: {str(e)[:150]})")
