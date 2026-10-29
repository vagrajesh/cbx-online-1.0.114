"""The server jobs Core offers the Builder (see actions.py). Each is a thin wrapper over the Core code
that already does the work — nothing is re-implemented here."""
from __future__ import annotations

from .actions import ActionError, Param, action

# ---- Site Schedules -------------------------------------------------------------------------------------
@action("swiftnet.read_quote", "Read a Swiftnet quotation", group="Site Schedules",
        description="Reads the site, quote number, province, the monthly and once-off fees and the antennas from a Swiftnet co-location quotation PDF. Anything it is unsure of becomes a line to check.",
        inputs=[Param("quote", "The quotation", "file", accept=("pdf",))],
        outputs=[Param("site", "Site name"), Param("quote_no", "Quote number"), Param("province", "Province"),
                 Param("accommodation", "Accommodation per month", "number"), Param("equipment", "Equipment accommodation"),
                 Param("antennas", "Antennas"), Param("electricity", "Electricity per month", "number"),
                 Param("access_road", "Access road", "number"), Param("once_off", "Once-off fee", "number"),
                 Param("total_monthly", "Total per month", "number"), Param("total_once_off", "Total once-off", "number"),
                 Param("checks", "What to check", "checklist")],
        tool="site-schedules")
def read_swiftnet_quote(job):
    from ..schedules import parser
    from ..warehouse.extract import ocr_pdf, pdf_text

    data = job.inputs["quote"].read_bytes()
    text, _pages = pdf_text(data)
    if not text.strip():
        text = ocr_pdf(data, max_pages=4)
    if not text.strip():
        raise ActionError("The quotation has no text I can read, even with OCR.")
    p = parser.parse(text)
    f = p["fields"]
    out = {k: (f.get(k) or {}).get("value") for k, _ in parser.FIELDS}
    out["site"], out["quote_no"] = p["site"] or out.get("site"), p["quote_no"] or out.get("quote_no")
    out["equipment"] = ((f.get("accommodation") or {}).get("quote") or "").split(" R")[0].strip() or "As per quotation"
    out["checks"] = [i["text"] for i in p["issues"]]
    return out


# 1.0.88: CBX-Connect 2's reading and Word agreement (cbx/schedules/agreement.py) — each comment its own line
def _ss_fields():
    from ..schedules import agreement as A

    return A


from ..schedules.agreement import FIELDS as _A_FIELDS  # noqa: E402 — the row's fields, same as CBX-Connect 2's form


@action("swiftnet.read_pdfs", "Read the Swiftnet PDFs (Site Schedule Agreement)", group="Site Schedules", tool="site-schedules",
        description="Reads a Swiftnet quotation, and the NTP or an earlier signed agreement if given, the way CBX-Connect 2 does: the site, the co-user, "
                    "the dates and every charge line (each numbered comment its own line). What it could not find becomes a line to check.",
        inputs=[Param("quote", "The quotation", "file", accept=("pdf",)), Param("more", "The NTP or an earlier agreement", "file", accept=("pdf",), required=False)],
        outputs=[Param(k, lab, "number" if kind == "int" else "text") for k, lab, kind in _A_FIELDS]
        + [Param("monthly", "Rental per month (6.1)", "number"), Param("charges", "The charge lines"), Param("read_from", "Read from"),
           Param("checks", "What to check", "checklist"), Param("details", "Everything read", "json")])
def read_swiftnet_pdfs(job):
    A = _ss_fields()
    files = [(job.names.get(k) or job.inputs[k].name, job.inputs[k].read_bytes()) for k in ("quote", "more") if job.inputs.get(k)]
    for name, data in files:
        if not data.startswith(b"%PDF"):
            raise ActionError(f"{name} is not a PDF.")
    try:
        return A.read(files)
    except ValueError as e:
        raise ActionError(str(e)) from e


