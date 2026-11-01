"""Renders the test invoice PDFs (3 vendor layouts, some degraded to scans)
and writes ground truth + expected decisions for every scenario."""
import csv
import io
import json
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

rl_config.invariant = 1  # same input, byte-identical PDF, so saved readings stay valid

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "invoices"
OUT.mkdir(exist_ok=True)
FD = "/usr/share/fonts/truetype/dejavu/"
HAND = Path(__file__).resolve().parent / "fonts"
pdfmetrics.registerFont(TTFont("Hand", str(HAND / "ReenieBeanie.ttf")))
pdfmetrics.registerFont(TTFont("Hand2", str(HAND / "NothingYouCouldDo.ttf")))
for name, f in [("Sans", "DejaVuSans.ttf"), ("SansB", "DejaVuSans-Bold.ttf"),
                ("Serif", "DejaVuSerif.ttf"), ("SerifB", "DejaVuSerif-Bold.ttf"),
                ("Cond", "DejaVuSansCondensed.ttf"), ("CondB", "DejaVuSansCondensed-Bold.ttf")]:
    pdfmetrics.registerFont(TTFont(name, FD + f))

BUYER = json.loads((DATA / "buyer.json").read_text())
VENDORS = {r["vendor_id"]: r for r in csv.DictReader(open(DATA / "vendors.csv"))}
PO_LINES = {}
for r in csv.DictReader(open(DATA / "purchase_order_lines.csv")):
    PO_LINES.setdefault(r["po_number"], []).append(
        (r["description"], r["hsn_sac"], int(r["qty"]), r["unit"], int(r["unit_price"]), int(r["gst_rate"])))

# ---------------------------------------------------------------- helpers
ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
        "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n):
    return ONES[n] if n < 20 else (TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else ""))


def _three(n):
    h, r = divmod(n, 100)
    return ((ONES[h] + " Hundred" + (" " if r else "")) if h else "") + (_two(r) if r else "")


