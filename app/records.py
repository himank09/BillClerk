"""Everything BillClerk remembers.

Master data (the buyer, vendors, purchase orders, policy) is read from files in
data/. What changes as invoices are checked lives in SQLite (data/billclerk.db):
  runs           every check, and whether it was saved to records
  po_billed      how much of each purchase order has been billed so far
  decision_log   the permanent audit trail, never cleared
`python check_invoices.py --reset` clears the first two and keeps the audit trail."""
import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = DATA / "billclerk.db"


def load_master():
    buyer = json.loads((DATA / "buyer.json").read_text(encoding="utf-8"))
    policy = json.loads((DATA / "rules.json").read_text(encoding="utf-8"))
    with open(DATA / "vendors.csv", encoding="utf-8") as f:
        vendors = list(csv.DictReader(f))
    with open(DATA / "purchase_orders.csv", encoding="utf-8") as f:
        pos = {r["po_number"]: r for r in csv.DictReader(f)}
    for p in pos.values():
        for k in ("subtotal", "tax_amount", "total"):
            p[k] = float(p[k])
        p["lines"] = []
    with open(DATA / "purchase_order_lines.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k in ("qty", "unit_price", "gst_rate", "line_value"):
                r[k] = float(r[k])
            r["line_no"] = int(r["line_no"])
            pos[r["po_number"]]["lines"].append(r)
    return buyer, policy, vendors, pos


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, processed_at TEXT, file TEXT, vendor_id TEXT, vendor_name TEXT,
  invoice_number TEXT, invoice_serial INTEGER, financial_year TEXT, invoice_date TEXT,
  po_number TEXT, po_inferred INTEGER, taxable_value REAL, total REAL,
  decision TEXT, headline TEXT, flags TEXT, result_json TEXT
);
CREATE TABLE IF NOT EXISTS po_billed (
  po_number TEXT, line_no INTEGER, qty REAL, value REAL, run_id TEXT
);
-- The decision log: one line per check, never edited away or deleted. It is
-- what we show if anyone later asks why an invoice was approved, held or
-- rejected. It keeps the reasons and a fingerprint of the file, not the file.
CREATE TABLE IF NOT EXISTS decision_log (
  run_id TEXT PRIMARY KEY, decided_at TEXT, file_name TEXT, file_sha256 TEXT, file_bytes INTEGER,
  vendor_name TEXT, vendor_gstin TEXT, invoice_number TEXT, invoice_date TEXT, total REAL,
  decision TEXT, headline TEXT, reasons TEXT, next_actions TEXT, checks TEXT, rules TEXT,
  saved_to_records INTEGER DEFAULT 0, saved_at TEXT, file_deleted_at TEXT
);
"""


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(runs)")}
    for col, typ in (("saved", "INTEGER DEFAULT 0"), ("saved_at", "TEXT"), ("bill_json", "TEXT")):
        if col not in cols:  # older databases: add the new columns
            con.execute(f"ALTER TABLE runs ADD COLUMN {col} {typ}")
    if "saved" not in cols:
        # before 'Save to records' existed every approval was billed automatically;
        # those checks now start as unsaved, so drop their billing too
        con.execute("DELETE FROM po_billed WHERE run_id NOT IN (SELECT run_id FROM runs WHERE saved=1)")
        con.commit()
    return con


def reset():
    """Forget every processed invoice. Clears the tables instead of deleting the
    file, because Windows won't delete a file another connection has open.
    The decision log is kept on purpose: it is the permanent audit trail."""
    con = connect()
    try:
        con.execute("DELETE FROM runs")
        con.execute("DELETE FROM po_billed")
        con.commit()
    finally:
        con.close()


def billed_on_po(con, po_number):
    """Approved quantity and value already billed per PO line."""
    rows = con.execute("SELECT line_no, SUM(qty) q, SUM(value) v FROM po_billed "
                       "WHERE po_number=? GROUP BY line_no", (po_number,)).fetchall()
    return {r["line_no"]: {"qty": r["q"], "value": r["v"]} for r in rows}


def history(con, vendor_id=None):
    """Invoices saved to records. Only these count when checking for duplicates."""
    q, args = "SELECT * FROM runs WHERE saved=1", ()
    if vendor_id:
        q, args = q + " AND vendor_id=?", (vendor_id,)
    return [dict(r) for r in con.execute(q + " ORDER BY saved_at", args)]


def add_check(con, rec, billed_lines, save=False):
    """Every check is kept in history. It only enters the records if saved."""
    rec = dict(rec, saved=0, saved_at=None, bill_json=json.dumps(billed_lines))
    cols = list(rec)
    con.execute(f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})", rec)
    con.commit()
    if save and rec["decision"] != "REJECT":
        set_saved(con, rec["run_id"], True)


def log_decision(con, e):
    """Bank accounts only ever reach the messages as their last 4 digits, so the
    log holds nothing that could be used to pay someone."""
    e = dict(e)
    for k in ("reasons", "next_actions", "checks", "rules"):
        e[k] = json.dumps(e[k], ensure_ascii=False, default=str)
    cols = list(e)
    con.execute(f"INSERT OR REPLACE INTO decision_log ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})", e)
    con.commit()


def earlier_decisions(con, sha, run_id=None):
    """Earlier checks of this exact file (same fingerprint), newest first."""
    rows = con.execute("SELECT run_id, decided_at, decision, headline, file_deleted_at FROM decision_log "
                       "WHERE file_sha256=? AND run_id IS NOT ? ORDER BY decided_at DESC", (sha, run_id)).fetchall()
    return [dict(r) for r in rows]


def log_file_deleted(con, file_name):
    con.execute("UPDATE decision_log SET file_deleted_at=datetime('now', 'localtime') "
                "WHERE file_name=? AND file_deleted_at IS NULL", (file_name,))
    con.commit()


class SaveError(Exception):
    pass


def set_saved(con, run_id, saved, policy=None):
    r = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not r:
        raise SaveError("No check with that ID.")
    if not saved:
        con.execute("UPDATE runs SET saved=0, saved_at=NULL WHERE run_id=?", (run_id,))
        con.execute("UPDATE decision_log SET saved_to_records=0, saved_at=NULL WHERE run_id=?", (run_id,))
        con.execute("DELETE FROM po_billed WHERE run_id=?", (run_id,))
        con.commit()
        return
    if r["saved"]:
        return
    if r["decision"] == "REJECT":
        raise SaveError("Rejected invoices aren't saved to records.")
    # records may have changed since this check ran: never save the same invoice twice
    if r["invoice_serial"] is not None:
        dup = con.execute("SELECT invoice_number, total FROM runs WHERE saved=1 AND vendor_id=? AND "
                          "financial_year=? AND invoice_serial=?",
                          (r["vendor_id"], r["financial_year"], r["invoice_serial"])).fetchone()
        if dup:
            raise SaveError(f"Invoice {dup['invoice_number']} from this vendor is already in records. "
                            "Check this one again to see whether it's a duplicate.")
    bill = json.loads(r["bill_json"] or "[]") if r["decision"] == "APPROVE" else []
    if bill and policy:
        _, _, _, pos = load_master()
        po = pos.get(bill[0]["po_number"])
        if po:
            already = sum(b["value"] for b in billed_on_po(con, po["po_number"]).values())
            allowed = min(po["subtotal"] * policy["tolerance"]["percent"] / 100, policy["tolerance"]["absolute_inr"])
            if already + sum(b["value"] for b in bill) - po["subtotal"] > allowed:
                raise SaveError(f"Since this check ran, other invoices were saved against {po['po_number']} and "
                                "this one would now take it over the order value. Check it again.")
    con.execute("UPDATE runs SET saved=1, saved_at=datetime('now', 'localtime') WHERE run_id=?", (run_id,))
    con.execute("UPDATE decision_log SET saved_to_records=1, saved_at=datetime('now', 'localtime') WHERE run_id=?", (run_id,))
    for b in bill:
        con.execute("INSERT INTO po_billed VALUES (?,?,?,?,?)",
                    (b["po_number"], b["line_no"], b["qty"], b["value"], run_id))
    con.commit()
