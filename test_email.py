"""Sends one test email so you can confirm email works (Resend from admin@capitolcapital.cc, or Gmail)."""
from congress_signals import mail

if not mail.send("Capitol Capital: test email",
                 "<p>Email from Capitol Capital is working.</p>"
                 f"<p><a href='{mail.app_url('/')}'>Open Capitol Capital</a></p>", print, ok_msg="Test email sent"):
    raise SystemExit("The test email couldn't be sent")
