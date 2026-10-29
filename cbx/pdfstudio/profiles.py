"""Profile helpers used by other features, the spend meter and the model bake-off."""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..cexa.gateway import Gateway, ModelError, BudgetExhausted
from ..config import settings
from ..db import utcnow
from ..models import Batch, BenchResult, Document, Extraction, ModelCall, Profile, ProfileVersion, Template
from . import export as E
from . import extract as X
from . import scoring as SC
from .corrections import pending_count  # noqa: F401  (home page reads cbx.pdfstudio.profiles.pending_count)
from .spans import cached, contains_verbatim


def working_version(prof: Profile) -> Optional[ProfileVersion]:
    if prof.live_version_id:
        for v in prof.versions:
            if v.id == prof.live_version_id:
                return v
    return prof.versions[-1] if prof.versions else None


def honest_accuracy(prof: Profile) -> dict:
    """What this profile's accuracy really is, and what it is a score *of*.

    A profile is authored and repaired on a training split; the figure reported here is its score on
    the documents it was never shown. Authoring on six bills and scoring on those same six is how
    52.7% wrong was once reported as 90% right. With too few documents to hold any back there is no
    number — `accuracy` is None and `basis` says so, because "too few to check" is honest and 100%
    is not.

        {accuracy, basis, phrase, training_accuracy, held_out, trained_on, by_column}"""
    v = working_version(prof)
    bt = (v.backtest if v else None) or {}
    held = int(bt.get("held_out") or 0)
    train = int(bt.get("trained_on") or 0)
    acc = bt.get("holdout_accuracy")
    acc = float(acc) if isinstance(acc, (int, float)) else None
    if acc is None and prof.accuracy is not None and not bt:
        # a profile from before held-out scoring existed: its stored number was measured on the
        # documents it was built from, so it is a training score and is labelled as one
        return {"accuracy": None, "basis": "training_only", "phrase": f"{prof.accuracy:.1%} on the bills it was built from — never checked on bills it hadn't seen", "training_accuracy": float(prof.accuracy), "held_out": 0, "trained_on": prof.docs_seen or 0, "by_column": {}}
    if acc is None:
        return {"accuracy": None, "basis": "too_few", "phrase": bt.get("note") or "too few to check", "training_accuracy": float(bt["accuracy"]) if isinstance(bt.get("accuracy"), (int, float)) else None, "held_out": held, "trained_on": train, "by_column": bt.get("by_column") or {}}
    return {
        "accuracy": acc,
        "basis": "held_out",
        "phrase": f"{acc:.0%} on {held} bill{'s' if held != 1 else ''} it had not seen",
        "training_accuracy": float(bt["accuracy"]) if isinstance(bt.get("accuracy"), (int, float)) else None,
        "held_out": held,
        "trained_on": train,
        "by_column": bt.get("by_column") or {},
    }


def spend(db: Session) -> dict:
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    per_batch = dict(db.query(ModelCall.batch_id, func.coalesce(func.sum(ModelCall.cost_zar), 0.0)).filter(ModelCall.batch_id.isnot(None)).group_by(ModelCall.batch_id).all())
    batches = db.query(Batch).order_by(Batch.created_at.desc()).limit(12).all()
    total_month = db.query(func.coalesce(func.sum(ModelCall.cost_zar), 0.0)).filter(ModelCall.at >= month_start).scalar() or 0.0
    calls_month = db.query(ModelCall).filter(ModelCall.at >= month_start).count()
    # share of documents read on rules alone (no model-authored rule produced a value)
    exts = db.query(Extraction).all()
    rules_only = 0
    for e in exts:
        cells = e.cells or {}
        if all(not str(c.get("method", "")).startswith("axl") for c in cells.values() if c.get("value") is not None):
            rules_only += 1
    return {
        "month_zar": round(float(total_month), 2),
        "calls_month": calls_month,
        "ceiling_zar": settings.model_batch_ceiling_zar,
        "mode": settings.model_mode,
        "rules_only_share": round(rules_only / len(exts), 4) if exts else 1.0,
        "docs": len(exts),
        "batches": [{"id": b.id, "name": b.name, "spend_zar": round(float(per_batch.get(b.id, b.spend_zar or 0.0)), 2), "ceiling_zar": b.ceiling_zar, "status": b.status} for b in batches],
    }


