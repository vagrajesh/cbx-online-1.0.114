"""The background job for a batch: Received → Read → Checking figures → Filling in → Ready.

    pipeline.submit_batch(batch_id)   # marks the id live, then jobs.submit(run_batch)

1. Read every PDF into positional spans (OCR where there is no text layer).
2. Group documents into layout families; pick up to 3 samples per family.
3. For each family find a profile whose match rules fit, or author one (heuristics, then C-EXA).
4. Run 1 on the samples. While accuracy < 99% and runs < 6: diagnose, patch (new profile version
   with a reason), rerun ALL documents. Each run is appended to batch.runs and a plain-English
   line to batch.log.
5. Fill the sheet: cells under 0.9 become misses for the user. Status review/done.
Model trouble (Ollama offline, budget exhausted) never fails a batch — the heuristics carry on.

Column kinds: `filename` columns take the PDF's own name (never a miss); `formula` columns are worked
out from other cells after extraction; an optional column (required=False) with nothing on the page
is `absent` — blank, counted as right, never handed to a person. Re-running a finished batch keeps
each document's earlier cells in Extraction.history and every cell a person filled or trusted.
"""
from __future__ import annotations

import json
import logging
import re
import traceback
from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log as audit
from ..cexa import agents
from ..cexa.gateway import BudgetExhausted, Gateway, ModelError
from ..config import settings
from ..db import session_scope, utcnow
from ..models import Batch, Column, Document, Extraction, Profile, ProfileVersion, StoredFile, Template, User
from ..warehouse import storage
from . import export as E
from . import extract as X
from . import lexicon as LX
from . import money as M
from . import profile as P
from . import repair as R
from . import scoring as SC
from .spans import read_document

log = logging.getLogger("cbx.pdfstudio")
_RUNNING: set[int] = set()  # batch ids with a live job in this process (a restart empties it)
MAX_RUNS = 6
SAMPLES_PER_FAMILY = 3
STEPS = ["Received", "Read", "Checking figures", "Filling in", "Ready"]


class _Ctx:
    def __init__(self, db: Session, batch: Batch):
        self.db = db
        self.batch = batch
        self.no_model = False
        self.gw = Gateway(db, batch_id=batch.id, ceiling_zar=batch.ceiling_zar, spent_zar=batch.spend_zar or 0.0)
        self.cexa = db.query(User).filter(User.is_axl.is_(True)).one_or_none()

    def say(self, text: str) -> None:
        entries = list(self.batch.log or [])
        entries.append({"at": utcnow().isoformat(), "text": text})
        self.batch.log = entries
        self.db.commit()

    def step(self, name: str, progress: float, agent=None, note: str = "") -> None:
        self.batch.step = name
        self.batch.progress = round(max(0.0, min(1.0, progress)), 3)
        if note:
            self.batch.step = f"{name} · {note}"
        if agent is not None:
            agent.progress(self.batch.progress, note or name)
        self.db.commit()

    def ask(self, task: str, user: str, schema: dict) -> Optional[dict]:
        """One model call, never fatal."""
        if self.no_model:
            return None
        system = P.AUTHOR_SYSTEM if task == "profile_author" else P.REPAIR_SYSTEM
        try:
            out = self.gw.json(task=task, system=system, user=user, schema=schema)
            self.batch.spend_zar = round(self.gw.spent, 4)
            return out
        except BudgetExhausted as e:
            self.no_model = True
            self.batch.spend_zar = round(self.gw.spent, 4)
            self.say(f"Model budget reached (R {self.gw.spent:.2f} of R {self.batch.ceiling_zar:.0f}). Finishing on rules alone. ({e})")
        except ModelError as e:
            self.no_model = True
            self.say(f"Ollama offline — running on rules only. ({str(e)[:120]})")
        except Exception as e:  # noqa: BLE001
            self.no_model = True
            self.say(f"Model call failed; carrying on with the rules. ({str(e)[:120]})")
        return None


def is_running(batch_id: int) -> bool:
    """Is a job for this batch alive in this process? A batch left in learning/running with no job
    (the server restarted mid-run) is stalled and may be started again."""
    return batch_id in _RUNNING


def submit_batch(batch_id: int) -> None:
    """Queue the job. The id is marked live before the thread starts so a poll in between doesn't
    report the batch as stalled."""
    _RUNNING.add(batch_id)
    try:
        jobs.submit(run_batch, batch_id)
    except Exception:
        _RUNNING.discard(batch_id)
        raise


def run_batch(batch_id: int) -> None:
    _RUNNING.add(batch_id)
    try:
        with session_scope() as db:
            batch = db.get(Batch, batch_id)
            if batch is None:
                return
            ctx = _Ctx(db, batch)
            task = f"Batch #{batch.id} · {batch.name or 'PDFs'}"
            try:
                with agents.working("harvester", task=task, ref=f"batch:{batch.id}") as agent:
                    _run(ctx, agent)
            except Exception as e:  # noqa: BLE001
                log.error("batch %s failed:\n%s", batch_id, traceback.format_exc())
                db.rollback()
                batch = db.get(Batch, batch_id)
                ctx.batch = batch
                batch.status = "failed"
                batch.error = str(e)[:500]
                batch.step = "Failed"
                ctx.say(f"I hit an error I couldn't recover from: {str(e)[:200]}")
                audit(db, "pdf_batch_failed", actor=ctx.cexa, actor_label="axl→agent:harvester", target=f"batch:{batch.id}", detail=str(e)[:200], outcome="failed")
                db.commit()
    finally:
        _RUNNING.discard(batch_id)


