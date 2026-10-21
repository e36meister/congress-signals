"""Read-only check of the Oct 21 repairs on GitHub: news via Google News, the Alpaca second opinion on set-aside
stocks, and how fast the Congress Press month refresh is now (written to a temp copy, nothing saved back)."""
import sys, os, json, time, glob, shutil, tempfile
sys.path.insert(0, ".")
import pandas as pd
import congress_signals.engine as E

print("=== News")
t0 = time.time()
for name in ("Nvidia", "Lockheed Martin", "Palantir", "Microsoft", "Boeing"):
    print(" ", name, E._news(name), "| with Pelosi:", E.news_together("Pelosi", name))
print(f"  {time.time() - t0:.0f}s")

print("=== Set-aside stocks: Alpaca second opinion")
bad = json.load(open("data/state/bad_prices.json"))
px = pd.read_pickle("data/cache/prices.pkl") if os.path.exists("data/cache/prices.pkl") else None
if px is None:
    print("  no price file found:", glob.glob("data/**/*.pkl", recursive=True)[:20])
else:
    px = px[[c for c in px.columns if c in bad]]
    keep, real = E.confirm_bad_prices(bad, px, {})
    print(f"  {len(bad)} set aside before; Alpaca confirms {len(real)} are real: {real}")
    print(f"  still set aside {len(keep)}: {sorted(keep)}")

print("=== Congress Press month refresh (temp copy)")
import congress_signals.social as S
tmp = tempfile.mkdtemp()
shutil.copytree("social/posts/press", os.path.join(tmp, "posts", "press"))
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": tmp}
tg = S.Tagger(cfg)
st = {}
t0 = time.time()
S.collect_press(cfg, st, tg, time.time() + 1500, years=False)
print(f"  press months refreshed in {(time.time() - t0) / 60:.1f} min")
