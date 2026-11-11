"""Read-only: where the app's Cisneros APP sell came from (source, report id, filed date, first-seen date),
plus his 2026 entries in the House Clerk index. Usage via research.yml: script = "cisneros_app_look.py"."""
import sys, os, glob, json
sys.path.insert(0, ".")
import pandas as pd
import cloud_store

NAME, T = "cisneros", "APP"
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_colwidth", 80)

print("=== app chart data (ticker_charts.json)")
try:
    ch = json.loads(cloud_store.get("ticker_charts.json", log=lambda m: None) or "{}")
    for x in (ch.get("trades") or {}).get(T) or []:
        if NAME in str(x.get("n", "")).lower():
            print("  ", x)
except Exception as e:
    print("  failed:", type(e).__name__, e)

print("\n=== saved trade files")
for f in sorted(glob.glob("data/**/*", recursive=True)):
    if not f.endswith((".pkl", ".csv")) or os.path.getsize(f) > 400e6:
        continue
    try:
        df = pd.read_pickle(f) if f.endswith(".pkl") else pd.read_csv(f, low_memory=False)
    except Exception:
        continue
    if not isinstance(df, pd.DataFrame) or not len(df):
        continue
    txt = df.astype(str).apply(lambda c: c.str.lower())
    m = txt.apply(lambda c: c.str.contains(NAME, regex=False)).any(axis=1)
    if not m.any():
        continue
    tk = [c for c in df.columns if c.lower() in ("ticker", "symbol", "t")]
    sub = df[m]
    if tk:
        sub = sub[sub[tk[0]].astype(str).str.upper() == T]
    print(f"--- {f}: {int(m.sum())} Cisneros rows, {len(sub)} {T}")
    if len(sub):
        print(sub.tail(10).T.to_string())

print("\n=== first-seen / live state mentioning Cisneros")
for f in glob.glob("data/**/*.json", recursive=True):
    try:
        s = open(f).read()
    except Exception:
        continue
    if NAME in s.lower() and os.path.getsize(f) < 50e6:
        i = s.lower().find(NAME)
        print(f"  {f}: ...{s[max(0, i-300):i+300]}...")

print("\n=== House Clerk 2026 index: Cisneros")
try:
    from congress_signals.engine import house_index
    ix = house_index(2026)
    ix = ix[ix.astype(str).apply(lambda c: c.str.lower().str.contains(NAME, regex=False)).any(axis=1)]
    print(ix.to_string())
except Exception as e:
    print("  failed:", type(e).__name__, e)