def _run(ctx: _Ctx, agent) -> None:
    db, batch = ctx.db, ctx.batch
    template = db.get(Template, batch.template_id)
    columns = E.columns_of(template)
    batch.status = "learning"
    ctx.step(STEPS[0], 0.03, agent)
    docs_db = list(batch.documents)
    if not docs_db:
        raise RuntimeError("There are no PDFs in this batch.")
    rerun = any(d.extraction is not None for d in docs_db)
    kinds = E.column_kinds(columns)
    kinds_txt = f"{kinds['total']} columns" + (f" · {kinds['optional']} optional" if kinds["optional"] else "") + (f" · {kinds['calculated']} calculated" if kinds["calculated"] else "") + (f" · {kinds['filename']} file name" if kinds["filename"] else "")
    n_pdf = f"{len(docs_db)} PDF{'s' if len(docs_db) != 1 else ''}"
    if rerun:
        ctx.say(f"Re-reading {n_pdf} for '{template.name}' with the current profile ({kinds_txt}). Earlier values are kept in each row's history; cells people filled stay.")
    else:
        ctx.say(f"Received {n_pdf} for '{template.name}' ({kinds_txt}).")
    # a template that gained columns since its profiles were built: author only those, keep the rest
    for v in author_missing_for_template(db, template):
        ctx.say(f"{v.profile.label}: {v.reason} → v{v.number}.")
    db.commit()

    # ---- 1. read ------------------------------------------------------------------------------
    docs: list[dict] = []
    scanned_n = 0
    ocr_missing_n = 0
    for i, d in enumerate(docs_db):
        f = db.get(StoredFile, d.file_id)
        try:
            data = storage.read_bytes(f)
        except (AttributeError, OSError) as e:  # row or file gone from the warehouse
            raise RuntimeError(f"The file for PDF #{d.ordinal} ({d.filename}) is missing from the warehouse. Start a new batch and upload it again.") from e
        spans = read_document(data, d.sha256)
        d.pages = spans["pages"]
        d.scanned = bool(spans.get("scanned"))
        d.status = "read"
        scanned_n += 1 if d.scanned else 0
        ocr_missing_n += 1 if spans.get("ocr_missing") else 0
        # a re-run keeps what people filled or trusted; everything else is read afresh
        kept = {k: c for k, c in ((d.extraction.cells if d.extraction else {}) or {}).items() if c.get("status") in SC.USER_OWNED}
        docs.append({"id": d.id, "ordinal": d.ordinal, "name": d.filename, "filename": original_name(f, d), "spans": spans, "cells": kept, "db": d})
        ctx.step(STEPS[1], 0.05 + 0.30 * (i + 1) / len(docs_db), agent, f"{i+1} of {len(docs_db)} read")
    ctx.say(f"Read all {len(docs)} PDFs" + (f" — {scanned_n} scanned (no text layer), read with OCR." if scanned_n and not ocr_missing_n else "."))
    if ocr_missing_n:
        ctx.say(f"{ocr_missing_n} scanned PDF{'s' if ocr_missing_n != 1 else ''} could not be read: OCR (Tesseract) is not installed on the server. Their cells will come to you. An admin can install Tesseract and set CBX_TESSERACT_CMD — see the README.")

    # ---- 1b. bills a CBX Harvester profile knows (1.0.75): read by its rules, never learned ----------
    from . import municipal as MU

    docs_all = docs
    by_doc = {x.id: x for x in docs_db}
    known = MU.claim(db, template, columns, docs, lambda d: storage.read_bytes(db.get(StoredFile, by_doc[d["id"]].file_id)), say=ctx.say)
    docs = [d for d in docs if d["id"] not in known]
    fams: list[dict] = []
    runs: list[dict] = []
    n = 0
    db.commit()
    if docs:
        # ---- 2. families → profiles ---------------------------------------------------------------
        fams = P.cluster([(d["id"], d["spans"]) for d in docs])
        by_id = {d["id"]: d for d in docs}
        for fam in fams:
            fam["docs"] = [by_id[i] for i in fam["doc_ids"]]
            # honest accuracy: the profile is built and repaired on `train` only. `holdout` is never
            # authored on, never repaired on, and is what the profile's accuracy is measured on.
            fam["train"], fam["holdout"] = R.split(fam["docs"])
            fam["samples"] = fam["train"][:SAMPLES_PER_FAMILY]
            for s in fam["samples"]:
                s["db"].is_sample = True
        train_docs = [d for f in fams for d in f["train"]]
        n_hold = sum(len(f["holdout"]) for f in fams)
        ctx.step(STEPS[2], 0.38, agent, "building profiles")
        summary = ", ".join(f"{_pretty(f['family'])} ×{len(f['docs'])}" for f in fams)
        ctx.say(f"{len(fams)} layout{'s' if len(fams) != 1 else ''}: {summary}. Reading {sum(len(f['samples']) for f in fams)} sample bills first.")
        if n_hold:
            ctx.say(f"Holding {n_hold} of {len(docs)} back. I build and fix the profile on the other {len(train_docs)} and only then read the {n_hold} I kept aside — that score is the one I report, because a profile marked on the bills it was built from marks its own homework.")
        else:
            ctx.say(f"{len(docs)} document{'s' if len(docs) != 1 else ''} is too few to keep any back, so I can't tell you how this profile does on bills it hasn't seen. I'll say 'too few to check' rather than quote a number that only proves I remember these.")
        for fam in fams:
            prof, ver, how = _profile_for(ctx, template, columns, fam, fams)
            fam["profile"], fam["version"], fam["rs"] = prof, ver, dict(ver.ruleset)
            for d in fam["docs"]:
                d["db"].profile_id = prof.id
            ctx.say(how)
        db.commit()

        # ---- 3. run / repair loop -----------------------------------------------------------------
        runs: list[dict] = []
        n = 0
        all_docs_run = False
        while True:
            n += 1
            scope_docs = [s for f in fams for s in f["samples"]] if n == 1 else train_docs
            all_docs_run = scope_docs is train_docs  # the whole training set, not yet the held-out set
            done_so_far = 0
            for fam in fams:
                fam_scope = [d for d in scope_docs if d in fam["docs"]]
                base = done_so_far

                def _prog(i, total, base=base):
                    frac = (base + i) / max(1, len(scope_docs))
                    ctx.step(STEPS[2], 0.4 + 0.45 * (n - 1) / MAX_RUNS + 0.45 / MAX_RUNS * frac, agent, f"run {n} · {base + i + 1} of {len(scope_docs)}")

                R.run_docs(fam["rs"], columns, fam_scope, on_progress=_prog)
                done_so_far += len(fam_scope)
            acc, by_col = R.summarise(scope_docs, columns)
            note = "samples" if n == 1 else f"training set of {len(train_docs)}"
            runs.append({"n": n, "accuracy": acc, "by_column": by_col, "note": note, "docs": len(scope_docs), "at": utcnow().isoformat()})
            batch.runs = list(runs)
            batch.accuracy, batch.by_column = acc, by_col
            for d in scope_docs:
                d["db"].score = d["row"]
            ctx.step(STEPS[2], 0.4 + 0.45 * n / MAX_RUNS, agent, f"run {n}: {acc:.1%}")
            low = [d for d in scope_docs if d["row"] < SC.CELL_OK]
            weak_cols = [c for c in columns if c.get("required", True) and by_col.get(c["key"], 0) < SC.CELL_OK]
            line = f"Run {n} ({note}) → {acc:.1%}."
            if weak_cols:
                line += " Below 90%: " + ", ".join(f"{c['label']} {by_col[c['key']]:.0%}" for c in weak_cols) + "."
            for d in low[:3]:
                cells = d["cells"]
                bad = [c for c in columns if c.get("required", True) and SC._cell_score(cells.get(c["key"])) < SC.CELL_OK]
                where = "; ".join(f"{c['label']}: {(cells.get(c['key']) or {}).get('note') or 'not found'}" + (f" (page {(cells.get(c['key']) or {}).get('page')})" if (cells.get(c["key"]) or {}).get("page") else "") for c in bad[:3])
                line += f" PDF #{d['ordinal']} scored {d['k']} of {d['n']} ({d['row']:.0%})" + (" — scanned." if d["spans"].get("scanned") else ".") + (f" Opened it: {where}." if where else "")
            ctx.say(line)

            if acc >= SC.GATE_BATCH and all_docs_run:
                ctx.say(f"Above the 99% target after {n} run{'s' if n != 1 else ''}. Filling in the sheet.")
                break
            if n >= MAX_RUNS:
                ctx.say(f"Stopping after {MAX_RUNS} runs at {acc:.1%}. Cells I'm not sure about go to you.")
                break
            if acc >= SC.GATE_BATCH and not all_docs_run:
                ctx.say(f"Samples passed the 90% gate ({acc:.1%}). Running the rest of the training set ({len(train_docs) - len(scope_docs)} more).")
                continue
            # repair, per profile
            patched_any = False
            for fam in fams:
                fam_docs = [d for d in scope_docs if d in fam["docs"]]
                diag = R.diagnose([c for c in columns if c.get("required", True) or n <= 2], fam_docs)
                if not diag:
                    continue
                new_rs, reason, changes = R.patch(fam["rs"], columns, fam_docs, diag, ask_model=ctx.ask, family=fam["family"])
                if new_rs is None:
                    ctx.say(f"{_pretty(fam['family'])}: " + " ".join(r for dg in diag for r in dg["reasons"][:2]) + " I have no better rule for this yet — it will go to you.")
                    continue
                patched_any = True
                fam["rs"] = new_rs
                fam["version"] = _new_version(db, fam["profile"], new_rs, reason=reason, notes_extra="\n".join("- " + c for c in changes), acc_before=acc)
                ctx.say(f"{_pretty(fam['family'])}: {reason} → profile v{fam['version'].number}. Rerunning the {len(train_docs)} I build on.")
            if not patched_any and all_docs_run:
                ctx.say("Nothing more I can fix on my own. Leaving the rest to you.")
                break
            if not patched_any and not all_docs_run:
                ctx.say(f"Running the rest of the training set ({len(train_docs) - len(scope_docs)} more) with what I have.")
        # everything not yet read — the held-out documents above all — is read once with the final rules
        for fam in fams:
            pending = [d for d in fam["docs"] if "row" not in d]
            if pending:
                R.run_docs(fam["rs"], columns, pending)
        if n_hold:
            ctx.say(f"Now reading the {n_hold} I kept aside, with the rules as they stand. Nothing about them changes the profile.")

    # ---- 4. fill in ----------------------------------------------------------------------------
    ctx.step(STEPS[3], 0.9, agent)
    now_iso = utcnow().isoformat()
    MU.save(db, [d for d in docs_all if d["id"] in known], columns, now_iso)
    docs = docs_all
    acc, by_col = R.summarise(docs, columns)
    batch.accuracy, batch.by_column = acc, by_col
    for fam in fams:
        new_docs = 0
        for d in fam["docs"]:
            dd: Document = d["db"]
            cells = d["cells"]
            for c in columns:
                cell = cells.get(c["key"]) or {}
                if cell.get("status") == "miss":
                    if cell.get("value") is not None and not cell.get("note"):
                        cell["note"] = "not sure about this one"
                    if d["spans"].get("scanned") and "scanned" not in (cell.get("note") or ""):
                        cell["note"] = (cell.get("note") or "") + ("; " if cell.get("note") else "") + "scanned page, digits unclear"
            ext = dd.extraction
            if ext is None:
                ext = Extraction(document_id=dd.id)
                db.add(ext)
                new_docs += 1
            elif ext.cells:
                # a re-run: the earlier read stays as history (last 5), like Harvest rows
                hist = list(ext.history or [])
                hist.append({"at": now_iso, "profile_version_id": ext.profile_version_id, "row_score": ext.row_score, "cells": ext.cells})
                ext.history = hist[-5:]
            ext.cells = cells
            ext.row_score = d["row"]
            ext.profile_version_id = fam["version"].id
            ext.validators = {k: v for k, v in (d.get("validators") or {}).items() if k != "failing"} | {"failing": (d.get("validators") or {}).get("failing", [])}
            dd.score = d["row"]
            dd.status = "ok" if all((cells.get(c["key"]) or {}).get("status") not in SC.FOR_USER for c in columns) else "review"
        # profile bookkeeping — the number kept on the profile is the HELD-OUT one
        prof: Profile = fam["profile"]
        bt = R.measure(columns, fam["train"], fam["holdout"])
        fam["backtest"] = bt
        fam_acc = R.honest(bt)  # None when there were too few documents to hold any back
        prof.docs_seen = (prof.docs_seen or 0) + new_docs
        prof.accuracy = round(fam_acc, 4) if fam_acc is not None else None
        fam["version"].backtest = bt
        prof.live_version_id = fam["version"].id if fam["version"].author == "axl" else prof.live_version_id
        if prof.status != "patch":
            prof.status = "live" if (fam_acc is None or fam_acc >= SC.GATE_PROFILE) else "below_gate"
        remembered = _remember_labels(db, columns, fam)
        ctx.say(f"{_pretty(fam['family'])}: {bt['note']}." + (f" {remembered} label{'s' if remembered != 1 else ''} from this layout added to what I know, so the next one starts warmer." if remembered else ""))
    counts = SC.cell_counts((d["cells"] for d in docs), columns)
    total, filled, misses, absent = counts["total"], counts["filled"], counts["for_user"], counts["absent"]
    batch.cells_total, batch.cells_filled, batch.cells_for_user = total, filled, misses
    # the gates read the held-out figures; a profile with too few documents to hold any back is not
    # gated on a number it cannot honestly produce — it is reported as "too few to check" instead
    profile_acc = {fam["profile"].key: R.honest(fam["backtest"]) for fam in fams}
    profile_acc = {k: v for k, v in profile_acc.items() if v is not None}
    hold_docs = [d for f in fams for d in f["holdout"]]
    gate_acc, gate_by_col = (R.summarise(hold_docs, columns) if hold_docs else (acc, by_col))
    g = SC.gates(gate_acc, gate_by_col, columns, profile_acc)
    if hold_docs:
        # the run that counts: the rules as they stand, on documents they were never fitted to
        runs.append({"n": len(runs) + 1, "accuracy": gate_acc, "by_column": gate_by_col, "docs": len(hold_docs), "at": utcnow().isoformat(), "note": f"held out — {len(hold_docs)} PDF{'s' if len(hold_docs) != 1 else ''} the profile was not built from"})
    batch.runs = list(runs)
    crit_fail = [c["label"] for c in columns if c.get("critical") and not g["critical"].get(c["key"], True)]
    if crit_fail:
        ctx.say("Money columns under the 99.5% gate: " + ", ".join(crit_fail) + ". Those cells are marked for you rather than guessed.")
    batch.status = "review" if misses else "done"
    batch.finished_at = utcnow()
    batch.error = ""
    batch.spend_zar = round(ctx.gw.spent, 4)
    ctx.step(STEPS[4], 1.0, agent)
    ctx.say(f"Done. {filled} of {total} cells filled at {acc:.1%}." + (f" {absent} blank where an optional column isn't on the document." if absent else "") + (f" {misses} cell{'s' if misses != 1 else ''} need you." if misses else " Nothing for you to check.") + (f" On the {len(hold_docs)} I had never read: {gate_acc:.1%} — that is the honest one." if hold_docs else ""))
    audit(db, "pdf_batch_finished", actor=ctx.cexa, actor_label="axl→agent:harvester", target=f"batch:{batch.id}", detail=f"{len(docs)} docs, {n} runs, {acc:.1%}, {misses} misses", outcome="ok")
    db.commit()


