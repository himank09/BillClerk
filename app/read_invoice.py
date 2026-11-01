"""Reads an invoice into structured fields with Gemini.

A digital PDF already contains its text, so we send just the text: it's cheaper
and the numbers come straight from the file. Scans and photos have no text, so
the image itself goes to the model.

The model only reads. It never decides anything, and every number it returns
is checked again by the rules in checks.py.

EXTRACTOR=mock skips Gemini and replays the correct answers from
data/correct_answers.json, which is handy for testing the rules offline.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import pdfplumber
from pydantic import BaseModel, Field

from .records import DATA


class LineItem(BaseModel):
    description: str
    hsn_sac: str | None = None
    qty: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    gst_rate: float | None = None
    taxable_value: float | None = None


class Confidence(BaseModel):
    vendor_gstin: float
    invoice_number: float
    invoice_date: float
    po_reference: float
    line_items: float
    total: float


class Invoice(BaseModel):
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    vendor_phone: str | None = None
    vendor_upi: str | None = Field(None, description="UPI ID printed on the bill (e.g. name@okaxis), if any")
    vendor_address: str | None = None
    gst_registration: str | None = Field(None, description="'registered' if a GSTIN is printed, 'applied' if it says GST applied for "
                                         "or shows a GST application number (ARN), 'none' otherwise")
    gst_arn: str | None = Field(None, description="GST application reference number (ARN), if printed")
    bill_book_no: str | None = Field(None, description="Bill-book number or series printed on a bill-book page (e.g. 'Book No. 12'), if any")
    amount_in_words: str | None = Field(None, description="The total written in words, exactly as written (e.g. 'Rupees Three Thousand Eight Hundred only')")
    paid_status: str | None = Field(None, description="'paid' if the bill is marked as already paid (PAID, Received with thanks, cash received), "
                                    "'credit' if marked as a credit bill, otherwise null")
    payment_mode: str | None = Field(None, description="How it was paid, if the bill says: 'cash', 'upi', 'card' or 'bank'")
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = Field(None, description="exactly as printed")
    po_reference: str | None = Field(None, description="buyer's PO / order / work-order number; null if not printed")
    place_of_supply: str | None = None
    line_items: list[LineItem] = []
    taxable_value: float | None = None
    cgst: float | None = None
    sgst: float | None = None
    igst: float | None = None
    total: float | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    ifsc: str | None = None
    is_invoice: bool = Field(description="true only if this is a supplier invoice or bill; false for anything else")
    document_type: str | None = Field(None, description="what the document is, in a few words, e.g. 'tax invoice', 'resume', 'quotation'")
    written_by_hand: bool = Field(False, description="true if the whole bill was filled in by hand, e.g. a bill book or cash memo")
    has_handwritten_changes: bool = Field(False, description="true only if a printed or typed bill was altered by hand afterwards")
    handwritten_notes: str | None = Field(None, description="what was changed by hand, in one or two sentences")
    confidence: Confidence


PROMPT = """You are reading a document sent to an accounts payable team. It is usually an Indian GST tax invoice,
but small suppliers also send simpler bills: a cash memo or credit memo from a bill book filled in by hand,
a bill of supply, or a plain printed bill with no GSTIN.
First decide whether it is a supplier invoice or bill asking to be paid, and always fill in is_invoice and
document_type. Cash memos, bill-book bills and bills of supply count as bills. If it is not a bill (a resume,
a report, a quotation, an estimate, a purchase order, a letter, a photo of something else), set is_invoice to
false, say what it is in document_type, and leave the invoice fields null.
Otherwise, extract the fields exactly as they appear on the document.
Rules:
- Copy identifiers (GSTIN, invoice number, PO number, account number, IFSC) character for character.
- Give the invoice date exactly as printed; do not reformat it.
- Amounts are plain numbers in rupees, no commas or currency symbols.
- If a field is not printed on the invoice, return null. Never guess, especially the PO reference.
- Put CGST, SGST and IGST amounts in their own fields; leave the ones not charged as null.
- If the whole bill was filled in by hand (a bill book, a cash memo), set written_by_hand to true. That is normal
  for small shops and is not a change.
- Separately, look for a printed or typed bill that was altered by hand: added lines, struck-out amounts,
  rewritten totals. Only then set has_handwritten_changes to true and describe it in handwritten_notes.
- For anything handwritten, give the values as written (include handwritten line items), and lower the
  confidence for any value that is hard to read.
- Copy the supplier's phone number, UPI ID and address if printed. If there is no GSTIN, set vendor_gstin to null and
  say in gst_registration whether GST registration is applied for (copy the ARN into gst_arn) or not mentioned.
- Copy the total in words exactly as written, if it is there. Note a printed bill-book number or series.
- If the bill is marked as already paid (PAID, Received with thanks, cash received), set paid_status to 'paid'
  and payment_mode to how it was paid; if "Credit" is ticked or written, set paid_status to 'credit'.
