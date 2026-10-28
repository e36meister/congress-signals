"""Oct 28 health check (read-only): is stooq.com reachable from GitHub (daily run got ConnectTimeout twice), and
which tickers did the daily run fail to price (data/ dead/fail lists)?"""
import json, os, time, requests
UA = {"User-Agent": "Mozilla/5.0"}
for t in ("spy", "aapl"):
    for url in (f"https://stooq.com/q/d/l/?s={t}.us&i=d", f"https://stooq.pl/q/d/l/?s={t}.us&i=d"):
        t0 = time.time()
        try:
            r = requests.get(url, headers=UA, timeout=30)
            print(f"{url}: HTTP {r.status_code}, {len(r.text)} bytes, {time.time()-t0:.1f}s, head={r.text[:80]!r}")
        except Exception as e:
            print(f"{url}: {type(e).__name__} after {time.time()-t0:.1f}s")
        time.sleep(2)
for root, _, files in os.walk("data"):
    for f in files:
        if "dead" in f or "fail" in f:
            p = os.path.join(root, f)
            try:
                d = json.load(open(p))
                items = sorted(d.items(), key=lambda kv: str(kv[1]))[-10:] if isinstance(d, dict) else d[-10:]
                print(p, len(d), items)
            except Exception as e:
                print(p, type(e).__name__)
