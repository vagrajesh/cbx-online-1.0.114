"""Mail through Outlook on this server (1.0.66) — until the SMTP mailer works.

CBX drives the Outlook that is open on this PC (COM, pywin32), using the mailbox named in
CBX_OUTLOOK_MAILBOX (the CBX-Connect mailbox, added to that Outlook):

    send()        a message leaves from that mailbox (CBX_MAIL_TRANSPORT=outlook routes all of CBX's mail here);
                  with `thread`, as a reply in that mail's own thread (1.0.71: the chain is underneath)
    save_draft()  saved in Drafts for a person to check, never sent (1.0.71); send_draft / drop_draft
    fetch_new()   the newest mails in its Inbox that CBX has not read yet (support tickets by mail)
    mark_read()   CBX puts the category "CBX" on a mail it has read; the mail itself stays where it is

Nothing is deleted, moved or forwarded. Outlook has to be open and signed in; if it is not, every call
says so in a sentence and the caller carries on. Each call opens its own COM session, so the routine
thread and a web request never share one.

The test suite (and any non-Windows machine) plugs in a stand-in: `BACKEND = Fake()`.
"""
from __future__ import annotations

import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..config import settings

log = logging.getLogger("cbx.outlook")
CATEGORY = "CBX"
MAX_ATTACH = 10 * 1024 * 1024
PR_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
PR_HEADERS = "http://schemas.microsoft.com/mapi/proptag/0x007D001F"
PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F"
PR_ATTACHMENT_HIDDEN = "http://schemas.microsoft.com/mapi/proptag/0x7FFE000B"
MAX_FILES = 12  # 1.0.98: as a ticket holds (real attachments first, pasted pictures fill what is left)
IMAGE = re.compile(r"\.(png|jpe?g|gif|bmp|emz|wmz)$", re.I)


def keep_attachment(name: str, content_id: str = "", size: int = 0, hidden: bool = False) -> bool:
    """1.0.84: a signature logo or a picture pasted in the text is not an attachment. A real mail carries up to
    a dozen of them (image001.png …) and they used to push the spreadsheet that mattered past the limit.
    Kept: every document; a picture only when it was attached as a file (no content id, not hidden) and is
    not a tiny one."""
    if not name:
        return False
    if not IMAGE.search(name):
        return True
    if hidden or content_id:
        return False
    if re.match(r"(?i)^(image\d{3}|~wrd\d+|outlook-\w+)\.", name):
        return False
    return size >= 15_000


# ---- 1.0.98 (from CBX-Connect 2.10.3 bridge/outlook_bridge.py): pictures pasted in a mail — a screenshot goes to the desk (Iris
# reads it), a signature logo does not. Real attachments always come first; pasted pictures only fill what is left, at most 6.
# 1.0.109 (T-0028: Tanuja's SAP screenshot, pasted in her reply, never reached the ticket): a pasted picture had to be 30 KB —
# a flat SAP screen is often 10–20 KB — and a picture Outlook names image001.png without a content id (a rich-text mail) was
# dropped as "not an attachment" without being looked at; neither said so. Now a picture is kept unless it is clearly a
# signature or a logo: tiny in pixels (either side under 120) or in bytes (under 5 KB), a banner (wider than 4:1), or the same
# picture this sender put in 2+ earlier mails. Size alone never drops a screenshot, and every picture or file left out is
# reported with its reason (`left_out`: an admin-only line under the message on the ticket).
INLINE_MIN = 5_000  # 1.0.109: under this many bytes a pasted picture is a logo (was 30 KB — it dropped flat screenshots)
MIN_SIDE = 120  # 1.0.109: either side under this many pixels: a logo or an icon
INLINE_MAX = 6  # Iris reads at most 6 pictures a time
BANNER_RATIO = 4.0
SIG_SENDERS = 300  # senders remembered
SIG_HASHES = 40  # pictures remembered per sender
SIG_MAILS = 3  # mails remembered per picture
_SIG: Optional[dict] = None
PREVIEW = re.compile(r"(?i)\.(emz|wmz)$")  # Outlook's preview of a pasted Office drawing (the picture itself comes as well)
PASTED_NAME = re.compile(r"(?i)^(image\d{3}|~wrd\d+|outlook-\w+)\.")


def pasted_picture(name: str, content_id: str = "", size: int = 0, hidden: bool = False) -> bool:
    """A picture pasted in the text (a content id, hidden, or Outlook's own image001.png name). 1.0.109: any size — whether
    it is a logo is decided on the picture itself (signature_like)."""
    return bool(name) and bool(IMAGE.search(name)) and bool(hidden or content_id or PASTED_NAME.match(name)) and not PREVIEW.search(name)


def kind_of(name: str, content_id: str = "", hidden: bool = False) -> str:
    """1.0.109: doc (a real attachment), picture (a picture attached as a file), pasted (pasted in the text), preview (.emz/.wmz)."""
    if not IMAGE.search(name or ""):
        return "doc"
    if PREVIEW.search(name):
        return "preview"
    return "pasted" if (hidden or content_id or PASTED_NAME.match(name)) else "picture"


def _sig_file() -> Path:
    return settings.path("support") / "signature-pictures.json"


