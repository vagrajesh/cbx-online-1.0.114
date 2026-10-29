# 1.0.104: new — Builder R (SPEC-1104 "Needs you gets only the person-actionable items, one small line each, linking into
# Controls"). Any Valuence control adds its lines here, without touching the Needs-you route or page.
"""Needs you → the Controls lines. A control module registers a function `fn(db, user) -> [{key, sub, params, text}]`
with `@register`; GET /api/ap/needs returns every line under "controls" and the AP page shows each as one small line
that opens Controls → <sub> with <params>. A function that fails is logged and left out (Needs you still opens)."""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.orm import Session

from ..models import User

logger = logging.getLogger("cbx.ap.controls")
LINES: list[Callable[[Session, User], list]] = []


def register(fn: Callable[[Session, User], list]) -> Callable[[Session, User], list]:
    if fn not in LINES:
        LINES.append(fn)
    return fn


def lines(db: Session, user: User) -> list[dict]:
    out: list[dict] = []
    for fn in LINES:
        try:
            got = fn(db, user) or []
        except Exception:  # noqa: BLE001 — one control's lines never stop Needs you
            logger.exception("needs-you lines %s", getattr(fn, "__qualname__", fn))
            db.rollback()
            continue
        out += [x for x in got if isinstance(x, dict) and x.get("text")]
    return out
