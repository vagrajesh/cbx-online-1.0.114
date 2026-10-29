"""1.0.109 (round 2, 23 Oct) — the ticket page's own routes (Screen): see cbx/support/rich.py, askaxl.py, msgread.py.

    POST /api/support/tickets/{id}/message-rich   {text, html, to_requester, wait, pics[]}   the reply editor (staff): words, marks
                                                  and pictures where they were pasted — Send to requester or Add note
    POST /api/support/tickets/{id}/reply-rich     {text, html, pics[]}   the requester's own reply on their ticket (the lead gets it)
    GET  /api/support/tickets/{id}/axl            Ask AXL: on/off and the chat (staff and admins who may see the ticket)
    POST /api/support/tickets/{id}/axl            {text}   ask
    POST /api/support/tickets/{id}/axl/{line}/card {do: send|confirm|close_silently|discard, text}   act on AXL's card
    GET  /api/support/tickets/{id}/files/{n}/preview   what the preview shows: kind, and the words of a text/CSV/mail file
    GET  /api/support/tickets/{id}/files/{n}/inline    a picture or a PDF shown inside CBX (same access as the download)
    GET  /api/support/tickets/{id}/files/{n}/page/{p}  a PDF page as a picture (when the browser has no PDF viewer)
    GET  /api/support/find?q=                     quick find: "28" or "T-0028" jumps; other words search what the person may see
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import SupportMessage, SupportTicket, User
from ..security.deps import current_user, require_human
from ..support import askaxl as AX
from ..support import core as C
from ..support import own as OWN
from ..support import rich as R
from ..util import read_capped
from .support_api import _do, _mine, _ticket, require_support

router = APIRouter(prefix="/api/support", tags=["support"])

PICTURE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}
TEXT_EXT = {".txt", ".csv", ".log"}
OFFICE_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
TEXT_MAX = 200 * 1024
PDF_PAGES = 10


def _seen_staff(db: Session, me: User, t: SupportTicket) -> bool:
    return C.is_staff(db, me) and C.staff_may_see(db, me, t)


async def _pics(pics: list[UploadFile]) -> list[tuple[str, bytes]]:
    if len(pics or []) > R.MAX_PICS:
        raise HTTPException(400, f"At most {R.MAX_PICS} pictures in one message.")
    out = []
    for f in pics or []:
        data = await read_capped(f, R.MAX_PIC, "picture")
        try:
            out.append(R.picture(f.filename or "picture", data))
        except ValueError as e:
            raise HTTPException(400, str(e))
    return out


def _words(html: str, text: str, n_pics: int) -> str:
    """The message as plain words (what search, the mail's plain part and every older screen read): from the safe HTML."""
    try:
        safe, _ = R.clean_html(html, {k: k + 1 for k in range(n_pics)})
    except ValueError as e:
        raise HTTPException(400, str(e))
    words = R.plain(safe) if safe else (text or "").strip()
    if not words and n_pics:
        words = "[picture]"
    return words[:8000]


@router.post("/tickets/{tid}/message-rich")
async def message_rich(tid: int, text: str = Form(""), html: str = Form(""), to_requester: bool = Form(True), wait: bool = Form(False),
                       pics: list[UploadFile] = File(default=[]), db: Session = Depends(get_db), me: User = Depends(require_support),
                       _h: User = Depends(require_human)):
    t = _ticket(db, tid)
    if not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    if to_requester and not C.can_work(db, me, t):
        raise HTTPException(403, "Take it first: only the person it is assigned to, or the owner or an admin, writes to the requester. Add a note instead.")
    got = await _pics(pics)
    words = _words(html, text, len(got))

    def files(m: SupportMessage) -> None:
        R.attach(db, t, m, html, got, me.name or "", author_id=me.id, note=not to_requester)

    # `wait`: the person's question is "waiting for the requester" — the follow-up clock runs (cbx/support/followup.py)
    _do(lambda: C.staff_message(db, t, me, words, to_requester, files=files, wait=bool(wait and to_requester)))
    if got:
        log(db, "support_ticket_files", actor=me, target=f"ticket:{C.ref(t)}", detail=f"{len(got)} picture(s) in a {'message' if to_requester else 'note'}")
    db.flush()
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.post("/tickets/{tid}/reply-rich")
async def reply_rich(tid: int, text: str = Form(""), html: str = Form(""), pics: list[UploadFile] = File(default=[]), db: Session = Depends(get_db),
                     me: User = Depends(require_human)):
    t = _ticket(db, tid)
    if not _mine(t, me) or C.is_removed(t):
        raise HTTPException(404, "No such ticket.")
    got = await _pics(pics)
    words = _words(html, text, len(got))

    def files(m: SupportMessage) -> None:
        R.attach(db, t, m, html, got, me.name or "", author_id=None, note=False)

    _do(lambda: C.requester_reply(db, t, words, author=me, files=files))
    if t.status == "new":
        C.start_reading(db, t)
    db.refresh(t)
    return C.ticket_out(db, t, full=True)


# ---- Ask AXL ------------------------------------------------------------------------------------------------------------------
class AskIn(BaseModel):
    text: str


class CardIn(BaseModel):
    do: str
    text: str = ""


def _staff_ticket(db: Session, me: User, tid: int) -> SupportTicket:
    t = _ticket(db, tid)
    if not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    return t


@router.get("/tickets/{tid}/axl")
def axl_chat(tid: int, db: Session = Depends(get_db), me: User = Depends(require_support)):
    t = _staff_ticket(db, me, tid)
    on, why = AX.status()
    return {"on": on, "why_off": why, "chat": AX.chat_out(db, t), "can_assign": C.can_assign(me), "can_work": C.can_work(db, me, t)}


@router.post("/tickets/{tid}/axl")
def axl_ask(tid: int, body: AskIn, db: Session = Depends(get_db), me: User = Depends(require_support), _h: User = Depends(require_human)):
    t = _staff_ticket(db, me, tid)
    try:
        chat = AX.ask(db, t, me, body.text)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"chat": chat}


