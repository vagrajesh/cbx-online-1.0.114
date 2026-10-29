"""Diagnose columns that scored below 90% and patch the ruleset.

Order of attempts, per failing column (heuristics first, model second):
  1. re-anchor  – the label matched only fuzzily → add an exact rule on the text actually printed
  2. re-author  – no value found → run the authoring heuristics on the failing documents and put
                  the rules that work there in front (kept only if they don't break passing docs)
  3. money combo– an arithmetic check failed → try every candidate rule for the excl/VAT/total
                  columns together and keep the combination where the figures add up on most docs
  4. C-EXA        – `profile_repair` with the rule outcomes and the text near the expected label
Every candidate ruleset is back-tested on all documents: a cell that was right may not change.
"""
from __future__ import annotations

import copy
import itertools
import json
from typing import Callable, Optional

from . import extract as X
from . import profile as P
from . import scoring as SC
from .spans import page_lines

Doc = dict  # {id, ordinal, name, spans, cells}


# ---- running and back-testing ----------------------------------------------------------------
def run_docs(rs: dict, columns: list[dict], docs: list[Doc], on_progress=None) -> None:
    """Extract + score every doc in place (doc['cells'], doc['row'], doc['k'], doc['n'], doc['validators']).
    `on_progress(i, n)` is called after each document so long runs show movement."""
    for i, d in enumerate(docs):
        if on_progress and (i % 5 == 0 or i == len(docs) - 1):
            on_progress(i, len(docs))
        cells = X.extract(rs, columns, d["spans"], filename=d.get("filename") or d.get("name"))
        # keep user-owned cells across reruns
        for key, old in (d.get("cells") or {}).items():
            if old.get("status") in SC.USER_OWNED and key in cells:
                cells[key] = old
        val = SC.run_validators(rs, columns, cells)
        SC.score_cells(cells, columns, val, d["spans"].get("scanned", False))
        X.apply_formulas(cells, columns)  # after scoring: a formula only uses inputs we stand behind
        d["cells"], d["validators"] = cells, val
        d["row"], d["k"], d["n"] = SC.row_score(cells, columns)


def summarise(docs: list[Doc], columns: list[dict]) -> tuple[float, dict]:
    acc = SC.batch_accuracy(d["row"] for d in docs)
    by_col = SC.column_accuracy((d["cells"] for d in docs), columns)
    return acc, by_col


# ---- honest accuracy: train here, measure there ------------------------------------------------
MIN_FOR_HOLDOUT = 4  # under four documents there is nothing honest to say about a held-out score
HOLDOUT_SHARE = 0.34


def split(docs: list[Doc], *, share: float = HOLDOUT_SHARE, min_docs: int = MIN_FOR_HOLDOUT) -> tuple[list[Doc], list[Doc]]:
    """(train, holdout). A profile is authored and repaired on `train` only; `holdout` is what its
    accuracy is measured on. Held-out documents are spread evenly through the batch rather than
    taken off the end, so a batch that is sorted by municipality still holds out some of each.
    Fewer than `min_docs` documents → everything trains and there is no honest number to report."""
    n = len(docs)
    if n < min_docs:
        return list(docs), []
    k = max(1, min(n - 2, int(round(n * share))))
    step = n / k
    idx = sorted({min(n - 1, int(step * i + step / 2)) for i in range(k)})
    hold = [docs[i] for i in idx]
    keep = set(idx)
    return [d for i, d in enumerate(docs) if i not in keep], hold


def measure(columns: list[dict], train: list[Doc], holdout: list[Doc], *, rs: Optional[dict] = None) -> dict:
    """The honest score of a ruleset.

        {docs, passed, accuracy, holdout_accuracy, by_column, trained_on, held_out, ...}

    `accuracy` is the score on the documents the profile was BUILT from — the flattering one, kept
    only so the two can be shown side by side. `holdout_accuracy` is the score on documents the
    profile has never seen; it is `None` when there were too few documents to hold any back, and
    `note` then says so rather than inventing a number. `by_column` is the held-out per-column
    score when there is a held-out set, so it always agrees with the headline figure.

    Pass `rs` to run that ruleset on copies of the documents; leave it out to score documents that
    have already been run (`d["row"]`, `d["cells"]` present)."""
    tr = _trial(rs, columns, train) if rs is not None else [d for d in train if "row" in d]
    ho = _trial(rs, columns, holdout) if rs is not None else [d for d in holdout if "row" in d]
    acc_t, by_t = summarise(tr, columns) if tr else (0.0, {})
    out = {
        "docs": len(tr) + len(ho),
        "passed": sum(1 for d in tr + ho if (d.get("row") or 0) >= SC.CELL_OK),
        "accuracy": acc_t,
        "trained_on": len(tr),
        "held_out": len(ho),
        "training_by_column": by_t,
    }
    if ho:
        acc_h, by_h = summarise(ho, columns)
        out["holdout_accuracy"] = acc_h
        out["by_column"] = by_h
        out["note"] = f"{acc_h:.1%} on {len(ho)} document{'s' if len(ho) != 1 else ''} it was not built from ({acc_t:.1%} on the {len(tr)} it was)"
    else:
        out["holdout_accuracy"] = None
        out["by_column"] = by_t
        out["note"] = f"too few to check — all {len(tr)} document{'s' if len(tr) != 1 else ''} were used to build the profile"
    return out


