"""Deterministic stand-in for the model. Used in tests and when Ollama is not installed yet,
so the whole app is exercisable end-to-end without a GPU. Each task returns plausible,
schema-valid output derived from the prompt text (never random)."""
from __future__ import annotations

import json
import re


def answer(task: str, system: str, user: str, *, want_json: bool) -> str:
    if task == "support_see":  # 1.0.98: Iris without a vision model sees nothing (cbx/support/vision.py)
        return json.dumps({"what": "other", "system": "", "text_seen": "", "error": "", "sap": {}, "site_or_asset": "", "confidence": 0.0})
    if task in ("ap_capture", "ap_capture_senior"):  # 1.0.98: no eyes in stub mode: a reading with nothing in it (cbx/ap/capture.py sends it up the line)
        return json.dumps({"is_invoice": True, "credit_note": False, "supplier": "", "bill_to": "", "invoice_no": "", "invoice_date": "", "lines": [], "confidence": {}})
    if task == "ap_match":  # 1.0.98: Mia's small-model match: no company in stub mode
        return json.dumps({"code": None, "reason": "stub"})
    if task in ("ap_sort", "ap_sap_check"):  # 1.0.98
        return json.dumps({})
    if task.startswith(("ceo_", "team_")):  # C-EXA's team (cbx/cexa/ceo.py) — deterministic answers built from the INPUT block
        from . import team_stub

        return team_stub.answer(task, user)
    if task in ("chat", "brief", "dev", "aside"):  # C-EXA-as-team-member tasks live in cbx.cexa.brain
        from .brain import stub_answer

        out = stub_answer(task, system, user, want_json=want_json)
        if out is not None:
            return out
    if task == "assess":
        text = user.lower()
        money = bool(re.search(r"\b(penalt\w*|rand|cost\w*|paid late|late payment|on hold|fine[sd]?)\b|\br\s?\d", text))
        recurring = any(w in text for w in ("every", "weekly", "monthly", "month-end", "daily", "each"))
        people = len(re.findall(r"\b(and|team|department|finance|procurement|everyone)\b", text))
        score = 1 + int(money) + int(recurring) + (1 if people >= 2 else 0)
        imp = ["low", "medium", "high", "critical"][min(score, 3) if score > 0 else 0]
        scanned = "scan" in text or "mixed layout" in text
        conf = 0.41 if scanned else (0.92 if recurring and money else 0.86)
        return json.dumps(
            {
                "importance": imp,
                "importance_reason": ("Money at risk, recurring, more than one team." if imp in ("high", "critical") else "Recurring but contained; no money at risk that I can see." if imp == "medium" else "One person, occasional. Worth doing, not urgent."),
                "confidence": conf,
                "confidence_reason": ("Scanned, mixed layouts — I can't promise the read rate without samples; a person should lead." if scanned else "I already hold this data and the steps are arithmetic and matching I do elsewhere."),
                "would_ask": "Which date starts the clock; who gets the result; what counts as done.",
                "wont_guess": "Nothing — every value will come from a source row I can point to.",
                "title": _title(user),
            }
        )
    if task == "interview":
        if "[follow-up]" in system:
            return json.dumps(_followup(user))
        return json.dumps(_interview_step(user))
    if task == "interview_suggest":
        return json.dumps(_suggest(user))
    if task == "profile_author":
        return json.dumps({"notes": "Drafted from the sample pages. Labels anchor on the left; amounts are the last money-looking token on the same line.", "rules": {}})
    if task == "profile_repair":
        return json.dumps({"notes": "Widened the label match and added a page-2 fallback.", "changes": [], "rules": {}})
    if task == "residual_read":
        return json.dumps({"value": "", "quote": "", "found": False})
    if task == "read_expiry":
        return json.dumps(_read_expiry(user))
    if task == "brief":
        return "Overnight: nothing broke. All scheduled workflows ran. I'm ready when you are."
    if task == "dev":
        return json.dumps({"title": "Proposal", "body": "Stub proposal", "evidence": ""})
    if task == "chat":
        return "I'm running in stub mode (no model connected yet), so I can't think properly. Everything else in CBX works; connect Ollama and I'll be myself."
    return "{}" if want_json else "ok"


