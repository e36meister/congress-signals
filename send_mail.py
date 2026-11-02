"""Sends the emails the app's server queued in R2 (mail/outbox/*.json: {to, subject, html}), oldest first, then
deletes each one. Sign-in links expire in 20 minutes, so anything older than that is dropped unsent.
The log shows counts only, never an address or a link (the run log can be public)."""
import json, time
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
    if not msg or not msg.get("to") or time.time() - made > 20 * 60:
        dropped += 1
    elif mail.send(str(msg.get("subject", ""))[:140], str(msg.get("html", "")), quiet, to=str(msg["to"])):
        sent += 1
    else:
        failed += 1
        continue                 # left in place: the next run tries again (until it's 20 minutes old)
    cloud_store.delete(k, log=quiet)
print(f"App emails: {sent} sent, {dropped} expired or empty, {failed} failed")
if failed:
    raise SystemExit("Some emails couldn't be sent (check the Gmail app password secret)")