# ---- the lexicon: what this layout taught C-EXA about labels -------------------------------------
LABEL_CONFIRM_SHARE = 0.8  # a label is confirmed when it read the column on this share of documents
# Meanings whose value must MOVE between one bill of a layout and the next. A rule that returns the
# same date, reading or amount on every bill is reading something fixed on the page (a deposit date,
# a heading) and its label must not be taught to the next layout — the design's variance test,
# applied where it costs nothing.
VARYING_GROUPS = {"date", "due_date", "period", "reading_prev", "reading_curr", "consumption", "total", "excl", "vat", "opening_balance", "demand", "invoice"}


def _remember_labels(db: Session, columns: list[dict], fam: dict) -> int:
    """A rule that actually produced a good value on this layout's documents has earned its label a
    place in the lexicon, and the next layout gets to try it first. A *leading* label rule on a
    column that failed anyway is marked as having failed, so a phrase that doesn't work stops being
    offered. Never raises: `lexicon` swallows its own trouble and a batch must not fail over memory."""
    docs = fam.get("docs") or []
    if not docs:
        return 0
    layout = fam.get("family") or ""
    fields = (fam.get("rs") or {}).get("fields") or {}
    blobs = _page_text(docs)
    n = 0
    for c in columns:
        if c.get("ctype") in SC.NO_RULE_TYPES:
            continue
        rules = [r for r in (fields.get(c["key"]) or {}).get("rules", []) if LX.placement_of(r.get("type", "")) and r.get("label")]
        if not rules:
            continue
        group = LX.group_for(c)
        used: Counter = Counter()
        example = ""
        good = 0
        heading_value = False
        values: set = set()
        for d in docs:
            cell = (d.get("cells") or {}).get(c["key"]) or {}
            if cell.get("status") == "ok" and (cell.get("score") or 0) >= SC.CELL_OK and cell.get("value") is not None:
                good += 1
                used[str(cell.get("method"))] += 1
                values.add(str(cell.get("value")))
                example = example or str(cell.get("value"))[:160]
                heading_value = heading_value or (group not in CONSTANT_TEXT_GROUPS and _is_heading_text(cell.get("value"), blobs))
        if heading_value:
            # the "value" is printed text of the layout itself (meter_number = "METER TYPE"). The
            # rule is consistent, not right — teaching its label would spread the mistake.
            continue
        if len(docs) >= 3 and len(values) == 1 and group in VARYING_GROUPS:
            continue  # the same amount or date on every bill: the rule found something fixed
        if c.get("ctype") == "text" and values and all(M.parse_date(v) for v in values):
            continue  # a text column reading a clean date has found the wrong field, consistently
        # a label only earns a place in the vocabulary if its column works on documents the profile
        # was not built from — the same bar the profile itself is held to
        if (fam.get("backtest") or {}).get("by_column", {}).get(c["key"], 1.0) < SC.GATE_PROFILE:
            continue
        floor = max(1, round(LABEL_CONFIRM_SHARE * len(docs)))
        leading = str(((fields.get(c["key"]) or {}).get("rules") or [{}])[0].get("id"))
        for r in rules:
            rid = str(r.get("id"))
            hits = used.get(rid, 0)
            placement = LX.placement_of(r.get("type", ""))
            if hits >= floor:
                if LX.label_conflicts_with(str(r["label"]), group):
                    continue  # "Consumer Name" is not where the consumption is
                LX.confirm(db, column_key=c["key"], label=str(r["label"]), placement=placement, layout=layout, example=example, ctype=c.get("ctype", ""))
                n += 1
            elif hits == 0 and rid == leading and len(docs) >= 2 and good < len(docs) / 2:
                # the rule that leads this column read nothing, and the column failed: the label is
                # wrong here, not merely unused as a fallback
                LX.reject(db, column_key=c["key"], label=str(r["label"]), placement=placement, layout=layout, ctype=c.get("ctype", ""))
    return n


