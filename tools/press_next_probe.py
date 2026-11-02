"""Read-only probe (Nov 2 2026): the backup reader on the Next.js House press pages whose library reader came back
empty and whose /feed/ redirects to an unreachable '<member>-admin.house.gov' backend."""
import time
from congress_signals import press_sites as PS

for h in ["moskowitz", "owens", "gottheimer", "luna", "budzinski", "kiley", "cloud", "nikemawilliams",
          "ritchietorres", "gluesenkampperez", "maxmiller"]:
    r = PS.read_site(f"https://{h}.house.gov/press")
    first = (r.get("items") or [{}])[0]
    admin = [u for u in r["links"] if "-admin." in u]
    print(f"{h:18s} {r['how']:18s} {len(r['links']):2d} links  admin-links={len(admin)}  newest={first.get('date')} {first.get('url')}")
    time.sleep(3)