def honest(backtest: Optional[dict]) -> Optional[float]:
    """The number a person may be shown: the held-out one, or None when there isn't one."""
    bt = backtest or {}
    v = bt.get("holdout_accuracy")
    return float(v) if isinstance(v, (int, float)) else None


def backtest(rs_new: dict, columns: list[dict], docs: list[Doc], only_key: Optional[str] = None) -> dict:
    """Run rs_new on copies of docs. A regression = a cell that scored ≥0.9 before and now has a
    different value or scores <0.9. Returns {docs, passed, accuracy, by_column, regressions, docs_out}."""
    trial = [{"id": d["id"], "ordinal": d.get("ordinal"), "name": d.get("name"), "filename": d.get("filename"), "spans": d["spans"], "cells": copy.deepcopy(d.get("cells") or {})} for d in docs]
    run_docs(rs_new, columns, trial)
    regressions = []
    keys = [only_key] if only_key else [c["key"] for c in columns]
    for old, new in zip(docs, trial):
        for k in keys:
            oc, nc = (old.get("cells") or {}).get(k), new["cells"].get(k)
            if oc and oc.get("status") == "ok" and (oc.get("score") or 0) >= SC.CELL_OK:
                if not nc or (nc.get("score") or 0) < SC.CELL_OK or str(nc.get("value")) != str(oc.get("value")):
                    regressions.append({"doc": old["id"], "column": k, "was": oc.get("value"), "now": nc.get("value") if nc else None})
    acc, by_col = summarise(trial, columns)
    bad_docs = {r["doc"] for r in regressions}
    return {"docs": len(docs), "passed": len(docs) - len(bad_docs), "accuracy": acc, "by_column": by_col, "regressions": regressions, "docs_out": trial}


# ---- diagnosis -------------------------------------------------------------------------------
def diagnose(columns: list[dict], docs: list[Doc]) -> list[dict]:
    """Per column below 90%: which docs fail and why (plain English). Filename / formula columns
    have no rules to fix; optional columns with nothing on the page are `absent`, not failing."""
    out = []
    for c in P.readable(columns):
        failing = [d for d in docs if SC._cell_score(d["cells"].get(c["key"])) < SC.CELL_OK]
        if not failing:
            continue
        reasons = []
        kinds = set()
        for d in failing:
            cell = d["cells"].get(c["key"]) or {}
            if cell.get("value") is None:
                kinds.add("miss")
                near = _near_label(c, d["spans"])
                reasons.append(f"PDF #{d.get('ordinal')}: nothing found for '{c['label']}'" + (f"; nearest text: \"{near}\"" if near else ""))
            elif cell.get("quality") == "fuzzy":
                kinds.add("fuzzy")
                reasons.append(f"PDF #{d.get('ordinal')}: label only roughly matched ('{cell.get('anchor')}') on page {cell.get('page')}")
            elif c["key"] in (d.get("validators") or {}).get("failing", []):
                kinds.add("arith")
                reasons.append(f"PDF #{d.get('ordinal')}: {d['validators'].get('note')}")
            elif not cell.get("parsed", True):
                kinds.add("parse")
                reasons.append(f"PDF #{d.get('ordinal')}: found the label but '{cell.get('raw')}' is not a {c['ctype']}")
            elif not cell.get("provenance", True):
                kinds.add("provenance")
                reasons.append(f"PDF #{d.get('ordinal')}: value '{cell.get('value')}' is not printed verbatim on the page")
            else:
                kinds.add("other")
                reasons.append(f"PDF #{d.get('ordinal')}: low score ({cell.get('score')}) — {cell.get('note')}")
        out.append({"key": c["key"], "label": c["label"], "docs": [d["id"] for d in failing], "kinds": sorted(kinds), "reasons": reasons[:6]})
    return out