# groups where a value printed on every document of the layout is the right answer, not furniture
CONSTANT_TEXT_GROUPS = {"issuer"}


def _page_text(docs: list[dict]) -> list[str]:
    """One normalised text blob per document. `|` separates lines, so a phrase can never be matched
    across two of them."""
    out = []
    for d in docs:
        lines = [" ".join(X.norm_tokens(l["text"])) for l in (d.get("spans") or {}).get("lines", [])]
        out.append(" | ".join(t for t in lines if t))
    return out


def _is_heading_text(value, blobs: list[str], share: float = 0.8) -> bool:
    """Is this "value" actually a piece of the printed layout? A run of words with no digits that is
    printed on nearly every document of the layout is a heading, not an answer — this is what turns
    `meter_number = "METER TYPE"` from a confident 100% into a label C-EXA refuses to learn."""
    toks = X.norm_tokens(str(value))
    if not toks or not blobs or any(any(ch.isdigit() for ch in t) for t in toks):
        return False
    needle = f" {' '.join(toks)} "
    hits = sum(1 for b in blobs if needle in f" {b} ")
    return hits >= max(2, round(share * len(blobs)))


# ---- profiles ------------------------------------------------------------------------------------
def _pretty(family: str) -> str:
    return family.replace("-", " ").title()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "template"


