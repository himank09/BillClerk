# Test bills: try BillClerk yourself

Invoices BillClerk has never seen, one problem each. Upload them on the Check page like any real invoice. They aren't among the saved examples, so Gemini reads each one live (5 to 40 seconds).

**Before you start:** go to History and press *Clear history*, so earlier checks don't change the results.

**T17 and T18 come in pairs.** Upload the *a* file and press **Save to records**, then upload the *b* file. The *b* file is only a problem compared with the saved original.

| # | What's wrong | Should be | Why |
|---|---|---|---|
| T01 | A supplier we've never onboarded | **Reject** | Kaveri Textile Machinery isn't an approved vendor, so nothing can be paid until procurement onboards it. |
| T02 | Apex's GSTIN with a wrong last character | **Hold** | 33AAJCA3417L1ZX fails the GSTIN check digit (the real one ends in Q), so it's either a typo or not Apex. |
| T03 | Addressed to a different company | **Reject** | The invoice is made out to Sri Murugan Knits, not RJ Garments, so RJ Garments can't pay it or claim its GST. |
| T04 | Printed total is ₹1,000 too high | **Hold** | ₹29,000 + ₹5,220 GST = ₹34,220, but the invoice asks for ₹35,220. |
| T05 | GST charged at 12% on an 18% service | **Hold** | The line says 18%, so GST should be ₹3,240; only ₹2,160 is charged. The total adds up, so only the rate check catches it. |
| T06 | CGST and SGST not split equally | **Hold** | Same total, but CGST ₹3,132 and SGST ₹2,088. They must be equal halves (₹2,610 each). |
| T07 | Dated 15 January 2027 | **Hold** | An invoice can't be dated in the future; the date needs confirming before it's booked. |
| T08 | Dated August 2025 | **Hold** | Over a year old, so someone should find out why it's arriving now (and the order it quotes is from 2026). |
| T09 | Quotes PO-2026-1099, which doesn't exist | **Hold** | There's no such purchase order, so the vendor has to confirm the right one. |
| T10 | Apex billing against Konkan's laptop order | **Reject** | PO-2026-1009 was raised on Konkan Tech Hardware, not Apex. |
| T11 | Shredders at ₹15,900 instead of ₹14,500 | **Hold** | About 10% above the agreed price, far beyond the 1% / ₹500 tolerance. |
| T12 | An extra repair visit nobody ordered | **Hold** | The ₹7,500 repair visit isn't on PO-2026-1007, and it takes the bill over the order value. |
| T13 | 20 keyboard sets billed, 15 ordered | **Hold** | Five more than the order allows. |
| T14 | No order number, and it fits neither open order | **Hold** | Coromandel has two open orders (housekeeping, pest control); this matches neither, so a person must pick. |
| T15 | Wrong GST type and a new bank account | **Hold** | A Mumbai vendor billing Tamil Nadu must charge IGST, not CGST + SGST, and the bank account has changed. |
| T16 | A crooked, grainy scan of a correct bill | **Pay** | Nothing is wrong with it; it's here to check a poor scan is still read correctly. |
| T17a | Nothing: the original, to save first | **Pay** | A correct Apex invoice (same as example 01). Press Save to records, then try T17b. |
| T17b | Reuses invoice number 0042 for a different bill | **Hold** | Apex already billed AOS/26-27/0042 (T17a). GST rules need a new number for every invoice in a year. |
| T18a | Nothing: the original, to save first | **Pay** | A correct Bluewave invoice (same as example 02). Press Save to records, then try T18b. |
| T18b | October's IT bill sent again under a new number | **Hold** | Same order and amount as T18a, just renumbered, which is how double payments slip through. |
| T19 | A quotation, not an invoice | **Reject** | Nothing is owed on a quotation; it should be removed from the invoice queue. |
| T20 | A text file renamed to .pdf | **Reject** | It can't be opened as a PDF, so the sender is asked for a fresh copy. |
| T21 | A hand-filled bill-book bill from a small shop, photographed | **Hold** → **Pay** once confirmed | Everything checks out: Selvi has no GSTIN and charges none, and it's under ₹6,000 so no order is needed. A hand-written bill is easy to make up, so the buyer confirms the goods arrived; then it's passed for payment, by UPI. |
| T22 | A shop we've never bought from, small amount | **Hold** → **Pay** once confirmed | Not on the supplier list but under ₹6,000, so the person who bought it confirms rather than an outright reject. |
| T23 | GST added by hand, but the shop has no GSTIN | **Hold** | A business that isn't registered can't charge GST (CGST Act s.32). Pay ₹3,450, not ₹4,071, or get a proper tax invoice. |
| T24 | Shop's GST registration still in progress, no GST charged | **Hold** → **Pay** once confirmed | Correct: GST can't be charged until the GSTIN is issued. Once the buyer confirms it, it's passed; there's no bank or UPI on file, so the next step is to ask for a UPI ID. |
| T25 | ₹38,500 with no bank details and no order | **Hold** | Over ₹10,000 it can't be paid in cash (income-tax rules on cash payments) and there are no bank details. It's also over the ₹25,000 a month for a supplier without a GSTIN, and too big to buy without an order. |
| T26a | Nothing: a normal Selvi bill, to confirm and save first | **Hold** → **Pay** once confirmed | Press Confirm received, then Save to records. Then try T26b and T27. |
| T26b | A second bill from the same shop on the same day | **Hold** | ₹3,900 alone is fine, but with T26a (₹3,800) it's ₹7,700 on one day, over the ₹6,000 limit without an order. Splitting one purchase into small bills is the usual way round the limit. |
| T27 | Bill number lower than an earlier bill's | **Hold** | Bill no. 0432 is dated after bill no. 0470 (T26a). Bill-book numbers only go up, so this may be from a second bill book. |
| T28 | Selvi's name, but a completely different phone number | **Hold** | The bill says +91 90036 44218; Selvi's number is +91 98432 17760. A fake bill usually copies the name, not the number. If Selvi confirms by phone that it's their second number, add it to their file and check again. |
| T29 | Selvi's phone number with two digits swapped | **Hold** → **Pay** once confirmed | 71760 instead of 17760 is a writing slip, not a sign of fraud. It's noted, the buyer confirms as usual, and payment still goes only to Selvi's UPI ID on file. |
| T30a | Nothing: a March bill with a high number, to confirm and save first | **Hold** → **Pay** once confirmed | Press Confirm received, then Save to records. Then try T30b. |
| T30b | Bill no. 0004 in April, after no. 0985 in March | **Hold** → **Pay** once confirmed | Not a problem: shops start a new bill book in April, with the new financial year. Numbers are only compared within one year. |
| T31 | Bill no. 0118 from Selvi's second bill book | **Hold** → **Pay** once confirmed | 0118 is in a different book (pages 0101-0200) from T26a's 0470. Many shops use two books at once, so it's only noted, and the buyer confirms as usual. |
| T32 | ₹3,800 in figures, 'three thousand two hundred' in words | **Hold** | Figures are easy to change (a 2 into an 8); words aren't. Whichever is right, the shop has to confirm it. |
| T33 | Cash memo marked 'PAID - Cash, Received with thanks' | **Hold** → **Pay** once confirmed | Our staff already paid at the counter. Paying Selvi again would pay twice, so once confirmed, the step is to repay the staff member who paid. |
| T34 | A second bill book and a phone typo on the same bill | **Hold** → **Pay** once confirmed | Each would only be noted on its own. Two together are worth a phone call, so Confirm received also asks someone to call Selvi on the number we have. |

