"""Reads the backlog of scanned House trade reports (older reports and re-reads by a newer reader) after the main
update, so it never delays trading. Uses OCR.space / Google Vision within their free allowances. About 25 minutes
at most; the next update continues where this stopped."""
import os
os.environ["SENATE_SCANS_SEPARATE"] = "0"
import run_daily as R
import congress_signals.engine as E

# the reader stops when fewer than 60 minutes of the budget are left: 85 gives it about 25 minutes
cfg = dict(R.cfg, TIME_BUDGET_MIN=85, HOUSE_PAPER_FRESH_DAYS=None)
try:
    tx = E.collect_house_paper(cfg)
    E.log(f"House scans (after trading): {len(tx)} transactions on file")
except Exception as e:
    E.log(f"House scans: stopped ({type(e).__name__}: {e})")
