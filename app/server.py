"""The web app's server: uploads, the live check stream, history, the audit
trail and the rules page. Start it with `python start_app.py`."""
import contextlib
import hashlib
import json
import os
import queue
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from . import records
from .checks import process
from .read_invoice import reading_file

ROOT = records.ROOT
INVOICES = ROOT / "invoices"
UPLOADS = records.DATA / "uploads"
STATIC = Path(__file__).resolve().parent / "static"
ALLOWED = {".pdf", ".jpg", ".jpeg", ".png"}
KEEP_UNSAVED_HOURS = 24  # uploads never saved to records are deleted after this

# Plain-language names for the sample invoices, in demo order
SAMPLES = {  # *word* is shown highlighted
    "01": ("Apex Office Supplies", "Normal invoice, *same state*"),
    "02": ("Bluewave IT Services", "Normal invoice, *another state*"),
    "03": ("Sahyadri Packaging, part 1", "Split order, *first delivery*"),
    "04": ("Sahyadri Packaging, part 2", "Split order, *goes over the order*"),
    "05": ("Apex, sent again as a scan", "*Duplicate* of 01, rescanned"),
    "06": ("Coromandel housekeeping", "Scan with *no PO number*"),
    "07": ("Apex paper shredders", "Right total, *wrong GST*"),
    "08": ("Bluewave, new bank account", "*Bank account changed*"),
    "09": ("Deccan Electricals", "*Blocked* vendor"),
    "10": ("Konkan laptops", "*Over ₹5 lakh*"),
    "11": ("Apex, phone photo", "*Phone photo* of the invoice"),
    "12": ("Coromandel pest control", "*Changed by hand*"),
    "13": ("Konkan barcode scanners", "*₹290 over*, within tolerance"),
}

app = FastAPI(title="BillClerk")

# The launcher page on GitHub Pages checks whether this server is awake.
app.add_middleware(CORSMiddleware, allow_origins=["https://himank09.github.io"], allow_methods=["GET"])


@app.get("/api/ping")
def ping():
    return {"app": "billclerk", "ok": True}


# ---- deleting uploads ------------------------------------------------------
# An upload leaves three things behind: the file, its preview picture, and the
# AI's reading of it. Deleting an upload removes all three. The sample invoices
# are never deleted.
def _forget_file(name):
    p = UPLOADS / Path(name).name
    if not p.exists():
        return
    reading = reading_file(p)
    p.unlink()
    reading.unlink(missing_ok=True)
    (records.DATA / "previews" / (p.stem + ".jpg")).unlink(missing_ok=True)
    con = records.connect()
    try:
        records.log_file_deleted(con, p.name)  # the decision stays in the audit trail; it just notes the file is gone
    finally:
        con.close()


def _delete_upload(con, name):
    """Remove an upload and every unsaved check of it. Refuses if it's in records."""
    if con.execute("SELECT 1 FROM runs WHERE file=? AND saved=1", (name,)).fetchone():
        raise HTTPException(409, "This invoice is saved to records, so its file is kept. Press Undo first to delete it.")
    con.execute("DELETE FROM runs WHERE file=?", (name,))
    con.commit()
    _forget_file(name)


def _purge_old():
    """Delete uploads that were never saved to records and are older than the limit."""
    if not UPLOADS.exists():
        return
    cutoff = time.time() - KEEP_UNSAVED_HOURS * 3600
    con = records.connect()
    try:
        for f in UPLOADS.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                with contextlib.suppress(HTTPException):  # saved to records: keep it
                    _delete_upload(con, f.name)
    finally:
        con.close()


with contextlib.suppress(Exception):
    _purge_old()  # tidy up on every start


def _expected():
    return {t["file"]: t for t in json.loads((records.DATA / "correct_answers.json").read_text(encoding="utf-8"))}


def _find(name):
    name = Path(name).name  # no directory tricks
    for d in (INVOICES, UPLOADS):
        p = d / name
        if p.exists():
            return p
    raise HTTPException(404, f"No invoice called {name}")


@app.get("/", response_class=HTMLResponse)
def index():
    # no-store: always serve the latest page, never a cached old version
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@app.get("/api/samples")
def samples():
    out = []
    for t in sorted(_expected().values(), key=lambda t: t["run_order"]):
        title, blurb = SAMPLES.get(t["id"], (t["file"], t["scenario"]))
        out.append(dict(id=t["id"], file=t["file"], title=title, blurb=blurb,
                        expected=t["expected_decision"].split()[0], why=t["why"], kind=t.get("kind")))
    return out


# On the public site anyone can upload, and each new file costs a Gemini call.
# Keep it to a sensible number per visitor so nobody can use up the quota.
UPLOADS_PER_HOUR = int(os.environ.get("UPLOADS_PER_HOUR", 20))
_recent = {}


def _rate_ok(request):
    ip = (request.headers.get("x-forwarded-for") or request.client.host or "?").split(",")[0].strip()
    now = time.time()
    hits = [t for t in _recent.get(ip, []) if now - t < 3600]
    if len(hits) >= UPLOADS_PER_HOUR:
        return False
    _recent[ip] = hits + [now]
    return True


