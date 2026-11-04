"""Read-only: the SHAPE of the R2 files a family account's app reads (keys, types, list lengths; no values).
Used to build a local test copy of the app for clicking through every family-account page (Nov 4 2026).
Nothing private is printed: strings show only their length, numbers only 'num', and keys that are member ids,
tickers or account ids are collapsed to one example key shape. The run log is public."""
import json, re, sys
sys.path.insert(0, ".")
import cloud_store

ID_KEY = re.compile(r"^([A-Z]\d{6}|n-[a-z0-9-]+|[A-Z][A-Z0-9.-]{0,6}|[a-z0-9]{12}|\d{4}-\d\d-\d\d.*|[A-Z]+\|.*|.*\|.*)$")


def shape(x, depth=0, maxd=7):
    if depth > maxd:
        return "…"
    if isinstance(x, dict):
        ks = list(x.keys())
        idlike = len(ks) > 6 and sum(bool(ID_KEY.match(str(k))) for k in ks) >= 0.8 * len(ks)
        if idlike:      # a map keyed by ids/tickers/dates: show one value's shape
            return {f"<{len(ks)} id-keys, e.g. {kind(ks[0])}>": shape(x[ks[0]], depth + 1, maxd)}
        return {k: shape(v, depth + 1, maxd) for k, v in x.items()}
    if isinstance(x, list):
        if not x:
            return "[]"
        if all(not isinstance(v, (dict, list)) for v in x):
            return f"[{len(x)} x {sorted({kind(v) for v in x})}]"
        # merge the keys seen in the first few elements
        sample = x[:5]
        if all(isinstance(v, dict) for v in sample):
            merged = {}
            for v in sample:
                for k, vv in v.items():
                    merged.setdefault(k, vv)
            return [f"{len(x)} items", shape(merged, depth + 1, maxd)]
        return [f"{len(x)} items", shape(x[0], depth + 1, maxd)]
    return kind(x)


def kind(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    if isinstance(v, str):
        if re.match(r"^\d{4}-\d\d-\d\d", v):
            return "date-str"
        if ID_KEY.match(v) and len(v) <= 8:
            return "ticker/id-str"
        return f"str({len(v)})"
    return type(v).__name__


def show(name, obj):
    print(f"\n===== {name}")
    print(json.dumps(shape(obj), indent=1)[:12000])


def get(name):
    raw = cloud_store.get(name, log=lambda m: None)
    if not raw:
        print(f"\n===== {name}: (missing)")
        return None
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"\n===== {name}: not JSON ({type(e).__name__})")
        return None


d = get("dashboard_data.json")
if d:
    print("\n===== dashboard_data.json top-level keys:", sorted(d.keys()))
    keep = {k: d.get(k) for k in ("version", "updated", "photos", "member_tags", "data_warning", "watchlist") if k in d}
    keep["adaptive.policy"] = (d.get("adaptive") or {}).get("policy")
    keep["small.slots"] = (d.get("small") or {}).get("slots")
    show("dashboard_data.json (viewer parts)", keep)
for n in ("members/index.json", "movers/latest.json", "charts/search_index.json", "charts/trades_by_ticker.json",
          "cache/sec_names.json", "push/pause_state.json"):
    o = get(n)
    if o is not None:
        show(n, o)
mi = get("members/index.json")
if isinstance(mi, dict) or isinstance(mi, list):
    keys = []
    if isinstance(mi, dict):
        for v in (mi.get("members") or mi.get("list") or []):
            if isinstance(v, dict) and v.get("k"):
                keys.append(v["k"])
    if keys:
        show("members/<one member>.json", get(f"members/{keys[0]}.json"))
tc = get("ticker_charts.json")
if isinstance(tc, dict):
    print("\n===== ticker_charts.json top-level keys:", list(tc.keys())[:12], "…")
    show("ticker_charts.json", tc)
idx = get("accounts/index.json")
if isinstance(idx, list):
    print(f"\n===== accounts/index.json: {len(idx)} account(s); fields: {sorted({k for a in idx for k in a})}")
    for i, a in enumerate(idx, 1):
        port = get(f"accounts/{a.get('id')}/portfolio.json")
        print(f"  account {i}: status={a.get('status')} keys={'yes' if a.get('keys_set_at') else 'no'} terms={'yes' if a.get('terms_at') else 'no'}"
              f" started={'yes' if a.get('started_at') else 'no'} paused={'yes' if a.get('paused') else 'no'} portfolio={'yes' if port else 'no'}")
        if port and i == 1:
            show("accounts/<id>/portfolio.json", port)
