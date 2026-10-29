"""A person fills or changes a cell. C-EXA checks the PDF for it, then learns from it.

verify(spans, ctype, value)         → {found, page, quote, bbox}
fill(db, batch, doc, col, value, u) → (Correction, cell)   status filled (found) or flagged (not found)
answer(db, correction, answer, note)→ other_source (note required) | trust_me → trusted ; retract → back to miss
teach(db, doc, col, value, old_cell)→ teaches the LEXICON the label the value sits next to
learn(db, profile, extraction, col_key, value) → pending ProfileVersion or None

A correction teaches two different things and both matter:

* `learn` makes a rule for *this* profile — the answer, on this layout.
* `teach` writes the *label* into the lexicon — so the phrase that turned out to be right here is
  offered first on every layout C-EXA meets from now on, including municipalities she has never seen.
  This is the single most valuable learning signal in the product: one person's fix on one bill
  makes the next new layout easier. It also marks the label the failed rule used as a failure, so a
  wrong phrase stops being offered.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log as audit
from ..db import utcnow
from ..models import Batch, Column, Correction, Document, Extraction, Profile, ProfileVersion, Template, User
from . import export as E
from . import extract as X
from . import lexicon as LX
from . import money as M
from . import repair as R
from . import scoring as SC
from .spans import cached


def verify(spans: dict, ctype: str, value) -> dict:
    """Is `value` printed on the document? Numbers compare by value (any SA format); text by
    case/whitespace-insensitive containment."""
    if spans is None:
        return {"found": False, "page": None, "quote": "", "bbox": None, "reason": "the PDF text is not available"}
    if ctype in ("money", "number"):
        v = M.parse_money(str(value)) if ctype == "money" else M.parse_number(str(value))
        if v is None:
            return {"found": False, "page": None, "quote": "", "bbox": None, "reason": f"'{value}' is not a {ctype}"}
        hit = X.find_number_in_doc(spans, v)
    elif ctype == "date":
        iso = M.parse_date(str(value))
        hit = None
        if iso:
            for l in spans["lines"]:
                for raw, parsed in M.find_dates(l["text"]):
                    if parsed == iso or parsed.split(" – ")[0] == iso:
                        hit = {"raw": raw, "page": l["page"], "bbox": X._bbox_for(l["words"], raw), "ocr": bool(l.get("ocr"))}
                        break
                if hit:
                    break
        if not hit:
            hit = X.find_text_in_doc(spans, str(value))
    else:
        hit = X.find_text_in_doc(spans, str(value))
    if hit:
        return {"found": True, "page": hit["page"], "quote": hit["raw"], "bbox": hit.get("bbox"), "reason": ""}
    return {"found": False, "page": None, "quote": "", "bbox": None, "reason": "not on any page"}


def typed_value(ctype: str, value):
    v, ok = M.parse_typed(ctype, str(value))
    return v if ok else value


def fill(db: Session, batch: Batch, doc: Document, col: Column, value: str, user: User) -> tuple[Correction, dict]:
    ext = doc.extraction
    if ext is None:
        ext = Extraction(document_id=doc.id, cells={})
        db.add(ext)
        db.flush()
    cells = dict(ext.cells or {})
    old = cells.get(col.key) or {}
    spans = cached(doc.sha256)
    res = verify(spans, col.ctype, value)
    corr = Correction(extraction_id=ext.id, column_key=col.key, old_value="" if old.get("value") is None else str(old.get("value")), new_value=str(value), user_id=user.id, verified=res["found"], verify_note=res["reason"] if not res["found"] else f"found on page {res['page']}: “{res['quote']}”")
    db.add(corr)
    db.flush()
    cell = dict(old)
    cell.update(
        {
            "value": typed_value(col.ctype, value),
            "raw": str(value),
            "quote": res["quote"],
            "page": res["page"] if res["found"] else old.get("page"),
            "bbox": res["bbox"] if res["found"] else old.get("bbox"),
            "method": f"user:{user.id}",
            "quality": "exact",
            "verified": res["found"],
            "score": 1.0 if res["found"] else 0.0,
            "status": "filled" if res["found"] else "flagged",
            "note": ("You filled this · C-EXA checked: found on page %s" % res["page"]) if res["found"] else f"C-EXA can't find “{value}” on this PDF",
            "correction_id": corr.id,
            "by": user.id,
        }
    )
    cells[col.key] = cell
    ext.cells = cells
    _rescore(ext, batch)
    audit(db, "pdf_cell_filled", actor=user, target=f"batch:{batch.id}/doc:{doc.id}/{col.key}", detail=f"{corr.old_value!r} → {value!r}; verified={res['found']}", outcome="ok" if res["found"] else "pending")
    if res["found"]:
        teach(db, doc, col, value, old, user=user, spans=spans)
    return corr, cell


def answer(db: Session, corr: Correction, ans: str, note: str, user: User) -> dict:
    ext = db.get(Extraction, corr.extraction_id)
    cells = dict(ext.cells or {})
    cell = dict(cells.get(corr.column_key) or {})
    if ans == "other_source":
        if not (note or "").strip():
            raise ValueError("Tell me where the value comes from.")
        cell.update({"status": "trusted", "score": 1.0, "note": f"From another source: {note.strip()}", "verified": False})
        corr.user_answer, corr.source_note = "other_source", note.strip()
    elif ans == "trust_me":
        cell.update({"status": "trusted", "score": 1.0, "note": "You asked me to trust this value" + (f": {note.strip()}" if note else ""), "verified": False})
        corr.user_answer, corr.source_note = "trust_me", (note or "").strip()
    elif ans == "retract":
        col = _col_of(db, ext, corr.column_key)
        cell.update({"value": None if corr.old_value == "" else typed_value(col.ctype if col else "text", corr.old_value), "raw": corr.old_value, "status": "miss", "score": 0.0, "note": "You withdrew your entry", "verified": None})
        if corr.old_value == "" and col is not None and not col.required and col.ctype not in SC.NO_RULE_TYPES:
            # an optional column goes back to "not on this document", not to the user's desk
            cell.update({"status": "absent", "score": 1.0, "quality": "absent", "note": "not on this document (you withdrew your entry)"})
        corr.user_answer = "retract"
    else:
        raise ValueError("Answer with other_source, trust_me or retract.")
    cells[corr.column_key] = cell
    ext.cells = cells
    batch = db.get(Batch, db.get(Document, ext.document_id).batch_id)
    _rescore(ext, batch)
    audit(db, "pdf_cell_answered", actor=user, target=f"extraction:{ext.id}/{corr.column_key}", detail=f"{ans}: {note}")
    return cell


def _col_of(db: Session, ext: Extraction, key: str) -> Optional[Column]:
    doc = db.get(Document, ext.document_id)
    batch = db.get(Batch, doc.batch_id)
    return db.query(Column).filter(Column.template_id == batch.template_id, Column.key == key).one_or_none()


def _rescore(ext: Extraction, batch: Batch) -> None:
    """Recompute formulas, row score, batch counters and status after a user touched a cell."""
    from sqlalchemy.orm import object_session

    db = object_session(ext)
    cols = [E.column_dict(c) for c in db.query(Column).filter(Column.template_id == batch.template_id).order_by(Column.ordinal)]
    cells = dict(ext.cells or {})
    X.apply_formulas(cells, cols)  # a formula follows the cell the person just fixed
    ext.cells = cells
    ext.row_score, _, _ = SC.row_score(ext.cells, cols)
    doc = db.get(Document, ext.document_id)
    doc.score = ext.row_score
    counts = SC.cell_counts(((d.extraction.cells if d.extraction else None) for d in batch.documents if d.extraction), cols)
    for d in batch.documents:
        e = d.extraction
        if not e:
            continue
        d.status = "ok" if all(((e.cells or {}).get(c["key"]) or {}).get("status") not in SC.FOR_USER for c in cols) else "review"
    batch.cells_total, batch.cells_filled, batch.cells_for_user = counts["total"], counts["filled"], counts["for_user"]
    if batch.status in ("review", "done"):
        batch.status = "review" if counts["for_user"] else "done"


# ---- teaching the lexicon (a fix teaches the label, not just the answer) -----------------------
_MIN_SKELETON_SHARE = 0.6  # a label must be printed on this share of the layout's other documents
_WHERE = {"right": "value to the right of it", "below": "value below it", "left": "value to the left of it"}


def layout_signature(profile: Optional[Profile]) -> str:
    """What the lexicon records a label as having worked on. A profile key is
    `<template-slug>.<family>`; the family is the layout skeleton, which is what transfers."""
    if profile is None:
        return ""
    return str(profile.key or "").split(".")[-1] or str(profile.key or "")


def teach(db: Session, doc: Document, col: Column, value, old_cell: Optional[dict] = None, *, user: Optional[User] = None, spans: Optional[dict] = None) -> Optional[dict]:
    """A person fixed this cell and the value IS printed on the page. Find the label the value sits
    next to and write it into the lexicon; mark the label the failed rule used as a failure.

    Returns {label, placement, rejected} when something was learned, else None. Never raises — a
    save must not fail because C-EXA couldn't remember something."""
    try:
        spans = spans if spans is not None else cached(doc.sha256)
        if spans is None or col.ctype in SC.NO_RULE_TYPES:
            return None
        coldict = {"key": col.key, "label": col.label, "ctype": col.ctype, "hint": col.hint or ""}
        found = label_near_value(spans, coldict, value)
        if found is None:
            return None
        label, placement = found
        profile = db.get(Profile, doc.profile_id) if doc.profile_id else None
        layout = layout_signature(profile)
        if not _is_skeleton(db, profile, doc, label):
            return None  # the text next to the value is data on this bill, not a printed heading
        LX.confirm(db, column_key=col.key, label=label, placement=placement, layout=layout, example=str(value)[:160], ctype=col.ctype, source="correction")
        rejected = _reject_failed_label(db, profile, col, old_cell or {}, layout, keep=(label, placement))
        audit(
            db,
            "pdf_lexicon_learned",
            actor=user,
            target=f"lexicon:{LX.group_for(coldict)}/{col.key}",
            detail=f"a fix on '{doc.filename}' taught '{label}' ({_WHERE[placement]}) for {col.label}" + (f"; '{rejected[0]}' ({_WHERE[rejected[1]]}) marked failed" if rejected else ""),
            outcome="ok",
        )
        return {"label": label, "placement": placement, "rejected": rejected}
    except Exception:  # noqa: BLE001 — C-EXA's memory must never block a person's fix
        return None


