"""Secrets at rest: API keys the owner attaches to tools are stored encrypted, not in clear text.

The key is derived from the server's own `data/secret.key` (the file that already signs sessions), so
a copy of the database on its own — a backup on a share, a zip sent to the wrong person — does not
give away the API keys. Nothing beyond the standard library is used: HMAC-SHA256 as the keystream
generator (counter mode) and a second HMAC-SHA256 as the authentication tag, checked before anything
is decrypted. Format: `v1.<nonce>.<ciphertext>.<tag>`, each part urlsafe base64.

If `secret.key` is lost the stored keys cannot be read back; the UI then says "enter it again".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from ..config import settings

_LABEL_ENC = b"cbx-secretbox-v1-enc"
_LABEL_MAC = b"cbx-secretbox-v1-mac"


class SecretBoxError(Exception):
    pass


def _keys(master: bytes | None = None) -> tuple[bytes, bytes]:
    m = master if master is not None else settings.secret_key.encode("utf-8")
    return hmac.new(m, _LABEL_ENC, hashlib.sha256).digest(), hmac.new(m, _LABEL_MAC, hashlib.sha256).digest()


def _stream(k_enc: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(k_enc, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def seal(plaintext: str, *, master: bytes | None = None) -> str:
    k_enc, k_mac = _keys(master)
    nonce = secrets.token_bytes(16)
    pt = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(pt, _stream(k_enc, nonce, len(pt))))
    tag = hmac.new(k_mac, b"v1" + nonce + ct, hashlib.sha256).digest()
    return f"v1.{_b64(nonce)}.{_b64(ct)}.{_b64(tag)}"


def open_(token: str, *, master: bytes | None = None) -> str:
    try:
        v, n, c, t = token.split(".")
        if v != "v1":
            raise ValueError("version")
        nonce, ct, tag = _unb64(n), _unb64(c), _unb64(t)
    except (ValueError, TypeError) as e:
        raise SecretBoxError("That is not a sealed secret.") from e
    k_enc, k_mac = _keys(master)
    if not hmac.compare_digest(tag, hmac.new(k_mac, b"v1" + nonce + ct, hashlib.sha256).digest()):
        raise SecretBoxError("The stored secret can't be read with this server's key (secret.key changed?). Enter it again.")
    return bytes(a ^ b for a, b in zip(ct, _stream(k_enc, nonce, len(ct)))).decode("utf-8")


def hint(plaintext: str) -> str:
    """What the UI may show of a key: its length and last four characters — enough to tell two apart."""
    s = plaintext.strip()
    if len(s) <= 4:
        return "•" * len(s)
    return "•" * min(8, len(s) - 4) + s[-4:]