@action("swiftnet.build_agreement", "Make the Site Schedule Agreement (Word)", group="Site Schedules", tool="site-schedules",
        description="Makes the Site Schedule Agreement in Word, laid out like the real one, from what was read and the row's fields as a person corrected them. "
                    "Signing people: one per line, 'Name, Title'.",
        inputs=[Param("details", "Everything read", "json")] + [Param(k, lab, "text", required=False) for k, lab, _ in _A_FIELDS]
        + [Param("swift_signers", "Signs for Swiftnet", "text", required=False), Param("couser_signers", "Signs for the co-user", "text", required=False)],
        outputs=[Param("agreement", "The agreement", "file")])
def build_swiftnet_agreement(job):
    A = _ss_fields()
    row = {k: job.inputs.get(k) for k, _, _ in _A_FIELDS}
    signers = {}
    for side, key in (("swift", "swift_signers"), ("couser", "couser_signers")):
        people = []
        for line in str(job.inputs.get(key) or "").splitlines():
            if line.strip():
                name, _, title = line.partition(",")
                people.append({"name": name.strip()[:120], "title": title.strip()[:160]})
        signers[side] = people[:6]
    try:
        name, data = A.build(job.inputs.get("details"), row, signers)
    except ValueError as e:
        raise ActionError(str(e)) from e
    return {"agreement": (name, data)}


# ---- IFRS 16 --------------------------------------------------------------------------------------------
@action("ifrs16.scan", "Read the IFRS 16 source files", group="IFRS 16", slow=True, tool="ifrs16",
        description="Reads the LA register and the RR06 / ARO06 movements (and the Sequentra export and step payments, if given) and lists the months the movements hold — the months a workflow can be run for.",
        inputs=[Param("la", "The LA register", "file", accept=("xlsx", "xls")), Param("movements", "The RR06 / ARO06 movements", "file", accept=("xlsx", "xls")),
                Param("sequentra", "The Sequentra export", "file", accept=("xlsx", "xls"), required=False), Param("steps", "The step payments report", "file", accept=("xlsx", "xls"), required=False)],
        outputs=[Param("months", "Months found"), Param("latest", "The newest month"), Param("summary", "What was read"), Param("movement", "RR06 or ARO06")])
def ifrs16_scan(job):
    from ..ifrs16 import scanning

    scan, err = scanning.scan_sources(job.inputs["la"], job.inputs["movements"], job.inputs.get("sequentra"), job.inputs.get("steps"))
    if err:
        raise ActionError(err)
    months = scan["months"]
    parts = [f"{scan['la_rows']} LA register rows", f"{scan['move_rows']} movement rows in {len(months)} month{'s' if len(months) != 1 else ''}"]
    if scan.get("sequentra_rows"):
        parts.append(f"{scan['sequentra_rows']} Sequentra rows")
    if scan.get("steps_rows"):
        parts.append(f"{scan['steps_rows']} step payments")
    return {"months": ", ".join(m["value"] for m in months), "latest": months[0]["value"] if months else "",
            "summary": " · ".join(parts), "movement": scan.get("movement_label") or ""}


@action("ifrs16.month", "Work out an IFRS 16 month", group="IFRS 16", slow=True, tool="ifrs16",
        description="Runs the IFRS 16 lease engine for one month from the kept source files — or with the lease facts from the Sequentra API — and writes the Lease Accelerator import workbooks (in one zip) and the monthly report, with the engine's reconciliation lines. SA leases take their discount rate from the tool's Rates table.",
        inputs=[Param("la", "The LA register", "file", accept=("xlsx", "xls")), Param("movements", "The RR06 / ARO06 movements", "file", accept=("xlsx", "xls")),
                Param("sequentra", "The Sequentra export", "file", accept=("xlsx", "xls"), required=False), Param("steps", "The step payments report", "file", accept=("xlsx", "xls"), required=False),
                Param("assets", "The LA BU asset report", "file", accept=("xlsx", "xls", "csv"), required=False, hint="fills the Asset IDs of terminations"),
                Param("ciw_master", "The CIW master", "file", accept=("xlsx", "xls"), required=False),
                Param("month", "The month", "month", hint="YYYY-MM, e.g. 2026-08"),
                Param("source", "Lease data from", "text", required=False, hint="'Sequentra API' or empty for the files"),
                Param("charges", "Use the API charge reports as step payments (test)", "text", required=False, hint="'yes' to switch on")],
        outputs=[Param("workbooks", "The import workbooks (zip)", "file"), Param("report", "The monthly report", "file"),
                 Param("papers", "The working papers (every tab of the run)", "file"),
                 Param("imports", "How many import workbooks", "number"), Param("recon", "The reconciliation")])
