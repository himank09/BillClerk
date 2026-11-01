# BillClerk

BillClerk checks supplier invoices before a company pays them. You give it an invoice (a PDF, a scan or a phone photo), it reads it, runs the checks an experienced accounts clerk would, and tells you in one line whether to pay, hold or reject it, and why. It only asks a person when something needs judgment.

It was built for Zamp's PS-1 case study: *invoice PDF in, decision out*.

## How it decides

The AI reads, the rules decide.

Gemini only does the reading: it turns the invoice into fields like vendor, GSTIN, invoice number, line items and totals. Every decision after that comes from plain rules in code, so the same invoice always gets the same answer and every answer can be explained. The AI's reading is checked too: the arithmetic has to add up, the GSTIN has to pass its check digit, and the items have to match the purchase order.

Each invoice goes through nine steps:

1. **Receive.** Is it a PDF or a photo, and can it be opened?
2. **Read.** Gemini pulls out the fields. Digital PDFs send their text, scans and photos send the image. Anything that isn't an invoice (a CV, a report) is rejected here.
3. **Check the document.** Required details present, numbers add up, GSTIN valid, billed to us, no handwritten changes.
4. **Check the vendor.** Approved vendor, not blocked, and the bank account matches the one on file. Vendors are matched by GSTIN, or by phone number or name when there isn't one.
5. **Check GST.** Same state means CGST + SGST, different states means IGST, and the amount must match the rate.
6. **Find the purchase order.** Use the one quoted, or work it out from the vendor's open orders if none is quoted.
7. **Check for duplicates.** Compare with invoices already in the records, ignoring how the number or date is written.
8. **Match the order.** Prices, quantities, and the running total billed against the order so far.
9. **Decide.** Reject, hold or pay, with the reason, the next step and draft emails to the vendor or the team.

## What else it does

- **Nothing changes until you say so.** Checking an invoice records nothing. *Save to records* is the human sign-off: only saved invoices count when spotting duplicates or adding up what's been billed against an order. *Undo* takes it back.
- **Audit trail.** Every decision is kept permanently with its reasons, the rules in force at the time and a fingerprint of the file. If a vendor asks months later why an invoice was rejected, the answer is there, even if the file was deleted. *Find by file* matches a disputed file to its decision without storing it.
- **Uploads don't pile up.** An upload you haven't saved can be deleted with one click, and is deleted automatically after 24 hours anyway.
- **Small suppliers count too, with their own safeguards.** Not every supplier has a GSTIN or prints proper invoices. A thread shop's hand-filled bill book, a photo of a cash memo, or a shop whose GST registration is still being processed are all valid bills, but they're also the easiest to make up. So BillClerk reads them, finds the supplier by phone or name, and then:
  - **never pays them on the bill alone.** A bill that passes every check waits for the person who bought the goods to press *Confirm received*. Their name and the time go into the audit trail.
  - **adds up bills from the same shop on the same day**, so one ₹9,000 purchase split into two ₹4,500 bills is caught, and the ₹10,000 cash limit is applied per day as the law says.
  - **holds a shop that goes over ₹25,000 a month.** Buying that much regularly needs a proper supplier set-up.
  - **checks the bill-book number goes up** and **the phone number matches the one on file**. A fake bill usually copies the shop's name, not its number.
  - **says how to pay:** by UPI to the ID on file, checking the payee name and keeping the receipt.

  This follows how expense tools and petty-cash systems handle receipts: a person vouches for small purchases, limits apply per day and per month as well as per bill, and the payment leaves a record.
- **It survives a bad day at Google.** If Gemini is busy or slow, BillClerk retries, then tries another model. If nothing works, the invoice is put on hold for a person instead of guessed at. Each reading is saved, so checking the same file again is instant.

## Run it on your computer

You need Python 3.11 or newer and a Gemini API key.

