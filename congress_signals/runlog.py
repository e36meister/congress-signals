"""Collection check (owner, Oct 16: "always log anything that comes back without collecting anything so we can fix it").

Every job (daily update, posts, institutions) ends with a block in its log listing:
  * sources that came back EMPTY (nothing on file at all),
  * sources with NOTHING NEW for longer than they normally go without new data,
  * sources that SHRANK sharply (data lost),
  * every failure line logged during the run (failed / skipped / couldn't / HTTP 4xx-5xx / not set ...).
Saved to <state dir>/collection_check_<job>.json; the daily job puts all three in the app's data.
Nothing here ever raises."""
import os, re, json, hashlib, datetime as dt

LINES = []
FAIL = re.compile(r"\b(fail(?:ed|ure|s)?|skipped|couldn't|could not|not reachable|not accepted|unreadable|errors?|"
                  r"timed? ?out|no answer|refused|blocked|rate.?limit\w*|HTTP [45]\d\d|not set(?: up)?|missing secret|"
                  r"no \w+ secret|gave up|unavailable)\b", re.I)
BENIGN = re.compile(r"\b0 errors?\b|mostly delisted|why \d+ stocks vanished|PerformanceWarning|test run: no orders", re.I)


def capture(line):
    """Called by every log function; keeps only the lines that look like a failure."""
    try:
        s = str(line)
        if FAIL.search(s) and not BENIGN.search(s):
            LINES.append(s.strip()[:300])
    except Exception:
        pass


def _size(x):
    try:
        if x is None:
            return 0
        if hasattr(x, "shape") and len(getattr(x, "shape", ())) >= 1:
            return int(x.shape[0])
        return len(x)
    except Exception:
        return 1