def sig_memory() -> dict:
    """{sender: {sha: [mail ids]}} — which pasted pictures each sender sent, in which mails (DATA_DIR/support/signature-pictures.json)."""
    global _SIG
    if _SIG is None:
        import json

        try:
            got = json.loads(_sig_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = {}
        _SIG = got if isinstance(got, dict) else {}
    return _SIG


def save_sig_memory() -> None:
    import json

    try:
        f = _sig_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(sig_memory()), encoding="utf-8")
    except OSError as e:
        log.warning("outlook: the signature memory was not kept: %s", e)


def remember_picture(sender: str, sha: str, mail_id: str) -> int:
    """Note the picture in this mail; returns in how many EARLIER mails this sender sent the same picture."""
    mem = sig_memory()
    per = mem.pop(sender, None) or {}
    mem[sender] = per  # most recent sender last (the oldest are dropped first)
    mails = [m for m in per.pop(sha, []) if m != mail_id]
    earlier = len(mails)
    per[sha] = (mails + [mail_id])[-SIG_MAILS:]
    while len(per) > SIG_HASHES:
        per.pop(next(iter(per)))
    while len(mem) > SIG_SENDERS:
        mem.pop(next(iter(mem)))
    return earlier


def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """(width, height) of a PNG, GIF, BMP or JPEG from its header. None: unknown."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if data[:2] == b"BM":
            return abs(int.from_bytes(data[18:22], "little", signed=True)), abs(int.from_bytes(data[22:26], "little", signed=True))
        if data[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = int.from_bytes(data[i + 2:i + 4], "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
                i += 2 + seg
    except (IndexError, ValueError):
        return None
    return None


def signature_like(data: bytes, sender: str, mail_id: str, remember: bool = True) -> str:
    """Why a picture is a signature, a logo or a banner and stays out ('' = keep it). 1.0.109: tiny in bytes (under 5 KB) or in
    pixels (either side under 120), wider than 4:1, or the same picture this sender put in 2+ earlier mails. Size alone never
    drops a screenshot. `remember`: note it in the sender's signature memory (pictures pasted in the text)."""
    import hashlib

    earlier = remember_picture(sender or "?", hashlib.sha256(data).hexdigest(), mail_id or "?") if remember else 0
    if len(data) < INLINE_MIN:
        return "looked like a signature logo (tiny)"
    wh = image_size(data)
    if wh and wh[0] and wh[1] and min(wh) < MIN_SIDE:
        return f"looked like a signature logo ({wh[0]}×{wh[1]} pixels)"
    if wh and wh[1] and wh[0] / wh[1] > BANNER_RATIO:
        return f"looked like a banner ({wh[0]}×{wh[1]} pixels)"
    if earlier >= 2:
        return "the same picture as in this sender's earlier mails (a signature)"
    return ""


def pick(atts: list, sender: str, mail_id: str) -> tuple[list, list, list]:
    """1.0.109: which of a mail's attachments reach the ticket. `atts`: [{name, size, cid, hidden, save}] (save() → the bytes,
    or raises). Real attachments first (up to MAX_FILES), then pictures attached as files, then pictures pasted in the text
    (at most INLINE_MAX). Returns (files [(name, bytes)], inline [names pasted in the text], left_out [{name, why}]) — nothing
    is ever left out without its reason."""
    files: list = []
    inline: list = []
    left: list = []
    groups: dict = {"doc": [], "picture": [], "pasted": []}
    for a in atts:
        name = a.get("name") or ""
        if not name:
            left.append({"name": "(no name)", "why": "an embedded object without a name"})
            continue
        if (a.get("size") or 0) > MAX_ATTACH:
            left.append({"name": name, "why": "over 10 MB"})
            continue
        k = kind_of(name, a.get("cid") or "", bool(a.get("hidden")))
        if k == "preview":
            left.append({"name": name, "why": "Outlook's preview of a pasted drawing (the picture itself is kept)"})
            continue
        groups[k].append(a)
    remembered = False
    for k in ("doc", "picture", "pasted"):
        for a in groups[k]:
            name = a["name"]
            if len(files) >= MAX_FILES:
                left.append({"name": name, "why": f"more than {MAX_FILES} files in one mail"})
                continue
            if k == "pasted" and len(inline) >= INLINE_MAX:
                left.append({"name": name, "why": f"more than {INLINE_MAX} pictures pasted in one mail"})
                continue
            try:  # 1.0.98 fix: one attachment Outlook can't save is skipped — the mail is still read
                data = a["save"]()
            except Exception as e:  # noqa: BLE001 — Outlook's own "Cannot save the attachment"
                log.warning("outlook: attachment %r of mail %s not saved, skipped: %s", name, mail_id, e)
                left.append({"name": name, "why": "Outlook could not save it"})
                continue
            if not data:
                left.append({"name": name, "why": "empty"})
                continue
            if k in ("picture", "pasted"):
                remembered = remembered or k == "pasted"
                why = signature_like(data, sender, mail_id, remember=k == "pasted")
                if why:
                    left.append({"name": name, "why": why})
                    continue
            if k == "pasted":
                inline.append(name)
            files.append((name, data))
    if remembered:
        save_sig_memory()
    return files, inline, left


# 1.0.73: CBX marks the mails it sends to a requester ("T-0012:requester") so the next one can reply to it —
# a named property in PS_PUBLIC_STRINGS; it stays on the copy in Sent Items, the requester never sees it
PR_CBX_MARK = "http://schemas.microsoft.com/mapi/string/{00020329-0000-0000-C000-000000000046}/cbx-mark"
REF_TOKEN = re.compile(r"\[T-\d{1,7}\]")

BACKEND: Any = None  # a stand-in (tests); None = the real Outlook


