"""Read-only probe (Oct 22, part 2): why openFDA finds nothing for any company, and what the 225-byte House press pages are."""
import sys, time, functools, faulthandler, json
sys.path.insert(0, ".")
import requests

print = functools.partial(print, flush=True)
faulthandler.dump_traceback_later(600, exit=True)
BOT = {"User-Agent": "CapitolCapital research (congress trade research; contact via GitHub e36meister/congress-signals)"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}

print("=== openFDA")
F = "https://api.fda.gov/drug/drugsfda.json"
for p in [{"limit": 1}, {"search": "sponsor_name:PFIZER", "limit": 2}, {"search": 'sponsor_name:"PFIZER INC"', "limit": 2},
          {"search": "sponsor_name:pfizer*", "limit": 2}, {"search": 'openfda.manufacturer_name:"pfizer"', "limit": 2},
          {"search": "sponsor_name.exact:\"PFIZER INC\"", "limit": 2}, {"count": "sponsor_name", "limit": 5},
          {"search": 'sponsor_name:"eli lilly"', "limit": 2}, {"search": "sponsor_name:lilly", "limit": 2}]:
    try:
        r = requests.get(F, params=p, headers=BOT, timeout=20)
        t = r.text
        try:
            js = r.json()
            if "results" in js:
                res = js["results"]
                t = f"total={js.get('meta', {}).get('results', {}).get('total')} first={json.dumps(res[0])[:300]}"
        except Exception:
            pass
        print(f"  {p}: {r.status_code} {t[:400]!r}")
    except Exception as e:
        print(f"  {p}: {type(e).__name__}")
    time.sleep(0.5)

print("=== House pages")
for u in ["https://ansari.house.gov/media/press-releases", "https://takano.house.gov/newsroom/press-releases",
          "https://balderson.house.gov/news/documentsingle.aspx?DocumentTypeID=27", "https://comer.house.gov/"]:
    for name, h in (("bot", {"User-Agent": "Mozilla/5.0 (compatible; CapitolCapital research; congress press releases)"}), ("browser", BROWSER)):
        try:
            s = requests.Session()
            r = s.get(u, headers=h, timeout=25)
            print(f"  {name} {u}: {r.status_code} {len(r.content)}B cookies={list(s.cookies.keys())} hdr={dict((k, v) for k, v in r.headers.items() if k.lower() in ('server', 'set-cookie', 'content-type', 'refresh', 'location'))}")
            print(f"     body: {r.text[:400]!r}")
            if len(r.content) < 2000:
                r2 = s.get(u, headers=h, timeout=25)
                print(f"     again with cookies: {r2.status_code} {len(r2.content)}B {r2.text[:200]!r}")
        except Exception as e:
            print(f"  {name} {u}: {type(e).__name__}")
        time.sleep(3)
    for feed in ("/rss.xml", "/news/rss.aspx", "/media/press-releases/rss.xml", "/rss/press-releases.xml"):
        try:
            r = requests.get(u.split("/", 3)[0] + "//" + u.split("/")[2] + feed, headers=BROWSER, timeout=20)
            print(f"     feed {feed}: {r.status_code} {len(r.content)}B {r.text[:120]!r}")
        except Exception as e:
            print(f"     feed {feed}: {type(e).__name__}")
        time.sleep(2)
print("done")
