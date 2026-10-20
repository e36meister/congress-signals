"""Read-only: slowly fetch a few member press pages and print status plus the links on each page."""
import sys, time, re
sys.path.insert(0, ".")
from congress_signals import press_sites as PS
SITES = ["https://comer.house.gov/media", "https://landsman.house.gov/", "https://yakym.house.gov/",
         "https://mast.house.gov/", "https://morrison.house.gov/", "https://norcross.house.gov/press-releases",
         "https://sessions.house.gov/press-releases", "https://stanton.house.gov/", "https://www.britt.senate.gov/",
         "https://www.kim.senate.gov/", "https://www.paul.senate.gov/news/"]
for u in SITES:
    time.sleep(8)
    st, html, final = PS._get(u)
    print(f"\n=== {u} -> {st} {final} {len(html)}b")
    if st != 200:
        print(html[:300].replace("\n", " ")); continue
    seen = set()
    for a, t in PS._anchors(html, final):
        if a in seen or not t: continue
        seen.add(a)
        if re.search(r"press|news|media|release|statement", a + " " + t, re.I):
            print("   ", t[:60], "|", a)
    print("   backup reader:", len(PS.release_links(html, final)), "release links;",
          "scripts:", len(re.findall(r"<script", html)), "json-api hints:", re.findall(r"/(?:api|wp-json|_next)/[^\"' ]{0,60}", html)[:5])