class OutlookError(Exception):
    pass


def available() -> tuple[bool, str]:
    if BACKEND is not None:
        return True, "Outlook (stand-in)."
    if sys.platform != "win32":
        return False, "Outlook can only be used when CBX runs on Windows."
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False, "pywin32 is not installed in this Python (pip install pywin32), so CBX cannot reach Outlook."
    c = _cfg()
    if not c["send_store"]:
        return False, "No mailbox chosen to send from — pick one in Mailer → Mail set-up."
    return True, f"Through Outlook on this PC, from {c['send_address'] or c['send_store']}."


class _Com:
    """The real Outlook. One instance per call (COM is per thread)."""

    def __enter__(self):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            self.app = win32com.client.Dispatch("Outlook.Application")
            self.ns = self.app.GetNamespace("MAPI")
        except Exception as e:  # noqa: BLE001
            pythoncom.CoUninitialize()
            raise OutlookError(f"Outlook did not answer — is it open and signed in on this PC? ({e})")
        return self

    def __exit__(self, *a):
        import pythoncom

        self.app = self.ns = None
        pythoncom.CoUninitialize()

    def _store(self, name: str = ""):
        want = (name or _cfg()["send_store"] or "").strip().lower()
        for i in range(1, self.ns.Folders.Count + 1):
            f = self.ns.Folders.Item(i)
            if f.Name.strip().lower() == want:
                return f
        for i in range(1, self.ns.Folders.Count + 1):
            f = self.ns.Folders.Item(i)
            if want and want.split("@")[0] in f.Name.strip().lower():
                return f
        raise OutlookError(f"Outlook on this PC has no mailbox called {(name or _cfg()['send_store'])!r}. Add it in Outlook (File → Account settings), or pick another in Mailer → Mail set-up.")

    def _inbox(self):
        root = self._store()
        name = settings.outlook_folder or "Inbox"
        try:
            return root.Folders.Item(name)
        except Exception:  # noqa: BLE001
            raise OutlookError(f"The mailbox {settings.outlook_mailbox} has no folder called {name!r}.")

    def _compose(self, to: list[str], subject: str, body: str, html: Optional[str], reply_to: str, thread: str = "", mark: str = ""):
        """A new message — or, with `thread` (the mail it answers), a reply in that mail's own thread so the
        whole chain is underneath. Only ever addressed to `to`: Reply() picks the sender, To is set anyway."""
        import html as H

        if thread:
            m = self.ns.GetItemFromID(thread).Reply()
            mine = html or ("<div>" + H.escape(body).replace("\n", "<br>") + "</div>")
            old = m.HTMLBody or ""
            b = re.search(r"<body[^>]*>", old, re.I)
            m.HTMLBody = old[: b.end()] + mine + "<br>" + old[b.end():] if b else mine + "<br>" + old
        else:
            m = self.app.CreateItem(0)
            if html:
                m.HTMLBody = html
            else:
                m.Body = body
        m.To = "; ".join(to)
        m.Subject = reply_subject(m.Subject or "", subject) if thread else subject
        if reply_to:
            m.ReplyRecipients.Add(reply_to)
        if mark:
            try:
                m.PropertyAccessor.SetProperty(PR_CBX_MARK, mark)
            except Exception as e:  # noqa: BLE001 — the next mail then starts a new chain, nothing worse
                log.info("outlook: mark not set: %s", e)
        c = _cfg()
        want = (c["send_address"] or c["send_store"] or "").strip().lower()
        acct = None
        for i in range(1, self.ns.Accounts.Count + 1):
            a = self.ns.Accounts.Item(i)
            if (a.SmtpAddress or "").lower() == want:
                acct = a
        if acct is not None:
            m._oleobj_.Invoke(*(64209, 0, 8, 0, acct))  # SendUsingAccount (set through Invoke: pywin32 can't assign it directly)
        elif want:
            m.SentOnBehalfOfName = c["send_address"] or c["send_store"]
        return m

    def send(self, to: list[str], subject: str, body: str, html: Optional[str], reply_to: str, thread: str = "", mark: str = "", attachments=None,
             inline=None, info=None) -> None:
        m = self._compose(to, subject, body, html, reply_to, thread, mark)
        _attach(m, attachments)  # 1.0.109: the requester's pictures on the lead's mail
        _attach(m, inline)  # 1.0.109 (round 2): pictures inside the message — hidden attachments with their Content-ID
        if info is not None:  # 1.0.109: Outlook's conversation of this mail (a new one has it once saved); its Message-ID comes later
            try:
                conv = m.ConversationID or ""
                if not conv:
                    m.Save()
                    conv = m.ConversationID or ""
                info["conversation"] = conv
            except Exception as e:  # noqa: BLE001 — the mail goes anyway
                log.info("outlook: conversation not read: %s", e)
        m.Send()

    def sent_ids(self, mark: str, scan: int = 400) -> list[dict]:
        """1.0.109: the mails CBX sent marked `mark` (Sent Items of the sending mailbox), newest first: {message_id, conversation, at}."""
        out = []
        try:
            sent = self._store().Store.GetDefaultFolder(5).Items
            sent.Sort("[SentOn]", True)
            token = mark.split(":", 1)[0]
            n = 0
            for it in sent:
                n += 1
                if n > scan:
                    break
                try:
                    if token not in (it.Subject or "") or it.PropertyAccessor.GetProperty(PR_CBX_MARK) != mark:
                        continue
                    out.append({"message_id": it.PropertyAccessor.GetProperty(PR_MESSAGE_ID) or "", "conversation": it.ConversationID or "",
                                "at": _local(it.SentOn)})
                except Exception:  # noqa: BLE001 — no mark on it
                    continue
        except Exception as e:  # noqa: BLE001
            log.info("outlook: sent items not searched: %s", e)
        return out

    def thread_for(self, mark: str, inbound: list[str], message_ids: list[str], boxes: list[dict]) -> str:
        """The newest mail of a ticket's chain to reply to: the requester's own mails (by Outlook id, or by
        Message-ID for mail read before 1.0.71) and the mails CBX sent them (marked `mark`, in Sent Items)."""
        best: list = [None, ""]

        def consider(it, when):
            if when is not None and (best[0] is None or when > best[0]):
                best[0], best[1] = when, it.EntryID

        for eid in inbound[:5]:
            try:
                it = self.ns.GetItemFromID(eid)
                consider(it, _local(it.ReceivedTime))
            except Exception:  # noqa: BLE001 — moved or deleted
                pass
        if best[0] is None and message_ids:
            for b in boxes:
                try:
                    items = self._folder(b["store"], b.get("folder") or "Inbox").Items
                except Exception:  # noqa: BLE001
                    continue
                for mid in message_ids[:3]:
                    try:
                        it = items.Restrict('@SQL="%s" = \'%s\'' % (PR_MESSAGE_ID, mid.replace("'", "''"))).GetFirst()
                        if it is not None:
                            consider(it, _local(it.ReceivedTime))
                    except Exception:  # noqa: BLE001
                        pass
        if mark:
            try:
                sent = self._store().Store.GetDefaultFolder(5).Items  # olFolderSentMail of the sending mailbox
                sent.Sort("[SentOn]", True)
                token = mark.split(":", 1)[0]
                n = 0
                for it in sent:
                    n += 1
                    if n > 400:
                        break
                    try:
                        if token not in (it.Subject or ""):
                            continue
                        if it.PropertyAccessor.GetProperty(PR_CBX_MARK) != mark:
                            continue  # e.g. the mail to the lead — never in the requester's chain
                        consider(it, _local(it.SentOn))
                        break  # newest first: the first match is the newest
                    except Exception:  # noqa: BLE001 — no mark on it
                        continue
            except Exception as e:  # noqa: BLE001
                log.info("outlook: sent items not searched: %s", e)
        return best[1]

    # ---- 1.0.71: drafts a person checks first --------------------------------------------------------

    def save_draft(self, to: list[str], subject: str, body: str, html: Optional[str], reply_to: str, thread: str = "", mark: str = "") -> str:
        """Saved, never sent — into the sending mailbox's Drafts where Outlook allows it. Returns its id."""
        m = self._compose(to, subject, body, html, reply_to, thread, mark)
        m.Save()
        try:
            dest = self._store().Store.GetDefaultFolder(16)  # olFolderDrafts of the sending mailbox
            if dest.EntryID != m.Parent.EntryID:
                m = m.Move(dest)
        except Exception as e:  # noqa: BLE001 — stays in the default Drafts
            log.info("outlook: draft left in the default Drafts: %s", e)
        return m.EntryID

    def _draft(self, entry_id: str):
        try:
            it = self.ns.GetItemFromID(entry_id)
        except Exception:  # noqa: BLE001
            return None
        try:
            if it.Sent:
                return None
            if it.Parent.DefaultItemType != 0 or (it.Parent.Name or "").lower() in ("deleted items", "sent items"):
                return None
        except Exception:  # noqa: BLE001
            pass
        return it

    def draft_alive(self, entry_id: str) -> bool:
        return self._draft(entry_id) is not None

    def send_draft(self, entry_id: str) -> None:
        it = self._draft(entry_id)
        if it is None:
            raise OutlookError("That draft isn't in Outlook any more — it was sent or deleted there.")
        it.Send()

    def drop_draft(self, entry_id: str) -> None:
        it = self._draft(entry_id)
        if it is not None:
            it.Delete()  # CBX's own unsent draft only — never a mail someone sent

    def fetch_new(self, limit: int) -> list[dict]:
        items = self._inbox().Items
        items.Sort("[ReceivedTime]", True)
        out = []
        n = 0
        for it in items:
            n += 1
            if n > 300 or len(out) >= limit:
                break
            try:
                if it.Class != 43:  # mail items only (not meeting requests, reports)
                    continue
                if CATEGORY in [c.strip() for c in (it.Categories or "").split(",")]:
                    continue
                out.append(self._read(it))
            except Exception as e:  # noqa: BLE001 — one odd item never stops the rest
                log.warning("outlook: skipped an item: %s", e)
        return out

    def _read(self, it) -> dict:
        sender = ""
        try:
            if it.SenderEmailType == "EX":
                ex = it.Sender.GetExchangeUser()
                sender = ex.PrimarySmtpAddress if ex else ""
            else:
                sender = it.SenderEmailAddress
        except Exception:  # noqa: BLE001
            sender = it.SenderEmailAddress or ""
        pa = it.PropertyAccessor
        try:
            mid = pa.GetProperty(PR_MESSAGE_ID)
        except Exception:  # noqa: BLE001
            mid = ""
        try:
            headers = pa.GetProperty(PR_HEADERS)
        except Exception:  # noqa: BLE001
            headers = ""
        # 1.0.109: every attachment is looked at (pick): real ones first, then pictures — a pasted screenshot of any size is kept
        # unless it is clearly a signature or a logo, and whatever is left out is said (left_out)
        tmp = Path(tempfile.mkdtemp(prefix="cbx-mail-"))
        sid = str(mid or it.EntryID or "")
        atts = []
        for i in range(1, it.Attachments.Count + 1):
            a = it.Attachments.Item(i)
            name = a.FileName or ""
            kind = getattr(a, "Type", 1)
            if kind == 5:  # an attached mail: kept as a .msg the lead can open
                name = (re.sub(r"[^\w.\- ]", "_", a.DisplayName or "attached mail")[:100] + ".msg") if not name.lower().endswith(".msg") else name
            elif kind == 6 and not IMAGE.search(name):  # 1.0.109: a picture pasted in a rich-text mail (an OLE object): saved, then sniffed
                name = f"pasted{i}.bmp"
            try:
                cid = a.PropertyAccessor.GetProperty(PR_ATTACH_CONTENT_ID) or ""
            except Exception:  # noqa: BLE001
                cid = ""
            try:
                hidden = bool(a.PropertyAccessor.GetProperty(PR_ATTACHMENT_HIDDEN))
            except Exception:  # noqa: BLE001
                hidden = False
            if kind == 6:
                hidden = True

            def save(a=a, name=name, i=i, ole=kind == 6):
                p = tmp / re.sub(r"[^\w.\- ]", "_", name or f"file{i}")[:120]
                try:
                    a.SaveAsFile(str(p))
                    data = p.read_bytes()
                finally:
                    p.unlink(missing_ok=True)
                if ole and image_size(data) is None:
                    raise OutlookError("an embedded object, not a picture")
                return data

            atts.append({"name": re.sub(r"[^\w.\- ]", "_", name)[:120] if name else "", "size": a.Size, "cid": cid, "hidden": hidden, "save": save})
        try:
            files, inline, left_out = pick(atts, (sender or "").strip().lower(), sid)
        finally:
            try:
                tmp.rmdir()
            except OSError:
                pass
        try:
            conv = it.ConversationID or ""
        except Exception:  # noqa: BLE001
            conv = ""
        return {"id": mid or it.EntryID, "entry_id": it.EntryID, "conversation": conv, "sender": (sender or "").strip().lower(), "sender_name": it.SenderName or "",
                "subject": it.Subject or "", "body": it.Body or "", "headers": headers or "", "files": files, "inline": inline,
                "left_out": left_out, "recipients": _recipients(it)}  # 1.0.109

    # ---- 1.0.69: any mailbox in this Outlook (My mail) ----------------------------------------------

    def list_stores(self) -> list[dict]:
        accts = {}
        for i in range(1, self.ns.Accounts.Count + 1):
            a = self.ns.Accounts.Item(i)
            try:
                accts[(a.DeliveryStore.DisplayName or "").lower()] = a.SmtpAddress or ""
            except Exception:  # noqa: BLE001
                pass
        out = []
        for i in range(1, self.ns.Folders.Count + 1):
            f = self.ns.Folders.Item(i)
            name = f.Name
            has_inbox = True
            try:
                f.Folders.Item("Inbox")
            except Exception:  # noqa: BLE001
                has_inbox = False
            out.append({"name": name, "address": accts.get(name.lower(), name if "@" in name else ""), "has_inbox": has_inbox})
        return out

    def _folder(self, store: str, folder: str):
        root = self._store(store)
        try:
            return root.Folders.Item(folder or "Inbox")
        except Exception:  # noqa: BLE001
            raise OutlookError(f"The mailbox {store} has no folder called {folder!r}.")

    def fetch_since(self, store: str, folder: str, since_local, limit: int, skip: set, skip_read: bool = False) -> list[dict]:
        items = self._folder(store, folder).Items
        items.Sort("[ReceivedTime]", True)
        out = []
        n = 0
        for it in items:
            n += 1
            if n > 2000 or len(out) >= limit:
                break
            try:
                if it.Class != 43:
                    continue
                got = _local(it.ReceivedTime)
                if got < since_local:
                    break  # newest first: everything after this is older
                if it.EntryID in skip:
                    continue
                cats = [c.strip() for c in (it.Categories or "").split(",")]
                if skip_read and CATEGORY in cats:
                    continue
                m = self._read(it)
                m["cbx"] = CATEGORY in cats
                m["received_local"] = got.isoformat()
                out.append(m)
            except Exception as e:  # noqa: BLE001
                log.warning("outlook: skipped an item: %s", e)
        return out

    def get(self, entry_id: str) -> dict:
        it = self.ns.GetItemFromID(entry_id)
        m = self._read(it)
        m["received_local"] = _local(it.ReceivedTime).isoformat()
        try:
            m["store"] = it.Parent.Store.DisplayName
        except Exception:  # noqa: BLE001
            m["store"] = ""
        return m

    def tag(self, entry_id: str, categories: list[str], flag: bool) -> None:
        it = self.ns.GetItemFromID(entry_id)
        cats = [c.strip() for c in (it.Categories or "").split(",") if c.strip()]
        for c in categories:
            if c not in cats:
                cats.append(c)
                try:
                    self.ns.Categories.Add(c)
                except Exception:  # noqa: BLE001 — already in the master list
                    pass
        it.Categories = ", ".join(cats)
        if flag:
            try:
                it.MarkAsTask(4)  # olMarkNoDate: a follow-up flag
            except Exception:  # noqa: BLE001
                pass
        it.Save()

    def move(self, entry_id: str, store: str, folder: str) -> None:
        root = self._store(store)
        try:
            dest = root.Folders.Item(folder)
        except Exception:  # noqa: BLE001
            dest = root.Folders.Add(folder)
        self.ns.GetItemFromID(entry_id).Move(dest)

    def draft_reply(self, entry_id: str, text: str) -> None:
        import html as H

        r = self.ns.GetItemFromID(entry_id).Reply()
        mine = "<div>" + H.escape(text).replace("\n", "<br>") + "</div><br>"
        body = r.HTMLBody or ""
        m = re.search(r"<body[^>]*>", body, re.I)
        r.HTMLBody = body[: m.end()] + mine + body[m.end():] if m else mine + body
        r.Save()  # into that mailbox's Drafts — never sent

    def mark_read(self, entry_id: str) -> None:
        it = self.ns.GetItemFromID(entry_id)
        cats = [c.strip() for c in (it.Categories or "").split(",") if c.strip()]
        if CATEGORY not in cats:
            it.Categories = ", ".join(cats + [CATEGORY])
        it.Save()