def ifrs16_month(job):
    import io
    import zipfile

    from ..ifrs16 import CODES_PATH, service, template_info
    from ..ifrs16 import api as SQ

    month = str(job.inputs.get("month") or "").strip()[:7]
    try:
        wm = service.month_date(month)
    except (ValueError, IndexError):
        raise ActionError(f"'{month}' is not a month — write it like 2026-08.")
    import lease_engine  # noqa: the engine module, unmodified

    out = job.tmp / "out"
    out.mkdir()
    s = lambda k: str(job.inputs[k]) if job.inputs.get(k) else None  # noqa: E731
    label = "ARO06" if "aro" in (job.names.get("movements") or s("movements") or "").lower() else "RR06"
    kind = "ARO" if label == "ARO06" else "SA"
    use_api = "api" in str(job.inputs.get("source") or "").lower() or "sequentra" in str(job.inputs.get("source") or "").lower()
    seq_df = steps_df = cfg = None
    notes: list[str] = []
    if use_api:
        try:
            cfg = SQ.config(job.db, use_charges=str(job.inputs.get("charges") or "").strip().lower() in ("yes", "y", "true", "1", "on"))
            seq_df, steps_df, notes = SQ.facts_and_steps(cfg, kind, wm)
        except SQ.ApiProblem as e:
            raise ActionError(f"{e} — or work the month out from the files instead.")
        if steps_df is None:
            notes.append(f"Step payments taken from the stored file '{job.names.get('steps')}'" if s("steps") else
                         "No step payments: add the step payments file until the API charge reports work - payment schedules are flagged, not drafted")
    rates = _ifrs16_rates(job)
    results = lease_engine.run_workflow_from_files(s("la"), s("movements"), s("sequentra"), workflow_month=wm, steps_path=s("steps"), codes_path=str(CODES_PATH),
                                                   rates_path=str(rates) if rates else None, assets_path=s("assets"), ciw_master_path=s("ciw_master"),
                                                   movement_label=label, steps_df=steps_df, sequentra_df=seq_df)
    if use_api:
        SQ.cross_check(results, seq_df, cfg, kind, wm, notes)
    written = lease_engine.write_import_workbooks(results, str(out))
    report = out / (service.REPORT_NAME % month)
    lease_engine.write_monthly_report(results, str(report))
    summary = template_info.run_summary(results, written)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.iterdir()):
            if p.is_file() and p != report:
                z.write(p, p.name)
    papers = out.parent / f"IFRS16_Working_papers_{month}.xlsx"
    _ifrs16_papers(results, papers)
    recon = "\n".join(f"{x.get('item')}: {x.get('value')}" for x in summary.get("recon") or [])
    if use_api:
        recon += "".join(f"\nAPI: {n}" for n in notes if f"API: {n}" not in recon)
    if not rates and kind == "SA":
        recon += "\nDiscount rates: none on the Rates page — SA rates came from Sequentra (flagged for review)."
    return {"workbooks": (f"IFRS16_Imports_{month}.zip", zbuf.getvalue()) if written else None,
            "report": (report.name, report.read_bytes()), "papers": (papers.name, papers.read_bytes()), "imports": len(written),
            "recon": recon or (f"{month} ran, but the engine found nothing to import for that month — only the report was written." if not written else "")}


