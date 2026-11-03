"""First buys for a family account right after it connects its Alpaca keys (started by the app's server through
family_start.yml). Usage: python family_start.py [ACCOUNT_ID]   (no id: every connected account not yet started).
Nothing about the account (name, email, balances) is written to the log, which can be public."""
import sys
import datetime as dt
from congress_signals import accounts


def log(m):
    print(dt.datetime.now().strftime("%H:%M:%S"), m, flush=True)


only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip() else None
if only and not only.isalnum():
    sys.exit("bad account id")
n = accounts.start(log, only=only)
log(f"Family start: {n} account(s) started")