PR_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"


def _recipients(it) -> list[str]:
    """1.0.109: the To and Cc addresses of a mail (lower case) — a desk person's reply that already went to the requester is
    shown as their reply, not passed on again. [] when Outlook can't say."""
    out: list[str] = []
    try:
        rs = it.Recipients
        for i in range(1, rs.Count + 1):
            r = rs.Item(i)
            addr = ""
            try:
                addr = r.PropertyAccessor.GetProperty(PR_SMTP_ADDRESS) or ""
            except Exception:  # noqa: BLE001
                try:
                    ae = r.AddressEntry
                    ex = ae.GetExchangeUser() if ae is not None and ae.Type == "EX" else None
                    addr = (ex.PrimarySmtpAddress if ex else "") or r.Address or ""
                except Exception:  # noqa: BLE001
                    addr = getattr(r, "Address", "") or ""
            if "@" in addr and addr.lower() not in out:
                out.append(addr.strip().lower())
    except Exception:  # noqa: BLE001 — no recipients to read: the old behaviour
        return []
    return out


def _cfg() -> dict:
    from .. import mailcfg

    return mailcfg.get()


def _local(dt):
    """Outlook's time as this PC's local time, without a zone (pywin32 labels local time as UTC)."""
    from datetime import datetime

    return datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _session():
    return BACKEND if BACKEND is not None else _Com()