def _ifrs16_papers(results: dict, path) -> None:
    """Every tab the run produced (Recon Summary, IBR Check, Routing, Exceptions, the drafts, the API Cross-Check …)
    in one workbook — what the desktop shows as its grid tabs."""
    import pandas as pd

    used: set[str] = set()
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in results.items():
            if not isinstance(df, pd.DataFrame):
                continue
            sheet = "".join(ch for ch in str(name) if ch not in "[]:*?/\\")[:31] or "Sheet"
            n = 2
            while sheet in used:
                sheet = f"{sheet[:28]} {n}"
                n += 1
            used.add(sheet)
            df.to_excel(xw, sheet_name=sheet, index=False)
        if not used:
            pd.DataFrame({"Note": ["The run produced no tabs."]}).to_excel(xw, sheet_name="Note", index=False)


def _ifrs16_rates(job):
    """The tool's Rates table (one row a month, bands A–F, as the client's treasury sends them) written the way
    the engine reads its rates workbook. None when the table is empty."""
    from ..engine import loader
    from ..engine.store import Store

    t = loader.registry().tools.get("ifrs16")
    if t is None or "rates" not in t.data:
        return None
    rows = Store("ifrs16").rows("rates", t.data["rates"]["columns"], limit=2000)["rows"]
    if not rows:
        return None
    import lease_engine  # noqa: the engine module, unmodified

    from ..ifrs16 import service

    path = job.tmp / "discount_rates.xlsx"
    for r in rows:
        try:
            m = service.month_date(str(r.get("month") or "")[:7])
        except (ValueError, IndexError):
            continue
        bands = {b.upper(): r.get(b) for b in "abcdef" if r.get(b) not in (None, "")}
        if bands:
            lease_engine.save_rate_month(str(path), m, bands)
    return path if path.exists() else None


@action("ifrs16.api_check", "Check the Sequentra API", group="IFRS 16", slow=True, tool="ifrs16",
        description="Tests the Sequentra connection (Settings → API connections) and calls each of its ten reports once: how many rows and fields came back, or why not. No lease data is kept.",
        inputs=[Param("month", "Month for the charge reports", "month", required=False, hint="YYYY-MM; empty = the month just ended")],
        outputs=[Param("result", "What each report returned"), Param("ok", "Connected (yes / no)")])
def ifrs16_api_check(job):
    from ..ifrs16 import api as SQ
    from ..ifrs16 import service

    m = str(job.inputs.get("month") or "").strip()[:7]
    try:
        month = service.month_date(m) if m else None
    except (ValueError, IndexError):
        raise ActionError(f"'{m}' is not a month — write it like 2026-08.")
    try:
        cfg = SQ.config(job.db)
    except SQ.ApiProblem as e:
        raise ActionError(str(e))
    lines = SQ.check(cfg, month)
    return {"result": "\n".join(lines)[:8000], "ok": "no" if lines and lines[0].startswith("Not connected") else "yes"}


@action("ifrs16.api_download", "Download every Sequentra report", group="IFRS 16", slow=True, tool="ifrs16",
        description="Every Sequentra report as it arrives, one CSV each, with a summary and a field list, zipped — to see what the reports hold before the engine reads them.",
        inputs=[Param("month", "From month", "month", hint="YYYY-MM"), Param("to_month", "To month", "month", required=False, hint="YYYY-MM; empty = the same month")],
        outputs=[Param("download", "The reports (zip)", "file"), Param("result", "What each report returned")])
