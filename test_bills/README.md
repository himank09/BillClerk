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

T07 is dated 15 January 2027, so it only counts as "in the future" until then. T08 is caught twice: it's over a year old, and it's dated before the order it quotes.

`answers.json` has exactly what each bill says, for comparing with what BillClerk read. To rebuild them: `python scripts/make_test_bills.py`.
