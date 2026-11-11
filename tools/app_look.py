"""Read-only: stock_look.py and why_pick.py for APP (owner, Nov 11: Cisneros APP sells)."""
import sys, runpy
for s in ("tools/stock_look.py", "tools/why_pick.py"):
    sys.argv = [s, "APP"]
    print(f"\n########## {s}")
    try:
        runpy.run_path(s, run_name="__main__")
    except Exception as e:
        print("failed:", type(e).__name__, e)