def ifrs16_api_download(job):
    from ..ifrs16 import api as SQ
    from ..ifrs16 import service

    try:
        a = service.month_date(str(job.inputs.get("month") or "")[:7])
        b = service.month_date(str(job.inputs["to_month"])[:7]) if job.inputs.get("to_month") else None
    except (ValueError, IndexError):
        raise ActionError("Months look like 2026-08.")
    if b is not None and b < a:
        raise ActionError("The 'to' month is before the 'from' month.")
    try:
        lines, data = SQ.download(SQ.config(job.db), a, b, job.tmp / f"Sequentra_{a:%Y-%m}")
    except SQ.ApiProblem as e:
        raise ActionError(str(e))
    return {"download": (f"Sequentra_{a:%Y-%m}{'_to_' + format(b, '%Y-%m') if b else ''}.zip", data), "result": "\n".join(lines)[:8000]}


@action("ifrs16.ledger_check", "Check an IFRS 16 ledger", group="IFRS 16", slow=True, tool="ifrs16",
        description="Checks a ledger export against what the leases should show, as at a month and a number of months back, and writes the consolidated report.",
        inputs=[Param("ledger", "The ledger export", "file", accept=("xlsx", "xls")), Param("as_at", "As at (month)", "month", required=False, hint="YYYY-MM; empty = today"),
                Param("months_back", "Months back", "number", required=False)],
        outputs=[Param("report", "The consolidated report", "file"), Param("overview", "The overview")])
def ifrs16_ledger(job):
    from ..ifrs16 import service

    import ledger_check  # noqa: the engine module, unmodified

    try:
        as_at = service.parse_as_at(str(job.inputs.get("as_at") or ""))
    except ValueError:
        raise ActionError("The 'as at' month must look like 2026-08 (year-month).")
    try:
        back = int(float(job.inputs.get("months_back") or 12))
    except (TypeError, ValueError):
        back = 12
    if not 1 <= back <= 120:
        raise ActionError("Months back must be between 1 and 120.")
    res = ledger_check.run_ledger_check(str(job.inputs["ledger"]), as_at=as_at, months_back=back)
    path = job.tmp / service.LEDGER_REPORT_NAME
    ledger_check.write_consolidated_workbook(res, str(path))
    lines = []
    try:
        for _, row in res["Report Overview"].iterrows():
            lines.append(f"{row['Item']}: {row['Value']}")
    except Exception:  # noqa: BLE001
        pass
    return {"report": (path.name, path.read_bytes()), "overview": "\n".join(lines[:20])}


# ---- PDF Studio -----------------------------------------------------------------------------------------
def _templates(db, user):
    from ..models import Template
    from sqlalchemy import or_

    q = db.query(Template)
    if user is not None and not (user.is_admin or user.is_axl):
        conds = [Template.owner_id == user.id]
        if user.team_id:
            conds.append((Template.team_id == user.team_id) & (Template.shared.is_(True)))
        q = q.filter(or_(*conds))
    return [(t.id, t.name) for t in q.order_by(Template.name).all()]


def _template_outputs(db, inputs):
    from ..models import Template
    from ..pdfstudio import export as E

    try:
        t = db.get(Template, int(inputs.get("template") or 0))
    except (TypeError, ValueError):
        t = None
    if t is None:
        return []
    kind = {"money": "number", "number": "number", "date": "date"}
    return [Param(c["key"], c["label"], kind.get(c["ctype"], "text"), hint="money" if c["ctype"] == "money" else "") for c in E.columns_of(t)]


PDF_OUT = [Param("score", "How sure (%)", "number"), Param("to_check", "What to check", "checklist"), Param("batch", "PDF Studio batch", "number"),
           Param("excel", "The results (Excel)", "file"), Param("state", "Where it stands")]