def _near_label(col: dict, spans: dict) -> str:
    for label in P.candidate_labels(col)[:6]:
        anchors = X.find_anchors(spans, label, fuzzy=True, limit=1)
        if anchors:
            return anchors[0]["line"]["text"][:90]
    return ""


# ---- patching --------------------------------------------------------------------------------
def patch(rs: dict, columns: list[dict], docs: list[Doc], diag: list[dict], ask_model: Optional[Callable[[str, str, dict], Optional[dict]]] = None, family: str = "") -> tuple[Optional[dict], str, list[str]]:
    """Return (new_ruleset or None, reason, changes). `ask_model(task, prompt, schema)` returns dict or None."""
    work = copy.deepcopy(rs)
    changes: list[str] = []
    by_key = {c["key"]: c for c in columns}
    arith_keys = [d["key"] for d in diag if "arith" in d["kinds"]]

    for d in diag:
        col = by_key[d["key"]]
        failing_docs = [x for x in docs if x["id"] in d["docs"]]
        field = work["fields"].setdefault(col["key"], {"rules": []})
        # 1. re-anchor fuzzy matches
        if "fuzzy" in d["kinds"]:
            for fd in failing_docs:
                cell = fd["cells"].get(col["key"]) or {}
                anchor = cell.get("anchor")
                if anchor and cell.get("method"):
                    orig = next((r for r in field["rules"] if r.get("id") == cell["method"]), None)
                    if orig and orig.get("type", "").startswith("label"):
                        new = dict(orig, id=f"{orig['id']}x", label=anchor, quality="exact")
                        cand = _with_rule(work, col["key"], new, front=True)
                        bt = backtest(cand, columns, docs, only_key=col["key"])
                        if not bt["regressions"]:
                            work = cand
                            field = work["fields"][col["key"]]
                            changes.append(f"{col['label']}: anchored on the printed label '{anchor}' instead of a rough match")
                            break
        # 2. re-author on the failing docs
        if "miss" in d["kinds"] or "parse" in d["kinds"] or "provenance" in d["kinds"]:
            tried = {str(r.get("label", "")).lower() for r in field["rules"]}
            others = set()
            for c in columns:
                if c["key"] != col["key"]:
                    others |= {" ".join(X.norm_tokens(l)) for l in P.candidate_labels(c)}
            new_rules, _ = P.author_column(col, [x["spans"] for x in failing_docs], family, other_labels=others)
            new_rules = [r for r in new_rules if r.get("type") != "const" and str(r.get("label", "")).lower() not in tried]
            for r in new_rules[:2]:
                r["id"] = f"r{_next_id(field)}"
                cand = _with_rule(work, col["key"], r, front=True)
                bt = backtest(cand, columns, docs, only_key=col["key"])
                if not bt["regressions"] and bt["by_column"].get(col["key"], 0) > SC.column_accuracy((x["cells"] for x in docs), [col])[col["key"]]:
                    work = cand
                    field = work["fields"][col["key"]]
                    pages = {(x["cells"].get(col["key"]) or {}).get("page") for x in bt["docs_out"] if x["id"] in d["docs"]} - {None}
                    where = {"label_right": "right of", "label_below": "below", "label_left": "left of"}.get(r["type"], r["type"])
                    changes.append(f"{col['label']}: added a rule — value {where} '{r.get('label')}'" + (f" (page {', '.join(str(p) for p in sorted(pages))})" if pages else ""))
                    break

    # 3. money combination when the figures don't add up
    if arith_keys:
        roles = SC.money_roles(columns)
        role_keys = [k for k in (roles.get("excl"), roles.get("vat"), roles.get("total")) if k]
        options: dict[str, list[dict]] = {}
        for k in role_keys:
            col = by_key[k]
            fails = [x for x in docs if k in (x.get("validators") or {}).get("failing", [])] or docs
            fresh, _ = P.author_column(col, [x["spans"] for x in fails], family)
            cur = [r for r in work["fields"].get(k, {}).get("rules", []) if r.get("type") != "derived"]
            seen = set()
            opts = []
            for r in cur + fresh:
                sig = (r.get("type"), str(r.get("label", "")).lower(), r.get("pick"))
                if sig not in seen:
                    seen.add(sig)
                    opts.append(r)
            options[k] = opts[:4]
        best, best_score = None, _arith_pass(work, columns, docs)
        for combo in itertools.product(*[options[k] for k in role_keys]):
            cand = copy.deepcopy(work)
            for k, r in zip(role_keys, combo):
                derived = [x for x in cand["fields"].get(k, {}).get("rules", []) if x.get("type") == "derived"]
                cand["fields"][k] = {"rules": [dict(r, id=r.get("id") or "m1")] + derived}
            score = _arith_pass(cand, columns, docs)
            if score > best_score:
                best, best_score = cand, score
        if best is not None:
            bt = backtest(best, columns, docs)
            if not bt["regressions"] or bt["accuracy"] > summarise(docs, columns)[0]:
                work = best
                changes.append("Money columns: chose the label/amount combination where VAT and total add up on the most documents")

    # 4. the model
    if ask_model is not None:
        still = [d for d in diagnose(columns, _trial(work, columns, docs)) if d["key"] in {x["key"] for x in diag}]
        if still:
            prompt = _repair_prompt(work, columns, docs, still)
            out = ask_model("profile_repair", prompt, P.REPAIR_SCHEMA)
            if out and isinstance(out.get("rules"), dict):
                cand = copy.deepcopy(work)
                added = 0
                for key, rules in out["rules"].items():
                    if key not in {s["key"] for s in still}:
                        continue
                    cleaned = P.clean_rules(rules, prefix=f"axl-{key}-")
                    for r in cleaned:
                        cand = _with_rule(cand, key, r, front=True)
                        added += 1
                if added:
                    bt = backtest(cand, columns, docs)
                    if not bt["regressions"] and bt["accuracy"] >= summarise(docs, columns)[0]:
                        work = cand
                        note = str(out.get("notes") or "").strip()
                        changes.append("C-EXA: " + (note or f"{added} rule(s) rewritten"))

    if not changes:
        return None, "", []
    return work, "; ".join(changes), changes