@router.post("/tickets/{tid}/axl/{line}/card")
def axl_card(tid: int, line: int, body: CardIn, db: Session = Depends(get_db), me: User = Depends(require_support), _h: User = Depends(require_human)):
    t = _staff_ticket(db, me, tid)
    try:
        chat = _do(AX.act, db, t, me, line, body.do, body.text)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"chat": chat, "ticket": C.ticket_out(db, t, staff=True, full=True, viewer=me)}


# ---- previews -----------------------------------------------------------------------------------------------------------------
def _file(db: Session, me: User, tid: int, n: int) -> tuple[SupportTicket, Path, str, dict]:
    """Exactly the download's access (support_api.get_file): the requester on their own (never removed) ticket, never a file a desk
    person mailed in or an invisible lead's; staff who may see the ticket."""
    t = _ticket(db, tid)
    staff = _seen_staff(db, me, t)
    if not ((_mine(t, me) and not C.is_removed(t)) or staff):
        raise HTTPException(404, "No such ticket.")
    f = next((x for x in C.files_seen(db, t, staff) if x.get("n") == n), None)
    p = C.file_path(t, n) if f else None
    if p is None:
        raise HTTPException(404, "That file isn't there.")
    return t, p, p.name.split("_", 1)[-1], f


def _kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext.lstrip(".") in PICTURE_TYPES:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    if ext in (".eml", ".msg"):
        return "mail"
    if ext in OFFICE_EXT:
        return "office"
    return "other"


