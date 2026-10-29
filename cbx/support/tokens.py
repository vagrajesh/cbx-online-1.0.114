# 1.0.98: from CBX-Connect 2.10.3 cbx/support/tokens.py
"""2.10.0: the desk's mail links that need no sign-in — Accept (one ticket, one assignment) and My settings (one person).

A link is never stored. It is worked out from a random nonce kept on the row and the server's own key
(DATA_DIR/secret.key), for one purpose only:

    token = HMAC-SHA256(key, "cbx-support|<purpose>|<row id>|<nonce>")      43 characters, base64url

Only its SHA-256 is kept (to find the row); the link is checked again against the worked-out token in constant time.
A new nonce kills every older link (a reassigned ticket: the first lead's Accept link stops working). The same link
can be put in a reminder later without keeping it anywhere.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

PURPOSES = ("accept", "settings")


def nonce() -> str:
    return secrets.token_hex(16)


def make(purpose: str, row_id: int, nonce_: str) -> str:
    if purpose not in PURPOSES or not nonce_:
        raise ValueError("unknown purpose or no nonce")
    from ..config import settings

    mac = hmac.new(settings.secret_key.encode(), f"cbx-support|{purpose}|{row_id}|{nonce_}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def digest(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def looks_right(token: str) -> bool:
    return bool(token) and 40 <= len(token) <= 64 and all(c.isalnum() or c in "-_" for c in token)


def check(purpose: str, row_id: int, nonce_: str, token: str) -> bool:
    """The link is this row's, for this purpose, and still alive (compared in constant time)."""
    if not nonce_ or not looks_right(token):
        return False
    return hmac.compare_digest(make(purpose, row_id, nonce_), token)