def _profile_for(ctx: _Ctx, template: Template, columns: list[dict], fam: dict, fams: list[dict]) -> tuple[Profile, ProfileVersion, str]:
    """Profiles are kept per template ("a column per municipality profile, kept separate"):
    1. a profile of THIS template whose match rules fit every sample is reused; columns it does not
       know yet (or whose hint changed) are authored one by one into a new version — the other fields
       are copied unchanged;
    2. otherwise a new profile is drafted. If a shared profile of ANOTHER template fits the same layout
       (Swiftnet reading the same PDFs as Telkom), its field rules seed ours for columns with the same
       key or synonym group;
    3. the template's OTHER profiles (a different vendor's layout under the same template — Megaglo
       with 2 invoices next to Capitec with 1 269) lend every field rule that yields a typed value on
       the new samples, best profile first. A borrowed rule leads the column (`seeded_from` on the
       field) when it reads more samples than the heuristics manage on their own; otherwise it rides
       along as a fallback — so a text rule that merely matches a label word never displaces a
       rule that reads the value;
    4. the rest is authored from the samples by the heuristics, then offered to C-EXA to improve."""
    db = ctx.db
    samples = [s["spans"] for s in fam["samples"]]
    seed: Optional[tuple[Profile, ProfileVersion]] = None
    own = db.query(Profile).filter(Profile.template_id == template.id).all()
    cands = own + db.query(Profile).filter(Profile.template_id != template.id, Profile.shared.is_(True)).all()
    for prof in cands:
        ver = _working(prof)
        if ver is None or not all(P.matches(ver.ruleset or {}, sp) for sp in samples):
            continue
        if prof.template_id == template.id:
            new_ver = author_missing_columns(db, template, prof, samples=samples, family=fam["family"], say=ctx.say)
            ver = new_ver or ver
            return prof, ver, f"{_pretty(fam['family'])}: matches profile '{prof.label}' (v{ver.number}, {prof.accuracy:.1%} so far)." if prof.accuracy else f"{_pretty(fam['family'])}: matches profile '{prof.label}' (v{ver.number})."
        if seed is None or (prof.accuracy or 0) > (seed[0].accuracy or 0):
            seed = (prof, ver)
    # author a new one, seeded where another template already reads this layout
    seeded: dict[str, str] = {}
    seed_fields: dict[str, dict] = {}
    if seed is not None:
        seed_prof, seed_ver = seed
        seed_tpl = db.get(Template, seed_prof.template_id) if seed_prof.template_id else None
        seed_cols = [{"key": c.key, "label": c.label, "ctype": c.ctype} for c in seed_tpl.columns] if seed_tpl else []
        seed_fields, seeded = P.seed_fields(seed_ver.ruleset or {}, seed_cols, columns)
    # ... and borrowed from this template's other layouts where their rules work on these samples
    siblings = []
    for sp in sorted(own, key=lambda p: -(p.accuracy or 0)):
        sv = _working(sp)
        if sv is not None and sv.ruleset:
            siblings.append((sp.label, sv.ruleset))
    sib_fields, _tried = P.sibling_rules(siblings, [c for c in columns if c["key"] not in seed_fields], samples)
    rest = [c for c in columns if c["key"] not in seed_fields]
    rs, notes = P.author_heuristic(rest or columns, samples, fam["family"], db=db)
    # borrowed rules lead where they read more samples than our own; otherwise they are fallbacks
    borrowed = P.merge_sibling_fields(rs, sib_fields) if sib_fields else {}
    if seed_fields or sib_fields:
        rs["fields"] = {c["key"]: (seed_fields.get(c["key"]) or rs["fields"].get(c["key"]) or {"rules": []}) for c in P.readable(columns)}
        for f in rs["fields"].values():
            f["rules"] = [r for r in f.get("rules", []) if r.get("type") != "derived"]
        P._add_derived(rs, columns, samples)
    if seed_fields:
        rs["seeded_from"] = {"profile_id": seed[0].id, "profile": seed[0].label, "version": seed[1].number, "columns": seeded}
    rs["match"] = P.match_rules_for(fam, fams)
    if not rs["match"].get("verified"):
        # An unproven match can claim another layout's documents. Say so and keep the profile a
        # draft: a person decides, rather than the wrong ruleset being applied silently.
        ctx.say(f"I could not find a way to recognise this layout that does not also match another one ({rs['match'].get('why') or 'not discriminative'}). I have kept it as a draft.")
        rs["needs_review"] = True
    heur_notes = "How I read this layout:\n" + "\n".join("- " + n for n in notes)
    if sib_fields:
        by_prof: dict[str, list[str]] = {}
        for k, lab in borrowed.items():
            by_prof.setdefault(lab, []).append(k)
        fallback_only = [k for k in sib_fields if k not in borrowed]
        heur_notes = ("Rules tried from this template's other layouts on these samples: " + ("; ".join(f"'{lab}' leads for {', '.join(ks)}" for lab, ks in by_prof.items()) if by_prof else "none read these samples better than my own") + (f"; kept as fallbacks for {', '.join(fallback_only)}" if fallback_only else "") + ".\n\n") + heur_notes
    if seeded:
        heur_notes = f"Seeded from profile '{seed[0].label}' (v{seed[1].number}) — same layout, built for '{seed_tpl.name if seed_tpl else 'another template'}': " + ", ".join(f"{k} ← {v}" for k, v in seeded.items()) + ".\n\n" + heur_notes
    model_notes = ""
    added = 0
    out = ctx.ask("profile_author", _author_prompt(columns, samples, rs), P.AUTHOR_SCHEMA)
    if out:
        added = P.merge_model_rules(rs, out.get("rules") or {}, columns, samples=samples)
        model_notes = str(out.get("notes") or "").strip()
    _remember_hints(rs, columns)
    # a figure seen on some but not most samples is not on every document: propose it as optional
    optional_notes = apply_optional_proposals(db, template, columns, rs, len(samples), say=ctx.say)
    if optional_notes:
        heur_notes += "\n\nOptional by evidence:\n" + "\n".join("- " + n for n in optional_notes)
    key = f"{_slug(template.name)}.{fam['family']}"
    label = f"{template.name} · {_pretty(fam['family'])}"
    prof = db.query(Profile).filter(Profile.key == key).one_or_none()
    if prof is not None and prof.template_id not in (None, template.id):
        # another template with the same name already owns that key: profiles stay per template
        key = f"{_slug(template.name)}-{template.id}.{fam['family']}"
        label = f"{template.name} #{template.id} · {_pretty(fam['family'])}"
        prof = db.query(Profile).filter(Profile.key == key).one_or_none()
    if prof is None:
        prof = Profile(key=key, label=label, template_id=template.id, team_id=ctx.batch.team_id, status="draft", created_by="axl")
        db.add(prof)
        db.flush()
    notes = (model_notes + "\n\n" if model_notes else "") + heur_notes
    reason = f"Drafted from {len(samples)} sample{'s' if len(samples) != 1 else ''} ({_pretty(fam['family'])})" + (f"; seeded from '{seed[0].label}' ({len(seeded)} column{'s' if len(seeded) != 1 else ''})" if seeded else "") + (f"; {len(borrowed)} rule set{'s' if len(borrowed) != 1 else ''} borrowed from this template's other profiles" if borrowed else "")
    ver = _new_version(db, prof, rs, reason=reason, notes_extra=notes, first=True)
    n_rules = sum(len(f.get("rules", [])) for f in rs["fields"].values())
    # summarise from the heuristic notes only ("Label: where 'x' — works on ..."); the model's free
    # text has no fixed shape and used to crash this line
    brief = "; ".join(n.split(":")[0] + ": " + n.split(":", 1)[1].split(" — ")[0].strip() for n in notes[:3] if ":" in n)
    how = f"{_pretty(fam['family'])}: built profile v1 — {len(rs['fields'])} fields, {n_rules} rules" + (f", {len(seeded)} reused from '{seed[0].label}'" if seeded else "") + (f", {len(borrowed)} borrowed from {', '.join(sorted(set(borrowed.values())))}" if borrowed else "") + (f", {added} from C-EXA" if added else "") + (". " + brief if brief else "") + "."
    return prof, ver, how