def words_inr(amount):
    n = int(round(amount))
    if n == 0:
        return "Rupees Zero Only"
    parts = []
    for div, label in [(10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand")]:
        q, n = divmod(n, div)
        if q:
            parts.append(f"{_three(q) if q >= 100 else _two(q)} {label}")
    if n:
        parts.append(_three(n))
    return "Rupees " + " ".join(parts) + " Only"


def inr(x, sym=False):
    """Indian digit grouping: 1,23,45,678.00"""
    neg, x = x < 0, abs(x)
    s = f"{x:.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return ("-" if neg else "") + ("₹ " if sym else "") + whole + "." + frac


def compute(lines, tax_type):
    rows, sub, tax = [], 0, 0.0
    for d, h, q, u, r, g in lines:
        val = q * r
        sub += val
        tax += val * g / 100
        rows.append((d, h, q, u, r, g, val))
    tax_r = round(tax)
    if tax_type == "IGST":
        split = {"IGST": tax_r}
    else:
        split = {"CGST": tax_r / 2, "SGST": tax_r / 2}
    return rows, sub, split, sub + tax_r


# ---------------------------------------------------------------- layouts
def header_fields(spec, v):
    pos = f"{BUYER['state']} ({BUYER['state_code']})"
    f = [("Invoice No.", spec["inv_no"]), ("Invoice Date", spec["inv_date"])]
    if spec.get("po_ref"):
        f.append(("Buyer's Order No.", spec["po_ref"]))
    f.append(("Place of Supply", pos))
    if spec.get("due"):
        f.append(("Payment Terms", spec["due"]))
    return f


def layout_classic(c, spec, v, calc):
    rows, sub, split, total = calc
    W, H = A4
    L, R = 15 * mm, W - 15 * mm
    c.setLineWidth(0.8)
    c.rect(L, 15 * mm, R - L, H - 30 * mm)
    y = H - 22 * mm
    c.setFont("SansB", 9); c.drawCentredString(W / 2, y, "TAX INVOICE"); y -= 4 * mm
    c.setFont("Sans", 6.5); c.drawRightString(R - 3 * mm, H - 22 * mm, "ORIGINAL FOR RECIPIENT")
    c.line(L, y, R, y); y -= 7 * mm
    c.setFont("SansB", 14); c.drawCentredString(W / 2, y, v["name"].upper()); y -= 5 * mm
    c.setFont("Sans", 8); c.drawCentredString(W / 2, y, v["address"]); y -= 4 * mm
    c.drawCentredString(W / 2, y, f"GSTIN/UIN: {spec.get('gstin_override', v['gstin'])}   |   State: {v['state']}, Code: {v['state_code']}   |   {v['email']}")
    y -= 4 * mm; c.line(L, y, R, y)
    top = y; mid = W / 2
    c.line(mid, top, mid, top - 34 * mm)
    yy = top - 5 * mm
    c.setFont("SansB", 8); c.drawString(L + 3 * mm, yy, "Buyer (Bill to)"); yy -= 4.5 * mm
    c.setFont("SansB", 9); c.drawString(L + 3 * mm, yy, BUYER["name"]); yy -= 4 * mm
    c.setFont("Sans", 8)
    for ln in [BUYER["address"], f"GSTIN/UIN: {BUYER['gstin']}", f"State Name: {BUYER['state']}, Code: {BUYER['state_code']}"]:
        c.drawString(L + 3 * mm, yy, ln); yy -= 4 * mm
    yy = top - 5 * mm
    for k, val in header_fields(spec, v):
        c.setFont("Sans", 8); c.drawString(mid + 3 * mm, yy, k)
        c.setFont("SansB", 8); c.drawString(mid + 35 * mm, yy, ": " + val); yy -= 5 * mm
    y = top - 34 * mm; c.line(L, y, R, y)
    cols = [("Sl", 8), ("Description of Goods / Services", 70), ("HSN/SAC", 18), ("Qty", 14),
            ("Unit", 13), ("Rate", 22), ("GST %", 12), ("Amount", 23)]
    xs = [L]
    for _, wdt in cols:
        xs.append(xs[-1] + wdt * mm)
    xs[-1] = R
    c.setFont("SansB", 7.5); hy = y - 5 * mm
    for i, (t, _) in enumerate(cols):
        c.drawString(xs[i] + 1.5 * mm, hy, t)
    y -= 7 * mm; c.line(L, y, R, y); tbl_top = y + 7 * mm
    c.setFont("Sans", 8)
    for i, (d, h, q, u, r, g, val) in enumerate(rows, 1):
        y -= 6 * mm
        c.drawString(xs[0] + 1.5 * mm, y, str(i)); c.drawString(xs[1] + 1.5 * mm, y, d[:46])
        c.drawString(xs[2] + 1.5 * mm, y, h); c.drawRightString(xs[4] - 1.5 * mm, y, f"{q:,}")
        c.drawString(xs[4] + 1.5 * mm, y, u); c.drawRightString(xs[6] - 1.5 * mm, y, inr(r))
        c.drawRightString(xs[7] - 1.5 * mm, y, f"{g}%"); c.drawRightString(R - 1.5 * mm, y, inr(val))
    y -= 40 * mm
    for x in xs[1:-1]:
        c.line(x, tbl_top, x, y)
    c.line(L, y, R, y)
    c.setFont("Sans", 8)
    lines = [("Taxable Value", sub)] + [(f"{k} @ {18 if k == 'IGST' else 9}%", amt) for k, amt in split.items()]
    for k, amt in lines:
        y -= 5 * mm; c.drawRightString(xs[7] - 2 * mm, y, k); c.drawRightString(R - 1.5 * mm, y, inr(amt))
    y -= 3 * mm; c.line(xs[5], y, R, y); y -= 5 * mm
    c.setFont("SansB", 9); c.drawRightString(xs[6] - 2 * mm, y, "Total"); c.drawRightString(R - 1.5 * mm, y, inr(total, True))
    y -= 3 * mm; c.line(L, y, R, y); y -= 5 * mm
    c.setFont("Sans", 7.5); c.drawString(L + 3 * mm, y, "Amount Chargeable (in words)")
    y -= 4.5 * mm; c.setFont("SansB", 8.5); c.drawString(L + 3 * mm, y, "INR " + words_inr(total))
    y -= 4 * mm; c.line(L, y, R, y)
    bank_and_sign(c, spec, v, L, R, y)


def layout_modern(c, spec, v, calc):
    rows, sub, split, total = calc
    W, H = A4
    L, R = 18 * mm, W - 18 * mm
    accent = colors.HexColor(spec.get("accent", "#0B4F6C"))
    c.setFillColor(accent); c.rect(0, H - 38 * mm, W, 38 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("SansB", 17); c.drawString(L, H - 17 * mm, v["name"])
    c.setFont("Sans", 8); c.drawString(L, H - 23 * mm, v["address"])
    c.drawString(L, H - 27.5 * mm, f"GSTIN {spec.get('gstin_override', v['gstin'])}  ·  PAN {v['pan']}  ·  {v['email']}")
    c.setFont("SansB", 20); c.drawRightString(R, H - 17 * mm, "Tax Invoice")
    c.setFont("Sans", 8); c.drawRightString(R, H - 23 * mm, "Original for Recipient")
    c.setFillColor(colors.black)
    y = H - 50 * mm
    c.setFillColor(accent); c.setFont("SansB", 8); c.drawString(L, y, "BILLED TO")
    c.drawString(W / 2 + 5 * mm, y, "INVOICE DETAILS"); c.setFillColor(colors.black)
    yy = y - 5.5 * mm
    c.setFont("SansB", 10); c.drawString(L, yy, BUYER["name"]); yy -= 4.5 * mm
    c.setFont("Sans", 8.5)
    for ln in [BUYER["address"], f"GSTIN {BUYER['gstin']}", f"{BUYER['state']} · State code {BUYER['state_code']}"]:
        c.drawString(L, yy, ln); yy -= 4.3 * mm
    yy = y - 5.5 * mm
    for k, val in header_fields(spec, v):
        c.setFont("Sans", 8.5); c.setFillColor(colors.HexColor("#555555")); c.drawString(W / 2 + 5 * mm, yy, k)
        c.setFillColor(colors.black); c.setFont("SansB", 8.5); c.drawRightString(R, yy, val); yy -= 5 * mm
    y -= 34 * mm
    c.setFillColor(colors.HexColor("#EEF3F6")); c.rect(L, y - 2.5 * mm, R - L, 8 * mm, stroke=0, fill=1)
    c.setFillColor(colors.black); c.setFont("SansB", 8)
    hx = [L + 2 * mm, L + 82 * mm, L + 104 * mm, L + 122 * mm, R - 2 * mm]
    c.drawString(hx[0], y, "ITEM / SERVICE"); c.drawString(hx[1], y, "HSN/SAC")
    c.drawRightString(hx[2] + 8 * mm, y, "QTY"); c.drawRightString(hx[3] + 18 * mm, y, "UNIT PRICE")
    c.drawRightString(hx[4], y, "TAXABLE VALUE")
    c.setFont("Sans", 8.5)
    for i, (d, h, q, u, r, _rate, val) in enumerate(rows):
        y -= 9 * mm
        if i % 2:
            c.setFillColor(colors.HexColor("#F7F9FA")); c.rect(L, y - 3 * mm, R - L, 9 * mm, stroke=0, fill=1)
            c.setFillColor(colors.black)
        c.drawString(hx[0], y, d[:48]); c.drawString(hx[1], y, h)
        c.drawRightString(hx[2] + 8 * mm, y, f"{q:,} {u}"); c.drawRightString(hx[3] + 18 * mm, y, inr(r))
        c.drawRightString(hx[4], y, inr(val))
    y -= 8 * mm; c.setStrokeColor(colors.HexColor("#CCCCCC")); c.line(L, y, R, y); c.setStrokeColor(colors.black)
    tx = W / 2 + 15 * mm
    y -= 7 * mm
    for k, amt in [("Subtotal", sub)] + [(f"{k} (18%)" if k == "IGST" else f"{k} (9%)", a) for k, a in split.items()]:
        c.setFont("Sans", 9); c.drawString(tx, y, k); c.drawRightString(R, y, inr(amt)); y -= 6 * mm
    y -= 3 * mm
    c.setFillColor(accent); c.rect(tx - 3 * mm, y - 3.5 * mm, R - tx + 3 * mm, 10 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white); c.setFont("SansB", 11)
    c.drawString(tx, y, "Total Due"); c.drawRightString(R - 2 * mm, y, inr(total, True)); c.setFillColor(colors.black)
    y -= 12 * mm; c.setFont("Sans", 8); c.drawString(L, y, "Amount in words: " + words_inr(total))
    y -= 4 * mm
    bank_and_sign(c, spec, v, L, R, y, boxed=False)


def layout_letterhead(c, spec, v, calc):
    rows, sub, split, total = calc
    W, H = A4
    L, R = 22 * mm, W - 22 * mm
    c.setFont("SerifB", 16); c.drawString(L, H - 25 * mm, v["name"])
    c.setFont("Serif", 8.5); c.drawString(L, H - 31 * mm, v["address"] + "  |  " + v["email"])
    c.drawString(L, H - 35.5 * mm, f"GSTIN: {spec.get('gstin_override', v['gstin'])}    PAN: {v['pan']}")
    c.setLineWidth(1.5); c.line(L, H - 39 * mm, R, H - 39 * mm); c.setLineWidth(0.6)
    c.setFont("SerifB", 12); c.drawCentredString(W / 2, H - 49 * mm, "TAX INVOICE")
    y = H - 60 * mm
    c.setFont("Serif", 9)
    c.drawString(L, y, "To,"); c.drawRightString(R, y, f"Bill No: {spec['inv_no']}")
    c.setFont("SerifB", 9); c.drawString(L, y - 5 * mm, f"The Manager - Administration, {BUYER['name']}")
    c.setFont("Serif", 9); c.drawRightString(R, y - 5 * mm, f"Date: {spec['inv_date']}")
    c.drawString(L, y - 10 * mm, BUYER["address"])
    c.drawString(L, y - 15 * mm, f"GSTIN: {BUYER['gstin']}   State: {BUYER['state']} ({BUYER['state_code']})")
    if spec.get("po_ref"):
        c.drawRightString(R, y - 10 * mm, f"Your W.O. Ref: {spec['po_ref']}")
    c.drawRightString(R, y - 15 * mm, f"Place of Supply: {BUYER['state']}")
    y -= 26 * mm
    c.drawString(L, y, spec.get("narrative", "Being charges towards services rendered as per details below:"))
    y -= 8 * mm
    c.rect(L, y - (len(rows) * 8 + 8) * mm, R - L, (len(rows) * 8 + 8) * mm + 2 * mm)
    c.setFont("SerifB", 8.5)
    c.drawString(L + 2 * mm, y - 4 * mm, "Particulars"); c.drawString(L + 100 * mm, y - 4 * mm, "SAC")
    c.drawRightString(R - 2 * mm, y - 4 * mm, "Amount (Rs.)")
    c.line(L, y - 6.5 * mm, R, y - 6.5 * mm)
    c.setFont("Serif", 8.5); yy = y - 6.5 * mm
    for d, h, q, u, r, _rate, val in rows:
        yy -= 8 * mm
        label = d if q == 1 else f"{d} ({q} {u} @ {inr(r)})"
        c.drawString(L + 2 * mm, yy + 2 * mm, label[:62]); c.drawString(L + 100 * mm, yy + 2 * mm, h)
        c.drawRightString(R - 2 * mm, yy + 2 * mm, inr(val))
    y = yy - 6 * mm
    for k, amt in [("Total taxable value", sub)] + [(f"Add: {k} @ {18 if k == 'IGST' else 9}%", a) for k, a in split.items()]:
        y -= 5.5 * mm; c.drawString(L + 95 * mm, y, k); c.drawRightString(R - 2 * mm, y, inr(amt))
    y -= 3 * mm; c.line(L + 95 * mm, y, R, y); y -= 6 * mm
    c.setFont("SerifB", 10); c.drawString(L + 95 * mm, y, "Grand Total"); c.drawRightString(R - 2 * mm, y, "Rs. " + inr(total))
    y -= 8 * mm; c.setFont("Serif", 8.5); c.drawString(L, y, "(" + words_inr(total) + ")")
    y -= 4 * mm
    bank_and_sign(c, spec, v, L, R, y, boxed=False, serif=True)


def bank_and_sign(c, spec, v, L, R, y, boxed=True, serif=False):
    f, fb = ("Serif", "SerifB") if serif else ("Sans", "SansB")
    bank = spec.get("bank_override") or {"bank_name": v["bank_name"], "bank_account": v["bank_account"], "ifsc": v["ifsc"]}
    y -= 6 * mm
    c.setFont(fb, 8); c.drawString(L + 3 * mm, y, "Company's Bank Details")
    c.setFont(f, 8)
    for ln in [f"Bank Name : {bank['bank_name']}", f"A/c No.     : {bank['bank_account']}", f"IFSC Code  : {bank['ifsc']}"]:
        y -= 4.3 * mm; c.drawString(L + 3 * mm, y, ln)
    if spec.get("bank_note"):
        y -= 4.3 * mm; c.setFont(fb, 8); c.drawString(L + 3 * mm, y, spec["bank_note"]); c.setFont(f, 8)
    y -= 7 * mm
    c.setFont(f, 7); c.drawString(L + 3 * mm, y, "Declaration: We declare that this invoice shows the actual price of the goods/services")
    c.drawString(L + 3 * mm, y - 3.5 * mm, "described and that all particulars are true and correct. Tax payable under reverse charge: No.")
    c.setFont(fb, 8); c.drawRightString(R - 3 * mm, y + 9 * mm, f"for {v['name']}")
    c.setFont(f, 8); c.drawRightString(R - 3 * mm, y - 3.5 * mm, "Authorised Signatory")
    c.setFont(f, 6.5); c.drawCentredString(A4[0] / 2, 19 * mm, f"This is a computer generated invoice. Subject to {re.search(r'([A-Za-z]+) \d{6}', v['address']).group(1)} jurisdiction.")


LAYOUTS = {"classic": layout_classic, "modern": layout_modern, "letterhead": layout_letterhead}


def render(spec):
    v = VENDORS[spec["vendor_id"]]
    lines = spec.get("lines") or PO_LINES[spec["po_lines_from"]]
    if spec.get("qty_override"):
        lines = [(d, h, spec["qty_override"], u, r, g) for d, h, q, u, r, g in lines]
    tax_type = spec.get("tax_type") or ("CGST+SGST" if v["state_code"] == BUYER["state_code"] else "IGST")
    calc = compute(lines, tax_type)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{v['name']} - {spec['inv_no']}"); c.setAuthor(v["name"])
    LAYOUTS[spec["layout"]](c, spec, v, calc)
    if spec.get("post"):
        spec["post"](c)
    if spec.get("stamp"):
        c.saveState(); c.setFillColor(colors.HexColor("#B22222")); c.setFont("SansB", 26)
        c.translate(A4[0] / 2 + 5 * mm, A4[1] - 122 * mm); c.rotate(12)
        c.drawCentredString(0, 0, spec["stamp"]); c.restoreState()
    c.showPage(); c.save()
    return buf.getvalue(), calc, tax_type


def to_scan(pdf_bytes, path, seed, angle):
    """Rasterise and degrade so the PDF has NO text layer (forces vision/OCR)."""
    rnd = random.Random(seed)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.pdf"; src.write_bytes(pdf_bytes)
        subprocess.run(["pdftoppm", "-r", "140", "-png", "-singlefile", str(src), str(Path(td) / "pg")], check=True)
        img = Image.open(Path(td) / "pg.png").convert("L")
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    img = ImageEnhance.Contrast(img).enhance(0.82)
    px = img.load(); w, h = img.size
    for _ in range(int(w * h * 0.0025)):  # speckle noise
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.choice([40, 90, 200])
    d = ImageDraw.Draw(img)
    for _ in range(3):  # faint scanner streaks
        x = rnd.randrange(w); d.line([(x, 0), (x, h)], fill=225, width=1)
    paper = Image.new("L", img.size, 236)
    img = Image.blend(img, paper, 0.12)
    jb = io.BytesIO(); img.save(jb, "JPEG", quality=55); jb.seek(0)
    fixed = time.strptime("2026-10-01", "%Y-%m-%d")  # a fixed date keeps rebuilt scans byte-identical
    Image.open(jb).convert("RGB").save(path, "PDF", resolution=140, creationDate=fixed, modDate=fixed)


def _perspective_coeffs(src, dst):
    """Coefficients mapping output quad -> input quad for Image.transform(PERSPECTIVE)."""
    import numpy as np
    m = []
    for (x, y), (u, v) in zip(dst, src):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(m, dtype=float)
    B = np.array([c for p in src for c in p], dtype=float)
    return np.linalg.solve(A, B).tolist()


def to_photo(pdf_bytes, path, seed):
    """Make it look like a phone photo of a printout lying on a desk: tilted,
    keystoned, unevenly lit, slightly out of focus. Saved as a JPG, the way it
    would arrive on WhatsApp or email."""
    import numpy as np
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.pdf"; src.write_bytes(pdf_bytes)
        subprocess.run(["pdftoppm", "-r", "120", "-png", "-singlefile", str(src), str(Path(td) / "pg")], check=True)
        page = Image.open(Path(td) / "pg.png").convert("RGB")
    pw, ph = page.size
    W, H = int(pw * 1.22), int(ph * 1.16)
    # desk background: warm brown with grain
    desk = np.zeros((H, W, 3), dtype=np.float32)
    desk[..., 0], desk[..., 1], desk[..., 2] = 121, 92, 66
    grain = np.random.default_rng(seed).normal(0, 9, (H, W, 1)).astype(np.float32)
    streak = (np.sin(np.linspace(0, 40, W))[None, :, None] * 6).astype(np.float32)
    desk = np.clip(desk + grain + streak, 0, 255).astype(np.uint8)
    canvas_img = Image.fromarray(desk)
    # where the page corners land in the photo (keystone: top edge narrower)
    ox, oy = (W - pw) / 2, (H - ph) / 2
    dst = [(ox + 70, oy + 30), (ox + pw - 25, oy + 55), (ox + pw + 30, oy + ph - 20), (ox - 35, oy + ph - 45)]
    srcq = [(0, 0), (pw, 0), (pw, ph), (0, ph)]
    coeffs = _perspective_coeffs(srcq, dst)
    warped = page.transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=None)
    mask = Image.new("L", page.size, 255).transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    canvas_img.paste(warped, (0, 0), mask)
    # uneven lighting: phone shadow from the top-left, brighter bottom-right
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    light = 0.62 + 0.38 * np.clip((xx / W) * 0.6 + (yy / H) * 0.6, 0, 1)
    shadow = np.exp(-(((xx - W * 0.18) / (W * 0.22)) ** 2 + ((yy - H * 0.12) / (H * 0.16)) ** 2)) * 0.25
    arr = np.asarray(canvas_img).astype(np.float32) * (light - shadow)[..., None]
    arr[..., 0] *= 1.04; arr[..., 2] *= 0.93  # warm indoor light
    arr += np.random.default_rng(seed + 1).normal(0, 4, arr.shape)  # sensor noise
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    img = img.filter(ImageFilter.GaussianBlur(1.0))
    img = img.resize((int(W * 0.85), int(H * 0.85)), Image.LANCZOS)
    img.save(path, "JPEG", quality=72)


def handwritten_changes(c):
    """Someone at the vendor 'corrected' the printed bill by hand: added a line,
    struck out the printed total and scribbled a new one. Not legal on a GST
    tax invoice: changes need a revised invoice or a debit note."""
    W, H = A4
    L, R = 22 * mm, W - 22 * mm
    ink = colors.HexColor("#1B2F8A")
    c.saveState()
    c.setFillColor(ink); c.setStrokeColor(ink)
    # extra line squeezed in under the table
    c.saveState(); c.translate(L + 3 * mm, H - 118.5 * mm); c.rotate(1.5)
    c.setFont("Hand", 23); c.drawString(0, 0, "+ Addl. treatment - canteen area")
    c.restoreState()
    c.saveState(); c.translate(R - 24 * mm, H - 115.5 * mm); c.rotate(-2)
    c.setFont("Hand", 24); c.drawString(0, 0, "4,5oo/-")
    c.restoreState()
    # strike out the printed grand total and write a new one
    c.setLineWidth(1.6)
    c.line(R - 42 * mm, H - 138.5 * mm, R + 1 * mm, H - 140.5 * mm)
    c.line(R - 41 * mm, H - 140.8 * mm, R + 2 * mm, H - 138.2 * mm)
    c.saveState(); c.translate(R - 36 * mm, H - 149 * mm); c.rotate(5)
    c.setFont("Hand", 27); c.drawString(0, 0, "26,55o/-")
    c.restoreState()
    c.saveState(); c.translate(R - 52 * mm, H - 157 * mm); c.rotate(-3)
    c.setFont("Hand2", 11); c.drawString(0, 0, "revised - S.K. 30/10")
    c.restoreState()
    c.restoreState()


# ---------------------------------------------------------------- scenarios
APEX_BANK_FRAUD = {"bank_name": "Yes Bank", "bank_account": "018863400002417", "ifsc": "YESB0000188"}
BLUE_BANK_NEW = {"bank_name": "AU Small Finance Bank", "bank_account": "2302227841156602", "ifsc": "AUBL0002278"}

SCENARIOS = [
    dict(id="01", file="01_apex_happy_intrastate.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0042", inv_date="14-Oct-2026", po_ref="PO-2026-1001", po_lines_from="PO-2026-1001",
         due="Net 30", type="happy path", run_order=1,
         expected="APPROVE",
         why="Approved vendor, valid GSTIN, PO matches line by line, intra-state so CGST 9% + SGST 9% is correct, no duplicate, under approval limit."),
    dict(id="02", file="02_bluewave_happy_interstate.pdf", layout="modern", vendor_id="V002",
         inv_no="BW/1026/118", inv_date="31 Oct 2026", po_ref="PO-2026-1002", po_lines_from="PO-2026-1002",
         due="Net 15", type="happy path", run_order=2,
         expected="APPROVE",
         why="Karnataka vendor billing a Tamil Nadu buyer: inter-state, so IGST 18% is correct. Services (SAC codes) match PO exactly."),
    dict(id="03", file="03_sahyadri_split_part1.pdf", layout="modern", vendor_id="V003", accent="#2E5E2A",
         inv_no="SIP/2627/0311", inv_date="09/10/2026", po_ref="PO-2026-1003", po_lines_from="PO-2026-1003",
         qty_override=240, type="edge case 1 (part A)", run_order=3,
         expected="APPROVE",
         why="First partial delivery: 240 of 400 rolls (Rs 60,000 of Rs 1,00,000 PO). Within PO; PO billed-so-far becomes Rs 60,000."),
    dict(id="04", file="04_sahyadri_split_part2_overbill.pdf", layout="modern", vendor_id="V003", accent="#2E5E2A",
         inv_no="SIP/2627/0347", inv_date="27/10/2026", po_ref="PO-2026-1003", po_lines_from="PO-2026-1003",
         qty_override=176, type="edge case 1 (part B)", run_order=4,
         expected="HOLD",
         why="On its own Rs 44,000 is under the PO, but cumulative billing = Rs 1,04,000 vs PO Rs 1,00,000 (+4%, 416 of 400 rolls). Exceeds 1%/Rs 500 tolerance. Only caught by tracking billed-so-far per PO."),
    dict(id="05", file="05_apex_duplicate_rescan.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS-2627-42", inv_date="14/10/2026", po_ref="PO-2026-1001", po_lines_from="PO-2026-1001",
         due="Net 30", stamp="PAYMENT REMINDER", scan=True, angle=-1.1, type="edge case 2", run_order=5,
         expected="REJECT",
         why="Same invoice as #01 resent as a scanned reminder: number written AOS-2627-42 instead of AOS/26-27/0042, date format changed. Normalised fingerprint (vendor GSTIN + invoice serial 42 + FY 26-27 + amount Rs 1,13,044) matches run #01 -> exact duplicate. Must be run after #01."),
    dict(id="06", file="06_coromandel_scan_no_po.pdf", layout="letterhead", vendor_id="V004",
         inv_no="CFS/OCT/2026/077", inv_date="31.10.2026", po_ref=None, po_lines_from="PO-2026-1004",
         narrative="Being charges towards housekeeping services for the month of October 2026:",
         scan=True, angle=0.8, type="edge case 3", run_order=6,
         expected="APPROVE (PO inferred)",
         why="Image-only PDF (no text layer) and no PO number. Vendor has two open POs (1004 housekeeping Rs 72,500; 1008 pest control Rs 18,000). Line items + amount match PO-2026-1004 exactly -> high-confidence inference, proceed. Decision card must say the PO was inferred."),
    dict(id="07", file="07_apex_wrong_tax_type.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0058", inv_date="21-Oct-2026", po_ref="PO-2026-1005", po_lines_from="PO-2026-1005",
         tax_type="IGST", due="Net 30", type="edge case 4", run_order=7,
         expected="HOLD",
         why="Total (Rs 34,220) matches the PO exactly, so a naive amount check passes. But vendor (TN, 33) and place of supply (TN, 33) are the same state -> must be CGST 9% + SGST 9%, not IGST 18%. Wrong tax head blocks the buyer's input tax credit; ask vendor for a corrected invoice."),
    dict(id="08", file="08_bluewave_bank_changed.pdf", layout="modern", vendor_id="V002",
         inv_no="BW/1126/131", inv_date="28 Oct 2026", po_ref="PO-2026-1007", po_lines_from="PO-2026-1007",
         bank_override=BLUE_BANK_NEW, bank_note="NOTE: Please remit to our NEW bank account above w.e.f. 25-Oct-2026.",
         due="Net 15", type="extra: fraud signal", run_order=8,
         expected="HOLD",
         why="Everything matches the PO, but the bank account on the invoice differs from the vendor master. Classic invoice-redirection fraud pattern -> hold and verify by calling the vendor on the number on file, never the one on the invoice."),
    dict(id="09", file="09_deccan_blocked_vendor.pdf", layout="classic", vendor_id="V005",
         inv_no="DET/0912", inv_date="19-Oct-2026", po_ref="PO-2026-1006", po_lines_from="PO-2026-1006",
         due="Immediate", type="extra: blocked vendor", run_order=9,
         expected="REJECT",
         why="Vendor is blocked in the master (failed KYC re-verification) and PO-2026-1006 is cancelled."),
    dict(id="10", file="10_konkan_over_approval_limit.pdf", layout="modern", vendor_id="V006", accent="#5B2A86",
         inv_no="KTH/26-27/1180", inv_date="24 Oct 2026", po_ref="PO-2026-1009", po_lines_from="PO-2026-1009",
         due="Net 45", type="extra: approval limit", run_order=10,
         expected="HOLD",
         why="Perfect 2-way match, but total Rs 6,46,640 exceeds the Rs 5,00,000 auto-approval limit -> route to finance manager."),
    dict(id="11", file="11_apex_phone_photo.jpg", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0063", inv_date="24-Oct-2026", po_ref="PO-2026-1010", po_lines_from="PO-2026-1010",
         due="Net 30", photo=True, type="extra: phone photo", run_order=11,
         expected="APPROVE",
         why="Someone photographed the printed invoice on a desk with their phone and sent the JPG. Tilted, keystoned, shadowed and slightly blurred, but everything on it is correct and matches PO-2026-1010. The system should read it and approve it like any other invoice."),
    dict(id="12", file="12_coromandel_handwritten_changes.pdf", layout="letterhead", vendor_id="V004",
         inv_no="CFS/OCT/2026/081", inv_date="30.10.2026", po_ref="PO-2026-1008", po_lines_from="PO-2026-1008",
         narrative="Being charges towards pest control services (quarterly contract, Oct-Dec 2026):",
         post=handwritten_changes, scan=True, angle=-0.6, type="extra: handwritten changes", run_order=12,
         extra_truth={"line_items_add": [{"description": "Addl. treatment - canteen area (handwritten)", "hsn_sac": None,
                                          "qty": None, "unit": None, "unit_price": None, "gst_rate": None,
                                          "taxable_value": 4500}],
                      "total_override": 26550, "has_handwritten_changes": True,
                      "handwritten_notes": "Extra line 'Addl. treatment - canteen area 4,500' added by hand; printed grand total Rs 21,240 struck out and 26,550 written by hand; initialled 'revised - S.K. 30/10'."},
         expected="HOLD",
         why="Printed bill for Rs 21,240 (PO-2026-1008, pest control Rs 18,000 + GST) has been altered by hand: an extra Rs 4,500 line, the printed total struck out and Rs 26,550 scribbled in. A GST tax invoice can't be changed by hand; any extra charge needs a revised invoice or a debit note. Even if the handwriting is misread, the numbers no longer add up and the extra line isn't on the PO, so it must go to a person."),
    dict(id="13", file="13_konkan_within_tolerance.pdf", layout="modern", vendor_id="V006", accent="#5B2A86",
         inv_no="KTH/26-27/1204", inv_date="29 Oct 2026", po_ref="PO-2026-1011",
         lines=[("Wireless 2D barcode scanner with cradle", "8471", 10, "Nos", 9829, 18)],
         due="Net 45", type="extra: within tolerance", run_order=13,
         expected="APPROVE",
         why="Unit price Rs 9,829 vs Rs 9,800 on the PO (+0.3%), so Rs 290 more in total. That is inside the 1% / Rs 500 tolerance, so it is approved automatically with a note. Shows the system doesn't stop a payment over a trivial difference."),
]


def main():
    truth = []
    for s in SCENARIOS:
        pdf, (rows, sub, split, total), ttype = render(s)
        path = OUT / s["file"]
        if s.get("photo"):
            to_photo(pdf, path, seed=int(s["id"]))
        elif s.get("scan"):
            to_scan(pdf, path, seed=int(s["id"]), angle=s["angle"])
        else:
            path.write_bytes(pdf)
        v = VENDORS[s["vendor_id"]]
        bank = s.get("bank_override") or {k: v[k] for k in ("bank_name", "bank_account", "ifsc")}
        truth.append({
            "id": s["id"], "file": s["file"], "scenario": s["type"], "run_order": s["run_order"],
            "is_scan": bool(s.get("scan") or s.get("photo")),
            "kind": "phone photo (JPG)" if s.get("photo") else "scanned PDF" if s.get("scan") else "digital PDF",
            "expected_decision": s["expected"], "why": s["why"],
            "ground_truth": {
                "vendor_name": v["name"], "vendor_gstin": s.get("gstin_override", v["gstin"]),
                "buyer_gstin": BUYER["gstin"], "invoice_number": s["inv_no"],
                "invoice_date_as_printed": s["inv_date"], "po_reference": s.get("po_ref"),
                "place_of_supply": f"{BUYER['state']} ({BUYER['state_code']})",
                "line_items": [{"description": d, "hsn_sac": h, "qty": q, "unit": u, "unit_price": r,
                                "gst_rate": g, "taxable_value": val} for d, h, q, u, r, g, val in rows],
                "taxable_value": sub, "tax_type": ttype,
                "tax": {k: round(a, 2) for k, a in split.items()}, "total": total,
                "bank": bank,
            },
        })
        ex = s.get("extra_truth")
        if ex:
            gt = truth[-1]["ground_truth"]
            gt["line_items"] += ex.get("line_items_add", [])
            gt["total"] = ex.get("total_override", gt["total"])
            gt["has_handwritten_changes"] = ex.get("has_handwritten_changes", False)
            gt["handwritten_notes"] = ex.get("handwritten_notes")
        print(f"{s['file']:42s} total {inr(total):>12s}  {ttype:9s} -> {s['expected']}")
    (DATA / "correct_answers.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
