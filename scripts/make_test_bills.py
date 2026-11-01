"""Makes a second set of invoices for trying BillClerk yourself: one problem
each, aimed at the rules the 13 examples don't cover. They go in test_bills/
and are meant to be uploaded like any real invoice, so Gemini reads them live.

Writes test_bills/answers.json with what each bill says and the expected result."""
import importlib.util
import io
import json
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "test_bills"
OUT.mkdir(exist_ok=True)

# reuse the example invoice layouts
spec_ = importlib.util.spec_from_file_location("make_invoices", HERE / "make_invoices.py")
mi = importlib.util.module_from_spec(spec_)
sys.modules["make_invoices"] = mi
spec_.loader.exec_module(mi)
sys.path.insert(0, str(ROOT))
from app.gstin import make as gstin  # noqa: E402

V = mi.VENDORS
PO = mi.PO_LINES

# a supplier RJ Garments has never onboarded
KAVERI = dict(name="Kaveri Textile Machinery Pvt Ltd", gstin=gstin("33", "AAKCK7731J"), pan="AAKCK7731J",
              address="14, Kamaraj Road, Coimbatore 641018", state="Tamil Nadu", state_code="33",
              bank_name="Indian Bank", bank_account="6123094471", ifsc="IDIB000C041",
              email="accounts@kaverimachinery.example")
# another company, so the bill is addressed to the wrong buyer
OTHER_BUYER = dict(name="Sri Murugan Knits Pvt Ltd", address="7, Kumar Nagar, Tiruppur 641603",
                   state="Tamil Nadu", state_code="33", gstin=gstin("33", "AAICS4410P"))


def wrong_total(calc):            # the printed total is ₹1,000 more than the lines and tax add up to
    rows, sub, split, total = calc
    return rows, sub, split, total + 1000


def tax_at_12(calc):              # lines say 18% GST, but only 12% is charged
    rows, sub, split, total = calc
    half = round(sub * 0.06)
    return rows, sub, {"CGST": half, "SGST": half}, sub + 2 * half


def uneven_split(calc):           # CGST and SGST should be equal halves; here they aren't
    rows, sub, split, total = calc
    tax = sum(split.values())
    return rows, sub, {"CGST": round(tax * 0.6), "SGST": tax - round(tax * 0.6)}, total


L = lambda *x: [x]  # noqa: E731  one invoice line: description, HSN/SAC, qty, unit, rate, GST%