def original_name(f: Optional[StoredFile], d: Document) -> str:
    """The name the PDF arrived with (the warehouse may have renamed a duplicate to 'x (2).pdf')."""
    meta = (f.meta if f is not None else None) or {}
    name = meta.get("original_name") or ""
    if not name and meta.get("origin"):
        name = str(meta["origin"]).replace("\\", "/").rstrip("/").split("/")[-1]
    return name or d.filename


def apply_optional_proposals(db: Session, template: Template, columns: list[dict], rs: dict, n_samples: int, say=None, only_keys: Optional[set] = None) -> list[str]:
    """Make the columns `profile.propose_optional` names optional on the template (and in the working
    column dicts), record why on the column, and say so. Returns the note lines."""
    props = P.propose_optional(rs, columns, n_samples, only_keys=only_keys)
    if not props:
        return []
    by_key = {c["key"]: c for c in columns}
    notes = []
    for p in props:
        why = f"C-EXA: found on {p['hits']} of {p['of']} sample PDFs — blank when a document has no such figure"
        col = db.query(Column).filter(Column.template_id == template.id, Column.key == p["key"]).one_or_none()
        if col is not None:
            col.required = False
            col.config = dict(col.config or {}, optional_reason=why)
        c = by_key.get(p["key"])
        if c is not None:
            c["required"] = False
            c["config"] = dict(c.get("config") or {}, optional_reason=why)
        notes.append(f"{p['label']}: found on {p['hits']} of {p['of']} samples — set to optional; a blank cell is the answer when the document has no such figure. An admin can make it required again in the template.")
    db.flush()
    if say:
        say("Optional by evidence: " + ", ".join(f"{p['label']} ({p['hits']} of {p['of']} samples)" for p in props) + ". These columns stay blank when a document has no such figure — that is not a miss. Untick 'optional' in the template if they must always be there.")
    return notes