# ---- bake-off --------------------------------------------------------------------------------------
STUB_ROWS = [
    {"model": settings.ocr_model, "runs_on": "GPU · ~2 GB", "role": "scanned", "cost_per_1000": 0.20, "latency_s": 0.8},
    {"model": settings.worker_model, "runs_on": "GPU · ~7 GB", "role": "misses", "cost_per_1000": 1.10, "latency_s": 1.2},
    {"model": settings.cexa_model, "runs_on": "GPU · ~18 GB", "role": "profiles", "cost_per_1000": 3.40, "latency_s": 3.9},
    {"model": "gemma3:27b", "runs_on": "GPU · ~20 GB", "role": "challenger", "cost_per_1000": 3.90, "latency_s": 4.4},
]
MODEL_TASK = {"scanned": "ocr", "misses": "residual_read", "profiles": "profile_repair", "challenger": "profile_repair"}


def run_bench(db: Session, template: Template) -> list[BenchResult]:
    columns = E.columns_of(template)
    docs = db.query(Document).join(Batch).filter(Batch.template_id == template.id).all()
    # gold: cells a person verified; if none yet, C-EXA's own ok cells (self-checked)
    gold: dict[tuple[int, str], object] = {}
    n_verified = 0
    for d in docs:
        cells = (d.extraction.cells if d.extraction else {}) or {}
        for k, c in cells.items():
            if c.get("status") == "filled" and c.get("verified") and c.get("value") is not None:
                gold[(d.id, k)] = c["value"]
                n_verified += 1
    if len(gold) < 10:  # too few hand-checked cells alone: add the cells C-EXA stands behind
        for d in docs:
            cells = (d.extraction.cells if d.extraction else {}) or {}
            for k, c in cells.items():
                if c.get("status") == "ok" and (c.get("score") or 0) >= SC.CELL_OK and (d.id, k) not in gold:
                    gold[(d.id, k)] = c["value"]
    verified = n_verified > 0
    gold_docs = {d for d, _ in gold}
    db.query(BenchResult).filter(BenchResult.template_id == template.id).delete()
    out: list[BenchResult] = []
    # rules only
    t0 = time.time()
    pred: dict[tuple[int, str], dict] = {}
    for d in docs:
        if d.id not in gold_docs:
            continue
        prof = db.get(Profile, d.profile_id) if d.profile_id else None
        ver = working_version(prof) if prof else None
        sp = cached(d.sha256)
        if not ver or not sp:
            continue
        cells = X.extract(ver.ruleset or {}, columns, sp, filename=d.filename)
        val = SC.run_validators(ver.ruleset or {}, columns, cells)
        SC.score_cells(cells, columns, val, sp.get("scanned", False))
        X.apply_formulas(cells, columns)
        for k, c in cells.items():
            if (d.id, k) in gold and c.get("value") is not None and (c.get("score") or 0) >= SC.CELL_OK:
                pred[(d.id, k)] = c
    secs = (time.time() - t0) / max(1, len(gold_docs))
    p, r, h = _metrics(gold, pred)
    label = f"Rules only (profile v{_version_label(db, docs)})"
    out.append(BenchResult(template_id=template.id, model=label, runs_on="CPU", docs=len(gold_docs), precision=p, recall=r, hallucination_rate=h, cost_per_1000=0.0, latency_s=round(secs, 2), role="first_pass"))
    # models
    for spec in STUB_ROWS:
        if settings.model_mode == "stub":
            out.append(BenchResult(template_id=template.id, model=spec["model"], runs_on="not measured — no model connected", docs=0, precision=0, recall=0, hallucination_rate=0, cost_per_1000=spec["cost_per_1000"], latency_s=spec["latency_s"], role=spec["role"]))
            continue
        mp, cost, lat = _model_pass(db, docs, gold, columns, MODEL_TASK[spec["role"]])
        p, r, h = _metrics(gold, mp)
        out.append(BenchResult(template_id=template.id, model=spec["model"], runs_on=spec["runs_on"], docs=len(gold_docs), precision=p, recall=r, hallucination_rate=h, cost_per_1000=cost, latency_s=lat, role=spec["role"]))
    # recommended: highest recall among rows with hallucination ≤ 0.1% and cost ≤ 2× the cheapest such row
    eligible = [b for b in out if b.hallucination_rate <= 0.001 and (b.docs or 0) > 0]
    if eligible:
        cheapest = min(b.cost_per_1000 for b in eligible)
        pool = [b for b in eligible if b.cost_per_1000 <= max(cheapest * 2, 0.0001) or b.cost_per_1000 == cheapest]
        best = max(pool, key=lambda b: (b.recall, -b.cost_per_1000))
        best.recommended = True
    for b in out:
        db.add(b)
    db.flush()
    meta = {"gold_cells": len(gold), "gold_docs": len(gold_docs), "verified": verified, "verified_cells": n_verified}
    for b in out:
        b._meta = meta  # not persisted; picked up by the API for the note line
    return out


