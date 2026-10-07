"""Read scanned paper Periodic Transaction Reports (House checkbox form) with OCR.

The form is a grid: owner | asset name | type boxes (Purchase, Sale, [Partial Sale], Exchange) |
date of transaction | date notified | 11 amount boxes (A-K). Pages are often scanned sideways.
Checkboxes are read from pixel darkness (works for handwritten forms too); names and dates use OCR.
"""
import io, re, difflib
import numpy as np

AMOUNTS = [(1001, 15000), (15001, 50000), (50001, 100000), (100001, 250000), (250001, 500000),
           (500001, 1000000), (1000001, 5000000), (5000001, 25000000), (25000001, 50000000),
           (50000001, 100000000), (1000001, 5000000)]      # K = spouse/child asset over $1M


def _cv():
    import cv2
    return cv2


def render_pages(pdf_bytes, dpi=200, max_pages=8):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as p:
        for pg in p.pages[:max_pages]:
            yield np.array(pg.to_image(resolution=dpi).original.convert("L"))


def _binarize(img):
    cv2 = _cv()
    return cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)


def _groups(idx, gap=4):
    out = []
    for v in idx:
        if not out or v - out[-1][-1] > gap:
            out.append([v])
        else:
            out[-1].append(v)
    return [int(np.mean(g)) for g in out]


def _vlines(bw, frac=0.12):
    cv2 = _cv()
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, int(bw.shape[0] * frac))))
    vl = cv2.morphologyEx(bw, cv2.MORPH_OPEN, k)
    return _groups(np.where(vl.sum(axis=0) > 0)[0]), vl


def _amount_block(xs, n=12, wide=None):
    """Best run of n equally spaced vertical lines (the 11 amount columns A-K), allowing a couple of
    missing or extra lines from scan noise and handwriting. Spacing comes from neighbouring lines first; only if
    that finds nothing is it measured over several columns (uneven scans)."""
    if wide is None:
        return _amount_block(xs, n, False) or _amount_block(xs, n, True)
    best = None
    for i in range(len(xs)):
        cands = set()
        for j in range(i + 1, min(i + (n if wide else 4), len(xs))):
            if not wide:
                if 30 <= xs[j] - xs[i] <= 160:
                    cands.add(xs[j] - xs[i])
                continue
            # spacing from xs[i] to xs[j] over the lines between (one gap alone is too noisy on uneven scans),
            # allowing one missing or extra line
            for m in (j - i - 1, j - i, j - i + 1):
                if 1 <= m < n and 30 <= (xs[j] - xs[i]) / m <= 160:
                    cands.add(round((xs[j] - xs[i]) / m, 1))
        for w in sorted(cands):
            pos = [xs[i] + k * w for k in range(n)]
            hits = [min(xs, key=lambda x: abs(x - p)) for p in pos]
            ok = [abs(h - p) <= 0.15 * w for h, p in zip(hits, pos)]
            sc = sum(ok)
            if sc >= n - 2:
                k_ok = [k for k in range(n) if ok[k]]
                fit = np.polyfit(k_ok, [hits[k] for k in k_ok], 1)
                snapped = [hits[k] if ok[k] else int(fit[1] + fit[0] * k) for k in range(n)]
                key = (sc, snapped[-1])
                if best is None or key > best[0]:
                    best = (key, snapped, float(fit[0]))
    return best


def _near(xs, target, tol):
    if not xs:
        return int(target), False
    x = min(xs, key=lambda v: abs(v - target))
    return (x, True) if abs(x - target) <= tol else (int(target), False)


def find_grid(img):
    """Column layout of the transaction grid (template ratios measured on 2015-2024 forms), or None."""
    bw = _binarize(img)
    xs, vl = _vlines(bw, 0.06)
    b = _amount_block(xs)
    if b is None:
        return None
    (score, _), amt, w = b
    a0 = amt[0]
    d1, ok1 = _near(xs, a0 - 1.9 * w, 0.3 * w)
    d0, ok0 = _near(xs, a0 - 3.5 * w, 0.35 * w)
    if not (ok1 or ok0) or a0 - 10 * w < 0:
        return None
    wt = (d0 - _near(xs, d0 - 2.46 * w, 0.3 * w)[0]) / 3 if True else 0.82 * w
    wt = wt if 0.6 * w < wt < 1.05 * w else 0.82 * w
    edges = [d0]
    for k in range(1, 5):
        x, ok = _near(xs, d0 - k * wt, 0.15 * wt)
        if k == 4 and not ok:
            break
        edges.append(x)
    edges = edges[::-1]
    types = list(zip(edges, edges[1:]))
    tleft = edges[0]
    far = [x for x in xs if tleft - 8.5 * w <= x < tleft - 3 * w]
    nl = min(far) if far else int(max(0, tleft - 6.1 * w))
    ol, oko = _near(xs, nl - 0.76 * w, 0.2 * w)
    # ink without the grid's own lines, so a slightly misplaced column edge doesn't look like a mark
    cv2 = _cv()
    kh = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, int(3 * w)), 1))
    hl = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kh)
    kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, int(1.5 * w))))
    vl2 = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kv)
    lines = cv2.dilate(cv2.bitwise_or(hl, vl2), np.ones((5, 5), np.uint8))
    ink = cv2.bitwise_and(bw, cv2.bitwise_not(lines))
    return {"bw": bw, "ink": ink, "amt": amt, "w": w, "types": types, "dates": (d0, d1, a0), "name": (nl, tleft),
            "owner": (ol, nl) if oko else None, "score": score}