BILLS = [
    dict(id="T01", file="T01_unknown_vendor.pdf", layout="classic", vendor=KAVERI,
         inv_no="KTM/26-27/0215", inv_date="22-Oct-2026", po_ref="PO-2026-1005",
         lines=L("Spare blades for fabric cutting machine", "8451", 10, "Nos", 1850, 18),
         expected="REJECT", problem="A supplier we've never onboarded",
         why="Kaveri Textile Machinery isn't an approved vendor, so nothing can be paid until procurement onboards it."),
    dict(id="T02", file="T02_invalid_gstin.pdf", layout="classic", vendor_id="V001",
         gstin_override="33AAJCA3417L1ZX",
         inv_no="AOS/26-27/0071", inv_date="27-Oct-2026", po_ref="PO-2026-1005", po_lines_from="PO-2026-1005",
         expected="HOLD", problem="Apex's GSTIN with a wrong last character",
         why="33AAJCA3417L1ZX fails the GSTIN check digit (the real one ends in Q), so it's either a typo or not Apex."),
    dict(id="T03", file="T03_billed_to_another_company.pdf", layout="modern", vendor_id="V002", buyer=OTHER_BUYER,
         inv_no="BW/1026/122", inv_date="29 Oct 2026", po_ref="PO-2026-1007", po_lines_from="PO-2026-1007",
         expected="REJECT", problem="Addressed to a different company",
         why="The invoice is made out to Sri Murugan Knits, not RJ Garments, so RJ Garments can't pay it or claim its GST."),
    dict(id="T04", file="T04_total_does_not_add_up.pdf", layout="classic", vendor_id="V001", calc_fn=wrong_total,
         inv_no="AOS/26-27/0074", inv_date="28-Oct-2026", po_ref="PO-2026-1005", po_lines_from="PO-2026-1005",
         expected="HOLD", problem="Printed total is ₹1,000 too high",
         why="₹29,000 + ₹5,220 GST = ₹34,220, but the invoice asks for ₹35,220."),
    dict(id="T05", file="T05_gst_charged_at_12_percent.pdf", layout="letterhead", vendor_id="V004", calc_fn=tax_at_12,
         inv_no="CFS/OCT/2026/090", inv_date="30.10.2026", po_ref="PO-2026-1008", po_lines_from="PO-2026-1008",
         expected="HOLD", problem="GST charged at 12% on an 18% service",
         why="The line says 18%, so GST should be ₹3,240; only ₹2,160 is charged. The total adds up, so only the rate check catches it."),
    dict(id="T06", file="T06_cgst_sgst_unequal.pdf", layout="classic", vendor_id="V001", calc_fn=uneven_split,
         inv_no="AOS/26-27/0077", inv_date="29-Oct-2026", po_ref="PO-2026-1005", po_lines_from="PO-2026-1005",
         expected="HOLD", problem="CGST and SGST not split equally",
         why="Same total, but CGST ₹3,132 and SGST ₹2,088. They must be equal halves (₹2,610 each)."),
    dict(id="T07", file="T07_dated_in_the_future.pdf", layout="modern", vendor_id="V006", accent="#5B2A86",
         inv_no="KTH/26-27/1251", inv_date="15 Jan 2027", po_ref="PO-2026-1011",
         lines=L("Wireless 2D barcode scanner with cradle", "8471", 2, "Nos", 9800, 18),
         expected="HOLD", problem="Dated 15 January 2027",
         why="An invoice can't be dated in the future; the date needs confirming before it's booked."),
    dict(id="T08", file="T08_more_than_a_year_old.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/25-26/0018", inv_date="12-Aug-2025", po_ref="PO-2026-1005",
         lines=L("Cross-cut paper shredder, 20-sheet", "8472", 1, "Nos", 14500, 18),
         expected="HOLD", problem="Dated August 2025",
         why="Over a year old, so someone should find out why it's arriving now (and the order it quotes is from 2026)."),
    dict(id="T09", file="T09_order_number_does_not_exist.pdf", layout="modern", vendor_id="V003", accent="#2E5E2A",
         inv_no="SIP/2627/0402", inv_date="30/10/2026", po_ref="PO-2026-1099",
         lines=L("LLDPE stretch wrap film, 500mm x 23 micron", "3920", 40, "Rolls", 250, 18),
         expected="HOLD", problem="Quotes PO-2026-1099, which doesn't exist",
         why="There's no such purchase order, so the vendor has to confirm the right one."),
    dict(id="T10", file="T10_another_vendors_order.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0080", inv_date="30-Oct-2026", po_ref="PO-2026-1009",
         lines=L("Laptop, 14-inch, i5 / 16GB / 512GB SSD", "8471", 2, "Nos", 68500, 18),
         expected="REJECT", problem="Apex billing against Konkan's laptop order",
         why="PO-2026-1009 was raised on Konkan Tech Hardware, not Apex."),
    dict(id="T11", file="T11_price_above_order.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0083", inv_date="31-Oct-2026", po_ref="PO-2026-1005",
         lines=L("Cross-cut paper shredder, 20-sheet", "8472", 2, "Nos", 15900, 18),
         expected="HOLD", problem="Shredders at ₹15,900 instead of ₹14,500",
         why="About 10% above the agreed price, far beyond the 1% / ₹500 tolerance."),
    dict(id="T12", file="T12_item_not_on_order.pdf", layout="modern", vendor_id="V002",
         inv_no="BW/1126/140", inv_date="30 Oct 2026", po_ref="PO-2026-1007",
         lines=[("Managed IT support & helpdesk - November 2026", "998313", 1, "Month", 85000, 18),
                ("Cloud backup licence (per user per month)", "998315", 25, "Users", 600, 18),
                ("On-site laptop repair visit (2 engineers)", "998713", 1, "Visit", 7500, 18)],
         expected="HOLD", problem="An extra repair visit nobody ordered",
         why="The ₹7,500 repair visit isn't on PO-2026-1007, and it takes the bill over the order value."),
    dict(id="T13", file="T13_more_than_ordered.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0086", inv_date="31-Oct-2026", po_ref="PO-2026-1010",
         lines=[("USB-C docking station, dual display", "8471", 6, "Nos", 6200, 18),
                ("Wireless keyboard and mouse combo", "8471", 20, "Nos", 1450, 18)],
         expected="HOLD", problem="20 keyboard sets billed, 15 ordered",
         why="Five more than the order allows."),
    dict(id="T14", file="T14_no_order_number_unclear.pdf", layout="letterhead", vendor_id="V004",
         inv_no="CFS/OCT/2026/094", inv_date="31.10.2026", po_ref=None,
         narrative="Being charges towards facility support for the festive season:",
         lines=L("Additional facility support staff - Diwali week", "998533", 1, "Job", 30000, 18),
         expected="HOLD", problem="No order number, and it fits neither open order",
         why="Coromandel has two open orders (housekeeping, pest control); this matches neither, so a person must pick."),
    dict(id="T15", file="T15_two_problems_at_once.pdf", layout="modern", vendor_id="V006", accent="#5B2A86",
         tax_type="CGST+SGST", bank_override=dict(bank_name="Yes Bank", bank_account="018763300004412", ifsc="YESB0000187"),
         bank_note="NOTE: Please pay to our new account above from this invoice onwards.",
         inv_no="KTH/26-27/1260", inv_date="30 Oct 2026", po_ref="PO-2026-1011",
         lines=L("Wireless 2D barcode scanner with cradle", "8471", 3, "Nos", 9800, 18),
         expected="HOLD", problem="Wrong GST type and a new bank account",
         why="A Mumbai vendor billing Tamil Nadu must charge IGST, not CGST + SGST, and the bank account has changed."),
    dict(id="T16", file="T16_rough_scan_clean_invoice.pdf", layout="letterhead", vendor_id="V004",
         inv_no="CFS/OCT/2026/082", inv_date="30.10.2026", po_ref="PO-2026-1008", po_lines_from="PO-2026-1008",
         narrative="Being charges towards pest control services (quarterly contract, Oct-Dec 2026):",
         scan=True, angle=-3.2,
         expected="APPROVE", problem="A crooked, grainy scan of a correct bill",
         why="Nothing is wrong with it; it's here to check a poor scan is still read correctly."),
]

