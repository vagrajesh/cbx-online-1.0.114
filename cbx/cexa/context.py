"""What is actually in CBX right now, written out for C-EXA.

The gap this closes, from two real requests on 17 Sep 2026:

* Otsile: *"Please add Nigeria Exxon mobile. it's a new entity"* — C-EXA asked back
  "what exactly is 'Nigeria Exxon mobile' — a client, a product, a phone number, a division?"
* Otsile: *"on Mozambique folder please add nuit"* — C-EXA asked "what is 'nuit' in your system?"

Both are plain requests from someone who works in Entity Compliance all day. "Entity" is not a
vague word here, it is a table; Mozambique is one of its rows. C-EXA asked because her prompt told
her who she was and nothing about what she runs, so the app's own nouns reached the model as
ordinary English words.

A person who has to explain what "entity" means to the system that stores entities stops filing
requests. So every prompt that reads a person's words now carries this: the tools that exist, and
the names actually in the database. It is built fresh each time — a list of names is cheap, and a
stale vocabulary is worse than none.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

MAX_NAMES = 60  # enough for every company CBRE Excellerate has; a cap so one prompt can't run away


def _names(db: Session, model, col="name", limit: int = MAX_NAMES, **filters) -> list[str]:
    try:
        q = db.query(getattr(model, col))
        for k, v in filters.items():
            q = q.filter(getattr(model, k) == v)
        return [r[0] for r in q.order_by(getattr(model, col)).limit(limit).all() if r[0]]
    except Exception:  # noqa: BLE001 — a missing table must never stop C-EXA answering
        return []


def _line(label: str, names: list[str], total: Optional[int] = None) -> str:
    if not names:
        return f"- {label}: none yet."
    n = total if total is not None else len(names)
    more = f" (+{n - len(names)} more)" if n > len(names) else ""
    return f"- {label} ({n}): " + ", ".join(names) + more + "."


def vocabulary(db: Session) -> str:
    """The nouns this app uses and the values behind them, as a short block for a prompt."""
    from ..models import MasterSheet, Team, Template, Tool
    from ..models.docs import DocType, Entity

    ents = _names(db, Entity)
    n_ents = 0
    try:
        n_ents = db.query(Entity).count()
    except Exception:  # noqa: BLE001
        n_ents = len(ents)

    out = [
        "WHAT CBX HOLDS RIGHT NOW — these are the system's own words, not general English. "
        "When someone uses one of them, they mean the thing below; do not ask them what it is.",
        "",
        "- ENTITY (also called a company) = one row of the Entity Compliance grid: a legal entity whose "
        "certificates we track. Adding one is a normal, small job — it creates a cell in every column.",
        "- DOCUMENT TYPE (also called a column) = one column of that grid, e.g. a tax clearance. Adding one "
        "gives every entity a new cell.",
        "- CELL = one entity's one document: the file, its expiry date, and its history.",
        "- FOLDER = a folder in the Data warehouse, where files live. On the Entity Compliance page people "
        "often say 'folder' when they mean an entity's page — check which they mean before assuming.",
        "- TEMPLATE / BATCH / PROFILE = PDF Studio: the columns to pull out, a run of PDFs, and the learned "
        "rules for one layout.",
        "- MASTER SHEET = the running spreadsheet that batch results are appended to.",
        "- TOOL = something a person is granted access to, listed below.",
        "",
        _line("Entities on the grid", ents, n_ents),
        _line("Document type columns", _names(db, DocType)),
        _line("Teams", _names(db, Team)),
        _line("PDF templates", _names(db, Template)),
        _line("Master sheets", _names(db, MasterSheet)),
        _line("Tools", _names(db, Tool)),
    ]
    return "\n".join(out)


def for_request(db: Session) -> str:
    """The block used when C-EXA reads something a person wrote, with the rule that matters most.

    The instruction is deliberately about what NOT to ask. The failure was never that C-EXA guessed —
    she is built not to — it was that she spent a person's request asking what its plainest word meant.
    """
    return (
        vocabulary(db)
        + "\n\n"
        + "Reading someone's words:\n"
        "- If a name they use matches something above, it IS that thing. Say so and get on with it.\n"
        "- If a name is new but the KIND is clear ('add X, it's a new entity'), that is not a thing to ask "
        "about — it is the job. Confidence should be high: adding an entity or a column is a small, safe, "
        "reversible action.\n"
        "- Country-specific paperwork is normal here and is not a mystery word. NUIT is Mozambique's tax "
        "number, COIDA and BBBEE are South African, a CR number is Gulf company registration. Treat an "
        "unfamiliar certificate name as a document type you have not met yet, not as something to interrogate.\n"
        "- Only ask about what genuinely changes what you would do. One question, not four. If you would do "
        "the same thing either way, do not ask at all.\n"
    )