# ---- incremental authoring: one column at a time, the rest untouched ----------------------------
def _remember_hints(rs: dict, columns: list[dict]) -> None:
    """Keep the hint each field was authored from, so a changed hint re-authors only that field."""
    for c in columns:
        f = rs["fields"].get(c["key"])
        if isinstance(f, dict):
            f["hint"] = (c.get("hint") or "").strip()


def _sample_spans(db: Session, prof: Profile, limit: int = SAMPLES_PER_FAMILY) -> list[dict]:
    """Spans of the documents this profile was learned on (cached on disk by sha) — samples first."""
    from .spans import cached

    docs = db.query(Document).filter(Document.profile_id == prof.id).order_by(Document.is_sample.desc(), Document.id.desc()).limit(limit * 4).all()
    out, seen = [], set()
    for d in docs:
        if d.sha256 in seen:
            continue
        sp = cached(d.sha256)
        if sp:
            seen.add(d.sha256)
            out.append(sp)
        if len(out) >= limit:
            break
    return out


def columns_needing_rules(template: Template, ver: Optional[ProfileVersion], force_keys: tuple[str, ...] = ()) -> list[dict]:
    """Template columns the version has no field for, whose hint changed since it was authored, or
    that the caller names in force_keys."""
    fields = ((ver.ruleset if ver else None) or {}).get("fields") or {}
    out = []
    for c in template.columns:
        if c.ctype in P.NO_RULE_TYPES:
            continue  # filename / formula columns never get rules
        f = fields.get(c.key)
        hint = (c.hint or "").strip()
        missing = not isinstance(f, dict)
        hint_changed = not missing and bool(hint) and "hint" in f and (f.get("hint") or "") != hint
        if c.key in force_keys or missing or hint_changed:
            out.append(E.column_dict(c))
    return out