def _trial(rs, columns, docs):
    t = [{"id": d["id"], "ordinal": d.get("ordinal"), "name": d.get("name"), "filename": d.get("filename"), "spans": d["spans"], "cells": {}} for d in docs]
    run_docs(rs, columns, t)
    return t


def _arith_pass(rs: dict, columns: list[dict], docs: list[Doc]) -> int:
    n = 0
    for d in docs:
        cells = X.extract(rs, columns, d["spans"], filename=d.get("filename") or d.get("name"))
        v = SC.run_validators(rs, columns, cells)
        if v["vat_ok"] is not False and v["sum_ok"] is not False and any(x is True for x in (v["vat_ok"], v["sum_ok"])):
            n += 1
    return n


def _with_rule(rs: dict, key: str, rule: dict, front: bool) -> dict:
    cand = copy.deepcopy(rs)
    field = cand["fields"].setdefault(key, {"rules": []})
    rules = [r for r in field["rules"] if not (r.get("type") == rule.get("type") and str(r.get("label", "")).lower() == str(rule.get("label", "")).lower() and r.get("pick") == rule.get("pick"))]
    field["rules"] = ([rule] + rules) if front else (rules + [rule])
    return cand


def _next_id(field: dict) -> int:
    return len(field.get("rules", [])) + 1


def _repair_prompt(rs: dict, columns: list[dict], docs: list[Doc], diag: list[dict]) -> str:
    by_key = {c["key"]: c for c in columns}
    parts = ["COLUMNS FAILING:"]
    for d in diag:
        col = by_key[d["key"]]
        parts.append(f"- {col['key']} ({col['label']}, {col['ctype']}): current rules {json.dumps(rs['fields'].get(col['key'], {}).get('rules', []))}")
        for r in d["reasons"]:
            parts.append(f"    {r}")
        for did in d["docs"][:2]:
            doc = next((x for x in docs if x["id"] == did), None)
            if doc:
                near = _context_lines(col, doc["spans"])
                parts.append(f"    text near the expected label in PDF #{doc.get('ordinal')}:\n      " + "\n      ".join(near))
    parts.append("FULL RULESET: " + json.dumps(rs))
    return "\n".join(parts)


def _context_lines(col: dict, spans: dict, radius: int = 3) -> list[str]:
    for label in P.candidate_labels(col)[:6]:
        anchors = X.find_anchors(spans, label, fuzzy=True, limit=1)
        if anchors:
            line = anchors[0]["line"]
            lines = page_lines(spans, line["page"])
            i = lines.index(line)
            return [f"[p{l['page']}] {l['text']}" for l in lines[max(0, i - radius) : i + radius + 1]]
    return [f"[p{l['page']}] {l['text']}" for l in spans["lines"][:8]]