ORDER_DEPENDENT = [
    dict(id="T17b", file="T17b_number_reused.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0042", inv_date="16-Oct-2026", po_ref="PO-2026-1010",
         lines=L("Wireless keyboard and mouse combo", "8471", 4, "Nos", 1450, 18),
         expected="HOLD", problem="Reuses invoice number 0042 for a different bill",
         why="Apex already billed AOS/26-27/0042 (T17a). GST rules need a new number for every invoice in a year."),
    dict(id="T18b", file="T18b_resent_with_new_number.pdf", layout="modern", vendor_id="V002",
         inv_no="BW/1026/125", inv_date="31 Oct 2026", po_ref="PO-2026-1002", po_lines_from="PO-2026-1002",
         expected="HOLD", problem="October's IT bill sent again under a new number",
         why="Same order and amount as T18a, just renumbered, which is how double payments slip through."),
]


def render(spec):
    """Like the example invoices, plus: a vendor not on file, another buyer, doctored maths."""
    v = spec.get("vendor") or V[spec["vendor_id"]]
    lines = spec.get("lines") or PO[spec["po_lines_from"]]
    tax_type = spec.get("tax_type") or ("CGST+SGST" if v["state_code"] == mi.BUYER["state_code"] else "IGST")
    calc = mi.compute(lines, tax_type)
    if spec.get("calc_fn"):
        calc = spec["calc_fn"](calc)
    real_buyer = mi.BUYER
    mi.BUYER = spec.get("buyer") or real_buyer
    try:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setTitle(f"{v['name']} - {spec['inv_no']}"); c.setAuthor(v["name"])
        mi.LAYOUTS[spec["layout"]](c, spec, v, calc)
        c.showPage(); c.save()
    finally:
        mi.BUYER = real_buyer
    return buf.getvalue(), calc, v


def quotation():
    """Something that looks invoice-like but isn't one: a quotation."""
    v = V["V006"]
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4); W, H = A4
    c.setFont("SansB", 18); c.drawString(20 * mm, H - 25 * mm, v["name"])
    c.setFont("Sans", 9); c.drawString(20 * mm, H - 31 * mm, v["address"] + "   GSTIN " + v["gstin"])
    c.setFont("SansB", 22); c.drawCentredString(W / 2, H - 50 * mm, "QUOTATION")
    c.setFont("Sans", 10)
    for i, t in enumerate(["Quotation No: KTH/Q/2611", "Date: 28 Oct 2026", "Valid until: 30 Nov 2026",
                           f"To: {mi.BUYER['name']}, {mi.BUYER['address']}"]):
        c.drawString(20 * mm, H - (64 + 6 * i) * mm, t)
    c.setFont("SansB", 10); c.drawString(20 * mm, H - 100 * mm, "Item"); c.drawRightString(W - 20 * mm, H - 100 * mm, "Price (excl. GST)")
    c.setFont("Sans", 10)
    for i, (d, p) in enumerate([("Label printer, 4-inch thermal", "Rs 24,500 each"),
                                ("Barcode labels, 100x50mm (roll of 1,000)", "Rs 650 per roll")]):
        c.drawString(20 * mm, H - (108 + 7 * i) * mm, d); c.drawRightString(W - 20 * mm, H - (108 + 7 * i) * mm, p)
    c.drawString(20 * mm, H - 135 * mm, "This is a price quotation, not a tax invoice. GST extra at 18%. Delivery 7 days from order.")
    c.drawString(20 * mm, H - 145 * mm, "We look forward to your purchase order.")
    c.showPage(); c.save()
    return buf.getvalue()