def label_near_value(spans: dict, col: dict, value) -> Optional[tuple[str, str]]:
    """(label, placement) for the printed label `value` sits next to: to its left on the same line
    (placement `right`), to its right on the same line (`left`), or on the line above (`below`).
    The label is kept only if applying it as a rule really returns the value."""
    ctype = col["ctype"]
    if ctype in ("money", "number"):
        v = M.parse_money(str(value)) if ctype == "money" else M.parse_number(str(value))
        hit = X.find_number_in_doc(spans, v) if v is not None else None
    else:
        hit = X.find_text_in_doc(spans, str(value))
    if not hit or not hit.get("line"):
        return None
    line = hit["line"]
    bbox = hit.get("bbox") or [line["x0"], line["top"], line["x1"], line["bottom"]]
    # a text hit reports the whole line as its box, which would hide the label sitting to its left.
    # Narrow it to the words the value is actually made of.
    tight = X._bbox_for(line["words"], str(hit.get("raw") or value))
    if tight and (tight[2] - tight[0]) < (bbox[2] - bbox[0]):
        bbox = tight
    # when the box still covers the whole line we cannot say what is left or right of the value, so
    # only the line above is worth looking at
    on_line = (bbox[2] - bbox[0]) < (line["x1"] - line["x0"]) - 1
    want = str(typed_value(ctype, value))

    def words_text(ws) -> str:
        return " ".join(w["t"] for w in ws).strip(" :.-")

    def works(label: str, placement: str) -> bool:
        rule = {"type": LX.RULE_TYPE[placement], "label": label, "pick": "first", "page": line["page"] if line["page"] > 1 else None, "quality": "exact"}
        for pick in ("first", "last"):
            rule["pick"] = pick
            got = X.apply_rule(rule, ctype, spans)
            if got is not None and str(got.get("value")) == want:
                return True
        return False

    if on_line:
        left = [w for w in line["words"] if w["x1"] <= bbox[0] + 1 and X.norm_tokens(w["t"]) and not M.find_numbers(w["t"])]
        if left:
            lab = words_text(left)
            if _label_shape_ok(lab) and works(lab, "right"):
                return lab, "right"
        right = [w for w in line["words"] if w["x0"] >= bbox[2] - 1 and X.norm_tokens(w["t"]) and not M.find_numbers(w["t"])]
        if right:
            lab = words_text(right)
            if _label_shape_ok(lab) and works(lab, "left"):
                return lab, "left"
    above = [l for l in spans["lines"] if l["page"] == line["page"] and l["bottom"] <= line["top"] + 1 and line["top"] - l["bottom"] <= X.BELOW_MAX_PT]
    above.sort(key=lambda l: -l["bottom"])
    for l in above:
        ws = [w for w in l["words"] if w["x1"] >= bbox[0] - 10 and w["x0"] <= bbox[2] + 10 and X.norm_tokens(w["t"]) and not M.find_numbers(w["t"])]
        if not ws:
            continue
        lab = words_text(ws)
        if _label_shape_ok(lab) and works(lab, "below"):
            return lab, "below"
    return None