# ---- Senate form ("Periodic Disclosure of Financial Transactions") -----------------------------------------
# Grid: row number | identification of assets | Purchase, Sale, Exchange | transaction date | 11 amount columns.
SENATE_AMOUNTS = [(1001, 15000), (15001, 50000), (50001, 100000), (100001, 250000), (250001, 500000),
                  (500001, 1000000), (1000001, 5000000),        # "Over $1,000,000***" (spouse/child asset)
                  (1000001, 5000000), (5000001, 25000000), (25000001, 50000000), (50000001, 100000000)]


def find_grid_senate(img):
    """Column layout of the Senate transaction grid, or None."""
    bw = _binarize(img)
    xs, vl = _vlines(bw, 0.06)
    b = _amount_block(xs)
    if b is None:
        return None
    (score, _), amt, w = b
    a0 = amt[0]
    left = [x for x in xs if a0 - 4.2 * w <= x <= a0 - 1.8 * w]       # left edge of the transaction-date column
    if not left:
        return None
    d0 = max(left)
    best = None
    for wt in np.arange(0.7, 1.3, 0.02) * w:                            # three equal type columns left of the date
        hits = [_near(xs, d0 - k * wt, 0.18 * wt) for k in (1, 2, 3)]
        sc = sum(ok for _, ok in hits)
        if sc >= 2 and (best is None or sc > best[0]):
            best = (sc, wt, hits)
    if best is None:
        return None
    _, wt, hits = best
    edges = [hits[2][0], hits[1][0], hits[0][0], d0]
    types = list(zip(edges, edges[1:]))
    tleft = edges[0]
    far = [x for x in xs if tleft - 11 * w <= x < tleft - 4 * w]
    if not far:
        return None
    nl = min(far)
    cv2 = _cv()
    kh = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, int(3 * w)), 1))
    hl = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kh)
    kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, int(1.5 * w))))
    vl2 = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kv)
    lines = cv2.dilate(cv2.bitwise_or(hl, vl2), np.ones((5, 5), np.uint8))
    ink = cv2.bitwise_and(bw, cv2.bitwise_not(lines))
    return {"bw": bw, "ink": ink, "amt": amt, "w": w, "types": types, "dates": (d0, a0, a0), "name": (nl, tleft),
            "owner": None, "score": score, "form": "senate"}


def load_image(raw, long_side=2200):
    """Scanned page (GIF/PNG/JPG bytes) as grayscale at about 200 dpi."""
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.seek(0)
    im = im.convert("L")
    m = max(im.size)
    if m > long_side * 1.2:
        f = long_side / m
        im = im.resize((int(im.size[0] * f), int(im.size[1] * f)), Image.LANCZOS)
    return np.array(im)


FORM_WORDS = {"report", "purchase", "sale", "sales", "exchange", "transaction", "transactions", "assets", "identification",
              "spouse", "dependent", "child", "joint", "example", "securities", "stock", "stocks", "amount", "notification",
              "bonds", "commodity", "futures", "involving", "reportable", "disclosure"}


def _form_words(im, g):
    """How many of the form's printed words read the right way up in the asset column (orientation check)."""
    import pytesseract
    x0, x1 = int(g["name"][0]), int(g["name"][1])
    crop = im[:, max(0, x0):x1]
    try:
        txt = pytesseract.image_to_string(crop, config="--psm 6")
    except Exception:
        return 0
    return sum(1 for t in re.findall(r"[A-Za-z]{4,}", txt) if t.lower() in FORM_WORDS)