# ---------------------------------------------------------------- informal bills
# Small suppliers without a GSTIN: a bill book filled in by hand, or a plain printed bill.
UNKNOWN_SHOP = dict(name="Sri Murugan Electricals", address="3, Mill Road, Tiruppur 641601", phone="+91 99940 31872")


def bill_book(shop, bill_no, date, rows, extra=None, ink="#1B2F8A", seed=1, words=None, paid=False):
    """A printed bill-book page (the kind sold in any stationery shop), filled in by hand."""
    import random
    rnd = random.Random(seed)
    W, H = A4[0], A4[1] * 0.62           # a half-height page, like a real bill book
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=(W, H))
    red = mi.colors.HexColor("#B3261E")
    L, R = 16 * mm, W - 16 * mm
    # the printed part, in red
    c.setFillColor(red); c.setStrokeColor(red)
    c.setFont("SerifB", 20); c.drawCentredString(W / 2, H - 17 * mm, shop["name"].upper())
    c.setFont("Serif", 9); c.drawCentredString(W / 2, H - 23 * mm, shop["address"])
    c.drawCentredString(W / 2, H - 28 * mm, f"Cell: {shop['phone'].replace('+91 ', '')}")
    c.setFont("SansB", 10); c.drawCentredString(W / 2, H - 36 * mm, "CASH / CREDIT BILL")
    c.setFont("Sans", 10); c.drawString(L, H - 45 * mm, "No."); c.setFont("SansB", 13); c.drawString(L + 8 * mm, H - 45 * mm, bill_no)
    c.setFont("Sans", 10); c.drawString(R - 52 * mm, H - 45 * mm, "Date:"); c.line(R - 41 * mm, H - 46 * mm, R, H - 46 * mm)
    c.drawString(L, H - 54 * mm, "M/s."); c.line(L + 10 * mm, H - 55 * mm, R, H - 55 * mm)
    top, rowh = H - 61 * mm, 9 * mm
    cols = [L, L + 12 * mm, L + 108 * mm, L + 126 * mm, L + 148 * mm, R]
    c.setLineWidth(0.8); c.rect(L, top - 8 * rowh, R - L, 8 * rowh + 7 * mm)
    c.line(L, top, R, top)
    for x in cols[1:-1]:
        c.line(x, top + 7 * mm, x, top - 8 * rowh)
    c.setFont("SansB", 9)
    for x, t in zip(cols, ["S.No.", "Particulars", "Qty", "Rate", "Amount"]):
        c.drawString(x + 2 * mm, top + 2.2 * mm, t)
    c.line(L, top - 7 * rowh, R, top - 7 * rowh)
    c.setFont("SansB", 10); c.drawRightString(cols[4] - 2 * mm, top - 8 * rowh + 3 * mm, "TOTAL")
    c.setFont("Sans", 8); c.drawString(L, 10 * mm, "Goods once sold will not be taken back.  E. & O.E.")
    c.drawRightString(R, 10 * mm, f"For {shop['name']}")
    # the handwritten part, in ballpoint blue, never quite on the line
    c.setFillColor(mi.colors.HexColor(ink))

    def hand(x, y, text, size=19, font="Hand"):
        c.saveState(); c.translate(x + rnd.uniform(-0.6, 0.6) * mm, y + rnd.uniform(-0.5, 0.7) * mm)
        c.rotate(rnd.uniform(-1.8, 1.8)); c.setFont(font, size); c.drawString(0, 0, text); c.restoreState()
    hand(R - 39 * mm, H - 45 * mm, date)
    hand(L + 13 * mm, H - 53.5 * mm, "R.J. Garments, Avinashi Rd")
    total = 0
    for i, (desc, qty, rate) in enumerate(rows):
        y = top - (i + 1) * rowh + 2.2 * mm
        amt = qty * rate; total += amt
        hand(cols[0] + 4 * mm, y, str(i + 1)); hand(cols[1] + 2 * mm, y, desc, 17)
        hand(cols[2] + 3 * mm, y, str(qty)); hand(cols[3] + 2 * mm, y, f"{rate}")
        hand(cols[4] + 2 * mm, y, f"{amt:,}/-")
    if extra:                             # e.g. GST written in by a shop that isn't registered
        label, amt = extra
        y = top - (len(rows) + 1) * rowh + 2.2 * mm
        hand(cols[1] + 2 * mm, y, label, 17); hand(cols[4] + 2 * mm, y, f"{amt:,}/-"); total += amt
    hand(cols[4] + 2 * mm, top - 8 * rowh + 2.5 * mm, f"{total:,}/-", 21)
    hand(R - 30 * mm, 15 * mm, "Selvi" if "Selvi" in shop["name"] else "S. Murugan", 18, "Hand2")
    # drawn last, so bills without them come out exactly as before
    if words:
        c.setFillColor(red); c.setFont("Sans", 9); c.drawString(L, 41 * mm, "Rupees")
        c.line(L + 13 * mm, 40 * mm, R, 40 * mm); c.setFillColor(mi.colors.HexColor(ink))
        hand(L + 15 * mm, 41 * mm, words, 16)
    if paid:   # a cash memo the shop marked as paid at the counter
        hand(L + 4 * mm, 26 * mm, "PAID - Cash", 24, "Hand2")
        hand(L + 4 * mm, 19 * mm, "Received with thanks", 15)
    c.showPage(); c.save()
    return buf.getvalue(), total


