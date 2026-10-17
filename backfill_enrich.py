"""History fill for company lookups, after the update (never slows trading).
Usage: python backfill_enrich.py MINUTES

The update itself only looks up the 800 most-traded companies, and some lookups only return the newest results
(lobbying: newest 500 filings; grants: newest 100; floor speeches: newest 60), so older years and less-traded
companies were missing. This fills every company that was ever traded, with every page since START_DATE,
most-traded first, a slice of time per source; resumable (each company is marked once done)."""
import os, sys, time, json
import pandas as pd
import congress_signals.engine as E

minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 45
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data",
       "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", ""), "CONGRESS_API_KEY": os.environ.get("CONGRESS_API_KEY", ""),
       "LDA_API_KEY": os.environ.get("LDA_API_KEY", "")}
p = os.path.join("data", "cache", "scored.pkl")
if not os.path.exists(p):
    print("No scored trades on file yet; nothing to fill")
    sys.exit(0)
sc = pd.read_pickle(p)
tickers = list(sc["ticker"].value_counts().index)
meta = json.load(open(os.path.join("data", "cache", "ticker_meta.json")))
steps = [("Lobbying", lambda tl: E.collect_lobbying(cfg_, meta, tl, deep=True), "lobbying.json"),
         ("Grants and loans", lambda tl: E.collect_assistance(cfg_, meta, tl, deep=True), "assistance.json"),
         ("Floor speeches", lambda tl: E.collect_speeches(cfg_, meta, tl, deep=True), "speeches.json"),
         ("Contracts", lambda tl: E.collect_contracts(cfg_, meta, tl, deep=True), "contracts.json"),
         ("Company events", lambda tl: E.collect_events(cfg_, meta, tl, deep=True), "company_events.json")]
share = {"Lobbying": 0.35, "Grants and loans": 0.2, "Floor speeches": 0.2, "Contracts": 0.15, "Company events": 0.1}
end = time.time() + minutes * 60


def done_count(fn):
    try:
        j = json.load(open(os.path.join("data", "cache", fn)))
        j = j.get("companies", j) if fn == "speeches.json" else j
        return sum(1 for t in tickers if (j.get(t) or {}).get("deep"))
    except Exception:
        return 0


left_total = 0
for name, fn, cache_file in steps:
    if time.time() > end:
        break
    slice_min = max(2.0, min(minutes * share[name], (end - time.time()) / 60))
    cfg_ = dict(cfg, TIME_BUDGET_MIN=slice_min + 60)        # out_of_time() keeps a 60-minute reserve
    E._RUN_START = time.time()
    before = done_count(cache_file)
    try:
        fn(tickers)
    except Exception as e:
        E.log(f"History fill: {name} failed ({type(e).__name__}: {e})")
    after = done_count(cache_file)
    left_total += len(tickers) - after
    E.log(f"History fill: {name}: {after - before} companies filled this run; {after:,} of {len(tickers):,} done")
E.log(f"History fill: {left_total:,} company lookups still to fill" if left_total else "History fill: complete")