def _label_shape_ok(label: str) -> bool:
    """A printed heading is short words, not a run of digits and not a sentence."""
    lab = (label or "").strip()
    if not LX._usable(lab):
        return False
    return not re.search(r"\d{4,}", lab)


def _is_skeleton(db: Session, profile: Optional[Profile], doc: Document, label: str) -> bool:
    """Is `label` printed on the layout's OTHER documents too? That is what makes it a label rather
    than a value that happens to be words. With fewer than two other documents to compare against we
    fall back to the shape test — better a cautious guess than no memory at all."""
    others: list[dict] = []
    if profile is not None:
        try:
            rows = db.query(Document).filter(Document.profile_id == profile.id, Document.id != doc.id).limit(24).all()
        except Exception:  # noqa: BLE001
            rows = []
        seen = {doc.sha256}
        for d in rows:
            if d.sha256 in seen:
                continue
            sp = cached(d.sha256)
            if sp:
                seen.add(d.sha256)
                others.append(sp)
            if len(others) >= 6:
                break
    if len(others) < 2:
        return True
    hits = sum(1 for sp in others if X.find_anchors(sp, label, fuzzy=False, limit=1))
    return hits >= max(1, round(_MIN_SKELETON_SHARE * len(others)))


def _reject_failed_label(db: Session, profile: Optional[Profile], col: Column, old_cell: dict, layout: str, keep: tuple[str, str]) -> Optional[tuple[str, str]]:
    """The rule that produced the wrong value used a label. Mark that label as having failed here,
    so it stops being offered for this kind of column on the next layout."""
    if profile is None:
        return None
    method = str((old_cell or {}).get("method") or "")
    if not method or method.startswith("user:") or method == "formula":
        return None
    ver = _working_version(profile)
    rules = ((ver.ruleset if ver else None) or {}).get("fields", {}).get(col.key, {}).get("rules", []) if ver else []
    rule = next((r for r in rules if str(r.get("id")) == method), None)
    if rule is None:
        return None
    placement = LX.placement_of(rule.get("type", ""))
    label = str(rule.get("label") or "")
    if not placement or not label:
        return None
    if (label.strip().lower(), placement) == (keep[0].strip().lower(), keep[1]):
        return None  # the rule had the right label all along; the value moved, not the vocabulary
    LX.reject(db, column_key=col.key, label=label, placement=placement, layout=layout, ctype=col.ctype, source="correction")
    return label, placement