def _version_label(db: Session, docs) -> str:
    vers = set()
    for d in docs:
        if d.extraction and d.extraction.profile_version_id:
            v = db.get(ProfileVersion, d.extraction.profile_version_id)
            if v:
                vers.add(v.number)
    return "/".join(str(v) for v in sorted(vers)) or "-"


def _metrics(gold: dict, pred: dict) -> tuple[float, float, float]:
    if not gold:
        return 0.0, 0.0, 0.0
    correct = sum(1 for k, v in gold.items() if k in pred and _same(pred[k].get("value"), v))
    n_pred = len(pred)
    halluc = sum(1 for k, c in pred.items() if not c.get("provenance", True))
    precision = correct / n_pred if n_pred else 0.0
    recall = correct / len(gold)
    return round(precision, 4), round(recall, 4), round(halluc / n_pred, 4) if n_pred else 0.0


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= 0.005
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _model_pass(db: Session, docs, gold: dict, columns: list[dict], task: str, max_cells: int = 40) -> tuple[dict, float, float]:
    gw = Gateway(db)
    pred: dict = {}
    by_key = {c["key"]: c for c in columns}
    items = list(gold.items())[:max_cells]
    t0 = time.time()
    spent = gw.spent
    pages = 0
    for (doc_id, key), _ in items:
        d = next((x for x in docs if x.id == doc_id), None)
        sp = cached(d.sha256) if d else None
        if not sp:
            continue
        pages += sp.get("pages", 1)
        col = by_key.get(key)
        text = "\n".join(f"[p{l['page']}] {l['text']}" for l in sp["lines"])[:12000]
        try:
            out = gw.json(task=task, system="You read one value from a document. Reply with the value exactly as printed in 'quote'. If it is not on the page, set found=false and leave value empty. Never guess.", user=f"Column: {col['label']} ({col['ctype']}).\n\nDOCUMENT:\n{text}", schema={"value": "string", "quote": "string", "found": "bool"})
        except (ModelError, BudgetExhausted):
            break
        if out and out.get("found") and out.get("value") not in (None, ""):
            prov = contains_verbatim(sp, str(out.get("quote") or ""))
            pred[(doc_id, key)] = {"value": out.get("value"), "provenance": prov}
    secs = time.time() - t0
    cost = ((gw.spent - spent) / max(1, pages)) * 1000
    return pred, round(cost, 2), round(secs / max(1, len(items)), 2)
