"""Read-only: every order, fill and saved sale reason for one stock in the trading account, for a question the owner
asked (Oct 29). The repo and its run logs are public, so nothing about the account is printed: the details are
written to out/ ENCRYPTED with a public key whose private half only the person who asked holds.
Usage (Research workflow script): order_lookup.py ACN"""
import sys, os, json, subprocess, datetime as dt
sys.path.insert(0, ".")
import broker
import cloud_store

PUB = """-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA4pd1AsnnJm3Dp4Cq/3W2
FT1lOs039Nk4MyLx8l4HddpnqudYwj89x3U/Koj8n+oVAgqssICang4llmB4Jir5
N5tdX18qoSNYvIwZqPGA9/959zYPk9Juic+aWIxvBUr4Q7haRDiFOCTw1opBMHAd
TzyTkR5M2HaMVobytI6kZOP1Nz+BRtcjM6yyA1xvn2Vv+Kljg2mOTkJehhmSFZ4Y
AhW84mMuN/F3cueRftwvPILd7Paj0XBKALwErqCkYi7XdcQQG6G7v/OqQdyvpNYD
a5++XPs5XAKXRCmXqw7313A34oHDWml4b0R2+7ZokCK86sH4VYn1ijtsvrK8sTuF
lpBQQSTwlRZayper6ysiAz6zgofrVGeTDmEid9c6zezwucRgaKRT5zovjjfdxcm3
XTjp9TwcB/2e5jyihHlD5oj17vdfVRcHYK2w5x1ITQoCCuM2tPUiIfBrwOyX7H1O
2uRLMB1pxsBT+nPJDH444GMxRaOKMvBxQnwIn98wIv0dAgMBAAE=
-----END PUBLIC KEY-----"""
sym = (sys.argv[1] if len(sys.argv) > 1 else "ACN").upper()
s = broker.settings(60)
api = broker.Alpaca(s)
out = {"symbol": sym, "mode": s["mode"], "at": dt.datetime.utcnow().isoformat()}
orders, until = [], None
for _ in range(40):
    p = {"status": "all", "limit": 500, "direction": "desc", "nested": "true", "symbols": sym}
    if until:
        p["until"] = until
    page = api.get("/v2/orders", **p)
    orders += page
    if len(page) < 500:
        break
    until = page[-1]["submitted_at"]
keep = ("id", "client_order_id", "side", "type", "time_in_force", "qty", "notional", "filled_qty", "filled_avg_price",
        "limit_price", "extended_hours", "status", "created_at", "submitted_at", "filled_at", "canceled_at",
        "expired_at", "replaced_at", "replaced_by", "replaces", "order_class")
out["orders"] = [{k: o.get(k) for k in keep} for o in orders]
try:
    out["fills"] = api.get("/v2/account/activities/FILL", direction="desc", page_size=100)
    out["fills"] = [a for a in out["fills"] if a.get("symbol") == sym]
except Exception as e:
    out["fills_error"] = str(e)
try:
    out["position"] = api.get(f"/v2/positions/{sym}")
except Exception as e:
    out["position"] = f"none ({type(e).__name__})"
acct = api.get("/v2/account")
out["account"] = {k: acct.get(k) for k in ("cash", "equity", "buying_power", "last_equity", "status")}
why = []
for k in sorted(cloud_store.list_keys("push/why/"))[-400:]:
    b = cloud_store.get(k)
    if b and sym.encode() in b:
        why.append({"key": k, "body": json.loads(b)})
out["saved_reasons"] = why
os.makedirs("out", exist_ok=True)
open("/tmp/pub.pem", "w").write(PUB)
raw = json.dumps(out, default=str, indent=1).encode()
key = os.urandom(32).hex()
open("/tmp/k", "w").write(key)
subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "file:/tmp/k", "-out", "out/lookup.enc"],
               input=raw, check=True)
subprocess.run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", "/tmp/pub.pem", "-in", "/tmp/k", "-out", "out/key.enc"],
               check=True)
os.remove("/tmp/k")
print(f"Wrote an encrypted lookup for one symbol ({len(out['orders'])} orders, {len(why)} saved reasons)")
