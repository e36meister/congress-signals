"""Read-only check (Oct 23) of the Senate-firewall changes: the eFD list now fails with a plain reason, and the
hourly members'-site reader notices the Senate block up front (all sites still counted as failed)."""
import sys, time, functools, faulthandler, datetime as dt
sys.path.insert(0, ".")
print = functools.partial(print, flush=True)
faulthandler.dump_traceback_later(900, exit=True)
import congress_signals.engine as E
import congress_signals.social as S
import congress_signals.press_backfill as PB
from python_statement.config import SCRAPER_CONFIG
import quick_check as Q

t = time.time()
print("quick_check.senate_ids ->", len(Q.senate_ids()), f"({time.time() - t:.0f}s)")
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social", "TIME_BUDGET_MIN": 0}
st = S._state(cfg)
leg = S.legislators(cfg, st)
sen = [n for n in sorted(SCRAPER_CONFIG) if "senate.gov" in str((SCRAPER_CONFIG[n] or {}).get("url_base") or (SCRAPER_CONFIG[n] or {}).get("url") or "")][:8]
house = [n for n in sorted(SCRAPER_CONFIG) if "house.gov" in str((SCRAPER_CONFIG[n] or {}).get("url_base") or (SCRAPER_CONFIG[n] or {}).get("url") or "")][:4]
print("sample", sen, house)
t = time.time()
n = PB.latest_current(cfg, S.Tagger(cfg), leg, time.time() + 300, workers=4, only=sen + house)
print("added", n, f"({time.time() - t:.0f}s)")
print("done")
