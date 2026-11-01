"""Sanity-checks the generated test data: valid GSTINs, one page per invoice,
digital PDFs carry their key numbers as text, scans carry none, and every
invoice adds up (except the hand-altered one, which isn't meant to)."""
import csv
import json
import sys
from pathlib import Path

import pdfplumber
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.gstin import validate  # noqa: E402

problems = []


def check(ok, what):
    if not ok:
        problems.append(what)
        print("  FAIL:", what)


def load_csv(name):
    with open(ROOT / "data" / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


truth = json.loads((ROOT / "data/correct_answers.json").read_text(encoding="utf-8"))
buyer = json.loads((ROOT / "data/buyer.json").read_text(encoding="utf-8"))
po_numbers = {p["po_number"] for p in load_csv("purchase_orders.csv")}

check(validate(buyer["gstin"])["valid"], f"buyer GSTIN {buyer['gstin']}")
for v in load_csv("vendors.csv"):
    check(validate(v["gstin"])["valid"], f"vendor GSTIN {v['gstin']}")

for t in truth:
    gt, path = t["ground_truth"], ROOT / "invoices" / t["file"]

    if path.suffix.lower() in (".jpg", ".png"):
        Image.open(path).verify()
        text, pages = "", 1
    else:
        with pdfplumber.open(path) as pdf:
            text, pages = pdf.pages[0].extract_text() or "", len(pdf.pages)
    print(f"{t['file']}: {pages} page, {len(text)} characters of text")
    check(pages == 1, f"{t['file']} should be one page")

    if t["is_scan"]:
        check(not text.strip(), f"{t['file']} is a scan, so it shouldn't have a text layer")
    elif text:
        for needle in (gt["vendor_gstin"], gt["invoice_number"]):
            check(needle in text, f"{t['file']} should show {needle}")

    if gt.get("has_handwritten_changes"):
        continue  # altered by hand on purpose: its numbers don't add up
    lines = gt["line_items"]
    subtotal = sum(li["taxable_value"] for li in lines)
    tax = sum(gt["tax"].values())
    check(subtotal == gt["taxable_value"], f"{t['file']}: lines add up to the subtotal")
    check(all(li["qty"] * li["unit_price"] == li["taxable_value"] for li in lines), f"{t['file']}: qty × rate")
    check(abs(subtotal + tax - gt["total"]) < 0.01, f"{t['file']}: subtotal + tax = total")
    check(abs(tax - round(subtotal * 0.18)) < 0.01, f"{t['file']}: GST is 18%")
    if gt["po_reference"]:
        check(gt["po_reference"] in po_numbers, f"{t['file']}: PO {gt['po_reference']} exists")

print("\nAll checks passed." if not problems else f"\n{len(problems)} problem(s) found.")
sys.exit(1 if problems else 0)