def orient_and_grid(img, finder=None):
    finder = finder or find_grid
    if finder is find_grid_senate:
        # every row of this form is the same height, so a sideways page can look like a grid too:
        # keep the turn where the printed instructions read the right way up
        best = None
        for k in (0, 1, 3, 2):
            im = np.ascontiguousarray(np.rot90(img, k))
            g = finder(im)
            if g is None:
                continue
            n = _form_words(im, g)
            if best is None or n > best[0]:
                best = (n, im, g)
            if n >= 6:
                break
        return (best[1], best[2]) if best and best[0] >= 2 else (None, None)
    best = None
    for k in (1, 3, 0, 2):
        im = np.ascontiguousarray(np.rot90(img, k))
        g = finder(im)
        if g is not None and (best is None or g["score"] > best[1]["score"]):
            best = (im, g)
            if g["score"] == 12:
                break
    return best if best else (None, None)


def _line_rows(g):
    """Row bands between the grid's horizontal lines (works with and without printed checkboxes)."""
    cv2 = _cv()
    bw, amt, w = g["bw"], g["amt"], g["w"]
    x0, x1 = int(g["name"][0]), int(amt[-1])
    reg = bw[:, x0:x1]
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, int((x1 - x0) * 0.3)), 1))
    hl = cv2.morphologyEx(reg, cv2.MORPH_OPEN, k)
    ys = _groups(np.where(hl.sum(axis=1) > 0)[0])
    bands = [(a, b) for a, b in zip(ys, ys[1:]) if 0.35 * w <= b - a <= 4.5 * w]
    if not bands:
        return []
    # the column-label header is the tallest band; transactions are the rows below it
    hdr = max(bands, key=lambda t: t[1] - t[0])
    return [(a, b) for a, b in bands if a >= hdr[1] - 2 and b - a <= 2.2 * w]


