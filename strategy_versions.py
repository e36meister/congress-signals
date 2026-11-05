"""Strategy version stamps (owner, Nov 5 2026).

Every time the rules the tool trades by change, a new version is logged with its start time and what changed:
changes made by hand (code or settings) and the weekly check's automatic switches alike. Each tool buy belongs to
the version in force when it filled, and the close report shows live results per version against the S&P over
the same days, so improving the strategy never muddies the earlier versions' records.

v1 = the rules in force on Nov 5 2026 (start of the live test). Buys before that are "before version stamps".
A change to the code that alters what gets bought or sold (not a bug fix) needs RULES_CODE bumped by hand,
with a one-line note; settings and weekly-check changes are caught automatically.
Stored in Cloudflare R2 at strategy/versions.json. A failed read never overwrites the log (fails loudly)."""
import json, hashlib, datetime as dt

KEY = "strategy/versions.json"
V1_START = "2026-11-05T21:00:00Z"            # the Nov 5 close (4 PM ET)
RULES_CODE = {"id": 1, "note": "Nov 5 2026 rules"}

PICK_KEYS = ("PICK_PERCENTILE", "PICKS_PER_WEEK", "MAX_PICKS_PER_MEMBER_WEEK", "SKIP_LATE_FILINGS", "NET_SELL_FILTER",
             "SMALL_CAP_USD", "USE_TUNED_WEIGHTS")
LABELS = {"hold_other": "main hold (days)", "hold_small": "smaller-company main hold (days)",
          "hold_sleeve": "small-company hold (days)", "price_exit": "main stop rule", "extend": "main hold-longer rule",
          "sleeve_price_exit": "small-company stop rule", "sleeve_extend": "small-company hold-longer rule",
          "signals_on": "signals switched on", "sizing": "position sizing", "main_method": "score method",
          "sleeve_coverage": "small-company analyst rule", "exit_on_member_sell": "sell when a member sells",
          "sale_discount": "weak-sales filter", "rare_weights": "rare-signal weights", "use_tuned": "tuned weights",
          "weights": "signal weights", "small_pct": "small-company share", "rules_code": "rules in the code",
          "PICK_PERCENTILE": "score bar (percentile)", "PICKS_PER_WEEK": "main picks per week",
          "MAX_PICKS_PER_MEMBER_WEEK": "picks per member per week", "SKIP_LATE_FILINGS": "skip late filings",
          "NET_SELL_FILTER": "skip when members are net selling", "SMALL_CAP_USD": "small-company size cutoff",
          "USE_TUNED_WEIGHTS": "tuned weights setting"}


def fingerprint(cfg, adaptive, small_pct):
    """What decides buys and sells, as plain values (rounded so tiny refits don't count as a new version)."""
    import congress_signals.engine as E
    pol = dict(adaptive.get("policy") or {})
    pol["signals_on"] = sorted(pol.get("signals_on") or [])
    pol["rare_weights"] = {k: round(float(v), 2) for k, v in sorted((pol.get("rare_weights") or {}).items())}
    try:
        bw = E.active_weights(cfg)[0]
        w = {k: round(float(v), 2) for k, v in sorted(bw.items()) if v}
    except Exception as e:
        w = {"_error": type(e).__name__}
    fp = {**{k: pol[k] for k in sorted(pol) if k in LABELS}, "use_tuned": bool(adaptive.get("use_tuned")),
          **{k: cfg.get(k) for k in PICK_KEYS}, "small_pct": float(small_pct), "rules_code": RULES_CODE["id"],
          "weights_hash": hashlib.sha1(json.dumps(w, sort_keys=True).encode()).hexdigest()[:10], "weights": w}
    return fp


def _describe(old, new):
    out, auto_only = [], True
    for k in sorted(set(old) | set(new)):
        if k in ("weights_hash",) or old.get(k) == new.get(k):
            continue
        if k == "weights":
            ch = sorted(s for s in set(old.get(k) or {}) | set(new.get(k) or {})
                        if (old.get(k) or {}).get(s) != (new.get(k) or {}).get(s))
            out.append(f"signal weights changed ({', '.join(ch[:6])}{'…' if len(ch) > 6 else ''})")
            continue
        if k == "rules_code":
            out.append(f"rules in the code: {RULES_CODE['note']}")
            auto_only = False
            continue
        if k in PICK_KEYS or k == "small_pct":
            auto_only = False
        if k == "signals_on":
            on = sorted(set(new.get(k) or []) - set(old.get(k) or []))
            off = sorted(set(old.get(k) or []) - set(new.get(k) or []))
            out.append("signals " + "; ".join(x for x in (f"on: {', '.join(on)}" if on else "", f"off: {', '.join(off)}" if off else "") if x))
            continue
        out.append(f"{LABELS.get(k, k)}: {old.get(k)} → {new.get(k)}")
    return out, auto_only


def load(cloud_store):
    """The version log, oldest first. Raises cloud_store.ReadError if R2 didn't answer."""
    raw = cloud_store.get_strict(KEY)
    return json.loads(raw) if raw else []


def stamp(cfg, adaptive, small_pct, cloud_store, log, by_hint=None):
    """Log a new version if the rules changed since the last one. Returns the current version number (or None)."""
    try:
        vs = load(cloud_store)
    except cloud_store.ReadError as e:
        log(f"Strategy versions: FAILED to read the version log ({e}); not changed this run")
        return None
    fp = fingerprint(cfg, adaptive, small_pct)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not vs:
        vs = [{"v": 1, "start": V1_START if now >= V1_START else now, "by": "owner",
               "what": ["the rules in force on Nov 5 2026 (start of the live test)"], "fp": fp}]
    elif vs[-1]["fp"] != fp:
        what, auto_only = _describe(vs[-1]["fp"], fp)
        if not what:                    # only the stored form changed (e.g. a new field): update in place
            vs[-1]["fp"] = fp
        else:
            vs.append({"v": vs[-1]["v"] + 1, "start": now, "by": by_hint or ("weekly check" if auto_only else "owner / code"),
                       "what": what, "fp": fp})
            log(f"Strategy versions: v{vs[-1]['v']} starts now ({vs[-1]['by']}): {'; '.join(what)[:300]}")
    else:
        return vs[-1]["v"]
    if not cloud_store.put(KEY, json.dumps(vs, indent=1, default=str), log):
        log("Strategy versions: FAILED to save the version log; the next run tries again")
    return vs[-1]["v"]


def version_at(vs, when):
    """Version number in force at an ISO time (None = before version stamps)."""
    cur = None
    for v in vs:
        if v["start"] <= when:
            cur = v["v"]
    return cur
