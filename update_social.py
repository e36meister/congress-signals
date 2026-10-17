"""Collects what members of Congress post (press releases, X archive, Bluesky, Mastodon, Substack, YouTube) into
./social. Usage: python update_social.py MINUTES [--daily]
  --daily: fresh posts only (after trading in the daily update); without it, also the one-time history build."""
import os, sys
import congress_signals.engine as E
import congress_signals.social as S

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "SOCIAL_DIR": "./social",
       "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", ""), "TIME_BUDGET_MIN": 0}
marker = os.path.join("social", "needs_more_runs")
if os.path.exists(marker):
    os.remove(marker)
mins = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 10
try:
    ok = S.update_all(cfg, mins, history="--daily" not in sys.argv)
finally:
    from congress_signals import runlog
    runlog.check_social(cfg, S._log, youtube_key=os.environ.get("YOUTUBE_API_KEY"))
if "--daily" not in sys.argv and not ok:
    open(os.path.join("social", "needs_more_runs"), "w").write("1")
