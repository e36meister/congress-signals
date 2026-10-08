"""Reads scanned Senate trade reports with the built-in text reader (tesseract), after the main update.
No daily limit, so OCR.space's allowance goes to the House's scanned reports. Runs in its own step so a crash
in the reader can't stop the update; the next update picks up whatever was read. About 25 minutes at most."""
import os
os.environ["SENATE_SCANS_SEPARATE"] = "0"
import run_daily as R
import congress_signals.engine as E

# the reader stops when fewer than 160 minutes of the budget are left: 185 gives it about 25 minutes
cfg = dict(R.cfg, OCR_SPACE_API_KEY="", GOOGLE_VISION_API_KEY="", TIME_BUDGET_MIN=185, SENATE_PAPER_READ=True)
try:
    tx = E.collect_senate_paper(cfg)
    E.log(f"Senate scans (built-in reader): {len(tx)} transactions on file")
except Exception as e:
    E.log(f"Senate scans: stopped ({type(e).__name__}: {e})")