def _safe_name(stem):
    """A file name that is safe on disk but keeps letters in any script (Tamil, Hindi...)."""
    import unicodedata
    out = "".join(ch if unicodedata.category(ch)[0] in "LMN" or ch in "-." else "_" for ch in stem)
    return re.sub(r"_+", "_", out).strip("._")[:60] or "invoice"


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    if not _rate_ok(request):
        raise HTTPException(429, f"That's {UPLOADS_PER_HOUR} uploads in the last hour. Please try again a bit later, or use the examples.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Upload a PDF, JPG or PNG of the invoice.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "That file is empty (0 bytes). Download or export it again, then upload the new copy.")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "That file is over 10 MB. Upload a smaller scan or photo.")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    _purge_old()
    safe = _safe_name(Path(file.filename).stem)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}_{safe}{ext}"  # unique even within a second
    (UPLOADS / name).write_bytes(data)
    return {"file": name}


RUNNING, RUNNING_LOCK = {}, threading.Lock()   # file name -> pages waiting for its check


@app.get("/api/run/{name}")
def run_stream(name: str):
    """Server-sent events: 'progress' while reading, one 'stage' per check, then
    'result' (or 'failed'). The check runs on its own thread, so it finishes and is
    saved to history even if the browser's connection drops halfway."""
    path = _find(name)
    q = queue.Queue()
    # one check per file at a time: a reconnect, or a second click, joins the check already
    # running instead of starting another one (which would put the bill in history twice)
    with RUNNING_LOCK:
        start = name not in RUNNING
        RUNNING.setdefault(name, []).append(q)

    def send(item):
        with RUNNING_LOCK:
            listeners = list(RUNNING.get(name, []))
        for lq in listeners:
            lq.put(item)

    def work():
        try:
            res = process(path, on_stage=lambda st: send(("stage", st)),
                          on_progress=lambda m: send(("progress", {"message": m})))
            send(("result", res))
        except Exception as e:  # never leave the page hanging
            send(("failed", {"message": f"Something went wrong running this invoice: {e}"}))
        with RUNNING_LOCK:
            listeners = RUNNING.pop(name, [])
        for lq in listeners:
            lq.put(None)

    if start:
        threading.Thread(target=work, daemon=True).start()

    def events():
        yield "retry: 100000\n\n"
        while True:
            try:
                item = q.get(timeout=5)
            except queue.Empty:
                yield ": still working\n\n"  # a heartbeat every few seconds keeps the connection open during slow reads
                continue
            if item is None:
                break
            kind, payload = item
            yield f"event: {kind}\ndata: {json.dumps(payload, default=str, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs")
