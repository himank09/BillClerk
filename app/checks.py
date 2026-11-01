"""The invoice pipeline: PDF in -> decision with reasons out.

Every stage reports what it checked, what it found and why. All decisions
are plain rules in code; the LLM is only used to read the document.

Decision logic
  REJECT  if any check says this invoice must never be paid as-is
  HOLD    if any check needs a human (or a corrected invoice)
  APPROVE only when every check passes
"""
import hashlib
import json
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz, utils

from . import records
from .read_invoice import extract, has_text_layer
from .gstin import validate as gstin_check
from .clean_values import (clean_po, financial_year, inr, invoice_serial,
                        parse_date, to_amount)

SEVERITY = {"PASS": 0, "INFO": 1, "HOLD": 2, "REJECT": 3}

# one-line versions of each finding, for headlines and the list of checks
SHORTS = {
    "NOT_A_PDF": "Not a PDF or a photo.",
    "MISSING_FIELD": "Key details are missing from the invoice.",
    "LOW_CONFIDENCE": "Some details couldn't be read clearly.",
    "INVALID_GSTIN": "The vendor's GSTIN isn't valid.",
    "WRONG_BUYER": "Addressed to another company, not us.",
    "BUYER_GSTIN_UNREADABLE": "Our GSTIN on the invoice couldn't be read.",
    "ARITHMETIC": "The invoice's own numbers don't add up.",
    "BAD_DATE": "The invoice date couldn't be read.",
    "FUTURE_DATE": "Dated in the future.",
    "STALE_INVOICE": "More than a year old.",
    "GSTIN_NOT_ON_FILE": "The vendor's GSTIN doesn't match our records.",
    "UNKNOWN_VENDOR": "Not one of our approved vendors.",
    "CGST_SGST_UNEQUAL": "CGST and SGST aren't equal.",
    "TAX_AMOUNT": "The GST amount doesn't match the rate.",
    "PO_NOT_FOUND": "The PO number on it doesn't exist.",
    "PO_OTHER_VENDOR": "The PO belongs to another vendor.",
    "PO_CLOSED": "The PO is closed or cancelled.",
    "PO_UNCLEAR": "No PO on it, and no open order clearly matches.",
    "NUMBER_REUSED": "Invoice number already used for a different amount.",
    "POSSIBLE_DUPLICATE": "Possibly a duplicate under a new number.",
    "LINE_NOT_ON_PO": "Has an item that isn't on the order.",
}
STATUS = {0: "pass", 1: "warn", 2: "hold", 3: "reject"}
# holds that only need the buyer to say "yes, this arrived": the Confirm received button clears them
CONFIRMABLE = {"BUYER_CONFIRM", "NEW_SMALL_SUPPLIER"}


def similar(a, b):
    """0-100 likeness of two names or descriptions, ignoring case and punctuation
    (invoices often print names in capitals: APEX OFFICE SUPPLIES = Apex Office Supplies)."""
    return fuzz.token_set_ratio(a or "", b or "", processor=utils.default_process)


