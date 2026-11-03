"""Sends one test email so you can confirm email works (Resend from admin@capitolcapital.cc, or Gmail)."""
import sys, os as _os
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "congress_signals"))
import mail                      # just the email file, not the whole engine (this job has no pandas)

if not mail.send("Capitol Capital: test email",
                 "<p>Email from Capitol Capital is working.</p>"
                 f"<p><a href='{mail.app_url('/')}'>Open Capitol Capital</a></p>", print, ok_msg="Test email sent"):
    raise SystemExit("The test email couldn't be sent")