# ---- learning --------------------------------------------------------------------------------
def learn(db: Session, profile: Profile, extraction: Extraction, col_key: str, value, user: Optional[User] = None) -> Optional[ProfileVersion]:
    """Locate the value the user typed, find the label next to it, make a rule, back-test it on every
    other document of this profile, and file a pending version if nothing regresses."""
    doc = db.get(Document, extraction.document_id)
    spans = cached(doc.sha256)
    if spans is None:
        return None
    batch = db.get(Batch, doc.batch_id)
    template = db.get(Template, batch.template_id)
    columns = E.columns_of(template)
    col = next((c for c in columns if c["key"] == col_key), None)
    if col is None or col["ctype"] in SC.NO_RULE_TYPES:
        return None
    base_version = _working_version(profile)
    if base_version is None:
        return None
    rule = synthesise_rule(spans, col, value)
    if rule is None:
        return None
    # every document of this profile with a cached read
    docs = []
    for d in db.query(Document).filter(Document.profile_id == profile.id).all():
        sp = cached(d.sha256)
        if sp is None:
            continue
        docs.append({"id": d.id, "ordinal": d.ordinal, "name": d.filename, "filename": d.filename, "spans": sp, "cells": dict((d.extraction.cells if d.extraction else {}) or {})})
    if not docs:
        return None
    rs = base_version.ruleset or {}
    n_user = sum(1 for r in rs.get("fields", {}).get(col_key, {}).get("rules", []) if str(r.get("id", "")).startswith("u"))
    rule["id"] = f"u{n_user+1}"
    before = SC.column_accuracy((d["cells"] for d in docs), [col])[col_key]
    best = None
    for front in (True, False):
        cand = R._with_rule(rs, col_key, rule, front=front)
        bt = R.backtest(cand, columns, docs, only_key=col_key)
        # the corrected cell itself must now come out right
        me = next((x for x in bt["docs_out"] if x["id"] == doc.id), None)
        hits_me = bool(me) and str((me["cells"].get(col_key) or {}).get("value")) == str(typed_value(col["ctype"], value))
        if not bt["regressions"] and hits_me and bt["by_column"].get(col_key, 0) >= before:
            best = (cand, bt, front)
            break
    if best is None:
        audit(db, "pdf_learn_rejected", actor=user, target=f"profile:{profile.id}/{col_key}", detail="candidate rule regressed other documents", outcome="blocked")
        return None
    cand, bt, front = best
    # honest score for the new version: the document the person fixed (and the rest of the training
    # split) built the rule; the held-out documents only mark it.
    others = [d for d in docs if d["id"] != doc.id]
    train, hold = R.split(others)
    me_doc = next((d for d in docs if d["id"] == doc.id), None)
    honest = R.measure(columns, ([me_doc] if me_doc else []) + train, hold, rs=cand)
    number = (max((v.number for v in profile.versions), default=0) or 0) + 1
    who = (user.name.split()[0] if user and user.name else "A user")
    where = {"label_right": "right of", "label_below": "below", "label_left": "left of"}[rule["type"]]
    page_txt = f"page-{rule['page']} " if rule.get("page") else ""
    reason = f"{who}'s fix on row {doc.ordinal} → {page_txt}'{rule['label']}' rule for {col['label']} ({where} the label)"
    ver = ProfileVersion(
        profile_id=profile.id,
        number=number,
        ruleset=cand,
        notes=(base_version.notes or "") + f"\n\n{reason}. Back-tested on {bt['docs']} documents: {bt['passed']} pass, column accuracy {before:.0%} → {bt['by_column'].get(col_key, 0):.0%}.",
        reason=reason,
        author=f"user:{user.id}" if user else "user",
        backtest={
            "docs": honest["docs"],
            "passed": honest["passed"],
            "accuracy": honest["accuracy"],
            "holdout_accuracy": honest["holdout_accuracy"],
            "by_column": honest["by_column"],
            "training_by_column": honest["training_by_column"],
            "trained_on": honest["trained_on"],
            "held_out": honest["held_out"],
            "note": honest["note"],
            "column": col_key,
            "before": before,
            "after": bt["by_column"].get(col_key, 0),
            "regressions": len(bt["regressions"]),
        },
        approved_at=None,
    )
    db.add(ver)
    db.flush()
    profile.status = "patch"
    audit(db, "pdf_profile_version_pending", actor=user, target=f"profile:{profile.id}", detail=f"v{number}: {reason}", outcome="pending")
    return ver


