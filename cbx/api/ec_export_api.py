"""1.0.99: Entity Compliance's Export — GET /api/ec/export: the companies this person sees, as one Excel workbook.

Entity Compliance is a tool folder (tools/entity-compliance); this is the one Core route its grid page's Export
button calls (a `links` block whose item points at /api/ec/export). The door is the tool's own: whoever may
open the tool (its access.roles, or the doc-watch grant) may export what they see; the rows are the tool's tables
seen through their own scopes, as on the grid. Registered through cbx/api/engine_api.py's `extra_routers`
(registry.py is left as it is).
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security.deps import current_user

router = APIRouter(prefix="/api/ec", tags=["entity-compliance"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/export")
def export(request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    from ..docwatch import ec_export as EX
    from .engine_api import _tool  # here, not at the top: engine_api imports this module to carry its router

    t = _tool(db, me, EX.SLUG, request)  # 404 without the folder, 403 (audited) without access
    if not all(n in t.data for n in ("companies", "doc_types", "documents")):
        raise HTTPException(404, "Entity Compliance's tables aren't there.")
    name, data = EX.export(db, me, t)
    return StreamingResponse(io.BytesIO(data), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})
