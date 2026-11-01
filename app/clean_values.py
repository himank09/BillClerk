"""Small helpers that turn messy printed values into clean comparable ones."""
import re
from datetime import date, datetime

DATE_FORMATS = ["%d-%b-%Y", "%d/%m/%Y", "%d %b %Y", "%d.%m.%Y", "%Y-%m-%d",
                "%d-%m-%Y", "%d %B %Y", "%d-%B-%Y", "%b %d %Y", "%B %d %Y", "%d/%m/%y", "%d-%m-%y"]


def parse_date(text):
    """Indian invoices are day-first, so 09/10/2026 is 9 October."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text).replace(",", " ").strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def financial_year(d: date):
    """Indian FY runs April to March: 14 Oct 2026 -> '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def to_amount(x):
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[^\d.\-]", "", str(x))
    return float(s) if s not in ("", ".", "-") else None


def _is_fy_pair(a: str, b: str):
    return len(a) in (2, 4) and len(b) == 2 and (int(a) + 1) % 100 == int(b) % 100


def invoice_serial(number: str):
    """Pull the running serial out of an invoice number, ignoring prefixes,
    separators, leading zeros and any financial-year chunk.
    'AOS/26-27/0042' -> 42, 'AOS-2627-42' -> 42, 'SIP/2627/0311' -> 311."""
    if not number:
        return None
    tokens = re.findall(r"\d+", number)
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if i + 1 < len(tokens) and _is_fy_pair(t, tokens[i + 1]):
            i += 2
            continue
        if len(t) == 4 and _is_fy_pair(t[:2], t[2:]):
            i += 1
            continue
        out.append(t)
        i += 1
    return int(out[-1]) if out else None


def clean_po(ref):
    if not ref:
        return None
    m = re.search(r"PO\W*(?:NO\.?|NUMBER|#)?\W*(\d{4})\W*(\d{3,5})", ref.upper())
    return f"PO-{m.group(1)}-{m.group(2)}" if m else ref.strip().upper()


def inr(x):
    """1,23,456.00 style formatting for messages."""
    neg, x = x < 0, abs(float(x))
    whole, frac = f"{x:.2f}".split(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head] + groups if head else groups) + "," + tail
    s = "₹" + whole + ("" if frac == "00" else "." + frac)
    return "-" + s if neg else s
