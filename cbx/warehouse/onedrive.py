"""OneDrive / SharePoint mirror through Microsoft Graph (client-credentials flow).

The local disk under DATA_DIR/warehouse is always the source of truth. When
`CBX_ONEDRIVE_ENABLED=1` and the Graph app settings are present, every folder and file is also
created under  drives/{drive_id}/root:/{onedrive_root}/<same path>.  Anything that fails here is
retried by the `onedrive-sync` routine; nothing here may ever block a local save.

Setup steps live in docs/onedrive-setup.md.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from ..config import settings
from ..db import session_scope
from ..jobs import routine
from ..models import Folder, StoredFile

logger = logging.getLogger("cbx.onedrive")
GRAPH = "https://graph.microsoft.com/v1.0"
SMALL_UPLOAD = 4 * 1024 * 1024
CHUNK = 5 * 1024 * 1024  # multiple of 320 KiB as Graph requires

_token: dict = {"value": "", "expires": 0.0}
_lock = threading.Lock()
_state: dict = {"last_sync": None, "last_error": ""}


class OneDriveError(Exception):
    pass


def record_error(where: str, e: BaseException) -> None:
    """Remember the last mirror failure so the warehouse page can show it; the sync routine retries."""
    _state["last_error"] = f"{where}: {str(e)[:180]}"
    logger.warning("onedrive %s failed: %s", where, e)


def configured() -> bool:
    return bool(settings.graph_tenant_id and settings.graph_client_id and settings.graph_client_secret and settings.graph_drive_id)


def enabled() -> bool:
    return bool(settings.onedrive_enabled and configured())


# ----------------------------------------------------------------------------- auth
def get_token(force: bool = False) -> str:
    if not configured():
        raise OneDriveError("OneDrive is not configured (tenant, client id, secret and drive id are needed).")
    with _lock:
        if not force and _token["value"] and _token["expires"] - 60 > time.time():
            return _token["value"]
        url = f"https://login.microsoftonline.com/{settings.graph_tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": settings.graph_client_id,
            "client_secret": settings.graph_client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        }
        r = httpx.post(url, data=data, timeout=20)
        if r.status_code != 200:
            raise OneDriveError(f"Microsoft sign-in refused the app ({r.status_code}): {_err(r)}")
        j = r.json()
        _token["value"] = j["access_token"]
        _token["expires"] = time.time() + int(j.get("expires_in", 3600))
        return _token["value"]


def _err(r: httpx.Response) -> str:
    try:
        j = r.json()
        return (j.get("error") or {}).get("message") or j.get("error_description") or r.text[:200]
    except Exception:  # noqa: BLE001
        return r.text[:200]


def _headers(extra: Optional[dict] = None) -> dict:
    h = {"Authorization": f"Bearer {get_token()}"}
    if extra:
        h.update(extra)
    return h


def _item_url(path: str, suffix: str = "") -> str:
    """drives/{id}/root:/{root}/{path}{suffix}  — suffix like ':/children' or ':/content'."""
    full = f"{settings.onedrive_root}/{path}".strip("/")
    return f"{GRAPH}/drives/{settings.graph_drive_id}/root:/{quote(full)}{suffix}"


# ----------------------------------------------------------------------------- folders
def ensure_remote_path(path: str) -> str:
    """Create every segment of `path` under the root; returns the id of the last folder."""
    parts = [p for p in path.split("/") if p]
    parent = settings.onedrive_root.strip("/")
    last_id = ""
    # make sure the root itself exists
    r = httpx.get(f"{GRAPH}/drives/{settings.graph_drive_id}/root:/{quote(parent)}", headers=_headers(), timeout=20)
    if r.status_code == 404:
        r = httpx.post(f"{GRAPH}/drives/{settings.graph_drive_id}/root/children", headers=_headers(), json={"name": parent, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}, timeout=20)
        if r.status_code not in (200, 201, 409):
            raise OneDriveError(f"Could not create the root folder: {_err(r)}")
    elif r.status_code >= 400:
        raise OneDriveError(f"Could not reach the drive: {_err(r)}")
    walked = ""
    for seg in parts:
        parent_url = f"{GRAPH}/drives/{settings.graph_drive_id}/root:/{quote((parent + '/' + walked).strip('/'))}:/children"
        r = httpx.post(parent_url, headers=_headers(), json={"name": seg, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}, timeout=20)
        walked = f"{walked}/{seg}".strip("/")
        if r.status_code in (200, 201):
            last_id = r.json().get("id", "")
        elif r.status_code == 409:
            g = httpx.get(_item_url(walked), headers=_headers(), timeout=20)
            last_id = g.json().get("id", "") if g.status_code == 200 else ""
        else:
            raise OneDriveError(f"Could not create folder '{walked}': {_err(r)}")
    return last_id


def ensure_remote_folder(folder: Folder) -> None:
    if not enabled():
        return
    rid = ensure_remote_path(folder.path)
    # set on the caller's object — it is flushed/committed with the caller's session
    folder.remote_id = rid or "ok"
    folder.synced_at = datetime.now(timezone.utc)
    _state["last_sync"] = datetime.now(timezone.utc)


# ----------------------------------------------------------------------------- files
def upload_file(f: StoredFile) -> None:
    if not enabled():
        return
    from . import storage

    data = storage.abs_path(f).read_bytes()
    folder_path = f.rel_path.rsplit("/", 1)[0] if "/" in f.rel_path else ""
    ensure_remote_path(folder_path)
    if len(data) < SMALL_UPLOAD:
        r = httpx.put(_item_url(f.rel_path, ":/content"), headers=_headers({"Content-Type": "application/octet-stream"}), content=data, timeout=60)
        if r.status_code not in (200, 201):
            raise OneDriveError(f"Upload of {f.name} failed: {_err(r)}")
        rid = r.json().get("id", "")
    else:
        rid = _upload_session(f.rel_path, data)
    f.remote_id = rid or "ok"
    f.synced_at = datetime.now(timezone.utc)
    _state["last_sync"] = datetime.now(timezone.utc)
    _state["last_error"] = ""


def _upload_session(rel_path: str, data: bytes) -> str:
    r = httpx.post(_item_url(rel_path, ":/createUploadSession"), headers=_headers(), json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}, timeout=30)
    if r.status_code not in (200, 201):
        raise OneDriveError(f"Could not start an upload session: {_err(r)}")
    url = r.json()["uploadUrl"]
    total = len(data)
    pos = 0
    rid = ""
    while pos < total:
        chunk = data[pos : pos + CHUNK]
        end = pos + len(chunk) - 1
        rr = httpx.put(url, headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {pos}-{end}/{total}"}, content=chunk, timeout=120)
        if rr.status_code not in (200, 201, 202):
            raise OneDriveError(f"Upload chunk failed: {_err(rr)}")
        if rr.status_code in (200, 201):
            rid = rr.json().get("id", "")
        pos += len(chunk)
    return rid


# ----------------------------------------------------------------------------- status / test
def pending_count(db) -> int:
    return db.query(StoredFile).filter(StoredFile.remote_id == "").count() + db.query(Folder).filter(Folder.remote_id == "").count()


def status(db) -> dict:
    last = _state["last_sync"]
    if last is None:
        # the most recent synced_at we know of survives restarts
        row = db.query(StoredFile).filter(StoredFile.synced_at.isnot(None)).order_by(StoredFile.synced_at.desc()).first()
        last = row.synced_at if row else None
    return {
        "enabled": bool(settings.onedrive_enabled),
        "configured": configured(),
        "connected": enabled(),
        "root": settings.onedrive_root,
        "last_sync": last.isoformat() if last else None,
        "last_error": _state["last_error"],
        "pending": pending_count(db) if enabled() else 0,
    }


def test_connection() -> dict:
    """Owner-facing check: token, drive, root folder listing. Plain English, no stack traces."""
    if not settings.onedrive_enabled:
        return {"ok": False, "message": "OneDrive sync is switched off (CBX_ONEDRIVE_ENABLED is not set). Files stay on this server only."}
    if not configured():
        missing = [k for k, v in (("tenant id", settings.graph_tenant_id), ("client id", settings.graph_client_id), ("client secret", settings.graph_client_secret), ("drive id", settings.graph_drive_id)) if not v]
        return {"ok": False, "message": "OneDrive is switched on but not fully set up. Missing: " + ", ".join(missing) + ". See docs/onedrive-setup.md."}
    try:
        get_token(force=True)
    except OneDriveError as e:
        return {"ok": False, "message": str(e) + " Check the tenant id, client id and secret in .env."}
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"Could not reach Microsoft sign-in: {e}. Is this server allowed out to the internet?"}
    try:
        r = httpx.get(f"{GRAPH}/drives/{settings.graph_drive_id}", headers=_headers(), timeout=20)
        if r.status_code == 403:
            return {"ok": False, "message": "Signed in, but the app is not allowed to see the drive. Grant Files.ReadWrite.All (or Sites.Selected on this site) and give admin consent."}
        if r.status_code == 404:
            return {"ok": False, "message": "Signed in, but no drive has that id. Re-check CBX_GRAPH_DRIVE_ID (docs/onedrive-setup.md shows how to find it)."}
        if r.status_code >= 400:
            return {"ok": False, "message": f"Graph refused the drive request ({r.status_code}): {_err(r)}"}
        drive_name = r.json().get("name", "drive")
        rr = httpx.get(_item_url("", ":/children?$top=5"), headers=_headers(), timeout=20)
        if rr.status_code == 404:
            return {"ok": True, "message": f"Connected to '{drive_name}'. The folder '{settings.onedrive_root}' does not exist yet; it will be created on the first upload."}
        if rr.status_code >= 400:
            return {"ok": False, "message": f"Connected to '{drive_name}' but could not list '{settings.onedrive_root}': {_err(rr)}"}
        names = [c.get("name") for c in rr.json().get("value", [])]
        return {"ok": True, "message": f"Connected to '{drive_name}'. '{settings.onedrive_root}' holds {len(names)} item(s): {', '.join(names) or 'nothing yet'}."}
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"Could not reach Microsoft Graph: {e}"}


# ----------------------------------------------------------------------------- routine
@routine("onedrive-sync", 120)
def sync_routine() -> None:
    if not enabled():
        return
    with session_scope() as db:
        folders = db.query(Folder).filter(Folder.remote_id == "").order_by(Folder.path).limit(50).all()
        files = db.query(StoredFile).filter(StoredFile.remote_id == "").order_by(StoredFile.created_at).limit(50).all()
        for fo in folders:
            try:
                ensure_remote_folder(fo)
            except Exception as e:  # noqa: BLE001
                _state["last_error"] = str(e)[:200]
                logger.warning("onedrive folder sync failed for %s: %s", fo.path, e)
                return
        for f in files:
            try:
                upload_file(f)
            except FileNotFoundError:
                continue
            except Exception as e:  # noqa: BLE001
                _state["last_error"] = str(e)[:200]
                logger.warning("onedrive upload failed for %s: %s", f.name, e)
                return
