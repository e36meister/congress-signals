"""Data health (owner, Oct 20: "if anything is broken, it should never just return nothing and be skipped. that goes
for every metric"). Nothing here ever raises.

1. Every web request any job makes is tallied per site (requests is patched once at import). A site where every
   request failed, most requests failed, or nearly everything came back "not found" is a problem even when the
   code that asked quietly carried on with nothing.
2. Each job ends with finish(job, problems, failures): the job's own checks (empty / shrank / nothing new for too
   long, from runlog) plus every failure line it logged plus the web-request problems. Saved to Cloudflare
   (health/jobs/<job>.json), which the app's Data health page shows.
3. A problem that is new (or back after being fixed for a day) sends ONE phone alert per run listing them.
4. A job that crashes still reports (exit hook), and a job that stops reporting at all is flagged by the next
   job that does (watchdog).
5. critical(): the checks that feed the picks. When one fails, the daily run marks the picks and pauses new
   auto-buys until it's fixed."""
import os, re, sys, json, time, atexit, datetime as dt
from urllib.parse import urlparse

JOB_KEY = "health/jobs/{}.json"        # one file per job, so jobs finishing together never overwrite each other
SIZES_KEY = "health/sizes.json"
_HTTP = {}                 # host -> {"n", "ok", "nf" (404/410), "bad" (other 4xx/5xx), "exc", "last"}
_JOB = {"name": None, "finished": False, "crash": None, "t0": time.time()}
# how often each job normally reports (hours); a job silent for longer is flagged
EXPECT_HOURS = {"daily": 30, "live": 3, "social": 30, "close": 80, "quick": 6, "institutions": 30,
                "scan_senate": 30, "scan_house": 30, "backfill_enrich": 30}
JOB_NAMES = {"daily": "Daily update", "live": "Hourly posts", "social": "Posts (twice daily)", "close": "Market-close report",
             "quick": "Filings and orders check", "institutions": "Institutions", "watchdog": "Job watchdog",
             "scan_senate": "Scanned Senate reports", "scan_house": "Scanned House reports",
             "backfill_enrich": "Older years and less-traded companies"}
# hosts that are ours or not data sources
SKIP_HOSTS = ("github.com", "githubusercontent.com", "pypi.org", "files.pythonhosted.org", "huggingface.co",
              "hf.co", "cdn-lfs.huggingface.co")
