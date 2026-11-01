"""Drafts the follow-up emails a clerk would otherwise write by hand.

Template-based on purpose: every sentence comes from a rule that fired, so the
email can never say something the checks didn't find. Nothing is sent; the
drafts wait for a human to review.
"""
from .clean_values import inr


def draft_emails(run, decision):
    f = run.ctx.get("f") or {}
    v = run.ctx.get("vendor")
    inv_no = f.get("invoice_number") or "your invoice"
    total = inr(run.ctx.get("total") or 0)
    emails = []

    vendor_items = [x for x in run.flags if x["audience"] == "vendor" and x["severity"] in ("HOLD", "REJECT")]
    # junk (not an invoice, unreadable file) needs no email: the next step says what to do
    junk = {"NOT_AN_INVOICE", "UNREADABLE_FILE", "NOT_A_PDF"}
    internal_items = [x for x in run.flags if x["audience"] == "internal" and x["severity"] in ("HOLD", "REJECT")
                      and x["code"] not in junk]
    reminders = [x for x in run.flags if x["code"] == "PO_INFERRED"]

    if any(x["code"] == "HANDWRITTEN_CHANGES" for x in vendor_items):
        # the other problems are side effects of the handwriting; one clear ask is better
        vendor_items = [x for x in vendor_items if x["code"] == "HANDWRITTEN_CHANGES"]
    if any(x["code"] == "DUPLICATE" for x in vendor_items):
        # a duplicate won't be paid whatever else is wrong with it: just say so
        vendor_items = [x for x in vendor_items if x["code"] == "DUPLICATE"]
    if v and vendor_items:
        codes = {x["code"] for x in vendor_items}
        if codes == {"DUPLICATE"}:
            subject = f"Invoice {inv_no}: already received"
            opening = (f"We received invoice {inv_no} for {total} again. It is the same invoice we have "
                       "already received and are processing, so there is no need to resend it, and it will "
                       "not be paid twice.")
            ask = "If this is actually a new invoice, please send it with its own invoice number."
        elif "VENDOR_BLOCKED" in codes:
            subject = f"Invoice {inv_no}: unable to process"
            opening = (f"We are unable to process invoice {inv_no} for {total} at the moment because your "
                       "vendor account with us is on hold.")
            ask = "Our procurement team will be in touch about re-verifying your account."
        else:
            subject = f"Invoice {inv_no}: correction needed before payment"
            opening = f"Thank you for invoice {inv_no} for {total}. We could not approve it for payment yet:"
            ask = "Please send a corrected invoice (or let us know if we have misunderstood), and we will process it straight away."
        bullets = "" if codes & {"DUPLICATE", "VENDOR_BLOCKED"} else "\n" + "\n".join(f"  • {x['vendor_msg']}" for x in vendor_items)
        body = (f"Dear {v['name']} team,\n\n{opening}{bullets}\n\n{ask}\n\n"
                f"Regards,\nAccounts Payable\n{run.buyer['name']}")
        emails.append(dict(audience="vendor", to=v["email"], subject=subject, body=body))

    if internal_items:
        lines = "\n".join(f"  • {x['message']}\n    → {x['action']}" for x in internal_items)
        who = v["name"] if v else (f.get("vendor_name") or "vendor not identified")
        if not f.get("invoice_number"):
            inv_no = run.path.name
        amount = f" for {total}" if run.ctx.get("total") else ""
        body = (f"Invoice {inv_no} from {who}{amount} is on {decision}.\n\n"
                f"What needs a person:\n{lines}\n\nRun ID: {run.run_id}")
        emails.append(dict(audience="internal", to=run.buyer.get("ap_email", "ap@example"),
                           subject=f"[{decision}] {who} · {inv_no}" + (f" · {total}" if amount else ""), body=body))

    if v and reminders and decision == "APPROVE":
        body = (f"Dear {v['name']} team,\n\nWe have processed invoice {inv_no}. It did not quote a purchase "
                f"order number, so we matched it to {run.ctx['po']['po_number']} ourselves. To avoid delays, "
                "please quote our PO number on future invoices.\n\n"
                f"Regards,\nAccounts Payable\n{run.buyer['name']}")
        emails.append(dict(audience="vendor", to=v["email"], subject=f"Invoice {inv_no}: please quote our PO number", body=body))
    return emails