def _read_expiry(user: str) -> dict:
    """The prompt carries the heuristic's own suggestion (HEURISTIC: {...}); the stub simply agrees with it,
    or says found=false when there was none — exactly what a careful model would do with no better evidence."""
    m = re.search(r"HEURISTIC:\s*(\{.*?\})\s*(?:\n|$)", user, flags=re.S)
    if m:
        try:
            h = json.loads(m.group(1))
            if h.get("found") and h.get("expiry"):
                return {"found": True, "expiry": h["expiry"], "quote": h.get("quote", ""), "doc_type": h.get("doc_type", ""), "entity": h.get("entity", "")}
        except json.JSONDecodeError:
            pass
    return {"found": False, "expiry": "", "quote": "", "doc_type": "", "entity": ""}


def _title(user: str) -> str:
    """First sentence of the problem, trimmed to ~60 characters at a word boundary."""
    m = re.search(r"problem[:\s]+(.+)", user, flags=re.I)
    t = (m.group(1) if m else user).strip().split("\n")[0]
    t = re.split(r"(?<=[a-z0-9])[.;!?]\s", t + " ", maxsplit=1)[0].strip()
    from ..util import clip

    t = clip(t, 60)
    return (t[:1].upper() + t[1:]).rstrip(".")


def _interview_step(user: str) -> dict:
    """The interview prompt carries the transcript; we count the person's turns to decide the
    next question. Turn 1 is the answer to "Ready?", so it never becomes a rule."""
    n = user.count("USER:")
    qs = [
        "How do I recognise the documents this applies to — is there a code, a name, or a folder they land in?",
        "When something fails a check, should I still add it to the result, or hold it back separately?",
        "If the same item appears twice, is that always a duplicate, or can one be a corrected re-issue?",
        "Are there amounts I should check — VAT, totals — and what do I do if they're off: flag or fix?",
        "Last one: who gets the result, how often should it run, and what does 'done' look like to you?",
    ]
    last = user.split("USER:")[-1].strip() if n else ""
    rule = _rule_from(last) if n >= 2 and len(last) > 8 else ""
    happy = any(w in last.lower() for w in ("happy", "that's all", "thats all", "we're done", "done"))
    if n - 1 >= len(qs) or (n >= 2 and happy):
        return {"done": True, "question": "", "rule": rule, "summary": "Every run: identify the documents, run the checks you gave me, hold back anything that fails into an exceptions list, deduplicate keeping the newest, and send one message to the people you named."}
    return {"done": False, "question": qs[max(n - 1, 0)], "rule": rule, "summary": ""}


def _followup(user: str) -> dict:
    last = user.split("USER:")[-1].strip()
    rule = _rule_from(last) if re.search(r"\b(should|must|always|never|instead|change|from now on)\b", last, flags=re.I) else ""
    reply = "Noted — I've added that as a rule and told the team; the workflow will be re-checked." if rule else "Noted. I've attached that to the ticket so the team sees it."
    return {"done": True, "question": reply, "rule": rule, "summary": ""}


def _rule_from(answer: str) -> str:
    a = answer.strip().rstrip(".")
    return a[:1].upper() + a[1:]


def _rules_in(user: str) -> list[str]:
    return [m[1].strip() for m in re.findall(r"^R(\d+)[:.]\s*(.+)$", user, flags=re.M)]


def _suggest(user: str) -> dict:
    rules = _rules_in(user)
    n = len(rules)
    qs = []
    if n:
        qs.append({"why": f"Because of R{n}", "text": f"You said “{rules[-1][:60]}” — does that hold every time, or are there exceptions I should know about?"})
    qs.append({"why": "Gap", "text": "If I can't read a value from a document, do you want the row flagged, held back, or left out?"})
    if n >= 2:
        qs.append({"why": "Overlap", "text": f"R1 and R{n} could pull in different directions on the same document — which one wins?"})
    else:
        qs.append({"why": "Gap", "text": "Who gets the result, how often should it run, and what does 'done' look like?"})
    conf = min(0.95, 0.41 + 0.05 * n)
    gaps = "Recipients and cadence not confirmed yet." if n < 4 else "Nearly there — one confirmation on exceptions and I can build it."
    return {"questions": qs[:3], "confidence": round(conf, 2), "gaps": gaps}
