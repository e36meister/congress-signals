"""Read-only probe (Oct 23): why every *.senate.gov request returns HTTP 403 from GitHub since ~10:30 UTC."""
import sys, time, functools, faulthandler, re
sys.path.insert(0, ".")
import requests

print = functools.partial(print, flush=True)
faulthandler.dump_traceback_later(600, exit=True)
UAS = {
    "engine": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-notebook"},
    "browser": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"},
    "bot": {"User-Agent": "CapitolCapital research (congress trade research; contact via GitHub e36meister/congress-signals)"},
    "python": {},
}
try:
    print("runner IP:", requests.get("https://api.ipify.org", timeout=10).text)
except Exception as e:
    print("ip?", e)

for u in ["https://efdsearch.senate.gov/search/home/", "https://www.senate.gov/", "https://www.warren.senate.gov/",
          "https://www.warren.senate.gov/newsroom/press-releases", "https://www.cruz.senate.gov/newsroom/press-releases"]:
    for name, h in UAS.items():
        try:
            r = requests.get(u, headers=h, timeout=25)
            hd = {k: v for k, v in r.headers.items() if k.lower() in ("server", "content-type", "x-cache", "via", "cf-ray", "x-akamai-transformed", "akamai-grn", "x-iinfo", "x-cdn")}
            body = re.sub(r"\s+", " ", r.text[:600])
            print(f"  {name:8} {u}: {r.status_code} {len(r.content)}B {hd}")
            if r.status_code != 200:
                print(f"           body: {body!r}")
        except Exception as e:
            print(f"  {name:8} {u}: {type(e).__name__} {e}")
        time.sleep(2)

print("=== eFD listing (engine path)")
try:
    from congress_signals import engine as E
    s = E._senate_session()
    print("  session cookies:", list(s.cookies.keys()))
    r = s.post(f"{E.EFD}/search/report/data/", data={"start": "0", "length": "5", "report_types": "[11]", "filer_types": "[]",
               "submitted_start_date": "10/01/2026 00:00:00", "submitted_end_date": "", "candidate_state": "",
               "senator_state": "", "office_id": "", "first_name": "", "last_name": ""}, timeout=60,
               headers={"Referer": f"{E.EFD}/search/", "X-CSRFToken": s.cookies.get("csrftoken", "")})
    print(f"  listing: {r.status_code} {r.headers.get('content-type')} {r.text[:500]!r}")
except Exception as e:
    print("  eFD:", type(e).__name__, e)
print("done")
