"""Oct 29 (read-only): builds bonds.json from the saved data exactly as run_daily will, and prints a summary
(market test, top groups, flagged bond trades, corporate bonds matched to a ticker). Nothing is published."""
import sys, json
sys.path.insert(0, ".")
import pandas as pd
import congress_signals.engine as E, congress_signals.bonds_report as BR
cfg = {**E.DEFAULT_CONFIG, "DATA_DIR": "./data"}
sc = pd.read_pickle("data/cache/scored.pkl")
out = BR.build(cfg, sc)
print("counts:", out["counts"], "| bond history from:", out["gauge"]["bond_history_from"])
print("test:", json.dumps(out["gauge"]["test"]))
print("last 6 months:", json.dumps(out["gauge"]["series"][-6:]))
print("\ngroups:")
for g in out["groups"][:25]:
    print("  ", g["kind"], "|", g["name"], g["tilt"], g["members"], g["safety_trades"], g["usd"])
print("\nflagged:")
for b in [b for b in out["bonds"] if b["flag"]][:40]:
    print("  ", b["flag"], b["date"], b["member"], b["tx"], b["lo"], "|", b["asset"], "|", b["why"])
print("\ncorporate bonds with a ticker:", [(b["member"], b["asset"], b["ticker"]) for b in out["bonds"] if b["ticker"]][:20])
print("unmatched corporate:", [b["asset"] for b in out["bonds"] if b["kind"] == "corp_bond" and not b["ticker"]][:20])
print("munis with no state:", [b["asset"] for b in out["bonds"] if b["kind"] == "muni" and not b["state"]][:25])
print("json size:", len(json.dumps(out)))
