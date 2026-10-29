"""C-EXA's periodic routines. Registered with cbx.jobs and imported by the scheduler on start.
Each one opens its own session and is idempotent, so a missed or repeated tick does no harm."""
from __future__ import annotations

import logging

from ..db import session_scope
from ..jobs import routine

log = logging.getLogger("cbx.cexa.routines")


@routine("cexa-brief", 300)
def morning_brief() -> None:
    from .team import send_briefs, snapshot_loads

    with session_scope() as db:
        made = send_briefs(db)
        snapshot_loads(db)
        if made:
            log.info("morning brief sent to %d people", len(made))


@routine("cexa-board", 300)
def board() -> None:
    from .developer import recover_stuck_builds, review_board

    with session_scope() as db:
        stuck = recover_stuck_builds(db)
        if stuck:
            log.warning("C-EXA handed %d stuck build(s) back to Noticed", len(stuck))
        notes = review_board(db)
        if notes:
            log.info("C-EXA commented on %d cards", len(notes))


@routine("cexa-notice", 900)
def notice() -> None:
    from .developer import notice as _notice

    with session_scope() as db:
        made = _notice(db)
        if made:
            log.info("C-EXA noticed %d things", len(made))
