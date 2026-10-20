"""Backup reader for members' press-release pages, used when the python-statement reader for a site returns
nothing or only old releases (sites get redesigned and the library's reader goes blind).

How it reads a site:
1. Find the press list: the configured URL if it works, else a link on the home page labelled like
   "Press Releases" / "News Releases" / "Press", else a few common paths.
2. Take every link on that page that points at a single release: a page under the list's own path, a
   documentsingle.aspx page, or a /press|news|media/... page with a slug.
3. WordPress sites: their built-in data feed (/wp-json), which gives each release's date, title and full text.
   This also works for Senate sites whose firewall refuses plain page reads from cloud servers.
4. Otherwise its RSS feed.
Elsewhere the release dates and text come from the release pages themselves (press_backfill._text).

The hourly job uses this for sites in BACKUP (readers known to be blind) and for any reader that errors or
returns nothing."""
import re, html as _html
from urllib.parse import urljoin, urlparse
import requests

# python-statement readers that went blind (checked Oct 20, 2026), with the site's current press page where it moved
BACKUP = {
    "britt": "https://www.britt.senate.gov/", "kim": "https://www.kim.senate.gov/", "paul": "https://www.paul.senate.gov/",
    "comer": "https://comer.house.gov/", "mast": "https://mast.house.gov/",
    "curtis": "https://www.curtis.senate.gov/newsroom/press-releases/",
    "davidson": "https://davidson.house.gov/media/press-releases",
    "landsman": "https://landsman.house.gov/media/press-releases",
    "morrison": "https://morrison.house.gov/news",
    "norcross": "https://norcross.house.gov/media/press-releases",
    "sessions": "https://sessions.house.gov/press-releases",
    "stanton": "https://stanton.house.gov/media/press-releases",
    "sullivan": "https://www.sullivan.senate.gov/newsroom/press-releases/",
    "warren": "https://www.warren.senate.gov/newsroom/press-releases/",
    "wicker": "https://www.wicker.senate.gov/",
    "yakym": "https://yakym.house.gov/media-center/press-releases",
    "dustyjohnson": "https://dustyjohnson.house.gov/media/press-releases",
}
WP_TYPES = ("press_release", "press_releases", "press-releases", "press-release", "pressreleases", "news", "posts")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36 CapitolCapital-research"}
LIST_WORDS = re.compile(r"^\s*(press releases?|news releases?|press|news|newsroom|media center|media)\s*$", re.I)
COMMON = ("/media/press-releases", "/press-releases", "/news/press-releases", "/newsroom/press-releases",
          "/media-center/press-releases", "/news", "/press", "/media", "/news/documentquery.aspx?DocumentTypeID=27")
NOT_RELEASE = re.compile(r"(/page/\d|[?&](page|pagenum|PageNum_rs)=|/tag/|/category/|/feed|/rss|/print/|"
                         r"\.(pdf|jpg|jpeg|png|gif|css|js|xml)$|#|/search|/contact|/subscribe|/email|"
                         r"/(about|services|issues|legislation|district|resources|videos?|photos?|gallery)(/|$))", re.I)
RELEASE_HINT = re.compile(r"/(press|news|media|newsroom|press-releases?|news-releases?|statements?|"
                          r"media-center)/[^/?#]{8,}", re.I)


def _get(url, timeout=30):
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text, r.url
    except Exception:
        return None, "", url


def _anchors(html, base):
    for m in re.finditer(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.I | re.S):
        href = m.group(1).strip()
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        text = re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip()
        yield urljoin(base, href), text


def release_links(html, list_url, limit=25):
    """Links on a press list page that point at single releases, in page order."""
    lu = urlparse(list_url)
    host = lu.netloc.lower().removeprefix("www.")
    lpath = lu.path.rstrip("/")
    out, seen = [], set()
    for u, text in _anchors(html, list_url):
        p = urlparse(u)
        if p.netloc.lower().removeprefix("www.") != host:
            continue
        path = p.path.rstrip("/")
        full = path + ("?" + p.query if p.query else "")
        ok = False
        if "documentsingle.aspx" in path.lower() and "documentid=" in p.query.lower():
            ok = True
        elif NOT_RELEASE.search(full) or path == lpath or not path:
            ok = False
        elif lpath and path.startswith(lpath + "/") and len(path) - len(lpath) > 8:
            ok = True
        elif RELEASE_HINT.search(path):
            ok = True
        key = u.split("#")[0].rstrip("/").lower()
        if ok and key not in seen and len(text) >= 12:
            seen.add(key)
            out.append(u.split("#")[0])
        if len(out) >= limit:
            break
    return out


