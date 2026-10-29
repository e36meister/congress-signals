"""Read-only: the Senate filings sent with the iPhone Shortcut. Prints the import log, and for each pending or
failed filing the text around the senator's name and filing date, so a 'no senator name' failure can be traced.
Nothing is written or deleted."""
import json, re, sys
sys.path.insert(0, ".")
import cloud_store
from congress_signals import engine as E
from bs4 import BeautifulSoup
log = json.loads(cloud_store.get("senate_import/log.json", log=lambda m: None) or b"[]")
print("LOG (newest first):")
for h in log[:10]:
    print("  ", {k: h.get(k) for k in ("id", "who", "filed", "rows", "at", "status", "done")})
for pre in ("senate_import/pending/", "senate_import/failed/"):
    keys = [k for k in cloud_store.list_keys(pre, log=lambda m: None) if k.endswith(".json")]
    print(f"\n{pre}: {len(keys)}")
    for k in keys[-6:]:
        rec = json.loads(cloud_store.get(k, log=lambda m: None) or b"{}")
        html = rec.get("html") or ""
        t = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        print("---", k, "| who:", rec.get("who"), "| filed:", rec.get("filed"), "| at:", rec.get("at"), "| html bytes:", len(html))
        print("   parser:", E._ptr_filer(html) if html else None)
        items = E.parse_senate_ptr_html(html) if html else None
        print("   trade rows read:", None if items is None else len(items), [ (t.get("ticker"), t.get("tx_raw"), t.get("trade_date")) for t in (items or [])[:25]])
        i = t.lower().find("honorable")
        print("   text near 'Honorable':", repr(t[max(0, i - 200):i + 300]) if i >= 0 else "(no 'Honorable')")
        print("   page start:", repr(t[:500]))
        for tag in ("h1", "h2", "h3"):
            print(f"   {tag}:", [x.get_text(" ", strip=True)[:120] for x in BeautifulSoup(html, "lxml").find_all(tag)][:6])
