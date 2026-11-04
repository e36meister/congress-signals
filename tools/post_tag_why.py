"""Read-only (owner, Nov 4): why a press release was tagged with the companies it was. Reads the release's full text
from the Congress Press monthly file, runs the current tagger, and for each company found prints the words in the
text that made it match (company name, or a person's name with their role). Env: URL (the release), MONTH (YYYY-MM)."""
import sys, os, re, json
sys.path.insert(0, ".")
import requests
import congress_signals.social as S

URL = os.environ.get("URL", "https://salazar.house.gov/media/press-releases/reps-salazar-bera-reintroduce-bipartisan-bill-combat-tuberculosis-worldwide")
MONTH = os.environ.get("MONTH", "2026-09")
r = requests.get(S.PRESS + f"{MONTH}.jsonl", timeout=180, headers=S.UA)
item = None
for ln in r.text.splitlines():
    if URL in ln:
        item = json.loads(ln)
        break
if not item:
    sys.exit(f"not found in {MONTH}")
title, text = item.get("title") or "", item.get("text") or ""
full = title + "\n" + text
print(f"title: {title}\nfull text: {len(full):,} characters\n")
tg = S.Tagger({"DATA_DIR": "data", "SOCIAL_DIR": "social"})
tk, pp, secs, tone = tg.tag(full)
print(f"tickers ({len(tk)}): {sorted(tk)}\npeople: {pp}\nindustries: {secs}\ntone: {tone}\n")
low = full.lower()
people_t = {t for _, t, _ in pp}
for t in sorted(tk):
    words = [w for w in (tg.names.get(t) or []) if len(w) > 2]
    why = "via a person: " + ", ".join(f"{n} ({role})" for n, t2, role in pp if t2 == t) if t in people_t else ""
    hits = []
    for w in words:
        for m in re.finditer(r"\b" + re.escape(w) + r"\b", low):
            hits.append(full[max(0, m.start() - 60):m.end() + 60].replace("\n", " "))
            break
    print(f"{t}: name words {words[:4]} {why}\n   " + (" | ".join(hits[:2]) if hits else "(no name words found in the text)"))
# the tail of the text, where site footers and 'related' lists usually sit
print("\n--- last 1,500 characters of the text ---\n" + full[-1500:])
