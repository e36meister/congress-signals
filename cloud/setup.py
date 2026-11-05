"""One-time and every-deploy Cloudflare setup, run by the 'Deploy phone app' workflow:
makes sure the private R2 bucket and the workers.dev address exist. Prints the app's address (no data)."""
import os, sys, json, random, string
import requests

ACC, TOK = os.environ["CLOUDFLARE_ACCOUNT_ID"].strip(), os.environ["CLOUDFLARE_API_TOKEN"].strip()
API = f"https://api.cloudflare.com/client/v4/accounts/{ACC}"
H = {"Authorization": f"Bearer {TOK}"}
BUCKET = "capitol-capital-data"
FILES = ("dashboard_data.json", "ticker_charts.json")


def fail(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def errs(r):
    try:
        return "; ".join(e.get("message", "") for e in r.json().get("errors", [])) or r.text[:200]
    except Exception:
        return r.text[:200]


# 1. the private storage bucket
r = requests.get(f"{API}/r2/buckets/{BUCKET}", headers=H, timeout=30)
if r.status_code == 403 or (r.status_code >= 400 and "enable R2" in r.text):
    fail("Cloudflare refused R2 access. Turn on R2 in the Cloudflare dashboard (R2 Object Storage, free plan) "
         "and check the API token has 'Workers R2 Storage: Edit'. Details: " + errs(r))
if r.status_code == 404:
    c = requests.post(f"{API}/r2/buckets", headers=H, json={"name": BUCKET}, timeout=30)
    if c.status_code >= 300:
        fail("Couldn't create the R2 bucket: " + errs(c) + " (R2 may need to be turned on in the dashboard first)")
    print("Created the private R2 bucket")
elif r.status_code >= 300:
    fail("Couldn't check the R2 bucket: " + errs(r))

# 2. the workers.dev address
r = requests.get(f"{API}/workers/subdomain", headers=H, timeout=30)
sub = (r.json().get("result") or {}).get("subdomain") if r.status_code < 300 else None
if not sub:
    for _ in range(3):
        name = "capitolcapital-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        c = requests.put(f"{API}/workers/subdomain", headers=H, json={"subdomain": name}, timeout=30)
        if c.status_code < 300:
            sub = name
            break
    if not sub:
        fail("Couldn't set up a workers.dev address: " + errs(c))
url = f"https://capitol-capital.{sub}.workers.dev"
print("App address saved for the email (not printed here, since the run log is public)")
with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
    f.write(f"url={url}\n")

# 3. (removed Nov 5 2026) the first-time copy of the data from Google Drive: Drive is retired, and its copy is no
# longer updated, so a passing R2 error here must never let an old Drive copy overwrite the app's data.