def reply_subject(theirs: str, ours: str) -> str:
    """A reply keeps the chain's own subject ("RE: Portal broken") so mail programs keep it in one
    conversation; the ticket number is added when it is missing (replies are matched on it), and a test
    run's "[test]" stays in front."""
    s = (theirs or "").strip() or ours
    tok = REF_TOKEN.search(ours or "")
    if tok and tok.group(0) not in s:
        s = f"{s} {tok.group(0)}"
    if (ours or "").startswith("[test]") and not s.startswith("[test]"):
        s = "[test] " + s
    return s[:250]


def thread_for(mark: str, inbound: list[str], message_ids: list[str], boxes: list[dict]) -> str:
    _can()
    with _session() as s:
        return s.thread_for(mark, inbound, message_ids, boxes)


def _attach(m, attachments) -> None:
    """1.0.109: files on an Outlook mail (Attachments.Add copies the file; the temporary copy is removed at once). 1.0.109 (round 2):
    an item (name, bytes, cid) is a picture inside the message: its Content-ID is set (the HTML's <img src="cid:…">) and it is hidden."""
    if not attachments:
        return
    tmp = Path(tempfile.mkdtemp(prefix="cbx-out-"))
    try:
        for i, item in enumerate(attachments):
            name, data = item[0], item[1]
            p = tmp / (f"{i}_" + re.sub(r"[^\w.\- ]", "_", name or "file")[:120])
            p.write_bytes(data)
            try:
                a = m.Attachments.Add(str(p), 1, 0, name)  # olByValue
                if len(item) > 2 and item[2]:
                    try:
                        a.PropertyAccessor.SetProperty(PR_ATTACH_CONTENT_ID, item[2])
                        a.PropertyAccessor.SetProperty(PR_ATTACHMENT_HIDDEN, True)
                    except Exception as e:  # noqa: BLE001 — it still goes, as a file
                        log.info("outlook: inline picture not marked: %s", e)
            finally:
                p.unlink(missing_ok=True)
    finally:
        try:
            tmp.rmdir()
        except OSError:
            pass