- A bill without a bill number, PO, or line-wise GST is fine: leave those fields null rather than guessing.
- confidence: for each listed field, 0-1, how sure you are you read it correctly
  (lower it for blurry scans, handwriting, or anything you had to infer)."""


MAX_PAGES = 4  # invoices are short; only the first pages are read
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def has_text_layer(path):
    """Returns (has_text, text, pages). Photos (JPG/PNG) never have a text layer."""
    if Path(path).suffix.lower() in IMAGE_TYPES:
        return False, "", 1
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:MAX_PAGES])
            return len(text.strip()) > 50, text, len(pdf.pages)
    except Exception:
        # some PDFs (odd exporters, slightly damaged files) trip up pdfplumber; try a second reader
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))  # raises if the file really isn't a readable PDF
        try:
            text = "\n".join(pdf[i].get_textpage().get_text_range() for i in range(min(len(pdf), MAX_PAGES)))
            return len(text.strip()) > 50, text, len(pdf)
        finally:
            pdf.close()


# temporary problems on Google's side: worth retrying (compared in lower case)
TRANSIENT = ("503", "unavailable", "429", "resource_exhausted", "overloaded", "timed out", "timeout",
             "500", "internal", "504", "deadline")


def _gemini(path, text, progress=print):
    from google import genai
    from google.genai import types
    # fail fast: we handle retries ourselves so the user sees what's happening
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                          http_options=types.HttpOptions(timeout=90_000,
                                                         retry_options=types.HttpRetryOptions(attempts=1)))
    if text:
        contents = [PROMPT, "Invoice text (extracted from the PDF's text layer):\n\n" + text]
    else:
        mime = IMAGE_TYPES.get(Path(path).suffix.lower(), "application/pdf")
        contents = [PROMPT, types.Part.from_bytes(data=Path(path).read_bytes(), mime_type=mime)]
    config = types.GenerateContentConfig(response_mime_type="application/json",
                                         response_schema=Invoice, temperature=0)
    # first choice from env, then fallbacks if a model is busy or retired
    models = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-3.6-flash",
                          "gemini-3-flash-preview", "gemini-3.1-flash-lite"] if m]
    last = None
    for model in dict.fromkeys(models):
        for attempt, wait in enumerate([3, 8, 0], 1):
            progress(f"reading with {model}" + (f" (attempt {attempt})" if attempt > 1 else "") + "…")
            try:
                resp = client.models.generate_content(model=model, contents=contents, config=config)
                inv = resp.parsed if resp.parsed is not None else Invoice.model_validate_json(resp.text)
                return inv.model_dump(), model
            except Exception as e:
                last, msg = e, str(e)
                if "404" in msg or "not found" in msg.lower():
                    progress(f"{model} isn't available, trying the next model")
                    break
                if any(t in msg.lower() for t in TRANSIENT):
                    if wait:
                        progress(f"{model} is busy ({msg[:3]}), retrying in {wait}s")
                        time.sleep(wait)
                        continue
                    progress(f"{model} still busy, switching model")
                    break
                raise
    raise RuntimeError(f"Gemini could not read the invoice: {str(last)[:200]}")


def _mock(path):
    truth = {t["file"]: t for t in json.loads((DATA / "correct_answers.json").read_text(encoding="utf-8"))}
    t = truth[Path(path).name]
    g = t["ground_truth"]
    c = 0.86 if t["is_scan"] else 0.97
    return {
        "vendor_name": g["vendor_name"], "vendor_gstin": g["vendor_gstin"],
        "buyer_name": json.loads((DATA / "buyer.json").read_text(encoding="utf-8"))["name"],
        "buyer_gstin": g["buyer_gstin"],
        "invoice_number": g["invoice_number"], "invoice_date": g["invoice_date_as_printed"],
        "po_reference": g["po_reference"], "place_of_supply": g["place_of_supply"],
        "line_items": g["line_items"], "taxable_value": g["taxable_value"],
        "cgst": g["tax"].get("CGST"), "sgst": g["tax"].get("SGST"), "igst": g["tax"].get("IGST"),
        "total": g["total"], **{k: g["bank"][k] for k in ("bank_name", "bank_account", "ifsc")},
        "has_handwritten_changes": g.get("has_handwritten_changes", False),
        "handwritten_notes": g.get("handwritten_notes"),
        "confidence": {k: c for k in ("vendor_gstin", "invoice_number", "invoice_date",
                                      "po_reference", "line_items", "total")},
    }, "mock (ground truth replay)"


def tidy(f):
    """Remove the stray spaces and lower-case letters a model sometimes puts in identifiers."""
    for k in ("vendor_gstin", "buyer_gstin", "ifsc"):
        if f.get(k):
            f[k] = "".join(str(f[k]).split()).upper()
    if f.get("bank_account"):
        f["bank_account"] = "".join(ch for ch in str(f["bank_account"]) if ch.isalnum())
    for li in f.get("line_items") or []:
        if li.get("hsn_sac") is not None:
            li["hsn_sac"] = str(li["hsn_sac"]).split(".")[0].strip()
    return f


def reading_file(path):
    """Where a file's reading is saved. Readings of the example invoices are kept
    with the code; readings of uploads stay private (never published)."""
    folder = "examples" if Path(path).resolve().parent == DATA.parent / "invoices" else "uploads"
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    return DATA / "saved_readings" / folder / f"{digest}.json"


def extract(path, progress=print):
    """Each reading is saved under the file's fingerprint, so checking the same
    file again is instant and gives the same answer. NO_CACHE=1 forces a fresh read."""
    textual, text, pages = has_text_layer(path)
    route = ("text layer → LLM" if textual else
             "photo → vision LLM" if Path(path).suffix.lower() in IMAGE_TYPES else "scanned image → vision LLM")
    if os.environ.get("EXTRACTOR", "gemini").lower() == "mock":
        fields, model = _mock(path)
        return {"route": route, "model": model, "pages": pages, "fields": tidy(fields), "cached": False}
    cache = reading_file(path)
    if cache.exists() and os.environ.get("NO_CACHE") != "1":
        c = json.loads(cache.read_text(encoding="utf-8"))
        return {"route": route, "model": c["model"] + ", cached", "pages": pages, "fields": tidy(c["fields"]), "cached": True}
    fields, model = _gemini(path, text if textual else None, progress)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"model": model, "file": Path(path).name, "fields": fields}, indent=2), encoding="utf-8")
    return {"route": route, "model": model, "pages": pages, "fields": tidy(fields), "cached": False}