def simple_bill(shop, bill_no, date, rows, arn):
    """A plain typed bill from a small shop whose GST registration is still in progress."""
    W, H = A4
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    L, R = 20 * mm, W - 20 * mm
    c.setFont("SansB", 17); c.drawString(L, H - 25 * mm, shop["name"])
    c.setFont("Sans", 9.5); c.drawString(L, H - 31 * mm, f"{shop['address']}   Ph: {shop['phone']}   {shop.get('email', '')}")
    c.drawString(L, H - 36 * mm, f"GSTIN: Applied for (ARN {arn})")
    c.setFont("SansB", 14); c.drawCentredString(W / 2, H - 50 * mm, "BILL")
    c.setFont("Sans", 10)
    c.drawString(L, H - 60 * mm, f"Bill No: {bill_no}"); c.drawRightString(R, H - 60 * mm, f"Date: {date}")
    c.drawString(L, H - 66 * mm, f"To: {mi.BUYER['name']}, {mi.BUYER['address']}")
    y = H - 80 * mm
    c.setFont("SansB", 10)
    for x, t in ((L, "Item"), (L + 105 * mm, "Qty"), (L + 125 * mm, "Rate")):
        c.drawString(x, y, t)
    c.drawRightString(R, y, "Amount")
    c.line(L, y - 2 * mm, R, y - 2 * mm); c.setFont("Sans", 10)
    total = 0
    for desc, qty, rate in rows:
        y -= 7 * mm; total += qty * rate
        c.drawString(L, y, desc); c.drawString(L + 105 * mm, y, str(qty)); c.drawString(L + 125 * mm, y, f"{rate:,.2f}")
        c.drawRightString(R, y, f"{qty * rate:,.2f}")
    c.line(L, y - 3 * mm, R, y - 3 * mm)
    c.setFont("SansB", 11); c.drawString(L + 105 * mm, y - 9 * mm, "Total"); c.drawRightString(R, y - 9 * mm, f"Rs. {total:,.2f}")
    c.setFont("Sans", 9); c.drawString(L, y - 20 * mm, "No GST charged: our GST registration is under process.")
    c.drawString(L, y - 25 * mm, "Payment by UPI or cash.")
    c.drawRightString(R, y - 40 * mm, f"For {shop['name']}")
    c.showPage(); c.save()
    return buf.getvalue(), total