def rss_links(base, list_url, limit=25):
    """For sites that refuse plain page reads: their RSS feed, if any."""
    root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    tries = [list_url.rstrip("/") + "/feed/", root + "/feed/", root + "/rss/feeds/?type=press",
             root + "/rss.xml", list_url.rstrip("/") + "/rss.xml", root + "/category/press-releases/feed/"]
    for f in tries:
        st, txt, _ = _get(f)
        if st == 200 and ("<rss" in txt[:2000] or "<feed" in txt[:2000]):
            links = re.findall(r"<link>\s*([^<\s]+)\s*</link>", txt) + re.findall(r"<link[^>]+href=\"([^\"]+)\"", txt)
            links = [l for l in links if l.rstrip("/") != root and not l.rstrip("/").endswith(("/feed", ".xml"))]
            if links:
                return f, links[:limit]
    return None, []


def _strip(h):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def wp_items(url, limit=20):
    """WordPress data feed: [{'url','date','title','text'}] newest first, or [] if the site has none."""
    root = f"{urlparse(url).scheme or 'https'}://{urlparse(url).netloc}"
    try:
        r = requests.get(root + "/wp-json/", headers=UA, timeout=30)
        routes = r.json().get("routes", {}) if r.status_code == 200 else {}
    except Exception:
        return []
    have = [t for t in WP_TYPES if f"/wp/v2/{t}" in routes]
    for t in have:
        try:
            r = requests.get(f"{root}/wp-json/wp/v2/{t}", headers=UA, timeout=30,
                             params={"per_page": limit, "_fields": "date,link,title,content"})
            js = r.json() if r.status_code == 200 else []
        except Exception:
            js = []
        if not isinstance(js, list) or not js:
            continue
        items = [{"url": x.get("link"), "date": str(x.get("date") or "")[:10], "title": _strip((x.get("title") or {}).get("rendered")),
                  "text": _strip((x.get("content") or {}).get("rendered"))} for x in js if x.get("link")]
        # a site's generic "posts" can hold a stray page or two; only use it if it's actually kept up
        if t == "posts" and len(items) < 3:
            continue
        return items
    return []


def find_list(url):
    """(list page URL, its html, how found) or (None, '', why)."""
    st, html, final = _get(url) if url else (None, "", url)
    if st == 200 and len(release_links(html, final)) >= 3:
        return final, html, "configured page"
    root = f"{urlparse(url).scheme or 'https'}://{urlparse(url).netloc}"
    st2, home, homef = _get(root + "/")
    if st2 == 200:
        cands = [u for u, t in _anchors(home, homef) if LIST_WORDS.match(t)]
        cands.sort(key=lambda u: 0 if re.search(r"press", u, re.I) else 1)
        for c in cands[:4]:
            s3, h3, f3 = _get(c)
            if s3 == 200 and len(release_links(h3, f3)) >= 3:
                return f3, h3, "home page link"
    for pth in COMMON:
        s4, h4, f4 = _get(root + pth)
        if s4 == 200 and len(release_links(h4, f4)) >= 3:
            return f4, h4, "common path " + pth
    return None, "", f"configured page HTTP {st}, home HTTP {st2}"


def read_site(url):
    """{'list': url, 'how': str, 'links': [...], 'items': [...]} — links newest-first as the site lists them;
    'items' (date, title, text included) when they came from a WordPress feed; empty if blind."""
    items = wp_items(url)
    if items:
        return {"list": url, "how": "WordPress feed", "links": [x["url"] for x in items], "items": items}
    lst, html, how = find_list(url)
    if lst:
        return {"list": lst, "how": how, "links": release_links(html, lst)}
    feed, links = rss_links(url, url)
    if links:
        return {"list": feed, "how": "RSS feed", "links": links}
    return {"list": None, "how": how, "links": []}
