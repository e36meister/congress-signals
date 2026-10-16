"""Read-only: how often members repeat in press releases what they post on X (Jun 2017 - Jul 2023, the years both
sources cover). For X posts that name a company: share with a press release from the same member within 7 days
that names the same company, and share whose wording is close (same sentence-level text)."""
import sys, os, re, gzip, json, collections
sys.path.insert(0, ".")
import congress_signals.social as S

root = "social/posts"
press = collections.defaultdict(list)          # member -> [(date, tickers, shingles)]


def sh(t):
    w = re.findall(r"[a-z0-9]+", (t or "").lower())
    return {" ".join(w[i:i + 5]) for i in range(max(0, len(w) - 4))}


for fn in sorted(os.listdir(f"{root}/press")):
    if not ("2017" <= fn[:4] <= "2023"):
        continue
    for r in S.read_jsonl(f"{root}/press/{fn}"):
        press[r[1]].append((r[0], set(r[5] or []), sh(r[3])))
print(f"Press releases 2017-2023 loaded for {len(press)} members")

n = co = co_same = close = 0
by_year = collections.defaultdict(lambda: [0, 0])
import datetime as dt
D = lambda s: dt.date.fromisoformat(s[:10])
for fn in sorted(os.listdir(f"{root}/x")):
    for r in S.read_jsonl(f"{root}/x/{fn}"):
        d, key, tk, text, rp = r[0], r[1], set(r[5] or []), r[3], r[9] if len(r) > 9 else ""
        if not tk or rp or not isinstance(key, str) or key.startswith("c:"):
            continue
        n += 1
        x0 = D(d)
        ps = [p for p in press.get(key, []) if abs((D(p[0]) - x0).days) <= 7]
        hit = any(tk & p[1] for p in ps)
        s0 = sh(text)
        cl = any(s0 and len(s0 & p[2]) >= max(3, len(s0) // 4) for p in ps)
        co += bool(ps)
        co_same += hit
        close += cl
        by_year[d[:4]][0] += 1
        by_year[d[:4]][1] += hit
print(f"\nX posts by members naming a company (own posts, not reposts): {n:,}")
print(f"  a press release by the same member within 7 days: {co / n * 100:.0f}%")
print(f"  ...that names the same company: {co_same / n * 100:.0f}%")
print(f"  ...with largely the same wording: {close / n * 100:.0f}%")
print("  by year (same company in a press release within 7 days): " +
      ", ".join(f"{y} {b / a * 100:.0f}%" for y, (a, b) in sorted(by_year.items())))
