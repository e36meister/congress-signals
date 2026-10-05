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


def _amount_block(xs, n=12):
    """Best run of n equally spaced vertical lines (the 11 amount columns A-K), allowing a couple of
    missing or extra lines from scan noise and handwriting."""
    best = None
    for i in range(len(xs)):
        for j in range(i + 1, min(i + 4, len(xs))):
            w = xs[j] - xs[i]
            if not 30 <= w <= 160:
                continue
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


def orient_and_grid(img):
    best = None
    for k in (1, 3, 0, 2):
        im = np.ascontiguousarray(np.rot90(img, k))
        g = find_grid(im)
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
    if yr:
        y = int(yr)
        y = y + 2000 if y < 100 else y
    else:
        y = filed.year
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


def parse_page(img, filed):
    """Rows of one scanned page: name text, owner, type, amount, trade date."""
    im, g = orient_and_grid(img)
    if g is None:
        return []
    out = []
    bw = g["bw"]
    bands = _line_rows(g) or _boxes_rows(g)
    cells = []
    for y0, y1 in bands:
        h = y1 - y0
        ink = g["ink"]
        cells.append((y0, y1, [_fill(bw, a, b, y0 + 2, y1 - 2, ink) for a, b in g["types"]],
                      [_fill(bw, a, b, y0 + 2, y1 - 2, ink) for a, b in zip(g["amt"], g["amt"][1:])]))
    allf = [f for c in cells for f in c[2] + c[3]]
    base = float(np.percentile(allf, 40)) if allf else 0.0
    for y0, y1, tf, af in cells:
        h = y1 - y0
        ti, ai = _pick(tf, base), _pick(af, base)
        if ti is None or ai is None:
            continue
        ny0, ny1 = y0 + 3, y1 - 2
        name = _ocr(im[ny0:ny1, g["name"][0] + 4:g["name"][1] - 4], "--psm 6")
        name = re.sub(r"\s+", " ", name)
        if re.search(r"(?i)example|mega corp", name):
            continue
        own = ""
        mo = re.match(r"\W*(SP|JT|DC)\b\W*", name)
        if mo:
            own, name = mo.group(1), name[mo.end():]
        if not own and g["owner"]:
            ot = _ocr(im[ny0:ny1, g["owner"][0] + 3:g["owner"][1] - 3], "--psm 7").upper()
            own = next((c for c in ("SP", "JT", "DC") if c in ot.replace("5", "S").replace("0", "D")), "")
        d0, d1, d2 = g["dates"]
        dt_txt = _ocr(im[ny0:ny1, d0 + 4:d1 - 4], "--psm 7 -c tessedit_char_whitelist=0123456789/-.")
        ntypes = len(g["types"])
        labels = ["P", "S", "S (partial)", "E"] if ntypes == 4 else ["P", "S", "E"]
        out.append({"name": name, "owner": own, "tx_raw": labels[ti] if ti is not None else None,
                    "amount": AMOUNTS[ai] if ai is not None else None, "date_text": dt_txt,
                    "trade_date": _date(dt_txt, filed), "fills": (round(max(tf), 2), round(max(af), 2))})
    return out


# ---- company name -> ticker ---------------------------------------------------------------------------------
_STOP = {"inc", "incorporated", "corp", "corporation", "co", "company", "companies", "ltd", "limited", "plc", "the",
         "common", "stock", "stocks", "shares", "share", "class", "cl", "a", "b", "c", "new", "holdings", "holding",
         "group", "llc", "lp", "l", "p", "sa", "nv", "ag", "adr", "ads", "ord", "ordinary", "com", "de", "del", "intl",
         "international", "and", "of", "sponsored", "units", "unit", "cmn", "stk", "co.", "trust", "sp", "jt", "dc",
         "via", "partners", "lp", "ltd", "etf", "fund", "funds", "shs"}


def _norm(s):
    s = re.sub(r"[^a-z0-9& ]", " ", s.lower().replace("&", " and "))
    toks = [t for t in s.split() if t not in _STOP]
    return " ".join(toks)


class NameIndex:
    def __init__(self, titles):          # titles: {ticker: company title}
        self.by_norm = {}
        for t, name in titles.items():
            n = _norm(name)
            if n and (n not in self.by_norm or len(t) < len(self.by_norm[n])):
                self.by_norm[n] = t
        self.keys = list(self.by_norm)
        self.first = {}
        for k in self.keys:
            self.first.setdefault(k.split()[0], []).append(k)

    def match(self, text, tickers=None):
        """Ticker for a written asset name, or None when unsure."""
        if not text:
            return None
        known = tickers if tickers is not None else set(self.by_norm.values())
        for m in re.finditer(r"[({\[]([A-Z][A-Z.\-]{0,5})[)}\]]", text):
            if m.group(1).replace(".", "-") in known:
                return m.group(1).replace(".", "-")
        n = _norm(text)
        if not n:
            return None
        if n in self.by_norm:
            return self.by_norm[n]
        cand = self.first.get(n.split()[0], [])
        best = difflib.get_close_matches(n, cand, n=1, cutoff=0.82) if cand else []
        if not best and cand:
            q = set(n.split())
            sub = [k for k in cand if q <= set(k.split())]
            if len(sub) == 1:
                best = sub
        if not best:
            best = difflib.get_close_matches(n, self.keys, n=1, cutoff=0.9)
        return self.by_norm[best[0]] if best else None
