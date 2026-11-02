"""Sends the emails the app's server queued in R2 (mail/outbox/*.json: {to, subject, html}), oldest first, then
deletes each one. Sign-in links expire in 20 minutes, so older ones are dropped unsent (other emails: 6 hours).
"to": "owner" means the owner's own address (bug reports from family accounts).
The log shows counts only, never an address or a link (the run log can be public)."""
import json, os, time
import cloud_store
from congress_signals import mail

quiet = lambda m: None
keys = sorted(cloud_store.list_keys("mail/outbox/", log=print))
sent = dropped = failed = 0
for k in keys:
    raw = cloud_store.get(k, log=quiet)
    try:
        msg = json.loads(raw) if raw else None
        made = int(k.rsplit("/", 1)[-1].split("-", 1)[0]) / 1000
    except Exception:
        msg, made = None, 0
    life = 20 * 60 if "sign-in" in str((msg or {}).get("subject", "")) else 6 * 3600    # links expire in 20 min
    if not msg or not msg.get("to") or time.time() - made > life:
        dropped += 1
    elif mail.send(str(msg.get("subject", ""))[:140], str(msg.get("html", "")), quiet,
                   to=(os.environ.get("ALERT_TO") or os.environ.get("GMAIL_ADDRESS")) if msg["to"] == "owner" else str(msg["to"])):
        sent += 1
    else:
        failed += 1
        continue                 # left in place: the next run tries again
    cloud_store.delete(k, log=quiet)
print(f"App emails: {sent} sent, {dropped} expired or empty, {failed} failed")
if failed:
    raise SystemExit("Some emails couldn't be sent (check the Gmail app password secret)")