SELVI, KUMARAN = V["V007"], V["V008"]
INFORMAL = [
    dict(id="T21", file="T21_handwritten_bill_book_photo.jpg", kind="book_photo", shop=SELVI, bill_no="0457", date="28/10/26",
         rows=[("Poly thread cones (black)", 20, 85), ("Machine needles DBx1 (pkt)", 10, 95), ("Elastic 1 inch (roll)", 4, 200)],
         expected="HOLD", after_confirm="APPROVE", problem="A hand-filled bill-book bill from a small shop, photographed",
         why="Everything checks out: Selvi has no GSTIN and charges none, and it's under ₹6,000 so no order is needed. "
             "A hand-written bill is easy to make up, so the buyer confirms the goods arrived; then it's passed for payment, by UPI."),
    dict(id="T22", file="T22_new_small_shop.pdf", kind="book_scan", shop=UNKNOWN_SHOP, bill_no="1182", date="29/10/26",
         rows=[("Tube light 4 ft", 6, 280), ("Wire roll 1.5 sqmm", 1, 1120)],
         expected="HOLD", after_confirm="APPROVE", problem="A shop we've never bought from, small amount",
         why="Not on the supplier list but under ₹6,000, so the person who bought it confirms rather than an outright reject."),
    dict(id="T23", file="T23_gst_added_without_gstin.pdf", kind="book_scan", shop=SELVI, bill_no="0463", date="30/10/26",
         rows=[("Poly thread cones (black)", 20, 85), ("Machine needles DBx1 (pkt)", 10, 95), ("Elastic 1 inch (roll)", 4, 200)],
         extra=("GST 18%", 621),
         expected="HOLD", problem="GST added by hand, but the shop has no GSTIN",
         why="A business that isn't registered can't charge GST (CGST Act s.32). Pay ₹3,450, not ₹4,071, or get a proper tax invoice."),
    dict(id="T24", file="T24_gst_applied_for.pdf", kind="simple", shop=KUMARAN, bill_no="KTS/112", date="27-10-2026",
         rows=[("Measuring tapes (fibreglass)", 20, 60), ("Tailor's chalk (box of 10)", 10, 90), ("Fabric scissors 10 inch", 6, 512.5)],
         arn="AA331026047118Q",
         expected="HOLD", after_confirm="APPROVE", problem="Shop's GST registration still in progress, no GST charged",
         why="Correct: GST can't be charged until the GSTIN is issued. Once the buyer confirms it, it's passed; "
             "there's no bank or UPI on file, so the next step is to ask for a UPI ID."),
    dict(id="T25", file="T25_big_cash_bill.pdf", kind="simple", shop=KUMARAN, bill_no="KTS/118", date="30-10-2026",
         rows=[("Industrial fabric scissors 12 inch", 10, 1850), ("Cutting table cover (heavy)", 5, 2400), ("Pattern paper roll", 20, 400)],
         arn="AA331026047118Q",
         expected="HOLD", problem="₹38,500 with no bank details and no order",
         why="Over ₹10,000 it can't be paid in cash (income-tax rules on cash payments) and there are no bank details. It's also over the "
             "₹25,000 a month for a supplier without a GSTIN, and too big to buy without an order."),
    # T26a must be confirmed and saved first: T26b and T27 are only problems next to it
    dict(id="T26a", file="T26a_confirm_and_save_this_first.pdf", kind="book_scan", shop=SELVI, bill_no="0470", date="31/10/26", seed=261,
         rows=[("Poly thread cones (white)", 24, 85), ("Overlock thread (pkt)", 8, 120), ("Shirt buttons (gross)", 4, 200)],
         expected="HOLD", after_confirm="APPROVE", problem="Nothing: a normal Selvi bill, to confirm and save first",
         why="Press Confirm received, then Save to records. Then try T26b and T27."),
    dict(id="T26b", file="T26b_second_bill_same_day.pdf", kind="book_scan", shop=SELVI, bill_no="0471", date="31/10/26", seed=262,
         needs_first="T26a",
         rows=[("Machine needles DBx1 (pkt)", 10, 95), ("Elastic 1 inch (roll)", 6, 200), ("Zips 7 inch (dozen)", 10, 175)],
         expected="HOLD", problem="A second bill from the same shop on the same day",
         why="₹3,900 alone is fine, but with T26a (₹3,800) it's ₹7,700 on one day, over the ₹6,000 limit without an order. "
             "Splitting one purchase into small bills is the usual way round the limit."),
    dict(id="T27", file="T27_bill_number_goes_backwards.pdf", kind="book_scan", shop=SELVI, bill_no="0432", date="01/11/26", seed=27,
         needs_first="T26a",
         rows=[("Poly thread cones (black)", 10, 85), ("Shirt buttons (gross)", 3, 200), ("Tailor's chalk (box)", 5, 130)],
         expected="HOLD", problem="Bill number lower than an earlier bill's",
         why="Bill no. 0432 is dated after bill no. 0470 (T26a). Bill-book numbers only go up, so this may be from a second bill book."),
    dict(id="T28", file="T28_different_phone_number.jpg", kind="book_photo", shop=dict(SELVI, phone="+91 90036 44218"),
         bill_no="0466", date="29/10/26",
         rows=[("Poly thread cones (black)", 30, 85), ("Elastic 1 inch (roll)", 5, 200), ("Machine needles DBx1 (pkt)", 7, 100)],
         expected="HOLD", problem="Selvi's name, but a completely different phone number",
         why="The bill says +91 90036 44218; Selvi's number is +91 98432 17760. A fake bill usually copies the name, not the number. "
             "If Selvi confirms by phone that it's their second number, add it to their file and check again."),
    dict(id="T29", file="T29_phone_number_typo.jpg", kind="book_photo", shop=dict(SELVI, phone="+91 98432 71760"),
         bill_no="0467", date="29/10/26",
         rows=[("Poly thread cones (black)", 30, 85), ("Elastic 1 inch (roll)", 5, 200), ("Machine needles DBx1 (pkt)", 7, 100)],
         expected="HOLD", after_confirm="APPROVE", problem="Selvi's phone number with two digits swapped",
         why="71760 instead of 17760 is a writing slip, not a sign of fraud. It's noted, the buyer confirms as usual, "
             "and payment still goes only to Selvi's UPI ID on file."),
    # T30a must be confirmed and saved first: bill books usually start again in April
    dict(id="T30a", file="T30a_march_bill_confirm_and_save_first.pdf", kind="book_scan", shop=SELVI, bill_no="0985", date="28/03/26", seed=301,
         rows=[("Poly thread cones (black)", 20, 85), ("Shirt buttons (gross)", 5, 200)],
         expected="HOLD", after_confirm="APPROVE", problem="Nothing: a March bill with a high number, to confirm and save first",
         why="Press Confirm received, then Save to records. Then try T30b."),
    dict(id="T30b", file="T30b_april_new_book_starts_at_4.pdf", kind="book_scan", shop=SELVI, bill_no="0004", date="02/04/26", seed=302,
         needs_first="T30a",
         rows=[("Machine needles DBx1 (pkt)", 12, 95), ("Elastic 1 inch (roll)", 4, 200)],
         expected="HOLD", after_confirm="APPROVE", problem="Bill no. 0004 in April, after no. 0985 in March",
         why="Not a problem: shops start a new bill book in April, with the new financial year. Numbers are only compared within one year."),
    dict(id="T31", file="T31_second_bill_book.pdf", kind="book_scan", shop=SELVI, bill_no="0118", date="01/11/26", seed=31,
         needs_first="T26a",
         rows=[("Overlock thread (pkt)", 6, 120), ("Zips 7 inch (dozen)", 4, 175)],
         expected="HOLD", after_confirm="APPROVE", problem="Bill no. 0118 from Selvi's second bill book",
         why="0118 is in a different book (pages 0101-0200) from T26a's 0470. Many shops use two books at once, so it's only noted, "
             "and the buyer confirms as usual."),
    dict(id="T32", file="T32_amount_in_words_differs.pdf", kind="book_scan", shop=SELVI, bill_no="0472", date="31/10/26", seed=32,
         rows=[("Poly thread cones (white)", 24, 85), ("Overlock thread (pkt)", 8, 120), ("Shirt buttons (gross)", 4, 200)],
         words="Three thousand two hundred only",
         expected="HOLD", problem="₹3,800 in figures, 'three thousand two hundred' in words",
         why="Figures are easy to change (a 2 into an 8); words aren't. Whichever is right, the shop has to confirm it."),
    dict(id="T33", file="T33_already_paid_at_counter.jpg", kind="book_photo", shop=SELVI, bill_no="0469", date="30/10/26", seed=33,
         rows=[("Poly thread cones (black)", 12, 85), ("Shirt buttons (gross)", 3, 200), ("Tailor's chalk (box)", 6, 130)],
         words="Two thousand four hundred only", paid=True,
         expected="HOLD", after_confirm="APPROVE", problem="Cash memo marked 'PAID - Cash, Received with thanks'",
         why="Our staff already paid at the counter. Paying Selvi again would pay twice, so once confirmed, the step is to "
             "repay the staff member who paid."),
    dict(id="T34", file="T34_two_small_signs_call_the_shop.jpg", kind="book_photo", shop=dict(SELVI, phone="+91 98432 17706"),
         bill_no="0119", date="01/11/26", seed=34, needs_first="T26a",
         rows=[("Poly thread cones (white)", 20, 85), ("Elastic 1 inch (roll)", 5, 200)],
         expected="HOLD", after_confirm="APPROVE", problem="A second bill book and a phone typo on the same bill",
         why="Each would only be noted on its own. Two together are worth a phone call, so Confirm received also asks "
             "someone to call Selvi on the number we have."),
]