```
python -m venv .venv
.venv\Scripts\activate            (on Mac or Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

Give it your key, either as an environment variable called `GEMINI_API_KEY` or in a file called `.env` in this folder:

```
GEMINI_API_KEY=your-key-here
```

Then start the app and open http://localhost:8000:

```
python start_app.py
```

New here? Click *Take the guided tour* on the first page. It walks through twelve of the invoices below, one problem at a time, in about three minutes.

`python start_app.py --mock` runs without a key, using the correct answers stored with the test data.

### From the command line

```
python check_invoices.py --all       # clear history, run all 13 invoices in order, print a score
python check_invoices.py invoices\01_apex_happy_intrastate.pdf
python check_invoices.py --reset     # clear history and records (the audit trail stays)
```

Add `--mock` to skip Gemini, `--fresh` to ignore saved readings, `-v` to see every check.

## Put it online

The repository includes `render.yaml`, so it deploys to [Render](https://render.com) as it is: *New → Blueprint*, pick this repository, and paste your Gemini key when asked. The key stays in Render's settings, never in the code.

On Render's free plan the app sleeps after about 15 minutes without visitors and takes up to a minute to wake. History and uploads start fresh after each restart; the 13 example invoices and their saved readings are part of the code, so they always work. Each visitor can upload 20 files an hour, so nobody can use up the Gemini quota.

To hide that wait, `docs/index.html` is a small launcher page served free by GitHub Pages, which never sleeps. It opens instantly, explains the wait, wakes the server and opens BillClerk the moment it's ready. Share the launcher's address rather than the app's.

## The test data

There's no public dataset that links real invoices to purchase orders and a vendor list (companies keep that private), so the data here is built to follow real Indian GST rules. Every company is fictional. The GSTINs have real state codes and pass the official check-digit test, but belong to no one.

**The buyer** is RJ Garments Pvt Ltd, a garment maker in Tiruppur, Tamil Nadu. It buys from eight vendors, two of them small local shops without a GSTIN:

| Vendor | Where | Supplies |
|---|---|---|
| Apex Office Supplies | Chennai | toner, chairs, shredders |
| Bluewave IT Services | Bengaluru | IT support, cloud backup |
| Sahyadri Industrial Packaging | Pune | stretch wrap film |
| Coromandel Facility Services | Chennai | housekeeping, pest control |
| Konkan Tech Hardware | Mumbai | laptops, barcode scanners |
| Deccan Electricals | Hyderabad | blocked after failing a KYC check |
| Selvi Thread & Needle Store | Tiruppur | thread, needles, elastic; no GSTIN, hand-written bill book, paid by UPI |
| Kumaran Tailoring Supplies | Tiruppur | tailoring supplies; GST registration applied for |

**The thirteen invoices** come in three different layouts, like real vendors' invoices would. 05, 06 and 12 are scans with no selectable text, and 11 is a phone photo.

| # | What's going on | Result |
|---|---|---|
| 01 | Normal invoice, same state, so CGST + SGST | Pay |
| 02 | Normal invoice from another state, so IGST | Pay |
| 03 | First 240 of 400 rolls ordered | Pay |
| 04 | Another 176 rolls. Fine alone, but with 03 it's ₹1,04,000 billed on a ₹1,00,000 order | Hold |
| 05 | Invoice 01 sent again as a scanned reminder, number written `AOS-2627-42` instead of `AOS/26-27/0042` | Reject |
| 06 | Scan with no order number; BillClerk works out which order it belongs to | Pay, with a note |
| 07 | Right total, but IGST charged on a same-state supply, so the tax credit would be lost | Hold |
| 08 | Asks to be paid into a new bank account, the most common sign of invoice fraud | Hold |
| 09 | Blocked vendor, cancelled order | Reject |
| 10 | Perfect, but ₹6,46,640 is over the ₹5 lakh limit, so a manager signs off | Hold |
| 11 | A tilted, shadowed phone photo of a correct invoice | Pay |
| 12 | Extra ₹4,500 line and a new total added by hand with a pen | Hold |
| 13 | ₹290 over the order (0.3%), inside the tolerance | Pay, with a note |

Order matters for two of them: 05 is only a duplicate once 01 is saved, and 04 only goes over the order once 03 is saved. `check_invoices.py --all` and the guided tour both run them in the right order.

**Want to try more?** `test_bills/` has 31 more invoices it has never seen, one problem each: an unknown vendor, an invalid GSTIN, a bill made out to another company, a wrong total, GST at the wrong rate, a future date, a made-up order number, a quotation pretending to be an invoice, hand-written bills from small shops, GST charged without a GSTIN, a big cash bill, one purchase split into two bills, a bill-book number that goes backwards, a fake phone number, and more. Its README lists what each one should get.

## Assumptions

These are written into `data/rules.json` and shown on the app's Rules page.

- **Small differences are fine:** up to 1% of the order or ₹500, whichever is less, measured on the running total billed against the order.
- **Anything over ₹5,00,000 needs a manager.** Real companies set this in their approval matrix; it's a setting, not a law.
- **Invoices are matched to the purchase order only.** Matching against goods received (a "3-way match") is left out.
- **Every item is taxed at 18%,** to keep the test data simple.
- **An invoice number can't repeat within a financial year,** as GST rules require. That's what makes duplicates detectable.
- **Small suppliers without a GSTIN:** a business with turnover under ₹40 lakh (goods) or ₹20 lakh (services) doesn't have to register for GST, and an unregistered business can't charge it (CGST Act s.32). Their bills are paid without GST credit. If one adds GST anyway, the bill is held.
- **Purchases up to ₹6,000 don't need a purchase order.** It's a company setting in `rules.json`, kept low because small suppliers are paid without the GST checks. Bills from one supplier on the same day count together. A first bill from a supplier not on the list is held for the buyer to confirm if it's under this; above it, it's rejected like any unknown vendor.
- **A supplier without a GSTIN can bill up to ₹25,000 a month.** Also a setting in `rules.json`. Above it they should be set up like any other vendor, with purchase orders and bank details.
- **Over ₹10,000 must go by bank or UPI.** Cash payments above ₹10,000 a day to one person can't be claimed as a business expense (Income Tax s.40A(3)), so bills like that with no bank or UPI details are held. Bills from the same supplier on the same day are added up for this.
- **An order is only worked out automatically when BillClerk is at least 85% sure** and the next best match is clearly behind. Otherwise a person picks.

## What's where

```
app/
  checks.py            the nine steps and every rule
  read_invoice.py      reads invoices with Gemini
  clean_values.py      tidies dates, amounts and invoice numbers so they can be compared
  gstin.py             GSTIN check-digit test
  emails.py            drafts emails to the vendor and the team
  records.py           records, history and the audit trail (SQLite)
  server.py            the web app's server
  static/index.html    the web page
docs/index.html        launcher page (GitHub Pages) that wakes the free server
data/
  buyer.json, vendors.csv, purchase_orders.csv, purchase_order_lines.csv
  rules.json           tolerance, approval limit and the other settings
  correct_answers.json the right reading and decision for each test invoice
  saved_readings/      Gemini's readings, so a file is only read once
invoices/              the 13 test invoices
test_bills/            31 more invoices to upload yourself, one problem each
scripts/               makes the test data, checks it, and pre-draws invoice previews
check_invoices.py      runs invoices from the command line
start_app.py           starts the web app
```

To rebuild the test data: `python scripts/make_company_data.py`, then `python scripts/make_invoices.py` (needs `numpy` and Poppler's `pdftoppm` for the scans), then `python scripts/check_test_data.py` to check it. Rebuilding with unchanged data gives byte-identical files, so their saved readings stay valid.
