"""Read-only: how many executive matches the wider (single-name) search finds in stored posts, by confidence."""
import sys, os, collections, random
sys.path.insert(0, ".")
import congress_signals.social as S

cfg = {"DATA_DIR": "data", "SOCIAL_DIR": "social"}
tg = S.Tagger(cfg)
print(f"single-name tables: last names {len(tg.last_idx):,}, first names {len(tg.first_idx):,}")
root = "social/posts"
bins = collections.Counter()
by = collections.Counter()
ex = {"high": [], "medium": [], "low": []}
n = 0
for src in sorted(os.listdir(root)):
    for fn in sorted(os.listdir(os.path.join(root, src))):
        for r in S.read_jsonl(os.path.join(root, src, fn)):
            if str(r[0]) < "2024-10-01":
                continue
            n += 1
            tg.tag(r[3])
            for c in tg.last_cands:
                b = "high" if c["c"] >= 0.75 else "medium" if c["c"] >= 0.45 else "low"
                bins[b] += 1
                by[(b, c["by"])] += 1
                if len(ex[b]) < 400:
                    ex[b].append((c, r[1], str(r[3])[:160]))
print(f"posts checked (last 2 years): {n:,}")
print("matches by confidence:", dict(bins), "| by kind:", dict(by))
random.seed(1)
for b in ("high", "medium", "low"):
    print(f"\n--- {b} examples ---")
    for c, who, txt in random.sample(ex[b], min(25, len(ex[b]))):
        print(f"  {c['c']:.2f} {c['by']:<5} '{c['w']}' -> {c['n']} ({c['t']}, {c['role']}) title={c['title']} co={c['co']} sec={c['sec']} | {who} | {txt}")