def make_informal(answers):
    for s in INFORMAL:
        if s["kind"] == "simple":
            pdf, total = simple_bill(s["shop"], s["bill_no"], s["date"], s["rows"], s["arn"])
            (OUT / s["file"]).write_bytes(pdf)
        else:
            seed = s.get("seed") or int(s["id"][1:])
            pdf, total = bill_book(s["shop"], s["bill_no"], s["date"], s["rows"], s.get("extra"), seed=seed,
                                   words=s.get("words"), paid=s.get("paid", False))
            if s["kind"] == "book_photo":
                mi.to_photo(pdf, OUT / s["file"], seed=seed)
            else:
                mi.to_scan(pdf, OUT / s["file"], seed=seed, angle=1.4)
        items = [dict(description=d, hsn_sac=None, qty=q, unit=None, unit_price=r, gst_rate=None, taxable_value=q * r)
                 for d, q, r in s["rows"]]
        sub = sum(i["taxable_value"] for i in items)
        tax = {"CGST": s["extra"][1] / 2, "SGST": s["extra"][1] / 2} if s.get("extra") else {}
        answers.append(dict(id=s["id"], file=s["file"], problem=s["problem"], expected=s["expected"], why=s["why"],
                            needs_first=s.get("needs_first"), after_confirm=s.get("after_confirm"),
                            printed=dict(vendor_name=s["shop"]["name"], vendor_gstin=None, vendor_phone=s["shop"]["phone"],
                                         gst_registration="applied" if s.get("arn") else "none", gst_arn=s.get("arn"),
                                         written_by_hand=s["kind"] != "simple", buyer_gstin=None,
                                         amount_in_words=s.get("words"), paid_status="paid" if s.get("paid") else None,
                                         payment_mode="cash" if s.get("paid") else None,
                                         invoice_number=s["bill_no"], invoice_date=s["date"], po_reference=None,
                                         place_of_supply=None, line_items=items, taxable_value=sub, tax=tax,
                                         total=sub + sum(tax.values()), bank={"bank_name": None, "bank_account": None, "ifsc": None})))
        print(f"{s['file']:42s} {mi.inr(total):>12s}  -> {s['expected']}")