def _boxes_rows(g):
    """Row bands from the checkbox squares in the amount columns."""
    cv2 = _cv()
    bw, amt, w = g["bw"], g["amt"], g["w"]
    reg = bw[:, amt[0]:amt[-1]]
    cnts, _ = cv2.findContours(reg, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    ys = []
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        if 0.3 * w < cw < 0.95 * w and 0.3 * w < ch < 0.95 * w and 0.7 < cw / max(ch, 1) < 1.4:
            ys.append((y, y + ch))
    if not ys:
        return []
    ys.sort()
    rows = []
    for y0, y1 in ys:
        if rows and y0 < rows[-1][1] - 0.3 * (y1 - y0):
            rows[-1][0] = min(rows[-1][0], y0)
            rows[-1][1] = max(rows[-1][1], y1)
            rows[-1][2] += 1
        else:
            rows.append([y0, y1, 1])
    return [(a, b) for a, b, n in rows if n >= 5]


def _fill(bw, x0, x1, y0, y1, ink=None):
    """Dark share inside the checkbox found in this cell (inner part, so the box border doesn't count)."""
    cv2 = _cv()
    cell = bw[int(y0):int(y1), int(x0):int(x1)]
    icell = ink[int(y0):int(y1), int(x0):int(x1)] if ink is not None else cell
    if cell.size == 0:
        return 0.0
    cnts, _ = cv2.findContours(cell, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    W = cell.shape[1]
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        # the printed box: about half to 9/10 of the column width, square
        if 0.4 * W < cw < 0.92 * W and 0.4 * W < ch < 0.92 * W and 0.75 < cw / max(ch, 1) < 1.33:
            if best is None or cw * ch > best[2] * best[3]:
                best = (x, y, cw, ch)
    if best is None:
        h, w_ = cell.shape
        best = (0, 0, w_, h)
    x, y, cw, ch = best
    inner = icell[y + int(0.22 * ch):y + ch - int(0.22 * ch), x + int(0.22 * cw):x + cw - int(0.22 * cw)]
    return float(inner.mean() / 255) if inner.size else 0.0


def _pick(fills, base):
    if not fills:
        return None
    arr = np.array(fills)
    i = int(arr.argmax())
    others = np.delete(arr, i)
    second = float(others.max()) if len(others) else 0.0
    return i if arr[i] >= max(0.025, base + 0.02) and arr[i] >= 2.0 * second + 0.01 else None


def _ocr(img, cfg):
    import pytesseract
    if img.size == 0:
        return ""
    return pytesseract.image_to_string(img, config=cfg).strip()


DATE = re.compile(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?:\s*[/\-.]\s*(\d{2,4}))?")


def _date(txt, filed):
    m = DATE.search(txt.replace(" ", ""))
    if not m:
        return None
    mo, da, yr = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mo <= 12 and 1 <= da <= 31):
        return None
    import datetime as dt
    fy = filed.year
    if yr:
        y = int(yr)
        y = y + 2000 if y < 100 else y
        if not fy - 2 <= y <= fy:           # misread or mistyped year ("5/3/208"): take the filing year
            y = fy
    else:
        y = fy
    try:
        d = dt.date(y, mo, da)
    except ValueError:
        return None
    if d > filed.date() if hasattr(filed, "date") else d > filed:
        try:
            d = dt.date(y - 1, mo, da)
        except ValueError:
            return None
    return d


class OcrSpace:
    """OCR.space API (free key: 25,000 requests/month, 500/day per IP, 1,000 Engine-3 pages/month).
    Engine 2 gives word positions; Engine 3 reads handwriting much better and is used only when needed."""
    URL = "https://api.ocr.space/parse/image"

    def __init__(self, key, usage_path, day_cap=450, month_cap=24000, e3_cap=950):
        import json, os
        self.key, self.path = key, usage_path
        self.caps = (day_cap, month_cap, e3_cap)
        try:
            self.u = json.load(open(usage_path)) if os.path.exists(usage_path) else {}
        except Exception:
            self.u = {}
        import threading
        self.lock = threading.Lock()
        self.misses = 0            # calls that were skipped (allowance) or failed: callers can retry the report later

    def _keys(self):
        import datetime as dt
        d = dt.date.today()
        return d.isoformat(), d.strftime("%Y-%m")

    def can(self, engine):
        day, mon = self._keys()
        if self.u.get("day", {}).get(day, 0) >= self.caps[0] or self.u.get("month", {}).get(mon, 0) >= self.caps[1]:
            return False
        return engine != 3 or self.u.get("e3", {}).get(mon, 0) < self.caps[2]

    def _count(self, engine):
        import json
        day, mon = self._keys()
        with self.lock:
            self.u.setdefault("day", {})
            self.u["day"] = {k: v for k, v in self.u["day"].items() if k >= day[:8]}
            self.u["day"][day] = self.u["day"].get(day, 0) + 1
            mo = self.u.setdefault("month", {})
            mo[mon] = mo.get(mon, 0) + 1
            if engine == 3:
                e3 = self.u.setdefault("e3", {})
                e3[mon] = e3.get(mon, 0) + 1
            try:
                json.dump(self.u, open(self.path, "w"))
            except Exception:
                pass

    def call(self, img, engine, overlay=False, table=False, orient=False):
        import requests
        cv2 = _cv()
        if not self.can(engine):
            self.misses += 1
            return None
        q = 70
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        while ok and len(buf) > 950_000 and q > 30:
            q -= 15
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        if not ok:
            return None
        self._count(engine)
        try:
            r = requests.post(self.URL, timeout=120, files={"file": ("p.jpg", buf.tobytes(), "image/jpeg")},
                              data={"apikey": self.key, "OCREngine": str(engine), "scale": "true",
                                    "isOverlayRequired": "true" if overlay else "false",
                                    "isTable": "true" if table else "false",
                                    "detectOrientation": "true" if orient else "false"})
            d = r.json()
            if d.get("IsErroredOnProcessing") or not d.get("ParsedResults"):
                self.misses += 1
                return None
            return d["ParsedResults"][0]
        except Exception:
            self.misses += 1
            return None


def _rows_from_overlay(res, bands, g, x_off, y_off):
    """Engine 2 words -> (name text, date text) for each row band, by position."""
    out = [{"name": [], "date": []} for _ in bands]
    lines = ((res or {}).get("TextOverlay") or {}).get("Lines") or []
    d0, d1, _ = g["dates"]
    for ln in lines:
        for w in ln.get("Words", []):
            cx = x_off + w["Left"] + w["Width"] / 2
            cy = y_off + w["Top"] + w["Height"] / 2
            bi = next((i for i, (a, b) in enumerate(bands) if a - 4 <= cy <= b + 4), None)
            if bi is None:
                continue
            if g["name"][0] - 10 <= cx <= g["name"][1]:
                out[bi]["name"].append((w["Top"], w["Left"], w["WordText"]))
            elif d0 <= cx <= d1:
                out[bi]["date"].append((w["Top"], w["Left"], w["WordText"]))
    res2 = []
    for o, (a, b) in zip(out, bands):
        step = max(12, 0.55 * (b - a))
        nm = " ".join(t for _, _, t in sorted(o["name"], key=lambda z: (int(z[0] // step), z[1])))
        dt_ = "".join(t for _, _, t in sorted(o["date"], key=lambda z: (int(z[0] // step), z[1])))
        res2.append((re.sub(r"\s+([.,])", r"\1", nm), dt_))
    return res2


def _rows_from_table(text):
    """Engine 3 returns a markdown-like table: one (name, date) per data row, example row dropped."""
    rows = []
    for ln in (text or "").splitlines():
        if not ln.strip().startswith("|") or re.match(r"^\|\s*-", ln.strip()):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if any(EXAMPLE.search(c) for c in cells):
            continue
        texts = [c for c in cells if re.search(r"[A-Za-z]{3,}", c) and c.upper() not in ("SP", "JT", "DC")]
        name = max(texts, key=len) if texts else ""
        date = next((c for c in cells if DATE.search(c.replace(" ", ""))), "")
        rows.append((name, date))
    return rows


EXAMPLE = re.compile(r"(?i)example|mega corp|IBM Corp\.?\s*\(stock\)\s*NYSE|Microsoft\s*\(stock\)\s*NASDAQ")


def parse_page(img, filed, ocr=None, matcher=None, form="house"):
    """Rows of one scanned page: name text, owner, type, amount, trade date (+ ticker when a matcher is given).
    Marks are read from pixels; names and dates come from OCR.space when a key is set, else tesseract."""
    im, g = orient_and_grid(img, find_grid_senate if form == "senate" else find_grid)
    if g is None:
        return []
    bw = g["bw"]
    bands = _line_rows(g) or _boxes_rows(g)
    cells = []
    for y0, y1 in bands:
        ink = g["ink"]
        cells.append((y0, y1, [_fill(bw, a, b, y0 + 2, y1 - 2, ink) for a, b in g["types"]],
                      [_fill(bw, a, b, y0 + 2, y1 - 2, ink) for a, b in zip(g["amt"], g["amt"][1:])]))
    allf = [f for c in cells for f in c[2] + c[3]]
    base = float(np.percentile(allf, 40)) if allf else 0.0
    picks = [(_pick(tf, base), _pick(af, base)) for _, _, tf, af in cells]
    if not any(t is not None and a is not None for t, a in picks):
        return []
    # OCR the rows' names and dates
    texts = [None] * len(bands)
    if ocr is not None and bands:
        x0 = int((g["owner"] or g["name"])[0]) - 5
        y0, y1 = int(bands[0][0]) - 5, int(bands[-1][1]) + 5
        crop = im[max(0, y0):y1, max(0, x0):int(g["dates"][2])]
        res = ocr.call(crop, 2, overlay=True)
        if res:
            texts = _rows_from_overlay(res, bands, g, max(0, x0), max(0, y0))
            need = [i for i, (t, a) in enumerate(picks) if t is not None and a is not None
                    and matcher is not None and not matcher(texts[i][0])]
            if need and ocr.can(3):
                r3 = ocr.call(crop, 3)
                t3 = _rows_from_table((r3 or {}).get("ParsedText"))
                if len(t3) == len(bands):
                    pairs = list(range(len(bands)))
                else:          # line up the filled rows only
                    filled = [i for i, (t, a) in enumerate(picks) if t is not None and a is not None]
                    t3f = [k for k, (nm, _) in enumerate(t3) if nm]
                    pairs = dict(zip(filled, t3f)) if len(filled) == len(t3f) else {}
                    pairs = [pairs.get(i) for i in range(len(bands))]
                for i in need:
                    k = pairs[i] if i < len(pairs) else None
                    if k is not None and k < len(t3):
                        texts[i] = (t3[k][0] or texts[i][0], t3[k][1] or texts[i][1])
    out = []
    for i, ((y0, y1, tf, af), (ti, ai)) in enumerate(zip(cells, picks)):
        if ti is None or ai is None:
            continue
        ny0, ny1 = y0 + 3, y1 - 2
        if texts[i] is not None:
            name, dt_txt = texts[i]
        else:
            name = re.sub(r"\s+", " ", _ocr(im[ny0:ny1, g["name"][0] + 4:g["name"][1] - 4], "--psm 6"))
            d0, d1, d2 = g["dates"]
            dt_txt = _ocr(im[ny0:ny1, d0 + 4:d1 - 4], "--psm 7 -c tessedit_char_whitelist=0123456789/-.")
        if EXAMPLE.search(name):
            continue
        if g.get("form") == "senate":
            name = re.sub(r"^\s*\d{1,2}\s+(?=\D)", "", name)     # row number printed left of the name
        own = ""
        ms = re.match(r"^\W*(?:\S{1,3}\s+)?\(\s*(S|SP|DC|J|JT)\s*\)\W*", name, re.I)   # Senate: (S) spouse, (DC) child, (J) joint
        if ms:
            own = {"S": "SP", "J": "JT"}.get(ms.group(1).upper(), ms.group(1).upper())
            name = name[ms.end():]
        # owner code written at the start of the name cell; handwriting often reads "DC" as "DB"/"D3", "SP" as "5P"
        mo = None if own else re.match(r"\W*(SP|5P|JT|JI|DC|DB|D3|OC)\b\W*", name)
        if mo:
            own = {"5P": "SP", "JI": "JT", "DB": "DC", "D3": "DC", "OC": "DC"}.get(mo.group(1), mo.group(1))
            name = name[mo.end():]
        if not own and g["owner"]:
            ot = _ocr(im[ny0:ny1, g["owner"][0] + 3:g["owner"][1] - 3], "--psm 7").upper()
            own = next((c for c in ("SP", "JT", "DC") if c in ot.replace("5", "S").replace("0", "D")), "")
        labels = ["P", "S", "S (partial)", "E"] if len(g["types"]) == 4 else ["P", "S", "E"]
        amts = SENATE_AMOUNTS if g.get("form") == "senate" else AMOUNTS
        if ai >= len(amts):
            continue
        out.append({"name": name, "owner": own, "tx_raw": labels[ti], "amount": amts[ai], "date_text": dt_txt,
                    "trade_date": _date(dt_txt, filed), "fills": (round(max(tf), 2), round(max(af), 2)),
                    "ticker": matcher(name) if matcher else None})
    return out


# ---- typed attachments (a list of trades instead of the form) ---------------------------------------------
AMT_RANGE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d{4,})\s*[-–—]\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d{4,})")
FULLDATE = re.compile(r"(?<![\d.])(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})(?!\d)")
AMT_OVER = re.compile(r"(?i)over\s*\$?\s*(\d{1,3}(?:,\d{3})+)")


def _snap_amount(lo, hi=None):
    """Nearest standard disclosure range for a written amount ("$1,001-15,000", "15,001 - 50,000")."""
    for a in SENATE_AMOUNTS[:6] + SENATE_AMOUNTS[7:]:
        if abs(lo - a[0]) <= 2 or (hi is not None and abs(hi - a[1]) <= 2 and lo < a[1]):
            return a
    return None


def parse_statement_page(img, filed, ocr, matcher=None):
    """A typed list of trades attached instead of the form (sections "Purchases"/"Sales", one trade per line with
    a date and an amount range). Read with OCR.space, which also turns the page the right way up."""
    if ocr is None or not ocr.can(2):
        return []
    res = ocr.call(img, 2, table=True, orient=True)
    text = (res or {}).get("ParsedText") or ""
    if not (AMT_RANGE.search(text) or AMT_OVER.search(text)):
        return []                                      # a cover letter or a page without trades
    out, section = [], None
    for ln in text.splitlines():
        low = ln.strip().lower()
        if not low:
            continue
        if re.match(r"^(purchases?|buys?|bought)\b", low) and not AMT_RANGE.search(ln):
            section = "P"
            continue
        if re.match(r"^(sales?|sells?|sold|dispositions?)\b", low) and not AMT_RANGE.search(ln):
            section = "S"
            continue
        if re.match(r"^exchanges?\b", low) and not AMT_RANGE.search(ln):
            section = "E"
            continue
        m = AMT_RANGE.search(ln)
        amt = None
        if m:
            lo, hi = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            if hi < lo and hi < 1000:                      # "$1,001-15" style shorthand is not used; skip
                continue
            amt = _snap_amount(lo, hi)
        else:
            mo = AMT_OVER.search(ln)
            if mo:
                v = int(mo.group(1).replace(",", ""))
                amt = (50000001, 100000000) if v >= 50000000 else _snap_amount(v + 1)
        if not amt:
            continue
        fulls = list(FULLDATE.finditer(ln[:m.start()] if m else ln))   # a date with its year ("3-7 Yr" is not one)
        dm = fulls[-1] if fulls else None
        head = ln[:min(x for x in (dm.start() if dm else len(ln), m.start() if m else len(ln)))]
        ty = section
        # a type column sits between the asset name and the date/amount, or after the amount; words inside the
        # name ("Best Buy", "Intercontinental Exchange") are not types
        TY = r"(purchase|buy|bought|sale(?:\s*\(partial\))?|partial sale|sell|sold|exchange|P|S|E)"
        tm = re.search(r"(?i)\s" + TY + r"\s*[|:]?\s*$", head)
        tail = ln[m.end():] if m else ""
        tm2 = None if tm else re.match(r"(?i)\s*[|:]?\s*" + TY + r"\b", tail)
        tm = tm or tm2
        if tm:
            w = tm.group(1).lower()
            ty = "P" if w in ("purchase", "buy", "bought", "p") else "E" if w in ("exchange", "e") else "S"
            if tm is not tm2:
                head = head[:tm.start()]
        if not ty:
            continue
        own = ""
        if re.search(r"(?i)\bspouse\b|\(s\)|\bSP\b", ln):
            own = "SP"
        elif re.search(r"(?i)\bjoint\b|\(j\)|\bJT\b", ln):
            own = "JT"
        elif re.search(r"(?i)\bdependent\b|\(dc\)|\bDC\b", ln):
            own = "DC"
        name = re.sub(r"\s+", " ", re.sub(r"(?i)\((s|j|dc|sp|jt)\)|\t", " ", head)).strip(" -:|")
        name = re.sub(r"^[^A-Za-z0-9]+", "", name)
        if len(re.sub(r"[^A-Za-z]", "", name)) < 3 or EXAMPLE.search(name):
            continue
        dtxt = dm.group(0) if dm else ""
        out.append({"name": name, "owner": own, "tx_raw": ty, "amount": amt, "date_text": dtxt,
                    "trade_date": _date(dtxt, filed) if dtxt else None, "fills": None,
                    "ticker": matcher(name) if matcher else None})
    return out


# ---- company name -> ticker ---------------------------------------------------------------------------------
_STOP = {"inc", "incorporated", "corp", "corporation", "co", "company", "companies", "ltd", "limited", "plc", "the",
         "common", "stock", "stocks", "shares", "share", "class", "cl", "a", "b", "c", "new", "holdings", "holding",
         "group", "llc", "lp", "l", "p", "sa", "nv", "ag", "adr", "ads", "ord", "ordinary", "com", "de", "del", "intl",
         "international", "and", "of", "sponsored", "units", "unit", "cmn", "stk", "co.", "trust", "sp", "jt", "dc",
         "via", "partners", "lp", "ltd", "etf", "fund", "funds", "shs", "nyse", "nasdaq", "otc", "amex", "arca"}


def _norm(s):
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\bcom+[a-z]*\s*st[a-z]*\b|\bstock\b|\bshares?\b", " ", s)     # "common stock" however it's spelled
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if t not in _STOP]
    return " ".join(toks)


OWNER_CODES = {"S", "J", "SP", "JT", "DC", "D"}


class NameIndex:
    def __init__(self, titles, primary=None, spans=None):
        """titles: {ticker: company title}, or a list of (ticker, title) pairs in priority order: the first
        `primary` pairs (today's list) win ties by the shorter ticker; later ones only fill names not seen yet."""
        pairs = list(titles.items()) if isinstance(titles, dict) else list(titles)
        primary = len(pairs) if primary is None else primary
        self.by_norm, self.span = {}, {}
        for i, (t, name) in enumerate(pairs):
            n = _norm(str(name or ""))
            if not n:
                continue
            if n not in self.by_norm or (i < primary and len(t) < len(self.by_norm[n])):
                self.by_norm[n] = t
                sp = spans[i] if spans is not None and i < len(spans) else None
                if sp:
                    self.span[n] = sp          # (first, last) dates a past name was in use
                else:
                    self.span.pop(n, None)
        self.keys = list(self.by_norm)
        self.first = {}
        for k in self.keys:
            self.first.setdefault(k.split()[0], []).append(k)

    def match(self, text, tickers=None, when=None):
        """Ticker for a written asset name, or None when unsure. With `when` (the filing date), a company's
        past name only counts if it was still in use then (a ticker can be reused by another company later)."""
        k = self._key(text, tickers)
        if k is None or isinstance(k, tuple):
            return k[1] if isinstance(k, tuple) else None
        sp = self.span.get(k)
        if sp and when is not None:
            import pandas as pd
            w = pd.Timestamp(when)
            if not (pd.Timestamp(sp[0]) - pd.Timedelta(days=730) <= w <= pd.Timestamp(sp[1]) + pd.Timedelta(days=400)):
                return None
        return self.by_norm[k]

    def _key(self, text, tickers=None):
        """The matching normalized name, or ("ticker", T) when the ticker is written in brackets."""
        if not text:
            return None
        known = tickers if tickers is not None else set(self.by_norm.values())
        for m in reversed(list(re.finditer(r"[({\[]([A-Z][A-Z.\-]{0,5})[)}\]]", text))):
            t = m.group(1).replace(".", "-")
            if t in OWNER_CODES:                   # "(S)" spouse, "(SP)", "(J)" joint, "(DC)" child: never tickers
                continue
            if t in known:
                return ("ticker", t)
        # just a ticker written as the name ("RE (stock) NYSE" is Everest Re's symbol, not RE/MAX)
        bare = re.sub(r"\s+", " ", re.sub(r"(?i)\([^)]*\)|\b(?:stock|nyse|nasdaq|otc|amex)\b|[-/]", " ", text)).strip(" .,:")
        if re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", bare) and bare.replace(".", "-") in known:
            return ("ticker", bare.replace(".", "-"))
        n = _norm(text)
        if not n:
            return None
        if n in self.by_norm:
            return n
        f0 = n.split()[0]
        cand = list(self.first.get(f0, []))
        if not cand and len(f0) >= 4:            # misspelled first word ("Walment"): try close first words
            for fw in difflib.get_close_matches(f0, list(self.first), n=3, cutoff=0.8):
                cand += self.first[fw]
        best = difflib.get_close_matches(n, cand, n=1, cutoff=0.82) if cand else []
        if not best and cand and len(n) >= 5:        # too short to tell apart ("re" isn't "re max")
            q = set(n.split())
            sub = [k for k in cand if q <= set(k.split())]
            if len(sub) == 1:
                best = sub
        if not best:
            best = difflib.get_close_matches(n, self.keys, n=1, cutoff=0.9)
        return best[0] if best else None


class GoogleVision:
    """Google Cloud Vision (DOCUMENT_TEXT_DETECTION), the free tier only: 1,000 pages a month, capped here at 950
    so a month never goes over (a count kept in a file, like OCR.space's). Reads handwriting well. Returns results
    shaped like OCR.space's (word boxes as TextOverlay, the text as ParsedText), so the page readers use either."""
    URL = "https://vision.googleapis.com/v1/images:annotate"

    def __init__(self, key, usage_path, month_cap=950):
        import json, os, threading
        self.key, self.path, self.cap = key, usage_path, month_cap
        try:
            self.u = json.load(open(usage_path)) if os.path.exists(usage_path) else {}
        except Exception:
            self.u = {}
        self.lock = threading.Lock()
        self.misses = 0

    def _mon(self):
        import datetime as dt
        return dt.date.today().strftime("%Y-%m")

    def can(self, engine=2):
        # engine 3 means "give me OCR.space's handwriting table", which Vision doesn't produce: not offered
        return engine != 3 and self.u.get("month", {}).get(self._mon(), 0) < self.cap

    def used(self):
        return self.u.get("month", {}).get(self._mon(), 0)

    def _count(self):
        import json
        with self.lock:
            mo = self.u.setdefault("month", {})
            m = self._mon()
            mo[m] = mo.get(m, 0) + 1
            try:
                json.dump(self.u, open(self.path, "w"))
            except Exception:
                pass

    def call(self, img, engine=2, overlay=False, table=False, orient=False):
        import base64, requests
        cv2 = _cv()
        if not self.can():
            self.misses += 1
            return None
        ok, buf = cv2.imencode(".png", img)
        if not ok or len(buf) > 9_000_000:
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        self._count()
        try:
            r = requests.post(self.URL, params={"key": self.key}, timeout=120, json={"requests": [{
                "image": {"content": base64.b64encode(buf.tobytes()).decode()},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": ["en"]}}]})
            d = (r.json().get("responses") or [{}])[0]
            if r.status_code != 200 or d.get("error"):
                self.misses += 1
                return None
            fta = d.get("fullTextAnnotation") or {}
            lines = []
            for page in fta.get("pages", []):
                for block in page.get("blocks", []):
                    for para in block.get("paragraphs", []):
                        words = []
                        for w in para.get("words", []):
                            txt = "".join(s.get("text", "") for s in w.get("symbols", []))
                            vs = (w.get("boundingBox") or {}).get("vertices") or []
                            xs = [v.get("x", 0) for v in vs] or [0]
                            ys = [v.get("y", 0) for v in vs] or [0]
                            words.append({"WordText": txt, "Left": min(xs), "Top": min(ys),
                                          "Width": max(xs) - min(xs), "Height": max(ys) - min(ys)})
                        if words:
                            lines.append({"Words": words})
            return {"ParsedText": fta.get("text", ""), "TextOverlay": {"Lines": lines}}
        except Exception:
            self.misses += 1
            return None


class OcrChain:
    """OCR.space first (its free daily allowance), then Google Vision's free monthly pages once that's used up."""

    def __init__(self, *readers):
        self.readers = [r for r in readers if r is not None]

    @property
    def misses(self):
        return sum(r.misses for r in self.readers)

    def can(self, engine=2):
        return any(r.can(engine) for r in self.readers)

    def call(self, img, engine=2, **kw):
        for r in self.readers:
            if r.can(engine):
                res = r.call(img, engine, **kw)
                if res:
                    return res
        return None


def make_ocr(space_key, space_usage, vision_key, vision_usage):
    sp = OcrSpace(space_key, space_usage) if space_key else None
    gv = GoogleVision(vision_key, vision_usage) if vision_key else None
    if sp and gv:
        return OcrChain(sp, gv)
    return sp or gv