def _decode(b: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _eml(data: bytes) -> dict:
    from email import policy
    from email.parser import BytesParser

    try:
        m = BytesParser(policy=policy.default).parsebytes(data)
        part = m.get_body(preferencelist=("plain", "html"))
        text = part.get_content() if part is not None else ""
        if part is not None and part.get_content_type() == "text/html":
            text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", text)
            text = re.sub(r"(?s)<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text))
        files = [a.get_filename() or "attachment" for a in m.iter_attachments()][:20]
        return {"subject": str(m.get("subject", "")), "from": str(m.get("from", "")), "to": str(m.get("to", "")), "cc": str(m.get("cc", "")),
                "date": str(m.get("date", "")), "text": re.sub(r"[ \t]+", " ", str(text)).strip(), "attachments": files}
    except Exception:  # noqa: BLE001 — a broken mail: name, size and Download
        return {}


@router.get("/tickets/{tid}/files/{n}/preview")
def preview(tid: int, n: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t, p, name, f = _file(db, me, tid, n)
    kind = _kind(name)
    out = {"n": n, "name": name, "size": p.stat().st_size, "kind": kind}
    if kind == "text":
        with p.open("rb") as fh:
            raw = fh.read(TEXT_MAX + 1)
        out.update(text=_decode(raw[:TEXT_MAX]), cut=len(raw) > TEXT_MAX, csv=name.lower().endswith(".csv"))
    elif kind == "mail":
        data = p.read_bytes()[:20 * 1024 * 1024]
        if name.lower().endswith(".eml"):
            got = _eml(data)
        else:
            from ..support import msgread

            got = msgread.read(data)
        if got:
            got["text"] = (got.get("text") or "")[:TEXT_MAX]
            out["mail"] = got
    elif kind == "pdf":
        out["pages"] = _pdf_count(p)
    return out


def _pdf_count(p: Path) -> int:
    try:
        import fitz

        with fitz.open(str(p)) as doc:
            return 0 if doc.needs_pass else doc.page_count
    except Exception:  # noqa: BLE001
        return 0


@router.get("/tickets/{tid}/files/{n}/inline")
def inline(tid: int, n: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    """A picture or a PDF shown inside CBX (the preview's frame) — never anything else inline."""
    t, p, name, f = _file(db, me, tid, n)
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in PICTURE_TYPES:
        return FileResponse(p, media_type=PICTURE_TYPES[ext], filename=name, content_disposition_type="inline",
                            headers={"Content-Security-Policy": "default-src 'none'", "Cache-Control": "private, max-age=3600"})
    if ext == "pdf":
        # the browser's own PDF viewer, framed by CBX only (same origin); the PDF can run nothing of its own
        return FileResponse(p, media_type="application/pdf", filename=name, content_disposition_type="inline",
                            headers={"Content-Security-Policy": "default-src 'none'; object-src 'self'; frame-ancestors 'self'", "X-Frame-Options": "SAMEORIGIN",
                                     "Cache-Control": "private, max-age=600"})
    raise HTTPException(415, "Only pictures and PDFs are shown inside CBX — download it to open it.")


@router.get("/tickets/{tid}/files/{n}/page/{page}")
def pdf_page(tid: int, n: int, page: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    """A PDF page as a picture — the fallback when the browser can't show the PDF in a frame."""
    t, p, name, f = _file(db, me, tid, n)
    if not name.lower().endswith(".pdf") or not (1 <= page <= PDF_PAGES):
        raise HTTPException(404, "No such page.")
    try:
        import fitz

        with fitz.open(str(p)) as doc:
            if doc.needs_pass or page > doc.page_count:
                raise HTTPException(404, "No such page.")
            pg = doc[page - 1]
            zoom = min(2.0, 1600 / max(pg.rect.width, pg.rect.height, 1))
            png = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "That PDF can't be shown — download it to open it.")
    return Response(png, media_type="image/png", headers={"Content-Security-Policy": "default-src 'none'", "Cache-Control": "private, max-age=600"})


# ---- quick find -----------------------------------------------------------------------------------------------------------------
REF = re.compile(r"^\s*(?:#|t-?\s*)?0*(\d{1,7})\s*$", re.I)


def _like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _snippet(text: str, words: list[str], n: int = 160) -> str:
    t = " ".join((text or "").split())
    low = t.lower()
    at = min([i for i in (low.find(w) for w in words) if i >= 0] or [0])
    start = max(0, at - n // 3)
    s = t[start:start + n]
    return ("…" if start else "") + s + ("…" if start + n < len(t) else "")


@router.get("/find")
def find(q: str = "", db: Session = Depends(get_db), me: User = Depends(require_support)):
    q = " ".join((q or "").split())[:120]
    if not q:
        return {"jump": None, "results": []}
    admin = C.can_assign(me)
    base = db.query(SupportTicket)
    base = base if admin else base.filter(C.live())  # the owner and admins also find a removed one (it says so)
    if OWN.restricted(db, me):
        base = base.filter(OWN.seen_filter(me))
    m = REF.match(q)
    if m:
        t = base.filter(SupportTicket.id == int(m.group(1))).first()
        return {"jump": {"id": t.id, "ref": C.ref(t)} if t else None, "results": []}
    words = [w.lower() for w in q.split() if len(w) >= 2][:5]
    if not words:
        return {"jump": None, "results": []}
    staff_whos = ("requester", "cexa", "staff", "note") + (("admin",) if admin else ())
    hits: dict[int, list] = {}
    for w in words:
        lk = _like(w)
        rows = base.filter(or_(SupportTicket.title.ilike(lk, escape="\\"), SupportTicket.requester_name.ilike(lk, escape="\\"),
                               SupportTicket.requester_email.ilike(lk, escape="\\"), SupportTicket.summary.ilike(lk, escape="\\"),
                               SupportTicket.description.ilike(lk, escape="\\"))).with_entities(SupportTicket.id).limit(400).all()
        ids = {r[0] for r in rows}
        mids = base.join(SupportMessage, SupportMessage.ticket_id == SupportTicket.id).filter(
            SupportMessage.who.in_(staff_whos), SupportMessage.text.ilike(lk, escape="\\")).with_entities(SupportTicket.id).limit(400).all()
        ids |= {r[0] for r in mids}
        for i in ids:
            hits.setdefault(i, []).append(w)
    best = sorted((i for i, ws in hits.items() if len(set(ws)) == len(set(words))), reverse=True)[:30]  # every word somewhere on the ticket
    out = []
    for t in db.query(SupportTicket).filter(SupportTicket.id.in_(best or [0])).order_by(SupportTicket.id.desc()).all():
        where = []
        for field, label in (("title", "Title"), ("requester_name", "Requester"), ("requester_email", "E-mail"), ("summary", "Summary"), ("description", "Description")):
            v = getattr(t, field) or ""
            if any(w in v.lower() for w in words):
                where.append({"field": label, "snippet": _snippet(v, words)})
        if len(where) < 2:
            for msg in db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who.in_(staff_whos)).order_by(SupportMessage.id.desc()).limit(80):
                if any(w in (msg.text or "").lower() for w in words):
                    where.append({"field": "Message" if msg.who != "note" else "Note", "snippet": _snippet(msg.text, words)})
                    break
        d = C.ticket_out(db, t, staff=True, viewer=me)
        out.append({"id": t.id, "ref": d["ref"], "title": d["title"], "requester": d["requester"], "status": d["status"], "word": d["word"],
                    "removed": bool(d.get("removed")), "hits": where[:3]})
    return {"jump": None, "results": out, "words": words}
