"""Collects institutional data (13D/13G, FINRA short interest, 13F fund holdings) into ./inst, after trading.
Usage: python update_institutions.py [minutes]   (default 8; the one-time history build uses ~300)"""
import os, sys
import congress_signals.engine as E
import congress_signals.institutions as I

cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data", "INST_DIR": "./inst",
       "SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", ""), "TIME_BUDGET_MIN": 0}
I.update_all(cfg, float(sys.argv[1]) if len(sys.argv) > 1 else 8)