**T21 to T34 are from small local shops** of the kind every garment unit buys from: a thread shop with a hand-written bill book, a tailoring-supplies shop whose GST registration is still being processed. There's little on their bills BillClerk can prove, so it makes sure of three things (the goods arrived, the money goes only to the shop's UPI ID or account on file, nothing is paid twice or too much) and counts what looks off:

- **Nothing, or one small thing** (a phone typo, a second bill book): the buyer types their name and presses **Confirm received**, and it's passed.
- **Two small things** (T34): the buyer confirms, and also ticks that they called the shop on the number on file.
- **Three small things, or one serious one** (a different phone number, a bill number going backwards in the same book, words not matching figures): held.

**Some bills need another saved first**, because they're only a problem (or only fine) next to it:

- **T26a** is the original for T26b, T27, T31 and T34: upload it, press **Confirm received**, then **Save to records**.
- **T30a** is the original for T30b: the same steps.

**T28** has a completely different phone number. If you pretend you've called Selvi and they said it's theirs, type your name under "Is this their second number?" and the bill is checked again, this time as a normal small-shop bill.

T07 is dated 15 January 2027, so it only counts as "in the future" until then. T08 is caught twice: it's over a year old, and it's dated before the order it quotes.

`answers.json` has exactly what each bill says, for comparing with what BillClerk read. To rebuild them: `python scripts/make_test_bills.py`.
