"""Builds the procurement side: buyer, vendor master, purchase orders.

All companies are fictitious. GSTINs are structurally valid (real state codes,
PAN-format middle, correct mod-36 checksum) but are not registered numbers.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.gstin import make  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

BUYER = {
    "name": "RJ Garments Pvt Ltd",
    "address": "SF No. 112, Avinashi Road, Tiruppur 641603",
    "state": "Tamil Nadu", "state_code": "33",
    "gstin": make("33", "AAKCR5148N"),
    "ap_email": "ap@rjgarments.example",
}

# bank details on file = what the vendor submitted at onboarding
VENDORS = [
    dict(vendor_id="V001", phone="+91 44 2434 1180", registration="registered", name="Apex Office Supplies Pvt Ltd",
         address="22, Ranganathan Street, T. Nagar, Chennai 600017",
         state="Tamil Nadu", state_code="33", pan="AAJCA3417L",
         bank_name="HDFC Bank", bank_account="50200041172839", ifsc="HDFC0001842",
         email="billing@apexoffice.example", status="approved"),
    dict(vendor_id="V002", phone="+91 80 4112 7720", registration="registered", name="Bluewave IT Services LLP",
         address="4th Floor, 80 Feet Road, Koramangala, Bengaluru 560034",
         state="Karnataka", state_code="29", pan="AAQFB7720M",
         bank_name="ICICI Bank", bank_account="002105019874", ifsc="ICIC0000021",
         email="accounts@bluewaveit.example", status="approved"),
    dict(vendor_id="V003", phone="+91 20 2712 5506", registration="registered", name="Sahyadri Industrial Packaging Pvt Ltd",
         address="Plot 61, MIDC Bhosari, Pune 411026",
         state="Maharashtra", state_code="27", pan="AAKCS5506P",
         bank_name="Bank of Maharashtra", bank_account="60284417730", ifsc="MAHB0000417",
         email="dispatch@sahyadripack.example", status="approved"),
    dict(vendor_id="V004", phone="+91 44 2446 9183", registration="registered", name="Coromandel Facility Services Pvt Ltd",
         address="8/3, Lattice Bridge Road, Adyar, Chennai 600020",
         state="Tamil Nadu", state_code="33", pan="AAECC9183R",
         bank_name="Indian Bank", bank_account="7041128856", ifsc="IDIB000A031",
         email="invoices@coromandelfs.example", status="approved"),
    dict(vendor_id="V005", phone="+91 40 2320 2290", registration="registered", name="Deccan Electricals & Traders",
         address="5-9-22, Abids Road, Hyderabad 500001",
         state="Telangana", state_code="36", pan="ABCFD2290Q",
         bank_name="State Bank of India", bank_account="38817265541", ifsc="SBIN0020063",
         email="deccanelec@example.com", status="blocked",
         block_reason="Failed annual vendor KYC re-verification (Aug 2026)"),
    dict(vendor_id="V006", phone="+91 22 2386 6612", registration="registered", name="Konkan Tech Hardware Pvt Ltd",
         address="Unit 12, Lamington Road, Grant Road East, Mumbai 400007",
         state="Maharashtra", state_code="27", pan="AAGCK6612D",
         bank_name="Kotak Mahindra Bank", bank_account="9912245087", ifsc="KKBK0000652",
         email="ar@konkantech.example", status="approved"),
    # small local suppliers with no GSTIN: below the turnover limit, or registration still in progress.
    # They bill from a bill book or a simple printed memo, and take payment by UPI or cash.
    dict(vendor_id="V007", phone="+91 98432 17760", registration="unregistered", name="Selvi Thread & Needle Store",
         address="12, Kamaraj Street, Khaderpet, Tiruppur 641601", state="Tamil Nadu", state_code="33", pan="",
         bank_name="", bank_account="", ifsc="", email="", upi="9843217760@okaxis", status="approved"),
    dict(vendor_id="V008", phone="+91 94433 52081", registration="applied", name="Kumaran Tailoring Supplies",
         address="45, Palladam Road, Tiruppur 641604", state="Tamil Nadu", state_code="33", pan="",
         bank_name="", bank_account="", ifsc="", email="kumarantailoring@example.com", status="approved"),
]
for v in VENDORS:
    v.setdefault("upi", "")
    v["gstin"] = make(v["state_code"], v["pan"]) if v["pan"] else ""
    v.setdefault("block_reason", "")

# Purchase orders. rate/qty are pre-tax; gst_rate applies per line.
# GST rates assumed 18% for all items here (computers, office furniture,
# toner, plastic film, IT and housekeeping services) - verify against the
# current CBIC rate schedule before quoting them as fact.
POS = [
    dict(po_number="PO-2026-1001", vendor_id="V001", po_date="2026-09-29", status="open",
         description="Office consumables and seating - Q3 refresh",
         lines=[("Toner cartridge, black, 85A compatible", "8443", 20, "Nos", 2450, 18),
                ("Ergonomic mesh office chair with headrest", "9401", 6, "Nos", 7800, 18)]),
    dict(po_number="PO-2026-1002", vendor_id="V002", po_date="2026-10-01", status="open",
         description="Managed IT support - October 2026",
         lines=[("Managed IT support & helpdesk - October 2026", "998313", 1, "Month", 85000, 18),
                ("Cloud backup licence (per user per month)", "998315", 25, "Users", 600, 18)]),
    dict(po_number="PO-2026-1003", vendor_id="V003", po_date="2026-09-22", status="open",
         description="Stretch wrap film for export pallets",
         lines=[("LLDPE stretch wrap film, 500mm x 23 micron", "3920", 400, "Rolls", 250, 18)]),
    dict(po_number="PO-2026-1004", vendor_id="V004", po_date="2026-10-01", status="open",
         description="Housekeeping services - October 2026",
         lines=[("Housekeeping services - Tiruppur factory, October 2026", "998533", 1, "Month", 64000, 18),
                ("Deep cleaning of cutting floor (one-time)", "998533", 1, "Job", 8500, 18)]),
    dict(po_number="PO-2026-1005", vendor_id="V001", po_date="2026-10-06", status="open",
         description="Paper shredders for accounts office",
         lines=[("Cross-cut paper shredder, 20-sheet", "8472", 2, "Nos", 14500, 18)]),
    dict(po_number="PO-2026-1006", vendor_id="V005", po_date="2026-06-18", status="cancelled",
         description="LED high-bay fittings (cancelled after vendor block)",
         lines=[("LED high-bay light 150W", "9405", 30, "Nos", 3200, 18)]),
    dict(po_number="PO-2026-1007", vendor_id="V002", po_date="2026-10-28", status="open",
         description="Managed IT support - November 2026",
         lines=[("Managed IT support & helpdesk - November 2026", "998313", 1, "Month", 85000, 18),
                ("Cloud backup licence (per user per month)", "998315", 25, "Users", 600, 18)]),
    dict(po_number="PO-2026-1008", vendor_id="V004", po_date="2026-10-01", status="open",
         description="Quarterly pest control - Oct-Dec 2026",
         lines=[("Pest control services - quarterly contract", "998531", 1, "Quarter", 18000, 18)]),
    dict(po_number="PO-2026-1009", vendor_id="V006", po_date="2026-10-12", status="open",
         description="Laptops for new finance team",
         lines=[("Laptop, 14-inch, i5 / 16GB / 512GB SSD", "8471", 8, "Nos", 68500, 18)]),
    dict(po_number="PO-2026-1010", vendor_id="V001", po_date="2026-10-14", status="open",
         description="Desk accessories for new finance team",
         lines=[("USB-C docking station, dual display", "8471", 6, "Nos", 6200, 18),
                ("Wireless keyboard and mouse combo", "8471", 15, "Nos", 1450, 18)]),
    dict(po_number="PO-2026-1011", vendor_id="V006", po_date="2026-10-15", status="open",
         description="Barcode scanners for warehouse dispatch",
         lines=[("Wireless 2D barcode scanner with cradle", "8471", 10, "Nos", 9800, 18)]),
]


def po_totals(po, buyer_state, vendor_state):
    sub = sum(q * r for _, _, q, _, r, _ in po["lines"])
    tax = round(sum(q * r * g / 100 for _, _, q, _, r, g in po["lines"]))
    return sub, tax, sub + tax, ("CGST+SGST" if buyer_state == vendor_state else "IGST")


def main():
    vmap = {v["vendor_id"]: v for v in VENDORS}
    (DATA / "buyer.json").write_text(json.dumps(BUYER, indent=2))

    vcols = ["vendor_id", "name", "gstin", "pan", "address", "state", "state_code",
             "bank_name", "bank_account", "ifsc", "email", "phone", "upi", "registration", "status", "block_reason"]
    with open(DATA / "vendors.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=vcols)
        w.writeheader()
        for v in VENDORS:
            w.writerow({k: v[k] for k in vcols})

    with open(DATA / "purchase_orders.csv", "w", newline="") as f, \
         open(DATA / "purchase_order_lines.csv", "w", newline="") as g:
        pw = csv.writer(f)
        pw.writerow(["po_number", "vendor_id", "po_date", "status", "description",
                     "subtotal", "tax_type", "tax_amount", "total", "amount_billed_so_far"])
        lw = csv.writer(g)
        lw.writerow(["po_number", "line_no", "description", "hsn_sac", "qty", "unit",
                     "unit_price", "gst_rate", "line_value"])
        for po in POS:
            v = vmap[po["vendor_id"]]
            sub, tax, tot, ttype = po_totals(po, BUYER["state_code"], v["state_code"])
            pw.writerow([po["po_number"], po["vendor_id"], po["po_date"], po["status"],
                         po["description"], sub, ttype, tax, tot, 0])
            for i, (d, h, q, u, r, gr) in enumerate(po["lines"], 1):
                lw.writerow([po["po_number"], i, d, h, q, u, r, gr, q * r])

    # matching policy the decision engine reads (assumptions, shown in the UI)
    policy = {
        "currency": "INR",
        "tolerance": {"percent": 1.0, "absolute_inr": 500,
                      "rule": "tighter of the two governs, applied to cumulative taxable value per PO"},
        "approval_limit_inr": 500000,
        "petty_limit_inr": 6000,
        "cash_limit_inr": 10000,
        "small_supplier_month_cap_inr": 25000,
        "duplicate_window_days": 365,
        "max_invoice_age_days": 365,
        "po_inference_min_confidence": 0.85,
        "extraction_min_confidence": 0.80,
        "matching": "2-way (invoice vs PO); GRN / 3-way match out of scope",
    }
    (DATA / "rules.json").write_text(json.dumps(policy, indent=2))
    print("Wrote buyer, vendors, purchase orders and policy to data/")


if __name__ == "__main__":
    main()