def send(to: list[str], subject: str, body: str, *, html: Optional[str] = None, reply_to: str = "", thread: str = "", mark: str = "",
         attachments=None, inline=None, info: Optional[dict] = None) -> tuple[bool, str]:
    """1.0.109: `info` (a dict) gets what the sent mail is known by (its conversation; a stand-in Outlook also its Message-ID)."""
    ok, why = available()
    if not ok:
        return False, why
    att = list(attachments or [])  # 1.0.109: [(name, bytes)]
    inl = {"inline": list(inline)} if inline else {}  # 1.0.109 (round 2): [(name, bytes, cid)]
    if info is not None:
        inl["info"] = info
    try:
        with _session() as s:
            try:
                s.send(list(to), subject, body, html, reply_to, thread, mark, *([att] if att or inl else []), **inl)
            except Exception:  # noqa: BLE001 — the mail it answers is gone: a new message instead
                if not thread:
                    raise
                s.send(list(to), subject, body, html, reply_to, "", mark, *([att] if att or inl else []), **inl)
        return True, f"Sent through Outlook to {', '.join(to)}."
    except OutlookError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 — Outlook's own errors, reported never raised
        log.warning("outlook send failed: %s", e)
        return False, f"Outlook could not send it: {e}"


def fetch_new(limit: int = 20) -> list[dict]:
    ok, why = available()
    if not ok:
        raise OutlookError(why)
    with _session() as s:
        return s.fetch_new(limit)


