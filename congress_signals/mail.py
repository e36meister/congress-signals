"""Shared email pieces: sending through Gmail (with inline images), the app's web address for links, and the
account-vs-S&P chart drawn as a picture (email apps don't run scripts or show SVG, so the chart is a PNG that links
to the app)."""
import os, io, datetime as dt

GREEN, RED, GREY, INK, LINE, ACCENT, SPY_C = "#0B7A4B", "#B42318", "#6B778A", "#152033", "#DCE1E8", "#1D4F91", "#8A93A3"
FALLBACK_URL = "https://claude.ai/artifact/Hkj8H6ZduZXcuvtgSaByru"
_ORIGIN = {}


def app_url(path="/"):
    """Link into the phone app (its address is saved by the app's server), or the dashboard if that isn't known."""
    if "v" not in _ORIGIN:
        o = ""
        try:
            import cloud_store
            raw = cloud_store.get("push/origin.txt", log=lambda m: None)
            o = (raw.decode() if isinstance(raw, bytes) else str(raw or "")).strip().rstrip("/")
        except Exception:
            o = ""
        _ORIGIN["v"] = o if o.startswith("https://") else ""
    return (_ORIGIN["v"] + path) if _ORIGIN["v"] else FALLBACK_URL


def send(subject, html, log, images=None, ok_msg="Email: sent"):
    """Send an HTML email to ALERT_TO (or yourself). images: {content_id: png bytes}, shown with <img src='cid:id'>."""
    addr, pw = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (addr and pw):
        log("Email: not set up (GMAIL_ADDRESS / GMAIL_APP_PASSWORD secrets missing)")
        return False
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    msg = MIMEMultipart("related")
    msg["Subject"], msg["From"], msg["To"] = subject[:140], addr, os.environ.get("ALERT_TO") or addr
    msg.attach(MIMEText(html, "html"))
    for cid, png in (images or {}).items():
        im = MIMEImage(png, "png")
        im.add_header("Content-ID", f"<{cid}>")
        im.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        msg.attach(im)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(addr, pw.replace(" ", ""))
            s.send_message(msg)
        log(ok_msg)
        return True
    except Exception as e:
        log(f"Email: sending failed ({type(e).__name__}); check the Gmail app password secret")
        return False


def _font(size, bold=False):
    from PIL import ImageFont
    for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                 ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def chart_png(dates, acct, spy, title="Your account vs the same money in the S&P 500"):
    """Two lines (% since start): the account (green if ahead of the S&P, red if behind) and the S&P (grey).
    Drawn at 2x so it's sharp on phones; shown at 560px wide."""
    from PIL import Image, ImageDraw
    pts = [(d, a, s) for d, a, s in zip(dates, acct, spy) if a is not None]
    if len(pts) < 2:
        return None
    S = 2
    W, H = 560 * S, 280 * S
    L, R, T, B = 48 * S, 88 * S, 40 * S, 30 * S
    img = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(img)
    vals = [p[1] for p in pts] + [p[2] for p in pts if p[2] is not None] + [0.0]
    lo, hi = min(vals), max(vals)
    pad = max(0.4, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad
    n = len(pts)
    x = lambda i: L + (W - L - R) * i / (n - 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    f_s, f_b, f_t = _font(11 * S), _font(12 * S, True), _font(13 * S, True)
    g.text((L, 12 * S), title, fill=INK, font=f_t)
    # grid: 4-5 round steps
    span = hi - lo
    step = next(s for s in (0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100) if span / s <= 5)
    v = (int(lo / step) - 1) * step
    while v <= hi:
        if v >= lo:
            g.line([(L, y(v)), (W - R, y(v))], fill="#9AA3AF" if abs(v) < 1e-9 else "#EEF1F5", width=S)
            g.text((8 * S, y(v) - 7 * S), f"{v:+g}%" if v else "0%", fill=GREY, font=f_s)
        v += step
    # dates: start, middle, end
    for i in sorted({0, n // 2, n - 1}):
        d = dt.date.fromisoformat(pts[i][0])
        lab = f"{d:%b} {d.day}"
        tw = g.textlength(lab, font=f_s)
        g.text((min(max(x(i) - tw / 2, L), W - R - tw), H - B + 8 * S), lab, fill=GREY, font=f_s)

    def poly(idx, color, width):
        line = [(x(i), y(pts[i][idx])) for i in range(n) if pts[i][idx] is not None]
        if len(line) > 1:
            g.line(line, fill=color, width=width * S, joint="curve")
        return line[-1] if line else None
    ahead = pts[-1][2] is None or pts[-1][1] >= pts[-1][2]
    acol = GREEN if ahead else RED
    end_s = poly(2, SPY_C, 2)
    end_a = poly(1, acol, 3)
    # end labels, nudged apart if they'd overlap
    ya, ys = (end_a[1] if end_a else 0), (end_s[1] if end_s else 0)
    if end_a and end_s and abs(ya - ys) < 16 * S:
        mid = (ya + ys) / 2
        ya, ys = (mid - 8 * S, mid + 8 * S) if ya <= ys else (mid + 8 * S, mid - 8 * S)
    if end_a:
        g.ellipse([end_a[0] - 4 * S, end_a[1] - 4 * S, end_a[0] + 4 * S, end_a[1] + 4 * S], fill=acol)
        g.text((W - R + 8 * S, ya - 8 * S), f"You {pts[-1][1]:+.1f}%", fill=acol, font=f_b)
    if end_s:
        g.text((W - R + 8 * S, ys - 8 * S), f"S&P {pts[-1][2]:+.1f}%", fill=GREY, font=f_s)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
