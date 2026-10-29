"""Oct 29 (read-only): what bond and other non-stock rows look like in recent House PTRs, to check the asset-name
reading before it is saved. Reads up to 60 recent House reports slowly (House site) and prints every row whose
asset type isn't a stock, ETF or option, plus a few raw text lines around one. Nothing is written."""
import sys, time, collections
sys.path.insert(0, ".")
from congress_signals import engine as E
idx = E.house_index(2026)
idx = idx[idx["FilingType"].astype(str).eq("P")].sort_values("FilingDate", ascending=False).head(60)
kinds, shown, raw_shown = collections.Counter(), 0, 0
for _, r in idx.iterrows():
    text = E._fetch_house_pdf(2026, str(r["DocID"]))
    time.sleep(1.0)
    if not text:
        continue
    for t in E.parse_house_ptr_text(text):
        kinds[t["asset_type"]] += 1
        if t["asset_type"] in ("ST", "EF", "OP"):
            continue
        if shown < 60:
            print(f"{r['Last']:<14} {str(t['asset_type']):<5} {t['tx_raw']:<12} {t['trade_date']} {t['amt_lo']:>10} | {t['asset']}")
            shown += 1
        if raw_shown < 3 and t["asset_type"] in ("GS", "CS", None):
            i = text.find(t["trade_date"])
            print("   RAW:", repr(text[max(0, i - 300):i + 200]))
            raw_shown += 1
print("asset types seen:", dict(kinds))