def mark_read(entry_id: str) -> None:
    with _session() as s:
        s.mark_read(entry_id)


def sent_ids(mark: str) -> list[dict]:
    """1.0.109: what CBX's sent mails marked `mark` are known by (support/inbox.backfill_sent)."""
    ok, _ = available()
    if not ok:
        return []
    with _session() as s:
        return s.sent_ids(mark)


def _can() -> None:
    if BACKEND is not None:
        return
    if sys.platform != "win32":
        raise OutlookError("Outlook can only be used when CBX runs on Windows.")
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        raise OutlookError("pywin32 is not installed in this Python, so CBX cannot reach Outlook.")


def list_stores() -> list[dict]:
    _can()
    with _session() as s:
        return s.list_stores()


def fetch_since(store: str, folder: str, since_local, limit: int = 30, skip: Optional[set] = None, skip_read: bool = False) -> list[dict]:
    _can()
    with _session() as s:
        return s.fetch_since(store, folder, since_local, limit, skip or set(), skip_read)


def get(entry_id: str) -> dict:
    """One mail by its Outlook id (the Mailer's "Make a ticket")."""
    _can()
    with _session() as s:
        return s.get(entry_id)


def tag(entry_id: str, categories: list[str], flag: bool = False) -> None:
    _can()
    with _session() as s:
        s.tag(entry_id, categories, flag)


def move(entry_id: str, store: str, folder: str) -> None:
    _can()
    with _session() as s:
        s.move(entry_id, store, folder)


def draft_reply(entry_id: str, text: str) -> None:
    _can()
    with _session() as s:
        s.draft_reply(entry_id, text)


def save_draft(to: list[str], subject: str, body: str, *, html: Optional[str] = None, reply_to: str = "", thread: str = "", mark: str = "") -> str:
    _can()
    with _session() as s:
        try:
            return s.save_draft(list(to), subject, body, html, reply_to, thread, mark)
        except OutlookError:
            raise
        except Exception:  # noqa: BLE001
            if not thread:
                raise
            return s.save_draft(list(to), subject, body, html, reply_to, "", mark)


def draft_alive(entry_id: str) -> bool:
    _can()
    with _session() as s:
        return s.draft_alive(entry_id)


def send_draft(entry_id: str) -> None:
    _can()
    with _session() as s:
        s.send_draft(entry_id)


def drop_draft(entry_id: str) -> None:
    _can()
    with _session() as s:
        s.drop_draft(entry_id)