REPUSH_AFTER_H = 24        # a problem fixed and back within this many hours doesn't alert again
ALERT_AFTER_RUNS = 2       # owner, Oct 21: push only for problems that last this many runs in a row...
ALERT_AT_ONCE = ("critical", "crash")      # ...except these (and the watchdog), which push right away


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _log(msg, log=None):
    try:
        (log or print)(msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------- web requests
SCRIPT_JOBS = {"run_daily": "daily", "posts_live": "live", "close_report": "close", "quick_check": "quick",
               "update_social": "social", "update_institutions": "institutions", "scan_house": "scan_house",
               "scan_senate": "scan_senate", "backfill_enrich": "backfill_enrich", "backfill_archive": "archive"}
_CRIT = []                 # critical() results this run (picks paused), reported with the job's problems


def install(job=None):
    """Patch requests once so every call is tallied; register the exit hook. Safe to call many times.
    The job is named after the script that's running (research tools in tools/ don't report)."""
    if not _JOB["name"]:
        _JOB["name"] = job or SCRIPT_JOBS.get(os.path.splitext(os.path.basename(sys.argv[0] if sys.argv else ""))[0])
    if getattr(install, "_done", False):
        return
    install._done = True
    try:
        import requests
        orig = requests.Session.send

        def send(self, request, **kw):
            host = (urlparse(getattr(request, "url", "") or "").hostname or "?").lower().removeprefix("www.")
            h = _HTTP.setdefault(host, {"n": 0, "ok": 0, "nf": 0, "bad": 0, "exc": 0, "last": ""})
            h["n"] += 1
            try:
                r = orig(self, request, **kw)
            except Exception as e:
                h["exc"] += 1
                h["last"] = type(e).__name__
                raise
            sc = getattr(r, "status_code", 0) or 0
            if sc < 400:
                h["ok"] += 1
            elif sc in (404, 410):
                h["nf"] += 1
                h["last"] = f"HTTP {sc}"
            else:
                h["bad"] += 1
                h["last"] = f"HTTP {sc}"
            return r
        requests.Session.send = send
    except Exception:
        pass
    try:
        prev = sys.excepthook

        def hook(t, v, tb):
            _JOB["crash"] = f"{t.__name__}: {str(v)[:200]}"
            prev(t, v, tb)
        sys.excepthook = hook
        atexit.register(_at_exit)
    except Exception:
        pass


def http_problems():
    out = []
    for host, h in sorted(_HTTP.items()):
        if any(host == s or host.endswith("." + s) for s in SKIP_HOSTS):
            continue
        fail = h["bad"] + h["exc"]
        if h["n"] >= 2 and h["ok"] == 0 and h["nf"] < h["n"]:
            out.append(f"{host}: every request failed ({h['n']} tries; last: {h['last'] or 'error'})")
        elif fail >= 5 and fail >= 0.3 * h["n"]:
            out.append(f"{host}: {fail} of {h['n']} requests failed (last: {h['last'] or 'error'})")
        elif h["n"] >= 20 and h["nf"] >= 0.95 * h["n"]:
            out.append(f"{host}: {h['nf']} of {h['n']} requests came back 'not found'; the site's address may have changed")
    return out


def http_summary():
    return {h: dict(v) for h, v in _HTTP.items() if v["n"]}


# ---------------------------------------------------------------------------------------------- state + alerts
def _key(s):
    return re.sub(r"\d[\d,.:%]*", "#", re.sub(r"\s+", " ", str(s))).strip().lower()[:160]


def _load(key):
    try:
        import cloud_store
        raw = cloud_store.get(key, log=lambda m: None)
        st = json.loads(raw) if raw else {}
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _save(key, st):
    try:
        import cloud_store
        cloud_store.put(key, json.dumps(st, default=str), log=lambda m: None)
    except Exception:
        pass


def finish(job, problems=(), failures=(), log=None, extra=None):
    """Record this run's problems for `job`, alert on new ones, flag silent jobs. Returns the job's open problems."""
    _JOB["finished"] = True
    try:
        now = _now()
        items = [("critical", "New auto-buys paused: " + p) for p in _CRIT]
        items += [("problem", p) for p in (problems or [])]
        items += [("site", p) for p in http_problems()]
        if _JOB["crash"]:
            items.append(("crash", f"the run stopped with an error: {_JOB['crash']}"))
        items += [("failure", re.sub(r"^\d\d:\d\d:\d\d\s+", "", str(f))) for f in (failures or [])]
        j = _load(JOB_KEY.format(job))
        old = {x["k"]: x for x in j.get("open") or []}
        fixed = j.setdefault("fixed", [])
        recent_fix = {x["k"]: x for x in fixed}
        open_, new = [], []
        for kind, text in items:
            k = _key(text)
            if any(x["k"] == k for x in open_):
                continue
            if k in old:
                # records from before Oct 21 were alerted (or quieted) when they first appeared
                rec = {**old[k], "text": text, "last": now.isoformat(timespec="seconds"), "runs": old[k].get("runs", 1) + 1,
                       "alerted": old[k].get("alerted", True)}
            else:
                rec = {"k": k, "kind": kind, "text": text, "first": now.isoformat(timespec="seconds"),
                       "last": now.isoformat(timespec="seconds"), "runs": 1, "alerted": False}
                f = recent_fix.get(k)
                if f and (now - dt.datetime.fromisoformat(f["at"])).total_seconds() < REPUSH_AFTER_H * 3600:
                    rec["alerted"] = True          # came back within a day of being fixed: no second alert
            # owner, Oct 21: alert only when a problem lasts (2 runs in a row); picks paused or a crash alert at once.
            # A one-run hiccup still shows on the Data health page.
            if not rec["alerted"] and (rec["runs"] >= ALERT_AFTER_RUNS or kind in ALERT_AT_ONCE):
                rec["alerted"] = True
                new.append(rec)
            open_.append(rec)
        for k, rec in old.items():
            if k not in {x["k"] for x in open_}:
                fixed.append({"k": k, "job": job, "text": rec["text"], "at": now.isoformat(timespec="seconds"),
                              "first": rec.get("first")})
        j["fixed"] = [x for x in fixed if (now - dt.datetime.fromisoformat(x["at"])).days < 14][-100:]
        j.update({"job": job, "at": now.isoformat(timespec="seconds"), "open": open_[:200], "minutes": round((time.time() - _JOB["t0"]) / 60, 1),
                  "http": {h: v for h, v in http_summary().items() if v["bad"] or v["exc"] or v["nf"]}})
        if extra:
            j["extra"] = extra
        _save(JOB_KEY.format(job), j)
        # watchdog: any job that has gone quiet for much longer than usual
        wd = []
        wj = _load(JOB_KEY.format("watchdog"))
        since = wj.get("since") or now.isoformat(timespec="seconds")     # when the watchdog first ran
        for name, hours in EXPECT_HOURS.items():
            if name == job:
                continue
            at = (_load(JOB_KEY.format(name)) or {}).get("at")
            if at and (now - dt.datetime.fromisoformat(at)).total_seconds() > hours * 3600:
                wd.append(f"{JOB_NAMES.get(name, name)} hasn't reported in {(now - dt.datetime.fromisoformat(at)).total_seconds() / 3600:.0f} hours "
                          f"(normally every {hours} or less); it may be stuck or failing to start")
            elif not at and (now - dt.datetime.fromisoformat(since)).total_seconds() > hours * 3600:
                # never reported at all (Oct 21: the daily posts update timed out every run and so never reported)
                wd.append(f"{JOB_NAMES.get(name, name)} hasn't reported in {(now - dt.datetime.fromisoformat(since)).total_seconds() / 3600:.0f} hours "
                          f"(normally every {hours} or less); it may be stuck, timing out, or missing its Cloudflare settings")
        wold = {x["k"]: x for x in wj.get("open") or []}
        wopen = []
        for text in wd:
            k = _key(text.split(" hasn't")[0])
            rec = wold.get(k) or {"k": k, "kind": "watchdog", "first": now.isoformat(timespec="seconds"), "runs": 0}
            rec = {**rec, "text": text, "last": now.isoformat(timespec="seconds"), "runs": rec.get("runs", 0) + 1}
            if k not in wold:
                new.append(rec)
            wopen.append(rec)
        wfix = [{"k": k, "job": "watchdog", "text": r["text"], "at": now.isoformat(timespec="seconds")}
                for k, r in wold.items() if k not in {x["k"] for x in wopen}]
        _save(JOB_KEY.format("watchdog"), {"job": "watchdog", "at": now.isoformat(timespec="seconds"), "since": since, "open": wopen,
                                           "fixed": ((wj.get("fixed") or []) + wfix)[-50:]})
        if new:
            _alert(job, new, log)
        _log(f"DATA HEALTH ({job}): {len(open_)} open problem(s), {len(new)} new" +
             (f", {len(old) - len([k for k in old if k in {x['k'] for x in open_}])} fixed" if old else ""), log)
        return open_
    except Exception as e:
        _log(f"DATA HEALTH ({job}): couldn't save ({type(e).__name__}: {e})", log)
        return []


def _alert(job, new, log):
    try:
        import cloud_store
        jn = JOB_NAMES.get(job, job)
        if len(new) == 1:
            title = f"Data problem: {_short(new[0]['text'])}"
        else:
            title = f"{len(new)} new data problems ({jn})"
        body = "\n".join("• " + x["text"][:160] for x in new[:6]) + (f"\n…and {len(new) - 6} more" if len(new) > 6 else "")
        cloud_store.notify(title, body, tag="health", url="/#health",
                           items=[{"l": x["text"][:200], "u": "/#health"} for x in new[:20]], log=lambda m: None)
    except Exception as e:
        _log(f"DATA HEALTH: alert not sent ({type(e).__name__})", log)


def _short(t):
    t = str(t)
    return (t[:70] + "…") if len(t) > 72 else t


def _at_exit():
    """A job that never called finish() (crashed, or just a script) still reports."""
    if _JOB["finished"] or not _JOB["name"]:
        return
    try:
        from congress_signals import runlog
        fails = sorted(set(runlog.LINES))
    except Exception:
        fails = []
    finish(_JOB["name"], [], fails)


# ---------------------------------------------------------------------------------------------- picks safety
def critical(cfg, part_sizes, px, prev=None):
    """Problems in the data the picks are built from. Empty list = safe to buy."""
    out = []
    try:
        st = prev if prev is not None else _load(SIZES_KEY)
        last = st.get("trades") or {}
        for part, need in (("house_clerk", True), ("senate_efd", True)):
            n = int(part_sizes.get(part) or 0)
            if need and n == 0:
                out.append(f"No {'House' if 'house' in part else 'Senate'} trade filings were read this run")
            elif last.get(part) and n < 0.8 * last[part]:
                out.append(f"{'House' if 'house' in part else 'Senate'} trade filings shrank from {last[part]:,} to {n:,}")
        for host in ("disclosures-clerk.house.gov", "efdsearch.senate.gov"):
            h = _HTTP.get(host)
            if h and h["n"] >= 2 and h["ok"] == 0:
                out.append(f"The {'House' if 'house' in host else 'Senate'} filings site refused every request ({h['last']})")
        if px is not None and len(px):
            spy = px["SPY"].dropna() if "SPY" in px else px.dropna(how="all")
            if len(spy):
                lastd = spy.index[-1].date()
                bdays = int(sum(1 for i in range(1, (dt.date.today() - lastd).days + 1)
                                if (lastd + dt.timedelta(days=i)).weekday() < 5))
                if bdays > 2:
                    out.append(f"Prices are stale: newest price is from {lastd} ({bdays} trading days old)")
        else:
            out.append("No price data loaded")
        _CRIT[:] = out
        if not out:          # only remember sizes from a healthy run
            _save(SIZES_KEY, {"trades": {k: int(v) for k, v in part_sizes.items()}, "at": _now().isoformat(timespec="seconds")})
    except Exception as e:
        out.append(f"Couldn't check the data the picks use ({type(e).__name__})")
        _CRIT[:] = out
    return out
