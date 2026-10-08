"""Read-only: compare Alpaca daily bars (SIP, adjusted) with Yahoo for a sample of stocks over the last 2 weeks.
Prints only public market data comparisons."""
import os, time, datetime as dt
import numpy as np, pandas as pd, requests
import yfinance as yf

H = {"APCA-API-KEY-ID": os.environ["ALPACA_PAPER_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["ALPACA_PAPER_SECRET_KEY"]}
px = pd.read_pickle("data/cache/prices.pkl")
cols = [c for c in px.columns if px[c].iloc[-60:].notna().all()][:400]
start = (pd.Timestamp.today() - pd.Timedelta(days=16)).strftime("%Y-%m-%d")
end = (pd.Timestamp.utcnow() - pd.Timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
for feed in ("sip", "iex"):
    t0 = time.time()
    rows, n_req = [], 0
    for i in range(0, len(cols), 200):
        syms = [c.replace("-", ".") for c in cols[i:i + 200]]
        tok = None
        while True:
            p = {"symbols": ",".join(syms), "timeframe": "1Day", "start": start, "end": end, "adjustment": "all",
                 "feed": feed, "limit": 10000}
            if tok:
                p["page_token"] = tok
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=H, params=p, timeout=60)
            n_req += 1
            if r.status_code != 200:
                print(feed, "HTTP", r.status_code, r.text[:200])
                break
            js = r.json()
            for s, bars in (js.get("bars") or {}).items():
                for b in bars:
                    rows.append((s.replace(".", "-"), b["t"], b["o"], b["c"], b["v"]))
            tok = js.get("next_page_token")
            if not tok:
                break
    a = pd.DataFrame(rows, columns=["t", "ts", "o", "c", "v"])
    print(f"{feed}: {len(a)} bars for {a['t'].nunique()} of {len(cols)} stocks, {n_req} requests, {time.time() - t0:.0f}s; sample timestamp {a['ts'].iloc[0] if len(a) else None}")
    if feed == "sip":
        A = a
y = yf.download(cols, start=start, auto_adjust=True, progress=False, threads=True)
A["d"] = pd.to_datetime(A["ts"]).dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
for k, yk in (("c", "Close"), ("o", "Open"), ("v", "Volume")):
    al = A.pivot_table(index="d", columns="t", values=k)
    yy = y[yk]
    common_d = al.index.intersection(yy.index)
    common_c = [c for c in al.columns if c in yy.columns]
    r = (al.loc[common_d, common_c] / yy.loc[common_d, common_c]).stack()
    r = r[np.isfinite(r)]
    dev = (r - 1).abs()
    print(f"{yk}: {len(r)} pairs; Alpaca/Yahoo median {r.median():.5f}; within 0.1%: {(dev < 0.001).mean() * 100:.1f}%; "
          f"within 0.5%: {(dev < 0.005).mean() * 100:.1f}%; worst {dev.max() * 100:.1f}%")
    if k == "o":
        w = dev.sort_values().tail(5)
        print("   biggest open differences:", [(i[0].date().isoformat(), i[1], round(float(al.loc[i]), 2), round(float(yy.loc[i]), 2)) for i in w.index])