class Fake:
    """A stand-in Outlook for the tests: `inbox` holds mails to be read, `sent` what went out."""

    def __init__(self):
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self.read: set = set()
        self.stores_ = {"support@cbx.test": {"address": "support@cbx.test", "items": []}}
        self.tags: dict = {}
        self.moves: list = []
        self.drafts: list = []
        self.outlook_drafts: dict = {}  # 1.0.71: drafts CBX saved for a person to check

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _item(self, eid):
        return next((m for st in self.stores_.values() for m in st["items"] if m["entry_id"] == eid), None) or next((m for m in self.sent if m.get("entry_id") == eid), None)

    def send(self, to, subject, body, html, reply_to, thread="", mark="", attachments=None, inline=None, info=None):
        from datetime import datetime

        src = self._item(thread) if thread else None
        if thread and src is None:
            raise OutlookError("gone")
        if src is not None:
            subject = reply_subject("RE: " + re.sub(r"^\s*((re|fw)\s*:\s*)+", "", src["subject"], flags=re.I), subject)
        n = len(self.sent) + 1
        self.sent.append({"to": list(to), "subject": subject, "body": body, "html": html, "thread": thread, "mark": mark, "entry_id": f"S{n}",
                          "at": datetime.now().isoformat(timespec="microseconds"), "attachments": [a[0] for a in (attachments or [])],
                          "inline": [{"name": a[0], "cid": a[2], "size": len(a[1])} for a in (inline or [])],  # 1.0.109 (round 2)
                          # 1.0.109: like Outlook — the conversation of the chain it answers (else its own); the Message-ID is in Sent Items
                          "message_id": f"<S{n}.{n * 7919}@fake.outlook>", "conversation": (src or {}).get("conversation") or f"FCONV{n}"})
        if info is not None:
            info["conversation"] = self.sent[-1]["conversation"]

    def sent_ids(self, mark, scan=400):
        from datetime import datetime

        return [{"message_id": m["message_id"], "conversation": m["conversation"], "at": datetime.fromisoformat(m["at"])}
                for m in reversed(self.sent) if m.get("mark") == mark]

    def thread_for(self, mark, inbound, message_ids, boxes):
        from datetime import datetime

        cands = []
        for eid in inbound[:5]:
            m = self._item(eid)
            if m is not None and "received_local" in m:
                cands.append((m["received_local"], eid))
        if not cands:
            for mid in message_ids[:3]:
                m = next((x for st in self.stores_.values() for x in st["items"] if x["id"] == mid), None)
                if m is not None:
                    cands.append((m["received_local"], m["entry_id"]))
        if mark:
            mine = [m for m in self.sent if m.get("mark") == mark]
            if mine:
                cands.append((mine[-1]["at"], mine[-1]["entry_id"]))
        return max(cands)[1] if cands else ""

    def save_draft(self, to, subject, body, html, reply_to, thread="", mark=""):
        self.n_drafts = getattr(self, "n_drafts", 0) + 1
        eid = f"D{self.n_drafts}"
        src = self._item(thread) if thread else None
        if src is not None:
            subject = reply_subject("RE: " + re.sub(r"^\s*((re|fw)\s*:\s*)+", "", src["subject"], flags=re.I), subject)
        self.outlook_drafts[eid] = {"to": list(to), "subject": subject, "body": body, "html": html, "thread": thread, "mark": mark}
        return eid

    def draft_alive(self, entry_id):
        return entry_id in self.outlook_drafts

    def send_draft(self, entry_id):
        from datetime import datetime

        d = self.outlook_drafts.pop(entry_id, None)
        if d is None:
            raise OutlookError("That draft isn't in Outlook any more — it was sent or deleted there.")
        self.sent.append(dict(d, entry_id=f"S{len(self.sent) + 1}", at=datetime.now().isoformat(timespec="microseconds")))

    def drop_draft(self, entry_id):
        self.outlook_drafts.pop(entry_id, None)

    def fetch_new(self, limit):
        return [m for m in self.inbox if m["entry_id"] not in self.read][:limit]

    def mark_read(self, entry_id):
        self.read.add(entry_id)

    # My mail (1.0.69): other mailboxes in the same Outlook
    def list_stores(self):
        return [{"name": k, "address": v["address"], "has_inbox": True} for k, v in self._stores().items()]

    def _stores(self):
        return self.stores_

    def add_store(self, name: str, address: str = "") -> None:
        self._stores()[name] = {"address": address or name, "items": []}

    def put(self, store: str, sender: str, subject: str, body: str, *, received_local=None, name: str = "") -> dict:
        from datetime import datetime

        st = self._stores()[store]
        n = sum(len(v["items"]) for v in self._stores().values()) + 1
        m = {"id": f"<p{n}@test>", "entry_id": f"P{n}", "sender": sender.lower(), "sender_name": name or sender.split("@")[0], "subject": subject, "body": body,
             "headers": "", "files": [], "received_local": (received_local or datetime.now()).isoformat()}
        st["items"].append(m)
        return m

    def fetch_since(self, store, folder, since_local, limit, skip, skip_read=False):
        from datetime import datetime

        st = self._stores().get(store)
        if st is None:
            raise OutlookError(f"Outlook on this PC has no mailbox called {store!r}.")
        rows = [dict(m, cbx=m["entry_id"] in self.read) for m in st["items"] if datetime.fromisoformat(m["received_local"]) >= since_local and m["entry_id"] not in skip
                and not any(x[0] == m["entry_id"] for x in self.moves) and not (skip_read and m["entry_id"] in self.read)]
        return sorted(rows, key=lambda m: m["received_local"], reverse=True)[:limit]

    def get(self, entry_id):
        for name, st in self.stores_.items():
            for m in st["items"]:
                if m["entry_id"] == entry_id:
                    return dict(m, store=name)
        raise OutlookError("That mail isn't in Outlook any more.")

    def tag(self, entry_id, categories, flag):
        t = self.tags.setdefault(entry_id, {"categories": [], "flag": False})
        t["categories"] = list(dict.fromkeys(t["categories"] + list(categories)))
        t["flag"] = t["flag"] or flag

    def move(self, entry_id, store, folder):
        self.moves.append((entry_id, store, folder))

    def draft_reply(self, entry_id, text):
        self.drafts.append((entry_id, text))

    def deliver(self, sender: str, subject: str, body: str, *, files=None, headers: str = "", name: str = "", store: str = "", conversation: str = "",
                inline=None, raw=None, recipients=None) -> dict:
        """A mail arriving in a support mailbox (the first mailbox unless named). 1.0.98: `inline` — the names among
        `files` that were pictures pasted in the text. 1.0.109: `raw` — the mail's attachments as Outlook has them
        ([{name, data, cid, hidden}]), read by the same rules as the real reader (pick: what is kept, what is left out and why);
        `recipients` — its To and Cc addresses."""
        from datetime import datetime

        n = len(self.inbox) + 1
        left: list = []
        files, inline = list(files or []), list(inline or [])
        if raw:
            got, pasted, left = pick([{"name": a["name"], "size": len(a["data"]), "cid": a.get("cid", ""), "hidden": a.get("hidden", False),
                                       "save": (lambda d=a["data"]: d)} for a in raw], sender.lower(), f"<m{n}@test>")
            files, inline = files + got, inline + pasted
        m = {"id": f"<m{n}@test>", "entry_id": f"E{n}", "sender": sender.lower(), "sender_name": name or sender.split("@")[0], "subject": subject, "body": body,
             "headers": headers, "files": files, "received_local": datetime.now().isoformat(timespec="microseconds"), "conversation": conversation,
             "inline": inline, "left_out": left, "recipients": [x.lower() for x in (recipients or [])]}
        self.inbox.append(m)
        self.stores_[store or next(iter(self.stores_))]["items"].append(m)
        return m
