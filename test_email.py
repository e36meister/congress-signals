"""Sends one test email so you can confirm the Gmail secrets work."""
import os, smtplib
from email.mime.text import MIMEText

addr, pw = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
to = os.environ.get("ALERT_TO") or addr
if not (addr and pw):
    raise SystemExit("GMAIL_ADDRESS or GMAIL_APP_PASSWORD secret is missing")
msg = MIMEText("<p>Email alerts from Capitol Capital are working. You'll get new BUY and avoid signals "
               "and a daily market-close report on the paper account.</p>"
               "<p><a href='https://claude.ai/artifact/Hkj8H6ZduZXcuvtgSaByru'>Open Capitol Capital</a></p>", "html")
msg["Subject"], msg["From"], msg["To"] = "Capitol Capital: test email", addr, to
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(addr, pw.replace(" ", ""))
        s.send_message(msg)
except smtplib.SMTPAuthenticationError:
    raise SystemExit("Gmail rejected the sign-in: check the app password (and that 2-Step Verification is on)")
print("Test email sent")
