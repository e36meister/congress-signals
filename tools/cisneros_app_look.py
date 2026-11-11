"""Read-only: the House Clerk's 2026 periodic-transaction entries for Cisneros (where his APP sells filed
Nov 10 came from: House report 20035788). Usage via research.yml: script = "cisneros_app_look.py"."""
import sys
sys.path.insert(0, ".")
import pandas as pd
from congress_signals.engine import house_index

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
for live in (False, True):
    try:
        ix = house_index(2026, live=live)
        ix = ix[ix["Last"].astype(str).str.lower().str.contains("cisneros")]
        print(f"=== House Clerk 2026 index (live={live}): {len(ix)} Cisneros PTRs")
        print(ix.tail(8).to_string())
    except Exception as e:
        print(f"=== live={live} failed:", type(e).__name__, e)
print("PTR 20035788: https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035788.pdf")