class Run:
    def __init__(self, path, progress=None, save=False):
        self.path = Path(path)
        self.save = save
        self.progress = progress or (lambda msg: None)
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.buyer, self.policy, self.vendors, self.pos = records.load_master()
        self.con = records.connect()
        self.flags = []          # every finding that affects the decision
        self.ctx = {}            # values later stages need
        self.stages = []

    # -- helpers ---------------------------------------------------------
    def flag(self, stage, severity, code, message, audience=None, action=None, short=None, vendor_msg=None, row=None):
        # row: what the check's own line says, when it shouldn't just repeat the headline
        self.flags.append(dict(stage=stage, severity=severity, code=code, message=message,
                               audience=audience, action=action, vendor_msg=vendor_msg or message,
                               short=short or SHORTS.get(code) or message, row=row))

    def _digits(self, x):
        return "".join(ch for ch in (x or "") if ch.isdigit())[-10:]  # last 10 digits of a phone number

    def _saved(self, v):
        return records.history(self.con, v["vendor_id"]) if v else []

    def _same_day(self, v, no_po_only=False):
        """Bills from this supplier with the same date that are already in records."""
        d = self.ctx.get("date")
        return [h for h in self._saved(v) if d and h["invoice_date"] == d.isoformat()
                and (not no_po_only or not h["po_number"])]

    def stage(self, key, name, fn):
        t0 = time.perf_counter()
        before = len(self.flags)
        checks, data, summary, skipped = [], {}, "", False
        stop = None
        try:
            summary, checks, data = fn()
        except SkipStage as s:
            summary, skipped = str(s), True
        except Stop as s:
            summary, stop = str(s), s
        mine = self.flags[before:]
        worst = max([SEVERITY[f["severity"]] for f in mine], default=0)
        blocking = [f for f in mine if f["severity"] in ("HOLD", "REJECT")]
        if blocking:
            summary = blocking[0].get("row") or blocking[0]["short"]
            if len(blocking) > 1:
                summary += f" (+{len(blocking) - 1} more)"
        elif key == "match" and any(f["code"] == "WITHIN_TOLERANCE" for f in mine):
            summary = next(f["short"] for f in mine if f["code"] == "WITHIN_TOLERANCE")
        res = dict(key=key, name=name, status="skipped" if skipped else STATUS[worst],
                   summary=summary, checks=checks, flags=mine, data=data,
                   ms=round((time.perf_counter() - t0) * 1000))
        if key == "decide" and self.ctx.get("decision"):
            res["status"] = {"APPROVE": "pass", "HOLD": "hold", "REJECT": "reject"}[self.ctx["decision"]]
        self.stages.append(res)
        if stop:
            raise stop
        return res

    # -- stages ----------------------------------------------------------
    def s_ingest(self):
        p = self.path
        if not p.exists() or p.suffix.lower() not in (".pdf", ".jpg", ".jpeg", ".png"):
            self.flag("ingest", "REJECT", "NOT_A_PDF", f"{p.name} is not a PDF or a photo we can read.",
                      audience="internal", action="Ask the sender for a PDF or a photo (JPG/PNG) of the invoice.",
                      row="Not a PDF or a photo")
            raise Stop("not a PDF or photo")
        self.ctx["sha"] = hashlib.sha256(p.read_bytes()).hexdigest()
        seen = records.earlier_decisions(self.con, self.ctx["sha"], self.run_id)
        self.ctx["seen_before"] = seen
        if p.stat().st_size == 0:
            self.flag("ingest", "REJECT", "UNREADABLE_FILE", f"{p.name} is empty (0 bytes).",
                      audience="internal", action="Download or export the invoice again, then upload the new copy.",
                      short="The file is empty.", row="Empty file, 0 bytes")
            raise Stop("file is empty")
        try:
            textual, _, pages = has_text_layer(p)
        except Exception:
            self.flag("ingest", "REJECT", "UNREADABLE_FILE",
                      f"{p.name} can't be opened. It may be damaged, password-protected, or not really a PDF.",
                      audience="internal", action="Ask the sender to export the invoice to PDF again, or send a photo of it.",
                      short="The file can't be opened.",
                      row="Damaged, password-protected, or not a real PDF")
            raise Stop("file can't be opened") from None
        self.ctx["scan"] = not textual
        photo = p.suffix.lower() != ".pdf"
        nice = "Photo" if photo else ("PDF" if textual else "Scanned PDF")
        return (nice + (f", {pages} pages" if pages > 1 else ""),
                [dict(label="File is a PDF or photo", ok=True, detail="photo" if photo else "PDF"),
                 dict(label="Checked before", ok=None,
                      detail=f"{len(seen)} time{'s' if len(seen) != 1 else ''}, last on {seen[0]['decided_at'][:16].replace('T', ' ')}: {seen[0]['decision'].lower()}"
                      if seen else "no, first time this exact file has been seen"),
                 dict(label="Text layer present", ok=textual,
                 detail="will use the vision model" if not textual else "will read the text directly")],
                dict(file=p.name, pages=pages, size_kb=p.stat().st_size // 1024, scan=not textual))

    def s_extract(self):
        try:
            ex = extract(self.path, progress=self.progress)
        except Exception as e:
            self.ctx["f"] = {}
            self.flag("extract", "HOLD", "EXTRACTION_FAILED",
                      f"The invoice could not be read automatically ({str(e)[:160]}).",
                      audience="internal", action="AP clerk to key in this invoice manually, or re-run it later.",
                      short="Couldn't read the invoice automatically.",
                      row="The AI couldn't read it this time")
            raise Stop("could not read the invoice") from None
        f = ex["fields"]
        self.ctx["f"] = f
        looks_like_invoice = any([f.get("vendor_gstin"), f.get("invoice_number"), f.get("total"), f.get("line_items")])
        if f.get("is_invoice") is False or not looks_like_invoice:
            what = (f.get("document_type") or "").strip().rstrip(".")
            if not what or what.lower() in ("tax invoice", "invoice", "bill"):
                what = "document with no vendor, invoice number, amount or items on it"
            self.flag("extract", "REJECT", "NOT_AN_INVOICE",
                      f"This isn't an invoice: it looks like {'an' if what[:1].lower() in 'aeiou' else 'a'} {what}.",
                      audience="internal", action="Remove it from the invoice queue, or forward it to whoever it was meant for.",
                      short="This isn't an invoice, so there's nothing to pay.",
                      row="Not an invoice")
            raise Stop("not an invoice")
        shown = {k: f.get(k) for k in ("vendor_name", "vendor_gstin", "invoice_number", "invoice_date",
                                       "po_reference", "taxable_value", "cgst", "sgst", "igst", "total",
                                       "bank_account", "ifsc")}
        n = len(f.get("line_items") or [])
        return (f"{n} item{'s' if n != 1 else ''}, total {inr(to_amount(f.get('total')) or 0)}" + (" (saved reading)" if ex.get("cached") else ""),
                [dict(label=k.replace("_", " "), ok=None, detail=str(v)) for k, v in shown.items()],
                dict(route=ex["route"], model=ex["model"], fields=f))

    def s_validate(self):
        f, pol, checks = self.ctx["f"], self.policy, []
        hw = bool(f.get("has_handwritten_changes"))
        checks.append(dict(label="No handwritten changes", ok=not hw,
                           detail=f.get("handwritten_notes") or ("none seen" if not hw else "changes seen")))
        if hw:
            self.flag("validate", "HOLD", "HANDWRITTEN_CHANGES",
                      "The invoice has been changed by hand" + (f": {f['handwritten_notes'].rstrip('.')}" if f.get("handwritten_notes") else "") +
                      ". A GST tax invoice can't be altered by hand, and handwritten figures can't be read reliably enough to pay on.",
                      audience="vendor", action="Ask the vendor for a revised invoice, or a debit note for any extra charge.",
                      short="The invoice has been changed by hand.",
                      vendor_msg="This invoice has been altered by hand" + (f" ({f['handwritten_notes'].rstrip('.')})" if f.get("handwritten_notes") else "") +
                                 ". We can't accept handwritten changes on a GST tax invoice. Please send a revised invoice, "
                                 "or raise a debit note for any additional charge.")
        # A supplier without a GSTIN can still send a valid bill (a cash memo, a bill-book bill):
        # it just isn't a tax invoice. Such bills often have no printed number either.
        tax_charged = sum(to_amount(f.get(k)) or 0 for k in ("cgst", "sgst", "igst"))
        unregistered = not f.get("vendor_gstin")
        self.ctx["unregistered"] = unregistered
        need = ("invoice_date", "total") if unregistered else ("vendor_gstin", "invoice_number", "invoice_date", "total")
        missing = [k for k in need if not f.get(k)] + ([] if f.get("line_items") else ["line_items"])
        checks.append(dict(label="Required fields present", ok=not missing,
                           detail=", ".join(missing) or "all present"))
        if f.get("written_by_hand") and not hw:
            checks.append(dict(label="Written by hand", ok=None, detail="a bill-book or hand-written bill; amounts double-checked below"))
            self.flag("validate", "INFO", "HANDWRITTEN_BILL", "A hand-written bill (bill book or cash memo).",
                      short="Hand-written bill")
        if unregistered:
            applied = (f.get("gst_registration") or "").lower() == "applied"
            arn = f.get("gst_arn")
            checks.append(dict(label="Supplier's GST registration", ok=None,
                               detail=("applied for" + (f", ARN {arn}" if arn else "")) if applied else "no GSTIN on the bill"))
            if tax_charged > 0.5:
                self.flag("validate", "HOLD", "GST_WITHOUT_GSTIN",
                          f"The bill charges {inr(tax_charged)} GST but shows no GSTIN"
                          + (" (registration is only applied for)" if applied else "")
                          + ". A business that isn't registered can't collect GST (CGST Act, section 32), and we couldn't claim it back.",
                          audience="vendor", action="Pay only the amount before GST, or ask the supplier for their GSTIN and a proper tax invoice.",
                          short=f"Charges {inr(tax_charged)} GST but has no GSTIN" + (" (registration only applied for)" if applied else "") + ".",
                          vendor_msg=f"Your bill adds {inr(tax_charged)} GST, but it doesn't show a GSTIN"
                                     + (" (your registration is still being processed)" if applied else "")
                                     + ". GST can only be charged once you're registered. Please send the bill without GST, "
                                     "or a tax invoice with your GSTIN.")
            elif applied:
                self.flag("validate", "INFO", "GST_APPLIED",
                          f"The supplier's GST registration is still being processed{f' (ARN {arn})' if arn else ''}, so no GST is charged.",
                          action="Ask the supplier to send GST invoices once their GSTIN is issued.",
                          short="GST registration applied for; no GST charged")
            else:
                self.flag("validate", "INFO", "NOT_TAX_INVOICE",
                          "The supplier has no GSTIN and charges no GST: a valid bill, but not a tax invoice, so there's no GST credit to claim.",
                          short="Not a tax invoice, so no GST credit")
        if missing:
            self.flag("validate", "HOLD", "MISSING_FIELD",
                      f"The invoice is missing: {', '.join(m.replace('_', ' ') for m in missing)}.",
                      audience="vendor", action="Ask the vendor to resend the invoice with these details.")

        conf = f.get("confidence") or {}
        # only fields that are actually on the bill (a missing PO or GSTIN can't be "read badly")
        low = {k: v for k, v in conf.items() if v is not None and v < pol["extraction_min_confidence"]
               and (f.get(k) if k != "line_items" else f.get("line_items"))}
        checks.append(dict(label=f"Reading confidence ≥ {pol['extraction_min_confidence']:.0%}",
                           ok=not low, detail=", ".join(f"{k} {v:.0%}" for k, v in low.items()) or
                           f"lowest {min(conf.values()):.0%}" if conf else "n/a"))
        if low:
            self.flag("validate", "HOLD", "LOW_CONFIDENCE",
                      "Some fields could not be read reliably: " + ", ".join(low) + ".",
                      audience="internal", action="AP clerk to check these fields against the PDF.")

        g = gstin_check(f.get("vendor_gstin"))
        checks.append(dict(label="Vendor GSTIN is well-formed (checksum)", ok=g["valid"],
                           detail=g["reason"] or f"{g['gstin']} · state {g['state_code']}"))
        if f.get("vendor_gstin") and not g["valid"]:
            self.flag("validate", "HOLD", "INVALID_GSTIN",
                      f"Vendor GSTIN {g['gstin']} is not valid ({g['reason']}). Either it was misread or the number is wrong.",
                      audience="internal", action="Check the GSTIN on the PDF and on the GST portal.",
                      short=f"The vendor's GSTIN {g['gstin']} isn't valid (its check digit is wrong).")

        bg = gstin_check(f.get("buyer_gstin"))
        ours = (f.get("buyer_gstin") or "").upper() == self.buyer["gstin"]
        checks.append(dict(label="Billed to our GSTIN", ok=ours,
                           detail=f.get("buyer_gstin") or "not printed"))
        if f.get("buyer_gstin") and not ours:
            if bg["valid"]:
                self.flag("validate", "REJECT", "WRONG_BUYER",
                          f"The invoice is addressed to {f.get('buyer_name') or 'GSTIN ' + bg['gstin']} ({bg['gstin']}), not to us ({self.buyer['gstin']}).",
                          audience="vendor", action="Return to vendor; it was billed to the wrong entity.",
                          short=f"Addressed to {f.get('buyer_name') or 'GSTIN ' + bg['gstin']}, not {self.buyer['name']}.")
            else:
                self.flag("validate", "HOLD", "BUYER_GSTIN_UNREADABLE",
                          "Buyer GSTIN on the invoice could not be read correctly.", audience="internal",
                          action="AP clerk to confirm the buyer GSTIN on the PDF.")

        # arithmetic: the document has to agree with itself
        lines = f.get("line_items") or []
        bad_lines = [li["description"] for li in lines if None not in (li.get("qty"), li.get("unit_price"), li.get("taxable_value"))
                     and abs(li["qty"] * li["unit_price"] - li["taxable_value"]) > 1]
        line_sum = sum(to_amount(li.get("taxable_value")) or 0 for li in lines)
        taxable = to_amount(f.get("taxable_value")) or line_sum
        tax = sum(to_amount(f.get(k)) or 0 for k in ("cgst", "sgst", "igst"))
        total = to_amount(f.get("total")) or 0
        ok_lines = not bad_lines and abs(line_sum - taxable) <= 1
        ok_total = abs(taxable + tax - total) <= 1
        checks.append(dict(label="Line items add up (qty × rate, sum = taxable value)", ok=ok_lines,
                           detail=f"lines {inr(line_sum)} vs taxable {inr(taxable)}"))
        checks.append(dict(label="Taxable value + GST = total", ok=ok_total,
                           detail=f"{inr(taxable)} + {inr(tax)} = {inr(taxable + tax)} vs printed {inr(total)}"))
        if not (ok_lines and ok_total):
            self.flag("validate", "HOLD", "ARITHMETIC",
                      "The invoice's own numbers don't add up "
                      f"(taxable {inr(taxable)} + GST {inr(tax)} vs total {inr(total)}).",
                      audience="vendor", action="Ask the vendor for a corrected invoice.",
                      short=(f"{inr(taxable)} + {inr(tax)} GST = {inr(taxable + tax)}, but the invoice asks for {inr(total)}."
                             if ok_lines else "The line items don't add up to the amounts printed."))

        d = parse_date(f.get("invoice_date"))
        age = (date.today() - d).days if d else None
        ok_date = d is not None and 0 <= age <= pol["max_invoice_age_days"]
        checks.append(dict(label="Invoice date is valid", ok=ok_date,
                           detail=f"{f.get('invoice_date')} → {d.isoformat()}" if d else f"could not read '{f.get('invoice_date')}'"))
        if not d:
            self.flag("validate", "HOLD", "BAD_DATE", f"Could not understand the date '{f.get('invoice_date')}'.",
                      audience="internal", action="AP clerk to enter the invoice date.")
        elif age < 0:
            self.flag("validate", "HOLD", "FUTURE_DATE", f"Invoice is dated in the future ({d}).",
                      audience="vendor", action="Ask the vendor to confirm the invoice date.",
                      short=f"Dated {d.strftime('%d %b %Y').lstrip('0')}, which is in the future.")
        elif age > pol["max_invoice_age_days"]:
            self.flag("validate", "HOLD", "STALE_INVOICE", f"Invoice is {age} days old.",
                      audience="internal", action="Check why this invoice is arriving so late.",
                      short=f"Dated {d.strftime('%d %b %Y').lstrip('0')}, more than a year ago.")
        self.ctx.update(date=d, taxable=taxable, tax=tax, total=total)
        return ("Complete, and the numbers add up",
                checks, {})

    def s_vendor(self):
        f, checks = self.ctx["f"], []
        gst = (f.get("vendor_gstin") or "").upper()
        digits = self._digits
        v = next((x for x in self.vendors if gst and x["gstin"] == gst), None)
        how = "GSTIN"
        if not v and f.get("vendor_phone") and len(digits(f["vendor_phone"])) == 10:
            # small suppliers have no GSTIN, but their phone number is on every bill
            v = next((x for x in self.vendors if digits(x.get("phone")) == digits(f["vendor_phone"])), None)
            how = "phone number"
        if not v and f.get("vendor_name"):
            best = max(self.vendors, key=lambda x: similar(x["name"], f["vendor_name"]))
            score = similar(best["name"], f["vendor_name"])
            if score >= 90:
                v, how = best, f"name ({score:.0f}% match)"
        if v and how != "GSTIN" and v["gstin"]:
            # a registered supplier's bill must carry its GSTIN, and the right one
            self.flag("vendor", "HOLD", "GSTIN_NOT_ON_FILE",
                      f"Matched to {v['name']} by {how}, but the GSTIN on the invoice ({gst or 'none'}) is not the one on file ({v['gstin']}).",
                      audience="internal", action="Procurement to confirm the vendor's GSTIN before payment.",
                      short=(f"{v['name']}'s GSTIN is missing from this bill." if not gst else
                             f"The GSTIN {gst} isn't the one on file for {v['name']}."))
        checks.append(dict(label="Vendor found in vendor master", ok=v is not None,
                           detail=f"{v['vendor_id']} · {v['name']} (matched by {how})" if v else
                           f"no vendor with GSTIN {gst}" if gst else "no matching name or phone number"))
        if not v:
            name = f.get("vendor_name") or "This supplier"
            name = name.title() if name.isupper() else name   # bill books print shop names in capitals
            if self.ctx.get("unregistered") and (self.ctx.get("total") or 0) <= self.policy["petty_limit_inr"]:
                # a small local shop we haven't bought from before: fine for a small amount, if someone vouches for it
                self.flag("vendor", "HOLD", "NEW_SMALL_SUPPLIER",
                          f"{name} isn't on our supplier list. It's a small bill (up to {inr(self.policy['petty_limit_inr'])}), "
                          "so it can be paid once the person who made the purchase confirms it.",
                          audience="internal", action="The person who made the purchase confirms it; procurement adds the supplier if we'll buy again.",
                          short=f"First bill from {name}: the person who made the purchase should confirm it.")
            else:
                self.flag("vendor", "REJECT", "UNKNOWN_VENDOR",
                          f"{name}{f' ({gst})' if gst else ''} is not an approved vendor.",
                          audience="internal", action="Send to procurement; vendor must be onboarded before any payment.",
                          short=f"{name} isn't one of our approved vendors.")
            self.ctx["vendor"] = None
            self._cash_check(None)
            return "Supplier not on our list", checks, {}
        self.ctx["vendor"] = v
        active = v["status"] == "approved"
        checks.append(dict(label="Vendor is active", ok=active,
                           detail=v["status"] + (f" · {v['block_reason']}" if v["block_reason"] else "")))
        if not active:
            self.flag("vendor", "REJECT", "VENDOR_BLOCKED",
                      f"{v['name']} is blocked in the vendor master ({v['block_reason'] or v['status']}).",
                      audience="vendor", action="Do not pay. Inform the vendor and loop in procurement.",
                      short=f"Vendor is blocked: {v['block_reason'] or v['status']}.")
        acct = "".join(ch for ch in (f.get("bank_account") or "") if ch.isalnum())
        ifsc = (f.get("ifsc") or "").upper().strip()
        if acct and not v["bank_account"]:
            checks.append(dict(label="Bank details", ok=None, detail=f"none on file; the bill gives …{acct[-4:]}"))
            self.flag("vendor", "INFO", "BANK_FROM_BILL",
                      f"We have no bank details on file for {v['name']}; the bill gives an account ending {acct[-4:]}.",
                      action=f"Confirm the account with {v['name']} by phone before the first payment.",
                      short="Bank details taken from the bill; confirm by phone first")
        elif acct:
            same = acct == v["bank_account"] and (not ifsc or ifsc == v["ifsc"])
            checks.append(dict(label="Bank details match the vendor master", ok=same,
                               detail=f"invoice {f.get('bank_name') or ''} …{acct[-4:]} / {ifsc} vs on file {v['bank_name']} …{v['bank_account'][-4:]} / {v['ifsc']}"))
            if not same:
                self.flag("vendor", "HOLD", "BANK_CHANGED",
                          f"The invoice asks for payment to {f.get('bank_name') or 'a'} account ending {acct[-4:]}, "
                          f"but our records say {v['bank_name']} account ending {v['bank_account'][-4:]}. "
                          "A changed bank account is the most common sign of invoice fraud.",
                          audience="internal",
                          action=f"Call {v['name']} on the phone number already on file (not one from the invoice or email) to confirm before any payment.",
                          short=f"Bank account changed to …{acct[-4:]} (on file: …{v['bank_account'][-4:]}). Possible payment fraud.")
        else:
            checks.append(dict(label="Bank details match the vendor master", ok=None, detail="not printed; we pay to the account on file"))
        if not v["gstin"]:
            self._small_supplier_checks(v, how, checks)   # includes the cash check
        else:
            self._cash_check(v)
        kind = "approved vendor" if v["gstin"] else "regular supplier, no GSTIN"
        return f"{v['name']}, {kind}", checks, dict(vendor_id=v["vendor_id"], vendor_name=v["name"])

    def _small_supplier_checks(self, v, how, checks):
        """Suppliers without a GSTIN are known by their phone number, and are only meant for small,
        occasional buys: a fake bill usually gets the shop's name right but not its number, and
        regular buying above a monthly limit should go through a proper supplier set-up."""
        f, total = self.ctx["f"], self.ctx.get("total") or 0
        on_bill, on_file = self._digits(f.get("vendor_phone")), self._digits(v.get("phone"))
        if len(on_bill) == 10 and on_file and on_bill != on_file:
            checks.append(dict(label="Phone number matches the one on file", ok=False,
                               detail=f"bill {f['vendor_phone']} vs on file {v['phone']}"))
            self.flag("vendor", "HOLD", "PHONE_MISMATCH",
                      f"The bill gives the phone number {f['vendor_phone']}, but {v['name']}'s number on file is {v['phone']}. "
                      "A supplier without a GSTIN is known by its phone number, and a fake bill usually copies the shop's name, not its number.",
                      audience="internal",
                      action=f"Call {v['name']} on {v['phone']} (the number on file) and ask whether they issued this bill.",
                      short=f"The phone number on the bill ({f['vendor_phone']}) isn't {v['name']}'s ({v['phone']}).")
        elif on_file:
            checks.append(dict(label="Phone number matches the one on file", ok=True if on_bill else None,
                               detail=f"{v['phone']}" if on_bill else "no phone number on the bill"))
        self._cash_check(v)
        d, cap = self.ctx.get("date"), self.policy["small_supplier_month_cap_inr"]
        if d:
            month = [h for h in self._saved(v) if (h["invoice_date"] or "")[:7] == d.isoformat()[:7]]
            before = sum(h["total"] or 0 for h in month)
            checks.append(dict(label=f"Within {inr(cap)} a month for a supplier without a GSTIN", ok=before + total <= cap,
                               detail=f"{inr(before)} already in records for {d.strftime('%B')} + {inr(total)} now = {inr(before + total)}"))
            if before + total > cap:
                self.flag("vendor", "HOLD", "MONTHLY_CAP",
                          (f"{inr(before)} from {v['name']} is already in records for {d.strftime('%B')}; with this bill it comes to "
                           if month else "This bill alone is ") + f"{inr(before + total)}, over the {inr(cap)} a month we buy from a supplier "
                          "without a GSTIN, purchase order or bank details. Buying this much regularly needs a proper supplier set-up.",
                          audience="internal",
                          action=f"Procurement sets {v['name']} up as a regular supplier: purchase orders, bank details and KYC.",
                          short=(f"{v['name']} would reach {inr(before + total)} in {d.strftime('%B')}" if month else
                                 f"{inr(total)} from {v['name']} in one bill") + f", over the {inr(cap)} monthly limit for small suppliers.")

    def _cash_check(self, v):
        """A big bill with no way to pay electronically would have to be paid in cash, and cash over
        Rs 10,000 a day to one person isn't allowed as a business expense (Income Tax Act, s. 40A(3)).
        The limit is per day, so bills from the same supplier on the same day are added up."""
        f, limit = self.ctx["f"], self.policy["cash_limit_inr"]
        has_bank = (v and (v["bank_account"] or v.get("upi"))) or f.get("bank_account") or f.get("vendor_upi")
        total = self.ctx.get("total") or 0
        others = self._same_day(v)
        day = total + sum(h["total"] or 0 for h in others)
        if not has_bank and day > limit:
            together = f" Together with {len(others)} other bill{'s' if len(others) > 1 else ''} from the same day, that's {inr(day)}." if others else ""
            self.flag("vendor", "HOLD", "CASH_LIMIT",
                      f"There are no bank or UPI details for this {inr(total)} bill.{together} Paying more than {inr(limit)} in cash to "
                      "one person in a day isn't allowed as a business expense (Income Tax Act, section 40A(3)).",
                      audience="vendor", action="Get the supplier's bank account or UPI ID and pay electronically.",
                      short=(f"{inr(day)} to one supplier on one day with no bank or UPI details; over {inr(limit)} it can't be paid in cash."
                             if others else f"No bank or UPI details for a {inr(total)} bill; over {inr(limit)} it can't be paid in cash."),
                      vendor_msg="Please share your bank account or UPI details; we can't pay bills of this size in cash.")

    def s_gst(self):
        f, v = self.ctx["f"], self.ctx.get("vendor")
        checks = []
        if self.ctx.get("unregistered"):
            # no GSTIN: no GST may be charged at all (checked with the document), so there's nothing to split or rate
            return ("No GST: the supplier isn't registered" if self.ctx["tax"] < 0.5 else "GST charged without a GSTIN"), [], {}
        supplier_state = (f.get("vendor_gstin") or (v or {}).get("gstin") or "")[:2]
        pos = f.get("place_of_supply") or ""
        pos_code = "".join(ch for ch in pos if ch.isdigit())[:2] or self.buyer["state_code"]
        intra = supplier_state == pos_code
        expected = "CGST + SGST" if intra else "IGST"
        cg, sg, ig = (to_amount(f.get(k)) or 0 for k in ("cgst", "sgst", "igst"))
        charged = "IGST" if ig and not (cg or sg) else ("CGST + SGST" if (cg or sg) and not ig else
                                                          "mixed" if (ig and (cg or sg)) else "none")
        why = (f"supplier state {supplier_state} {'=' if intra else '≠'} place of supply {pos_code}")
        ok_type = charged == expected
        checks.append(dict(label="Correct type of GST charged", ok=ok_type,
                           detail=f"{why} → should be {expected}; invoice charges {charged}"))
        if not ok_type:
            self.flag("gst", "HOLD", "WRONG_TAX_TYPE",
                      f"The vendor charged {charged}, but this is an {'intra' if intra else 'inter'}-state supply "
                      f"({why}), so it must be {expected}. The total looks right, but we could not claim "
                      "input tax credit on a wrongly-charged tax.",
                      audience="vendor", action=f"Ask the vendor to cancel this invoice and reissue it with {expected}.",
                      short=f"Wrong GST type: {charged} charged on an {'intra' if intra else 'inter'}-state supply, should be {expected}.",
                      vendor_msg=f"This invoice charges {charged}, but {'both your registration and the place of supply are in the same state' if intra else 'your registration and the place of supply are in different states'} "
                                 f"({why}), so {expected} applies. We can't claim input tax credit on tax charged under the wrong head, "
                                 f"so please cancel this invoice and issue a fresh one with {expected}.")
        if cg or sg:
            ok_split = abs(cg - sg) <= 1
            checks.append(dict(label="CGST equals SGST", ok=ok_split, detail=f"{inr(cg)} / {inr(sg)}"))
            if not ok_split:
                self.flag("gst", "HOLD", "CGST_SGST_UNEQUAL", "CGST and SGST should be equal halves.",
                          audience="vendor", action="Ask the vendor for a corrected invoice.",
                          short=f"CGST {inr(cg)} and SGST {inr(sg)} should be equal halves of {inr(cg + sg)}.")
        # rate check: GST should be rate × taxable value, rounded to the rupee
        exp_tax = round(sum((to_amount(li.get("taxable_value")) or 0) * (li.get("gst_rate") or 18) / 100
                            for li in f.get("line_items") or []))
        ok_amt = abs(exp_tax - self.ctx["tax"]) <= 1
        checks.append(dict(label="GST amount = rate × taxable value", ok=ok_amt,
                           detail=f"expected {inr(exp_tax)}, charged {inr(self.ctx['tax'])}"))
        if not ok_amt:
            self.flag("gst", "HOLD", "TAX_AMOUNT",
                      f"GST charged ({inr(self.ctx['tax'])}) does not match the rates on the invoice ({inr(exp_tax)}).",
                      audience="vendor", action="Ask the vendor to correct the tax amount.",
                      short=f"GST should be {inr(exp_tax)} at the rates shown, but {inr(self.ctx['tax'])} is charged.")
        return (f"{expected}, correct for {'a same-state' if intra else 'an out-of-state'} supply", checks, dict(expected=expected, charged=charged))

    def s_po(self):
        f, v = self.ctx["f"], self.ctx.get("vendor")
        if not v:
            raise SkipStage("Not checked: vendor unknown")
        checks, ref = [], clean_po(f.get("po_reference"))
        if ref:
            po = self.pos.get(ref)
            checks.append(dict(label="PO number on invoice exists", ok=po is not None, detail=ref))
            if not po:
                self.flag("po", "HOLD", "PO_NOT_FOUND", f"PO {ref} quoted on the invoice does not exist in our system.",
                          audience="vendor", action="Ask the vendor to confirm the correct PO number.",
                          short=f"{ref} isn't one of our purchase orders.")
                self.ctx["po"] = None
                return f"{ref} not found", checks, {}
            mine = po["vendor_id"] == v["vendor_id"]
            checks.append(dict(label="PO belongs to this vendor", ok=mine, detail=f"{ref} is with {po['vendor_id']}"))
            if not mine:
                owner = next((x["name"] for x in self.vendors if x["vendor_id"] == po["vendor_id"]), po["vendor_id"])
                self.flag("po", "REJECT", "PO_OTHER_VENDOR", f"{ref} was raised on a different vendor ({owner}).",
                          audience="vendor", action="Return to vendor; the PO quoted is not theirs.",
                          short=f"{ref} was raised on {owner}, not {v['name']}.")
            is_open = po["status"] == "open"
            checks.append(dict(label="PO is open", ok=is_open, detail=po["status"]))
            if not is_open:
                self.flag("po", "REJECT", "PO_CLOSED", f"{ref} is {po['status']}; nothing more can be billed against it.",
                          audience="vendor", action="Do not pay against this PO.")
            # an invoice can't come before the order it bills against: a sign of backdating or recycling
            po_date = date.fromisoformat(po["po_date"])
            d = self.ctx.get("date")
            if mine and d and d < po_date:
                checks.append(dict(label="Invoice dated after the PO", ok=False, detail=f"invoice {d}, PO raised {po_date}"))
                self.flag("po", "HOLD", "INVOICE_BEFORE_PO",
                          f"The invoice is dated {d.strftime('%d %b %Y').lstrip('0')}, before {ref} was raised on "
                          f"{po_date.strftime('%d %b %Y').lstrip('0')}. An invoice can't come before its order.",
                          audience="internal", action="Check with the vendor; the invoice may be backdated or reused.",
                          short=f"Dated before its own order ({ref} was raised on {po_date.strftime('%d %b %Y').lstrip('0')}).")
            self.ctx.update(po=po, po_inferred=False)
            return f"{ref}, {po['description']}", checks, dict(po_number=ref, inferred=False)

        # no PO printed: work out which open PO this invoice is for
        cands = [p for p in self.pos.values() if p["vendor_id"] == v["vendor_id"] and p["status"] == "open"]
        checks.append(dict(label="PO number on invoice", ok=False, detail="not printed - trying to infer"))
        scored = []
        for p in cands:
            billed = sum(b["value"] for b in records.billed_on_po(self.con, p["po_number"]).values())
            remaining = max(p["subtotal"] - billed, 1)
            amt = max(0.0, 1 - abs(self.ctx["taxable"] - remaining) / remaining)
            ls = [max((similar(li["description"], pl["description"]) / 100) *
                      (1.0 if str(li.get("hsn_sac")) == pl["hsn_sac"] else 0.85) for pl in p["lines"])
                  for li in f.get("line_items") or []]
            lines = sum(ls) / len(ls) if ls else 0
            scored.append(dict(po_number=p["po_number"], description=p["description"],
                               amount_score=round(amt, 2), lines_score=round(lines, 2),
                               confidence=round(0.5 * amt + 0.5 * lines, 2)))
        scored.sort(key=lambda s: -s["confidence"])
        for s in scored:
            checks.append(dict(label=f"Candidate {s['po_number']}", ok=None,
                               detail=f"{s['description']} · amount fit {s['amount_score']:.0%}, items fit {s['lines_score']:.0%} → {s['confidence']:.0%}"))
        need = self.policy["po_inference_min_confidence"]
        top = scored[0] if scored else None
        margin = top["confidence"] - (scored[1]["confidence"] if len(scored) > 1 else 0) if top else 0
        if top and top["confidence"] >= need and margin >= 0.15:
            self.ctx.update(po=self.pos[top["po_number"]], po_inferred=True)
            self.flag("po", "INFO", "PO_INFERRED", short=f"PO {top['po_number']} worked out from the items", message=
                      f"No PO on the invoice; matched to {top['po_number']} ({top['description']}) "
                      f"with {top['confidence']:.0%} confidence from vendor, amount and line items.",
                      audience="vendor", action="Remind the vendor to quote the PO number on future invoices.")
            return f"{top['po_number']}, worked out from the items ({top['confidence']:.0%} match)", checks, dict(po_number=top["po_number"], inferred=True, candidates=scored)
        self.ctx["po"] = None
        petty = self.policy["petty_limit_inr"]
        total = self.ctx.get("total") or 0
        others = self._same_day(v, no_po_only=True)
        day = total + sum(h["total"] or 0 for h in others)
        if total <= petty < day:
            # each bill is small, but together they're over the limit: one purchase split up to avoid an order
            nos = ", ".join(h["invoice_number"] or "no number" for h in others)
            self.flag("po", "HOLD", "SPLIT_BILLS",
                      f"Each bill is under {inr(petty)}, but {v['name']} already has {len(others)} bill{'s' if len(others) > 1 else ''} dated "
                      f"{self.ctx['date'].strftime('%d %b %Y').lstrip('0')} in records ({nos}). Together that's {inr(day)}, over the "
                      f"{inr(petty)} limit for buying without an order. Splitting one purchase into small bills is the usual way round that limit.",
                      audience="internal",
                      action="Ask the buyer why this wasn't bought as one order; if it was one purchase, raise a purchase order for the whole amount.",
                      short=f"Another bill from {v['name']} on the same day: together {inr(day)}, over the {inr(petty)} limit without an order.")
            return f"No order, and {inr(day)} on one day", checks, dict(candidates=scored[:2])
        if total <= petty:
            # small everyday buys (thread, tea, a repair) rarely have a purchase order
            self.flag("po", "INFO", "PETTY_NO_PO",
                      f"No purchase order, which is fine for a small purchase (up to {inr(petty)}).",
                      short=f"no order needed under {inr(petty)}")
            return f"No order needed: under {inr(petty)}", checks, dict(candidates=scored[:2])
        self.flag("po", "HOLD", "PO_UNCLEAR",
                  ("No PO on the invoice and no open PO is a clear match. Closest: " +
                   "; ".join(f"{s['po_number']} ({s['confidence']:.0%})" for s in scored[:2]) + ".") if scored else
                  f"No PO on the bill, there's no open order with {v['name']}, and it's over the {inr(petty)} limit for small purchases.",
                  audience="internal", action="AP clerk to pick the right PO, or ask the vendor." if scored else
                  "Get the purchase approved (or an order raised) before paying.",
                  short=None if scored else f"No purchase order, and over the {inr(petty)} limit for small purchases.")
        return "Could not identify the PO", checks, dict(candidates=scored[:2])

    def s_duplicate(self):
        f, v = self.ctx["f"], self.ctx.get("vendor")
        if not v:
            raise SkipStage("Not checked: vendor unknown")
        d = self.ctx["date"]
        serial = invoice_serial(f.get("invoice_number"))
        fy = financial_year(d) if d else None
        self.ctx.update(serial=serial, fy=fy)
        past = records.history(self.con, v["vendor_id"])
        if serial is None:
            # bills without a number (common in bill books): same supplier, same day, same amount is suspicious
            same_day = [h for h in past if d and h["invoice_date"] == d.isoformat() and abs(h["total"] - self.ctx["total"]) <= 1]
            checks = [dict(label="Not already processed", ok=not same_day,
                           detail="no bill number, so compared by supplier, date and amount" +
                                  (f": matches one saved {h['saved_at'][:10]}" if (h := (same_day or [None])[0]) else ""))]
            if same_day:
                self.flag("duplicate", "HOLD", "POSSIBLE_DUPLICATE",
                          f"This bill has no number, and a bill from {v['name']} for the same amount on the same date is already in records.",
                          audience="internal", action="Check with the person who made the purchase whether this was bought twice.",
                          short=f"Same supplier, date and amount ({inr(self.ctx['total'])}) as a bill already in records.")
                return "Possible duplicate", checks, {}
            return "Not in records yet", checks, {}
        same_no = [h for h in past if h["invoice_serial"] == serial and h["financial_year"] == fy]
        checks = [dict(label="Fingerprint", ok=None,
                       detail=f"vendor {v['vendor_id']} · FY {fy} · serial {serial} · total {inr(self.ctx['total'])}"
                              f"  (printed as '{f.get('invoice_number')}')"),
                  dict(label="Compared against", ok=None, detail=f"{len(past)} saved invoice(s) from this vendor")]
        exact = [h for h in same_no if abs(h["total"] - self.ctx["total"]) <= 1]
        if exact:
            h = exact[0]
            was = {"APPROVE": "passed for payment", "HOLD": "on hold", "REJECT": "rejected"}.get(h["decision"], h["decision"])
            checks.append(dict(label="Not already processed", ok=False,
                               detail=f"same as '{h['invoice_number']}', processed {h['processed_at'][:16].replace('T', ' ')} ({was})"))
            self.flag("duplicate", "REJECT", "DUPLICATE",
                      f"This is invoice '{h['invoice_number']}' again (same vendor, invoice serial {serial} in FY {fy}, "
                      f"same amount {inr(h['total'])}), already saved to records on {(h.get('saved_at') or h['processed_at'])[:10]} "
                      f"({was})." + (" Only the number format and date format differ."
                                      if h["invoice_number"] != f.get("invoice_number") else ""),
                      audience="vendor", action="Do not pay twice. Tell the vendor the original is already being processed.",
                      short=f"Duplicate of {h['invoice_number']}, already in records.")
            self.ctx["duplicate"] = True
            return "Duplicate of an earlier invoice", checks, dict(original_run=h["run_id"])
        if same_no:
            h = same_no[0]
            checks.append(dict(label="Not already processed", ok=False,
                               detail=f"number reused: '{h['invoice_number']}' was {inr(h['total'])}"))
            self.flag("duplicate", "HOLD", "NUMBER_REUSED",
                      f"Invoice number {serial} was already used by this vendor in FY {fy} for a different amount "
                      f"({inr(h['total'])}). GST rules require a unique number per financial year.",
                      audience="vendor", action="Ask the vendor whether this replaces the earlier invoice.",
                      short=f"Invoice number {h['invoice_number']} was already used for {inr(h['total'])}; this one is {inr(self.ctx['total'])}.")
            return "Invoice number reused", checks, {}
        if not v["gstin"] and d:
            # bill-book pages are numbered in order: a lower number dated after a higher one suggests a second bill book
            ahead = [h for h in past if h["invoice_serial"] is not None and h["invoice_serial"] > serial
                     and h["invoice_date"] and h["invoice_date"] < d.isoformat()]
            checks.append(dict(label="Bill number follows the earlier ones", ok=not ahead,
                               detail=(f"no. {serial} dated {d}, but no. {ahead[0]['invoice_serial']} was dated {ahead[0]['invoice_date']}"
                                       if ahead else "in order with the bills in records")))
            if ahead:
                h = max(ahead, key=lambda x: x["invoice_serial"])
                hd = date.fromisoformat(h["invoice_date"]).strftime("%d %b %Y").lstrip("0")
                self.flag("duplicate", "HOLD", "SERIAL_BACKWARDS",
                          f"This is bill no. {f.get('invoice_number')}, dated {d.strftime('%d %b %Y').lstrip('0')}, but {v['name']} already gave us "
                          f"bill no. {h['invoice_number']} on {hd}. Bill-book numbers only go up, so this bill may come from a second bill book.",
                          audience="internal",
                          action=f"Call {v['name']} on {v['phone']} (the number on file) and ask whether they issued this bill.",
                          short=f"Bill no. {f.get('invoice_number')} is lower than no. {h['invoice_number']}, which {v['name']} gave us earlier ({hd}).")
        po = self.ctx.get("po")
        near = [h for h in past if po and h["po_number"] == po["po_number"] and h["decision"] != "REJECT"
                and abs(h["total"] - self.ctx["total"]) <= 1 and d and h["invoice_date"]
                and abs((d - date.fromisoformat(h["invoice_date"])).days) <= 45]
        checks.append(dict(label="Not already processed", ok=not near,
                           detail="no earlier invoice with this number" if not near else
                           f"same PO and amount as '{near[0]['invoice_number']}'"))
        if near:
            self.flag("duplicate", "HOLD", "POSSIBLE_DUPLICATE",
                      f"Different invoice number, but same PO and same amount as '{near[0]['invoice_number']}' "
                      f"from {near[0]['invoice_date']}. Could be a resend under a new number.",
                      audience="internal", action="AP clerk to confirm these are two separate supplies.",
                      short=f"Same order and amount as {near[0]['invoice_number']}, under a new number: possibly sent twice.")
        return "Not in records yet" if not near else "Possible duplicate", checks, {}

    def s_match(self):
        f, po = self.ctx["f"], self.ctx.get("po")
        if not po:
            raise SkipStage("Not checked: no order to compare with")
        if self.ctx.get("duplicate"):
            raise SkipStage("Not checked: it's a duplicate")
        if po["status"] != "open":
            raise SkipStage(f"Not checked: {po['po_number']} is {po['status']}")
        tol_pct, tol_abs = self.policy["tolerance"]["percent"], self.policy["tolerance"]["absolute_inr"]
        billed = records.billed_on_po(self.con, po["po_number"])
        checks, to_bill, problems = [], [], []
        rate_issues, qty_issues, line_issues = [], [], {}   # line_issues: invoice line number -> what's wrong
        for i, li in enumerate(f.get("line_items") or []):
            best = max(po["lines"], key=lambda pl: similar(li["description"], pl["description"])
                       + (20 if str(li.get("hsn_sac")) == pl["hsn_sac"] else 0))
            sim = similar(li["description"], best["description"])
            if sim < 70 and str(li.get("hsn_sac")) != best["hsn_sac"]:
                checks.append(dict(label=f"'{li['description'][:40]}' is on the PO", ok=False, detail="no matching PO line"))
                self.flag("match", "HOLD", "LINE_NOT_ON_PO", f"'{li['description']}' is not on {po['po_number']}.",
                          audience="vendor", action="Ask the vendor which order this item belongs to.",
                          short=f"'{li['description']}' ({inr(to_amount(li.get('taxable_value')) or 0)}) isn't on {po['po_number']}.")
                line_issues[i] = "not on the order"
                continue
            qty, price = li.get("qty") or 0, li.get("unit_price") or 0
            value = to_amount(li.get("taxable_value")) or 0
            lump = ""
            # service invoices often print only an amount per line: fill in what's missing
            if value and not qty and not price:
                price, qty = best["unit_price"], value / best["unit_price"]
                lump = f"lump sum {inr(value)} = {qty:g} {best['unit']} at the PO rate; "
            elif value and qty and not price:
                price = value / qty
            elif value and price and not qty:
                qty = value / price
            prev_q = (billed.get(best["line_no"]) or {}).get("qty") or 0
            price_ok = abs(price - best["unit_price"]) <= best["unit_price"] * tol_pct / 100
            qty_ok = prev_q + qty <= best["qty"] + 1e-9
            name = best["description"] if len(best["description"]) <= 40 else best["description"][:39] + "…"
            checks.append(dict(label=f"Line {best['line_no']}: {name}",
                               ok=price_ok and qty_ok,
                               detail=f"{lump}rate {inr(price)} vs PO {inr(best['unit_price'])}; qty {prev_q:g} already billed + {qty:g} now = {prev_q + qty:g} of {best['qty']:g} {best['unit']}"))
            if not price_ok:
                problems.append(f"rate for '{best['description']}' is {inr(price)} vs {inr(best['unit_price'])} on the PO")
                rate_issues.append((best, price))
                line_issues[i] = f"order rate {inr(best['unit_price'])}"
            if not qty_ok:
                problems.append(f"quantity billed would reach {prev_q + qty:g} {best['unit']} against {best['qty']:g} ordered")
                qty_issues.append((best, qty, prev_q))
                line_issues[i] = (line_issues.get(i, "") + "; " if i in line_issues else "") + f"order is for {best['qty']:g}"
            to_bill.append(dict(po_number=po["po_number"], line_no=best["line_no"], qty=qty,
                                value=value or qty * price))
        prev_val = sum(b["value"] for b in billed.values())
        new_val = prev_val + self.ctx["taxable"]
        allowed = min(po["subtotal"] * tol_pct / 100, tol_abs)
        over = new_val - po["subtotal"]
        ok_val = over <= allowed
        checks.append(dict(label="Cumulative billing within PO value + tolerance", ok=ok_val,
                           detail=f"{inr(prev_val)} already billed + {inr(self.ctx['taxable'])} now = {inr(new_val)} "
                                  f"vs PO {inr(po['subtotal'])} (tolerance {inr(allowed)})"))
        if not ok_val:
            pct = over / po["subtotal"] * 100
            problems.insert(0, f"{inr(prev_val)} already billed on {po['po_number']} + {inr(self.ctx['taxable'])} on this invoice "
                               f"= {inr(new_val)}, which is {inr(over)} ({pct:.1f}%) over the {inr(po['subtotal'])} ordered; "
                               f"we allow up to {inr(allowed)}")
        if ok_val and over > 0.5:
            self.flag("match", "INFO", "WITHIN_TOLERANCE",
                      f"Billed {inr(over)} ({over / po['subtotal'] * 100:.1f}%) more than {po['po_number']}; "
                      f"within the {inr(allowed)} tolerance, so not worth stopping the payment.",
                      short=f"{inr(over)} over the PO, within tolerance")
        if problems:
            # lead with the cause a clerk can act on: a wrong rate, too many units, or simply the running total
            if rate_issues and not qty_issues:
                pl, price = rate_issues[0]
                short = (f"Charged {inr(price)} per unit for '{pl['description']}'; the order says {inr(pl['unit_price'])} "
                         f"({(price / pl['unit_price'] - 1) * 100:+.1f}%).")
            elif qty_issues and not rate_issues:
                pl, qty, prev_q = qty_issues[0]
                short = (f"{qty:g} {pl['unit']} of '{pl['description']}' billed" + (f" (plus {prev_q:g} already)" if prev_q else "")
                         + f"; the order is for {pl['qty']:g}.")
            elif not ok_val:
                short = f"Over-billed: {inr(new_val)} billed against a {inr(po['subtotal'])} PO ({over / po['subtotal'] * 100:+.1f}%)."
            else:
                short = "Invoice doesn't match the PO."
            only_total = not (rate_issues or qty_issues)
            self.flag("match", "HOLD", "OVER_PO" if not ok_val else "PO_MISMATCH",
                      "Doesn't match the PO: " + "; ".join(problems) + ".",
                      audience="vendor", action="Ask the vendor to correct the invoice, or get a PO amendment from the buyer.",
                      short=short,
                      vendor_msg=(f"Our order {po['po_number']} is for {inr(po['subtotal'])} before GST. {inr(prev_val)} has already been "
                                  f"invoiced against it, so {inr(max(po['subtotal'] - prev_val, 0))} remains, but this invoice is for "
                                  f"{inr(self.ctx['taxable'])}. Please revise it to the remaining amount, or let us know if more was "
                                  "supplied so we can amend the order." if only_total and not ok_val else
                                  "This invoice doesn't match our purchase order: " + "; ".join(problems) + "."))
        self.ctx["line_issues"] = line_issues
        self.ctx["to_bill"] = to_bill
        return ("Prices and quantities match the order" if not problems else f"Doesn't match {po['po_number']}", checks,
                dict(already_billed=prev_val, after_this=new_val, po_value=po["subtotal"]))

    def s_decide(self):
        limit = self.policy["approval_limit_inr"]
        total = self.ctx.get("total") or 0
        within = total <= limit
        checks = [dict(label=f"Within auto-approval limit ({inr(limit)})", ok=within, detail=inr(total))]
        if not within and not any(f["severity"] == "REJECT" for f in self.flags):
            self.flag("decide", "HOLD", "APPROVAL_LIMIT",
                      f"Everything checks out, but {inr(total)} is above the {inr(limit)} limit for automatic approval.",
                      audience="internal", action="Route to the finance manager for sign-off.",
                      short=f"Everything checks out, but {inr(total)} is over the {inr(limit)} limit.")
        sev = max([SEVERITY[f["severity"]] for f in self.flags], default=0)
        f, v = self.ctx.get("f") or {}, self.ctx.get("vendor")
        why = [w for w, yes in (("from a supplier without a GSTIN", self.ctx.get("unregistered")),
                                ("written by hand", f.get("written_by_hand")),
                                ("bought without a purchase order", v and not self.ctx.get("po"))) if yes]
        if sev <= 1 and why:
            # a bill like this is easy to make up, so it's never paid on the bill alone
            checks.append(dict(label="Goods received, confirmed by the buyer", ok=False, detail="waiting for the buyer"))
            self.flag("decide", "HOLD", "BUYER_CONFIRM",
                      f"Everything on the bill checks out, but it's {' and '.join(why)}, so it isn't paid on the bill alone: "
                      "the person who made the purchase confirms the goods arrived.",
                      action="The person who made the purchase presses Confirm received.",
                      short=f"Ready to pay {inr(total)} to {v['name']}, once the buyer confirms the goods arrived.")
            sev = 2
        decision = {3: "REJECT", 2: "HOLD"}.get(sev, "APPROVE")
        self.ctx["decision"] = decision
        return decision, checks, {}

    # -- orchestration ---------------------------------------------------
    def steps(self):
        plan = [("ingest", "Receive invoice", self.s_ingest),
                ("extract", "Read the invoice", self.s_extract),
                ("validate", "Check the document", self.s_validate),
                ("vendor", "Check the vendor", self.s_vendor),
                ("gst", "Check GST", self.s_gst),
                ("po", "Find the purchase order", self.s_po),
                ("duplicate", "Check for duplicates", self.s_duplicate),
                ("match", "Match against the PO", self.s_match),
                ("decide", "Decide", self.s_decide)]
        for key, name, fn in plan:
            try:
                yield self.stage(key, name, fn)
            except Stop:
                yield self.stages[-1]
                yield self.stage("decide", "Decide", self.s_decide)
                break

    def _pay_step(self, v, f):
        name = v["name"] if v else (f.get("vendor_name") or "the supplier")
        name = name.title() if name.isupper() else name
        if v and v.get("bank_account"):
            return "Schedule payment on the vendor's terms to the account on file."
        if v and v.get("upi"):
            return (f"Pay by UPI to {v['upi']} (on file). Check the app shows {name} as the payee, "
                    "and keep the payment receipt with this bill.")
        if f.get("vendor_upi"):
            return (f"Pay by UPI to {f['vendor_upi']} after confirming it with {name} by phone (it isn't on file yet). "
                    "Check the app shows their name as the payee, and keep the payment receipt with this bill.")
        if f.get("bank_account"):
            return "Pay to the bank account printed on the bill, after confirming it with the supplier by phone."
        phone = (v or {}).get("phone") or f.get("vendor_phone")
        call = f" (call {phone})" if phone else ""
        return (f"No bank or UPI details on file: ask {name} for a UPI ID{call}. Cash only up to "
                f"{inr(self.policy['cash_limit_inr'])} a day, against a signed receipt.")

    def result(self):
        decision = self.ctx.get("decision", "HOLD")
        f, v = self.ctx.get("f") or {}, self.ctx.get("vendor")
        # worst problem first: it is the headline, and its next step comes first
        blocking = sorted([x for x in self.flags if x["severity"] in ("HOLD", "REJECT")],
                          key=lambda x: -SEVERITY[x["severity"]])
        info = [x for x in self.flags if x["severity"] == "INFO"]
        payee = v["name"] if v else (f.get("vendor_name") or "the supplier")
        payee = payee.title() if payee.isupper() else payee
        pay_headline = f"Pay {inr(self.ctx.get('total') or 0)} to {payee}"
        pay_short = pay_headline
        if info:
            # notes read as one sentence: "(hand-written bill; not a tax invoice, ...)", keeping acronyms like PO intact
            notes = [x["short"].rstrip(".") for x in info]
            notes = [n[0].lower() + n[1:] if len(n) > 1 and n[1].islower() else n for n in notes]
            pay_headline += " (" + "; ".join(notes) + ")"
        headline = pay_headline if decision == "APPROVE" else blocking[0]["short"]
        from .emails import draft_emails
        res = dict(run_id=self.run_id, file=self.path.name, decision=decision, headline=headline,
                   reasons=[x["message"] for x in blocking] or ["All checks passed."],
                   notes=[x["message"] for x in info],
                   next_actions=list(dict.fromkeys(x["action"] for x in (
                       [y for y in blocking if y["code"] == "HANDWRITTEN_CHANGES"] or blocking + info) if x["action"])) or
                   [self._pay_step(v, f)],
                   vendor=v and {"id": v["vendor_id"], "name": v["name"]},
                   invoice=dict(number=f.get("invoice_number"), date=str(self.ctx.get("date")),
                                po=(self.ctx.get("po") or {}).get("po_number"),
                                po_inferred=self.ctx.get("po_inferred", False),
                                taxable=self.ctx.get("taxable"), tax=self.ctx.get("tax"), total=self.ctx.get("total")),
                   emails=draft_emails(self, decision), stages=self.stages, flags=self.flags,
                   seen_before=self.ctx.get("seen_before") or [], sha256=self.ctx.get("sha"),
                   line_issues=self.ctx.get("line_issues") or {},
                   confirmable=bool(blocking) and all(x["code"] in CONFIRMABLE for x in blocking),
                   pay_headline=pay_headline, pay_short=pay_short, pay_step=self._pay_step(v, f))
        return res

    def commit(self, res):
        v = self.ctx.get("vendor") or {}
        rec = dict(run_id=self.run_id, processed_at=datetime.now().isoformat(timespec="seconds"),
                   file=self.path.name, vendor_id=v.get("vendor_id"), vendor_name=v.get("name"),
                   invoice_number=res["invoice"]["number"], invoice_serial=self.ctx.get("serial"),
                   financial_year=self.ctx.get("fy"),
                   invoice_date=self.ctx["date"].isoformat() if self.ctx.get("date") else None,
                   po_number=res["invoice"]["po"], po_inferred=int(bool(res["invoice"]["po_inferred"])),
                   taxable_value=self.ctx.get("taxable"), total=self.ctx.get("total"),
                   decision=res["decision"], headline=res["headline"],
                   flags=",".join(x["code"] for x in self.flags),
                   result_json=json.dumps(res, default=str, ensure_ascii=False))
        f = self.ctx.get("f") or {}
        records.log_decision(self.con, dict(
            run_id=self.run_id, decided_at=rec["processed_at"], file_name=self.path.name,
            file_sha256=self.ctx.get("sha"), file_bytes=self.path.stat().st_size if self.path.exists() else None,
            vendor_name=v.get("name") or f.get("vendor_name"), vendor_gstin=f.get("vendor_gstin"),
            invoice_number=rec["invoice_number"], invoice_date=rec["invoice_date"], total=rec["total"],
            decision=res["decision"], headline=res["headline"], reasons=res["reasons"] + res["notes"],
            next_actions=res["next_actions"],
            checks=[dict(name=st["name"], status=st["status"], summary=st["summary"]) for st in self.stages],
            rules=self.policy))
        records.add_check(self.con, rec, self.ctx.get("to_bill", []), save=self.save)


class Stop(Exception):
    """Abort remaining stages (e.g. the file isn't a PDF)."""


class SkipStage(Exception):
    """This stage can't run because an earlier one failed."""


def process(path, on_stage=None, on_progress=None, save=False):
    """save=True also enters the invoice into records (used by the command line);
    the web app checks first and lets a person press 'Save to records'."""
    run = Run(path, on_progress, save)
    try:
        for st in run.steps():
            if on_stage:
                on_stage(st)
        res = run.result()
        run.commit(res)
        return res
    finally:
        run.con.close()  # don't leave the database file locked (matters on Windows)
