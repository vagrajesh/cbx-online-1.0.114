"""1.0.103 (CBX-Connect 2.10.8's cbx/api/busy.py): which bot is busy with which document, for the pages' live chips
(cbx/busy.py). Registered through cbx/api/ap_api.py's `extra_routers` (registry.py stays as it is).

    GET /api/ap/busy        everyone with the AP Tool (a team tool: everyone sees the team's folders) — the files and
                            captures a bot is on now, and each invoice's place in a reader's queue
    GET /api/support/busy   the support desk: the managers (owner, admins) see every ticket being read; the rest of the
                            desk only the tickets they may see (theirs and the free ones); nobody else

The pages poll these every 5 s while anything is busy, and stop when nothing is."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import busy
from ..ap import capture as C
from ..ap import service as S
from ..db import get_db, utcnow
from ..models import SupportTicket, User
from ..security.deps import current_user, require_tool

router = APIRouter(tags=["busy"])


@router.get("/api/ap/busy")
def ap_busy(me: User = Depends(require_tool(S.SLUG)), db: Session = Depends(get_db)):
    return C.busy_view(db)


def ticket_marks(db: Session, me: User) -> dict:
    from ..support import core as SC

    if not SC.is_staff(db, me):
        raise HTTPException(403, "This is for the support team.")
    may = getattr(SC, "staff_may_see", None)
    now, rows = utcnow(), {}
    for t in db.query(SupportTicket).filter(SupportTicket.busy_bot != "").order_by(SupportTicket.id).limit(500).all():
        if SC.can_assign(me) or (may is not None and may(db, me, t)):
            rows[t.id] = busy.out(t, now)
    return {"tickets": rows, "any": bool(rows), "now": now.isoformat(timespec="seconds")}


@router.get("/api/support/busy")
def support_busy(me: User = Depends(current_user), db: Session = Depends(get_db)):
    return ticket_marks(db, me)