def _fingerprint(x):
    """Cheap change detector: size plus a hash of a sample of the content."""
    try:
        if hasattr(x, "to_csv") and hasattr(x, "shape"):
            n = len(x)
            idx = sorted(set([0, n // 4, n // 2, (3 * n) // 4] + list(range(max(0, n - 50), n)))) if n else []
            body = x.iloc[idx].to_csv(index=False) if n else ""
        elif isinstance(x, dict):
            ks = sorted(map(str, x.keys()))
            body = "|".join(ks[:200] + ks[-200:]) + json.dumps({k: x[k] for k in list(x)[-50:]}, default=str, sort_keys=True)[:200000]
        elif isinstance(x, (list, tuple)):
            body = json.dumps(list(x)[-200:], default=str)[:200000]
        else:
            body = str(x)[:200000]
        return f"{_size(x)}:{hashlib.md5(body.encode('utf-8', 'ignore')).hexdigest()}"
    except Exception:
        return f"{_size(x)}:?"


def dir_snapshot(path):
    """(number of files, total bytes, newest change) of a folder tree: changes whenever new data is written."""
    n = b = 0
    newest = 0.0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    s = os.stat(os.path.join(root, f))
                except OSError:
                    continue
                n += 1
                b += s.st_size
                newest = max(newest, s.st_mtime)
    except Exception:
        pass
    return n, f"{n}:{b}"


def report(job, state_dir, items, log=print, labels=None):
    """items: {name: (size, fingerprint, max_days_without_new_data or None)}. Logs the check, saves it, returns it."""
    labels = labels or {}
    now = dt.datetime.now(dt.timezone.utc)
    path = os.path.join(state_dir, f"collection_check_{job}.json")
    try:
        old = json.load(open(path))
    except Exception:
        old = {}
    prev = old.get("sources") or {}
    sources, problems = {}, []
    for name, (size, fp, max_days) in items.items():
        p = prev.get(name) or {}
        changed = fp != p.get("fp")
        rec = {"size": size, "fp": fp, "changed_at": now.isoformat(timespec="seconds") if changed or not p.get("changed_at")
               else p["changed_at"], "max_days": max_days}
        sources[name] = rec
        lab = labels.get(name, name)
        if size == 0:
            problems.append(f"{lab}: came back empty (nothing on file)")
            continue
        if p.get("size") and size < 0.8 * p["size"] and p["size"] - size > 5:
            problems.append(f"{lab}: shrank from {p['size']:,} to {size:,} since the last run")
        if max_days:
            days = (now - dt.datetime.fromisoformat(rec["changed_at"])).total_seconds() / 86400
            if days > max_days:
                problems.append(f"{lab}: nothing new for {days:.0f} days (normally new data within {max_days})")
    seen, fails = set(), []
    for s in LINES:
        k = re.sub(r"\d[\d,.]*", "#", re.sub(r"^\d\d:\d\d:\d\d\s+", "", s))[:120]
        if k not in seen:
            seen.add(k)
            fails.append(re.sub(r"^\d\d:\d\d:\d\d\s+", "", s))
    res = {"job": job, "at": now.isoformat(timespec="seconds"), "problems": problems, "failures": fails[:80],
           "sources": sources}
    try:
        os.makedirs(state_dir, exist_ok=True)
        json.dump(res, open(path, "w"), indent=1)
    except Exception:
        pass
    try:
        if problems or fails:
            log(f"COLLECTION CHECK ({job}): {len(problems)} source problem(s), {len(fails)} failure line(s)")
            for s in problems:
                log(f"   - {s}")
            for s in fails[:40]:
                log(f"   ! {s}")
        else:
            log(f"COLLECTION CHECK ({job}): every source returned data; no failures logged")
    except Exception:
        pass
    try:
        from . import health
        health.finish(job, problems, fails, log)
    except Exception:
        pass
    return res


# How long each daily-update data set normally goes without anything new (days). None = don't time it.
DAILY_MAX_DAYS = {
    "trades": 4, "insiders": 4, "volume": 4, "contracts": 21, "lobbying": 120, "donations": 120, "bills": 10,
    "employee_donations": 150, "annual_fd": 150, "sec_companies": 45, "meetings": 30, "votes": 30, "speeches": 30,
    "bluesky": 3, "events": 21, "fedreg": 10, "k8": 7, "bill_actions": 10, "campaign_vendors": 150,
    "analyst_history": 10, "assistance": 45, "shares": 120, "legislators": None, "meta": 30, "spouse_ties": None,
    "relative_ties": None, "countries": None, "actions": None, "bill_policy": None,
}
DAILY_LABELS = {
    "trades": "Congress trades (House + Senate)", "insiders": "Company insider trades (SEC)", "volume": "Trading volume",
    "contracts": "Federal contracts", "lobbying": "Lobbying filings", "donations": "Campaign donations",
    "bills": "Bills", "employee_donations": "Company employees' donations", "annual_fd": "Yearly financial disclosures",
    "sec_companies": "SEC company info", "meetings": "Committee hearings and markups", "votes": "Votes",
    "speeches": "Floor speeches", "bluesky": "Bluesky (daily update)", "events": "Company events",
    "fedreg": "Federal Register rules", "k8": "Company 8-K announcements", "bill_actions": "Bill status",
    "campaign_vendors": "Campaign spending", "analyst_history": "Analyst ratings", "assistance": "Federal grants and loans",
    "shares": "Share counts", "legislators": "Members list", "meta": "Company names and sectors",
    "spouse_ties": "Spouse insider matches", "relative_ties": "Relative insider matches",
}


_STAGED = {}


def stage_daily(cfg, data, tx=None):
    """Measured right after collecting (the daily update's data is gone by the end of the run)."""
    try:
        items = {}
        for k, v in (data or {}).items():
            if k.startswith("_") or k not in DAILY_MAX_DAYS:
                continue
            items[k] = (_size(v), _fingerprint(v), DAILY_MAX_DAYS.get(k))
        if tx is not None:
            items["trades"] = (_size(tx), _fingerprint(tx), DAILY_MAX_DAYS["trades"])
        _STAGED["daily"] = (cfg, items)
    except Exception:
        pass


def finish_daily(log=print):
    if "daily" not in _STAGED:
        return None
    cfg, items = _STAGED.pop("daily")
    return report("daily", os.path.join(cfg["DATA_DIR"], "state"), items, log, DAILY_LABELS)


def check_daily(cfg, data, tx=None, log=print):
    items = {}
    for k, v in (data or {}).items():
        if k.startswith("_") or k in ("backtest",) or k not in DAILY_MAX_DAYS:
            continue
        items[k] = (_size(v), _fingerprint(v), DAILY_MAX_DAYS.get(k))
    if tx is not None:
        items["trades"] = (_size(tx), _fingerprint(tx), DAILY_MAX_DAYS["trades"])
    return report("daily", os.path.join(cfg["DATA_DIR"], "state"), items, log, DAILY_LABELS)


SOCIAL_MAX_DAYS = {"press": 3, "bsky": 3, "masto": 30, "substack": 30, "youtube": 10, "x": None, "press_bf": None,
                   "press_wb": None}
SOCIAL_LABELS = {"press": "Press releases (Congress Press)", "bsky": "Bluesky posts", "masto": "Mastodon posts",
                 "substack": "Substack newsletters", "youtube": "YouTube videos", "x": "X archive (2017-2023)",
                 "press_bf": "Press-release backfill (members' sites)", "press_wb": "Press-release backfill (Internet Archive)"}


def check_social(cfg, log=print, youtube_key=None):
    root = os.path.join(cfg.get("SOCIAL_DIR") or "social", "posts")
    items = {}
    for src, mx in SOCIAL_MAX_DAYS.items():
        if src == "youtube" and not youtube_key:
            continue
        n, fp = dir_snapshot(os.path.join(root, src))
        if src == "masto":
            # Oct 31: when this run read every Mastodon account and each was already up to date, a long gap means
            # members aren't posting there, not a broken reader (see social.MASTO_STATUS); accounts that fail are
            # logged as failure lines by collect_mastodon and keep the normal check
            from . import social as _S
            if _S.masto_quiet_ok():
                mx = None
        items[src] = (n, fp, mx)
    return report("social", cfg.get("SOCIAL_DIR") or "social", items, log, SOCIAL_LABELS)


INST_MAX_DAYS = {"sched13": 10, "short": 25, "f13": 110}
INST_LABELS = {"sched13": "13D/13G stake filings", "short": "FINRA short interest", "f13": "13F fund holdings"}


def check_institutions(cfg, log=print):
    root = cfg.get("INST_DIR") or "inst"
    items = {}
    for src, mx in INST_MAX_DAYS.items():
        n, fp = dir_snapshot(os.path.join(root, src))
        items[src] = (n, fp, mx)
    return report("institutions", root, items, log, INST_LABELS)
