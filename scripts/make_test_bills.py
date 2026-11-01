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
    dict(id="T17", file="T17_number_reused_after_01.pdf", layout="classic", vendor_id="V001",
         inv_no="AOS/26-27/0042", inv_date="16-Oct-2026", po_ref="PO-2026-1010",
         lines=L("Wireless keyboard and mouse combo", "8471", 4, "Nos", 1450, 18),
         expected="HOLD", problem="Reuses invoice number 0042 for a different bill",
         why="Apex already billed AOS/26-27/0042 (example 01). GST rules need a new number per invoice in a year. Save example 01 to records first."),
    dict(id="T18", file="T18_resent_with_new_number_after_02.pdf", layout="modern", vendor_id="V002",
         inv_no="BW/1026/125", inv_date="31 Oct 2026", po_ref="PO-2026-1002", po_lines_from="PO-2026-1002",
         expected="HOLD", problem="October's IT bill sent again under a new number",
         why="Same order and amount as example 02, just renumbered, which is how double payments slip through. Save example 02 to records first."),
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


def main():
    answers = []
    for s in BILLS + ORDER_DEPENDENT:
        pdf, (rows, sub, split, total), v = render(s)
        path = OUT / s["file"]
        if s.get("scan"):
            mi.to_scan(pdf, path, seed=int(s["id"][1:]), angle=s["angle"])
        else:
            path.write_bytes(pdf)
        answers.append(dict(
            id=s["id"], file=s["file"], problem=s["problem"], expected=s["expected"], why=s["why"],
            needs_first=("01" if s["id"] == "T17" else "02" if s["id"] == "T18" else None),
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
    (OUT / "answers.json").write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
