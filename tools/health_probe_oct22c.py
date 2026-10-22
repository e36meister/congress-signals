"""Read-only check (Oct 22): the new openFDA sponsor lookup (engine._fda_apps) for a few drug makers."""
import sys, functools
sys.path.insert(0, ".")
print = functools.partial(print, flush=True)
import congress_signals.engine as E
for name in ["Pfizer Inc.", "Eli Lilly and Company", "Merck & Co., Inc.", "AbbVie Inc.", "Johnson & Johnson",
             "Bristol-Myers Squibb Company", "Intuitive Surgical, Inc.", "UnitedHealth Group Incorporated"]:
    k = E.company_key(name)
    apps = E._fda_apps(k)
    ap = sum(1 for a in apps for s in a.get("submissions", []) if s.get("submission_status") == "AP")
    print(f"  {name!r} key={k!r}: {len(apps)} applications, {ap} approvals, sponsors={sorted({a.get('sponsor_name') for a in apps})[:6]}")
print("fda done")

print("=== Bluesky (new read rules): 30 least recently read accounts, 90 s cap")
import time, faulthandler
faulthandler.dump_traceback_later(500, exit=True)
import congress_signals.social as S
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social", "TIME_BUDGET_MIN": 0}
st = S._state(cfg)
print("  bsky_v", st.get("bsky_v"), "BSKY_READ_VERSION", S.BSKY_READ_VERSION)
leg = S.legislators(cfg, st)
acc = S.discover_accounts(cfg, leg, st, time.time() + 40)
hmap = S.bluesky_accounts(cfg, leg, acc, st, time.time() + 40)
sub = dict(sorted(hmap.items())[:30])
tg = S.Tagger(cfg)
t = time.time()
ok = S.collect_bluesky(cfg, st, tg, sub, time.time() + 90, ocr_days=3, max_ocr=10)
print(f"  finished={ok} in {time.time() - t:.0f}s; accounts read {sum(1 for h in sub if h in st.get('bsky_last', {}))} of {len(sub)}")
print("fda done")
import os
os._exit(0)