def runs():
    con = records.connect()
    rows = [dict(r) for r in con.execute(
        "SELECT run_id, processed_at, file, vendor_name, invoice_number, po_number, po_inferred, "
        "taxable_value, total, decision, headline, flags, saved, saved_at FROM runs ORDER BY processed_at DESC, rowid DESC")]
    con.close()
    return rows


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    con = records.connect()
    r = con.execute("SELECT result_json, saved, saved_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404, "No check with that ID")
    return JSONResponse(dict(json.loads(r["result_json"]), saved=bool(r["saved"]), saved_at=r["saved_at"]))


@app.post("/api/runs/{run_id}/save")
def save_run(run_id: str):
    _, policy, _, _ = records.load_master()
    con = records.connect()
    try:
        records.set_saved(con, run_id, True, policy)
    except records.SaveError as e:
        raise HTTPException(409, str(e)) from None
    finally:
        con.close()
    return {"saved": True}


async def _json(request):
    """The request's JSON body, or a clear 400 if it isn't valid JSON."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Couldn't read that request. Please try again.") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "Couldn't read that request. Please try again.")
    return body


@app.post("/api/runs/{run_id}/confirm")
async def confirm_run(run_id: str, request: Request):
    body = await _json(request)
    con = records.connect()
    try:
        res = records.confirm(con, run_id, body.get("name"), bool(body.get("called")))
        r = con.execute("SELECT saved, saved_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
    except records.SaveError as e:
        raise HTTPException(409, str(e)) from None
    finally:
        con.close()
    return JSONResponse(dict(res, saved=bool(r["saved"]), saved_at=r["saved_at"]))


@app.post("/api/vendors/{vendor_id}/phones")
async def add_vendor_phone(vendor_id: str, request: Request):
    """A clerk called the shop on the number we have and they confirmed a second number is theirs."""
    body = await _json(request)
    _, _, vendors, _ = records.load_master()
    if vendor_id not in {v["vendor_id"] for v in vendors}:
        raise HTTPException(404, "No supplier with that ID.")
    con = records.connect()
    try:
        records.add_phone(con, vendor_id, body.get("phone"), body.get("name"))
    except records.SaveError as e:
        raise HTTPException(409, str(e)) from None
    finally:
        con.close()
    return {"added": True}


@app.post("/api/runs/{run_id}/unsave")
def unsave_run(run_id: str):
    con = records.connect()
    try:
        records.set_saved(con, run_id, False)
    except records.SaveError as e:
        raise HTTPException(404, str(e)) from None
    finally:
        con.close()
    return {"saved": False}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    """Delete a check. For an upload, the file and everything made from it go too."""
    con = records.connect()
    try:
        r = con.execute("SELECT file, saved FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not r:
            raise HTTPException(404, "No check with that ID")
        if (UPLOADS / r["file"]).exists():
            _delete_upload(con, r["file"])
        else:  # a sample: only the history entry goes
            if r["saved"]:
                raise HTTPException(409, "This invoice is saved to records. Press Undo first to delete it.")
            con.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
            con.commit()
    finally:
        con.close()
    return {"deleted": True}


# ---- decision log ------------------------------------------------------------
def _log_row(r):
    d = dict(r)
    for k in ("reasons", "next_actions", "checks", "rules"):
        d[k] = json.loads(d[k] or "null")
    d["file_kept"] = bool((INVOICES / d["file_name"]).exists() or (UPLOADS / d["file_name"]).exists())
    return d


@app.get("/api/log")
def decision_log(q: str = "", sha: str = ""):
    """Every decision ever made, newest first. Clear history doesn't touch this.
    sha= gives every decision on one exact file (its timeline)."""
    con = records.connect()
    sql, args = "SELECT * FROM decision_log", ()
    if sha:
        sql, args = sql + " WHERE file_sha256=?", (sha,)
    elif q.strip():
        like = f"%{q.strip()}%"
        sql += (" WHERE invoice_number LIKE ? OR vendor_name LIKE ? OR file_name LIKE ? "
                "OR vendor_gstin LIKE ? OR file_sha256 LIKE ? OR run_id LIKE ?")
        args = (like, like, like, like, q.strip().lower() + "%", like)
    rows = [_log_row(r) for r in con.execute(sql + " ORDER BY decided_at DESC LIMIT 500", args)]
    con.close()
    return rows


@app.post("/api/log/match")
async def log_match(file: UploadFile = File(...)):
    """Someone disputes a decision and sends the file again: find every past decision
    on exactly this file. The file is only fingerprinted in memory, never stored."""
    data = await file.read()
    sha = hashlib.sha256(data).hexdigest()
    con = records.connect()
    rows = [_log_row(r) for r in con.execute(
        "SELECT * FROM decision_log WHERE file_sha256=? ORDER BY decided_at DESC", (sha,))]
    con.close()
    return dict(sha256=sha, matches=rows)


@app.get("/api/file/{name}")
def file(name: str):
    return FileResponse(_find(name))


@app.get("/api/preview/{name}")
def preview(name: str):
    """First page as a JPEG, so PDFs and photos show the same way in every browser."""
    src = _find(name)
    try:
        return _render_preview(src)
    except Exception:
        # the page shows its own "can't show this file" card when the preview fails
        raise HTTPException(415, "This file can't be previewed") from None


def _render_preview(src):
    # JPEG, not PNG: a fifth of the size, so the picture appears quickly even on the free server
    out = records.DATA / "previews" / (src.stem + ".jpg")
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        out.parent.mkdir(exist_ok=True)
        if src.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(src))
            img = pdf[0].render(scale=1.5).to_pil()
            pdf.close()
        else:
            from PIL import Image
            img = Image.open(src)
            img.thumbnail((1240, 1754))
        img.convert("RGB").save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/setup")
def setup():
    buyer, policy, vendors, pos = records.load_master()
    con = records.connect()
    po_rows = []
    for p in pos.values():
        billed = sum(b["value"] for b in records.billed_on_po(con, p["po_number"]).values())
        v = next(x for x in vendors if x["vendor_id"] == p["vendor_id"])
        po_rows.append(dict(po_number=p["po_number"], vendor=v["name"], description=p["description"],
                            status=p["status"], value=p["subtotal"], billed=billed, tax_type=p["tax_type"],
                            lines=[dict(description=l["description"], qty=l["qty"], unit=l["unit"],
                                        unit_price=l["unit_price"]) for l in p["lines"]]))
    con.close()
    vend = [dict(id=v["vendor_id"], name=v["name"], state=v["state"],
                 gstin=v["gstin"] or ("Applied for" if v.get("registration") == "applied" else "None (small supplier)"),
                 bank=f"{v['bank_name']} …{v['bank_account'][-4:]}" if v["bank_account"] else
                 f"UPI {v['upi']}" if v.get("upi") else "None on file", status=v["status"],
                 block_reason=v["block_reason"]) for v in vendors]
    return dict(buyer=buyer, policy=policy, vendors=vend, pos=po_rows,
                extractor="mock" if os.environ.get("EXTRACTOR") == "mock" else "gemini")


@app.post("/api/reset")
def reset():
    """Clear history and records, and delete every uploaded file with them."""
    records.reset()
    if UPLOADS.exists():
        for f in UPLOADS.iterdir():
            if f.is_file():
                _forget_file(f.name)
    return {"ok": True}