def main():
    # T17a / T18a are the originals (copies of examples 01 and 02): save them, then try T17b / T18b
    for tid, src, saved in (("T17a", "01_apex_happy_intrastate.pdf", "AOS/26-27/0042"),
                            ("T18a", "02_bluewave_happy_interstate.pdf", "BW/1026/118")):
        (OUT / f"{tid}_save_this_first.pdf").write_bytes((ROOT / "invoices" / src).read_bytes())
    answers = [dict(id="T17a", file="T17a_save_this_first.pdf", problem="Nothing: the original, to save first", expected="APPROVE",
                    why="A correct Apex invoice (same as example 01). Press Save to records, then try T17b.", needs_first=None,
                    printed="example 01"),
               dict(id="T18a", file="T18a_save_this_first.pdf", problem="Nothing: the original, to save first", expected="APPROVE",
                    why="A correct Bluewave invoice (same as example 02). Press Save to records, then try T18b.", needs_first=None,
                    printed="example 02")]
    for s in BILLS + ORDER_DEPENDENT:
        pdf, (rows, sub, split, total), v = render(s)
        path = OUT / s["file"]
        if s.get("scan"):
            mi.to_scan(pdf, path, seed=int(s["id"][1:]), angle=s["angle"])
        else:
            path.write_bytes(pdf)
        answers.append(dict(
            id=s["id"], file=s["file"], problem=s["problem"], expected=s["expected"], why=s["why"],
            needs_first=("T17a" if s["id"] == "T17b" else "T18a" if s["id"] == "T18b" else None),
            printed=dict(vendor_name=v["name"], vendor_gstin=s.get("gstin_override", v["gstin"]),
                         buyer_gstin=(s.get("buyer") or mi.BUYER)["gstin"], invoice_number=s["inv_no"],
                         invoice_date=s["inv_date"], po_reference=s.get("po_ref"),
                         place_of_supply=f"{mi.BUYER['state']} ({mi.BUYER['state_code']})",
                         line_items=[dict(description=d, hsn_sac=h, qty=q, unit=u, unit_price=r, gst_rate=g,
                                          taxable_value=val) for d, h, q, u, r, g, val in rows],
                         taxable_value=sub, tax={k: round(a, 2) for k, a in split.items()}, total=total,
                         bank=s.get("bank_override") or {k: v[k] for k in ("bank_name", "bank_account", "ifsc")})))
        print(f"{s['file']:42s} {mi.inr(total):>12s}  -> {s['expected']}")

    (OUT / "T19_quotation_not_invoice.pdf").write_bytes(quotation())
    (OUT / "T20_not_really_a_pdf.pdf").write_text("This is a plain text file renamed to .pdf, so it can't be opened.\n")
    answers += [
        dict(id="T19", file="T19_quotation_not_invoice.pdf", problem="A quotation, not an invoice", expected="REJECT",
             why="Nothing is owed on a quotation; it should be removed from the invoice queue.", needs_first=None, printed=None),
        dict(id="T20", file="T20_not_really_a_pdf.pdf", problem="A text file renamed to .pdf", expected="REJECT",
             why="It can't be opened as a PDF, so the sender is asked for a fresh copy.", needs_first=None, printed=None),
    ]
    print("T19_quotation_not_invoice.pdf  -> REJECT\nT20_not_really_a_pdf.pdf       -> REJECT")
    make_informal(answers)
    (OUT / "answers.json").write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