def _read_back(db, b, t) -> dict:
    """A PDF Studio batch of one PDF as a row: the template's columns, how sure, what a person must look at."""
    from ..pdfstudio import export as E
    from ..pdfstudio import scoring as SC

    cols = E.columns_of(t)
    rows = E.rows_of(b, cols)
    if not rows:
        raise ActionError("PDF Studio kept no row for that PDF.")
    r = rows[0]
    out = {c["key"]: (r["cells"].get(c["key"]) or {}).get("value") for c in cols}
    out["to_check"] = [f"{c['label']}: {'not found' if (r['cells'].get(c['key']) or {}).get('value') is None else 'unsure — ' + str((r['cells'].get(c['key']) or {}).get('value'))}"
                       for c in cols if (r["cells"].get(c["key"]) or {}).get("status") in SC.FOR_USER]
    out["score"] = round(100 * r["pct"], 1) if r.get("pct") is not None else None
    out["batch"] = b.id
    out["state"] = {"review": "Waiting for a person in PDF Studio", "done": "Read", "failed": f"Failed: {b.error or ''}"}.get(b.status, b.status)
    out["excel"] = (f"{(b.name or 'PDF')[:60]}.xlsx", E.workbook_bytes(b, t))
    return out


@action("pdfstudio.read", "Read a PDF with PDF Studio", group="PDF Studio", slow=True, tool="pdf-studio",
        description="Hands the PDF to PDF Studio with one of its templates (the columns it reads, and the profile it has learnt). The template's columns come back into the row, with how sure it is and what a person must look at; the batch stays in PDF Studio, where answering its questions teaches the profile.",
        inputs=[Param("pdf", "The PDF", "file", accept=("pdf",)), Param("template", "The PDF Studio template", "choice", choices=_templates)],
        outputs=PDF_OUT, outputs_for=_template_outputs)
def pdfstudio_read(job):
    from ..config import settings
    from ..models import Batch, Document, Template
    from ..pdfstudio import pipeline
    from ..warehouse import storage
    from ..pdfstudio.spans import quick_text

    db, me = job.db, job.user
    try:
        tid = int(job.inputs.get("template") or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid not in {i for i, _ in _templates(db, me)}:
        raise ActionError("That PDF Studio template is not one this person may use.")
    t = db.get(Template, tid)
    data = job.inputs["pdf"].read_bytes()
    if not data.startswith(b"%PDF"):
        raise ActionError("That file is not a PDF.")
    name = job.names.get("pdf") or "document.pdf"
    b = Batch(template_id=t.id, user_id=me.id, team_id=me.team_id, name=f"{t.name} · {name}"[:200], status="learning", step="Received", progress=0.02, ceiling_zar=settings.model_batch_ceiling_zar)
    db.add(b)
    db.flush()
    text, pages = quick_text(data)
    f = storage.store(db, me, name, data, sub="Inbox", source="pdfstudio", text=text, pages=pages, meta={"batch_id": b.id, "original_name": name})
    f.pipeline_status, f.detected_as, f.goes_to = "added", "PDF Studio batch", b.name
    db.add(Document(batch_id=b.id, ordinal=1, file_id=f.id, filename=f.name, sha256=f.sha256, pages=pages, status="queued"))
    db.commit()
    pipeline.run_batch(b.id)  # this is already the background half of the rule: read it here, then carry on
    db.expire_all()
    b = db.get(Batch, b.id)
    if b.status == "failed":
        raise ActionError(f"PDF Studio could not read it: {b.error or 'no reason given'}")
    return _read_back(db, b, t)


@action("pdfstudio.fetch", "Fetch what PDF Studio now knows", group="PDF Studio", tool="pdf-studio",
        description="After someone answered PDF Studio's questions about a batch, brings the corrected values back into the row.",
        inputs=[Param("batch", "The PDF Studio batch", "number"), Param("template", "The PDF Studio template", "choice", choices=_templates)], outputs=PDF_OUT, outputs_for=_template_outputs)
def pdfstudio_fetch(job):
    from ..models import Batch, Template
    from ..security.rls import scope

    try:
        bid = int(float(job.inputs.get("batch") or 0))
    except (TypeError, ValueError):
        bid = 0
    b = scope(job.db.query(Batch), Batch, job.user).filter(Batch.id == bid).one_or_none()
    if b is None:
        raise ActionError("That PDF Studio batch is not here (or not yours).")
    return _read_back(job.db, b, job.db.get(Template, b.template_id))