def _working_version(profile: Profile) -> Optional[ProfileVersion]:
    if profile.live_version_id:
        for v in profile.versions:
            if v.id == profile.live_version_id:
                return v
    return profile.versions[-1] if profile.versions else None


def synthesise_rule(spans: dict, col: dict, value) -> Optional[dict]:
    """Find `value` on the page and the label nearest to it (left on the same line, else above)."""
    ctype = col["ctype"]
    if ctype in ("money", "number"):
        v = M.parse_money(str(value)) if ctype == "money" else M.parse_number(str(value))
        hit = X.find_number_in_doc(spans, v) if v is not None else None
    else:
        hit = X.find_text_in_doc(spans, str(value))
    if not hit or not hit.get("line"):
        return None
    line = hit["line"]
    bbox = hit.get("bbox") or [line["x0"], line["top"], line["x1"], line["bottom"]]
    left = [w for w in line["words"] if w["x1"] <= bbox[0] + 1 and X.norm_tokens(w["t"]) and not M.find_numbers(w["t"])]
    page = line["page"] if line["page"] > 1 else None
    if left:
        label = " ".join(w["t"] for w in left).strip(" :.-")
        # pick: does the first typed token to the right of the label give the value?
        rule = {"type": "label_right", "label": label, "pick": "first", "page": page, "quality": "exact"}
        got = X.apply_rule(rule, ctype, spans)
        if got is None or str(got.get("value")) != str(typed_value(ctype, value)):
            rule["pick"] = "last"
            got = X.apply_rule(rule, ctype, spans)
        if got is not None and str(got.get("value")) == str(typed_value(ctype, value)):
            return rule
    # label above: the closest line above whose words overlap the value's x-range
    above = [l for l in spans["lines"] if l["page"] == line["page"] and l["bottom"] <= line["top"] + 1 and line["top"] - l["bottom"] <= X.BELOW_MAX_PT]
    above.sort(key=lambda l: -l["bottom"])
    for l in above:
        words = [w for w in l["words"] if w["x1"] >= bbox[0] - 10 and w["x0"] <= bbox[2] + 10 and X.norm_tokens(w["t"]) and not M.find_numbers(w["t"])]
        if words:
            label = " ".join(w["t"] for w in words).strip(" :.-")
            rule = {"type": "label_below", "label": label, "pick": "first", "page": page, "quality": "exact"}
            got = X.apply_rule(rule, ctype, spans)
            if got is not None and str(got.get("value")) == str(typed_value(ctype, value)):
                return rule
    return None


def pending_count(db: Session) -> int:
    return db.query(ProfileVersion).filter(ProfileVersion.approved_at.is_(None), ProfileVersion.author.like("user%")).count()


def approve(db: Session, ver: ProfileVersion, user: User) -> None:
    prof = db.get(Profile, ver.profile_id)
    ver.approved_by_id, ver.approved_at = user.id, utcnow()
    prof.live_version_id = ver.id
    prof.status = "live"
    # the profile carries the held-out figure of whichever version is live, or nothing at all when
    # this version could not be measured on documents it was not built from
    h = (ver.backtest or {}).get("holdout_accuracy")
    prof.accuracy = round(float(h), 4) if isinstance(h, (int, float)) else None
    audit(db, "pdf_profile_version_approved", actor=user, target=f"profile:{prof.id}", detail=f"v{ver.number}: {ver.reason}")
