"""One-off (owner, Nov 12 2026): queue the newsletter sample to the owner's own address in the app's mail outbox, so
the Send app emails job sends it from admin@capitolcapital.cc. Writes one file to mail/outbox/ and nothing else."""
import sys, json, time
sys.path.insert(0, ".")
import cloud_store

html = open("tools/newsletter_sample.html", encoding="utf-8").read()
msg = {"to": "owner", "subject": "C)C Weekly Mix · Issue 01 (sample) · Nov 6–12", "html": html}
key = f"mail/outbox/{int(time.time() * 1000)}-newsletter-sample.json"
ok = cloud_store.put(key, json.dumps(msg), log=print)
print("queued" if ok else "NOT queued")
if not ok:
    raise SystemExit(1)