def author_missing_columns(db: Session, template: Template, prof: Profile, *, samples: Optional[list[dict]] = None, family: str = "", force_keys: tuple[str, ...] = (), author: str = "axl", say=None) -> Optional[ProfileVersion]:
    """When a template gained a column (or a column's hint changed), author ONLY that column's rules
    into a new version of the live profile; every other field is copied unchanged. Returns the new
    version, or None when nothing was missing or there are no sample pages to learn from."""
    ver = _working(prof)
    if ver is None:
        return None
    todo = columns_needing_rules(template, ver, force_keys)
    if not todo:
        return None
    samples = samples or _sample_spans(db, prof)
    if not samples:
        return None
    columns = E.columns_of(template)
    family = family or prof.key.split(".")[-1]
    new_rs = json.loads(json.dumps(ver.ruleset or {}))
    new_rs.setdefault("fields", {})
    notes = []
    for c in todo:
        others = set()
        for o in P.readable(columns):
            if o["key"] != c["key"]:
                others |= {" ".join(X.norm_tokens(l)) for l in P.candidate_labels(o)}
        rules, note = P.author_column(c, samples, family, other_labels=others, db=db)
        new_rs["fields"][c["key"]] = {"rules": rules, "hint": (c.get("hint") or "").strip()}
        notes.append(note)
    # derived money fallbacks are re-added only where they are not already there
    for f in new_rs["fields"].values():
        f["rules"] = [r for r in f.get("rules", []) if r.get("type") != "derived"]
    P._add_derived(new_rs, columns, samples)
    # a new figure seen on some but not most samples → optional (only the columns authored here)
    notes += apply_optional_proposals(db, template, columns, new_rs, len(samples), say=say, only_keys={t["key"] for t in todo})
    labels = ", ".join(c["label"] for c in todo)
    verb = "Re-authored" if force_keys else "Added"
    reason = f"{verb} column{'s' if len(todo) != 1 else ''} {labels} for '{template.name}' — other fields unchanged"
    new_ver = _new_version(db, prof, new_rs, reason=reason, notes_extra="\n".join("- " + n for n in notes))
    if author != "axl":
        new_ver.author = author
    db.flush()
    return new_ver


def author_missing_for_template(db: Session, template: Template) -> list[ProfileVersion]:
    """Before a run: every profile of this template gets rules for columns it lacks."""
    out = []
    for prof in db.query(Profile).filter(Profile.template_id == template.id).all():
        try:
            v = author_missing_columns(db, template, prof)
        except Exception:  # noqa: BLE001 — a profile that can't be extended must not stop the batch
            log.warning("author_missing_columns failed for profile %s", prof.key, exc_info=True)
            v = None
        if v is not None:
            out.append(v)
    return out


def _working(prof: Profile) -> Optional[ProfileVersion]:
    if prof.live_version_id:
        for v in prof.versions:
            if v.id == prof.live_version_id:
                return v
    cexa_vers = [v for v in prof.versions if v.author == "axl"]
    return (cexa_vers or prof.versions or [None])[-1]


def _new_version(db: Session, prof: Profile, rs: dict, *, reason: str, notes_extra: str = "", acc_before: Optional[float] = None, first: bool = False) -> ProfileVersion:
    number = (max((v.number for v in prof.versions), default=0) or 0) + 1
    prev = prof.versions[-1] if prof.versions else None
    notes = notes_extra if first or prev is None else (prev.notes or "") + ("\n\n" if notes_extra else "") + f"v{number}: {reason}" + (("\n" + notes_extra) if notes_extra else "")
    ver = ProfileVersion(profile_id=prof.id, number=number, ruleset=rs, notes=notes.strip(), reason=reason + (f" ({acc_before:.0%} before)" if acc_before is not None else ""), author="axl", backtest={}, approved_at=None)
    db.add(ver)
    db.flush()
    prof.versions.append(ver)
    prof.live_version_id = ver.id
    if prof.status == "draft":
        prof.status = "live"
    db.flush()
    return ver


def _author_prompt(columns: list[dict], samples: list[dict], draft: dict) -> str:
    cols = [{"key": c["key"], "label": c["label"], "type": c["ctype"], "hint": c.get("hint", "")} for c in P.readable(columns)]
    return (
        "COLUMNS WANTED (one row per PDF):\n" + json.dumps(cols, ensure_ascii=False) + "\n\n"
        "MY FIRST DRAFT OF THE RULES (heuristic — improve or add fallbacks, keep what works):\n" + json.dumps(draft.get("fields", {}), ensure_ascii=False) + "\n\n"
        "SAMPLE PAGES:\n" + P.sample_text_for_prompt(samples)
    )
