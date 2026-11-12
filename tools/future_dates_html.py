"""Read-only (Nov 12): where does a Thune release page (and his list page) show the full date? Prints <time> tags,
date meta tags, JSON-LD dates and text around month names. Nothing is saved or changed."""
import sys, re, time
sys.path.insert(0, ".")
import requests
from congress_signals.press_backfill import UA
for u in ["https://www.thune.senate.gov/news/press-releases/thune-applauds-tax-reform-conference-committee-agreement/",
          "https://www.thune.senate.gov/news/press-releases/thune-energy-security-is-national-security-2/",
          "https://www.thune.senate.gov/news/press-releases/?e-page-b9709a3=40"]:
    r = requests.get(u, headers=UA, timeout=30)
    h = r.text
    print("==", r.status_code, len(h), u)
    for m in re.findall(r"<time[^>]*>.{0,80}?</time>", h, re.S)[:8]:
        print("  time:", re.sub(r"\s+", " ", m))
    for m in re.findall(r"<meta[^>]+(?:date|time|modified|published)[^>]*>", h, re.I)[:8]:
        print("  meta:", m)
    for m in re.findall(r'"date(?:Published|Modified|Created)"\s*:\s*"[^"]+"', h)[:6]:
        print("  ld:", m)
    for m in re.findall(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}", h)[:6]:
        print("  text:", m)
    for m in re.findall(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", h)[:6]:
        print("  num:", m)
    for m in re.findall(r"wp-content/uploads/(\d{4}/\d{2})", h)[:4]:
        print("  upload:", m)
    i = h.find("news-item")
    if i > 0:
        print("  item:", re.sub(r"\s+", " ", h[i:i+700]))
    time.sleep(3)
