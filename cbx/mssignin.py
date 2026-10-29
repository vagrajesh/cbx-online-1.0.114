"""Sign in with Microsoft (Edwin, 13 Oct: "can we make it use microsoft login?" — both apps; company staff may use
Microsoft or their CBX password).

The company's Entra ID (Azure AD) signs the person in, with the company's own MFA; CBX still decides who gets
in and what they see. Only someone who already has an active CBX account under that email comes in; anyone
else is told to ask for access. Outside people keep email + password + CBX's own second step.

The flow is OpenID Connect, authorization code with PKCE, for a confidential client (a client secret):

    GET /auth/microsoft           → the Microsoft sign-in page (state, nonce and the PKCE verifier sealed in a
                                    short-lived cookie that only this path receives)
    GET /auth/microsoft/callback  → the code is exchanged at Microsoft's token endpoint, server to server over
                                    TLS; the ID token's issuer, audience, tenant, times and nonce are checked
                                    (OIDC Core 3.1.3.7: a token received straight from the token endpoint may
                                    rely on TLS instead of its signature, so no extra libraries are needed)
                                    → the CBX account by email; its Microsoft object id is pinned at the first
                                    sign-in, so a re-used mailbox name never opens someone else's account.

The owner fills in the tenant id, client id and client secret on the settings card (the secret is sealed with
secretbox, never shown again). Environment values CBX_MS_TENANT_ID / CBX_MS_CLIENT_ID / CBX_MS_CLIENT_SECRET
work too. The same file in the old CBX and CBX-Connect 2.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from .security import secretbox

STATE_KEY = "ms_signin"
COOKIE = "cbx_ms"
PATH = "/auth/microsoft"
CALLBACK = "/auth/microsoft/callback"
COOKIE_MAX_AGE = 600  # ten minutes to finish signing in at Microsoft
SKEW = 300  # seconds of clock difference allowed on the token's times
TENANT_RX = re.compile(r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$")
GUID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
LOGIN = "https://login.microsoftonline.com"

# what the sign-in page says for each refusal (the code travels as ?ms=<code>)
WORDS = {
    "off": "Microsoft sign-in isn't switched on yet. Use your email and password.",
    "state": "That sign-in took too long or came from another window. Try again.",
    "denied": "Microsoft didn't sign you in. Try again, or use your email and password.",
    "nouser": "Your Microsoft account has no CBX-Connect access yet. Ask the owner to add you.",
    "blocked": "This account can't sign in right now. Ask the owner.",
    "other": "This CBX account is linked to a different Microsoft account. Ask the owner.",
    "failed": "Microsoft sign-in didn't work. Try again, or use your email and password.",
}


class SignInError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or WORDS.get(code, code))
        self.code = code
        self.detail = detail


# ---- settings -------------------------------------------------------------------------------------------------
def _row(db: Session):
    from .models import AppState

    return db.get(AppState, STATE_KEY)


def _stored(db: Session) -> dict:
    row = _row(db)
    v = row.value if row is not None else None
    if isinstance(v, str):  # the old CBX keeps app_state values as text
        try:
            v = json.loads(v or "{}")
        except ValueError:
            v = {}
    return v if isinstance(v, dict) else {}


def config(db: Session) -> dict:
    """{enabled, tenant, client_id, secret (clear, server side only), secret_hint, source}."""
    s = _stored(db)
    tenant = (s.get("tenant") or os.environ.get("CBX_MS_TENANT_ID") or "").strip()
    client = (s.get("client_id") or os.environ.get("CBX_MS_CLIENT_ID") or "").strip()
    secret = ""
    if s.get("secret_sealed"):
        try:
            secret = secretbox.open_(s["secret_sealed"])
        except Exception:  # noqa: BLE001 — secret.key changed: say so on the card
            secret = ""
    secret = secret or os.environ.get("CBX_MS_CLIENT_SECRET", "")
    return {"enabled": bool(s.get("enabled")), "tenant": tenant, "client_id": client, "secret": secret,
            "secret_hint": s.get("secret_hint") or (secretbox.hint(secret) if secret else ""),
            "sealed_unreadable": bool(s.get("secret_sealed")) and not secret}


def ready(db: Session) -> tuple[bool, str]:
    c = config(db)
    if not c["tenant"] or not c["client_id"] or not c["secret"]:
        return False, "Fill in the tenant id, client id and client secret from IT's app registration."
    if not TENANT_RX.match(c["tenant"]) or c["tenant"].lower() in ("common", "organizations", "consumers"):
        return False, "The tenant must be the company's own tenant id (a GUID) or its domain."
    if not base_url(db).lower().startswith("https://"):
        return False, "Microsoft needs the app on an https:// address. It switches on once the company name and certificate are in."
    return True, ""


def on(db: Session) -> bool:
    return config(db)["enabled"] and ready(db)[0]


def public(db: Session) -> dict:
    """For the sign-in page: whether to show the button."""
    return {"enabled": on(db)}


def card(db: Session) -> dict:
    """For the owner's settings card (the secret never leaves the server)."""
    c = config(db)
    ok, why = ready(db)
    return {"enabled": c["enabled"], "tenant": c["tenant"], "client_id": c["client_id"], "has_secret": bool(c["secret"]),
            "secret_hint": c["secret_hint"], "ready": ok, "why": why, "redirect_uri": redirect_uri(db), "working": c["enabled"] and ok,
            "unreadable": c["sealed_unreadable"]}


def save(db: Session, user, values: dict) -> dict:
    if not getattr(user, "is_owner", False):
        raise PermissionError("Only the owner changes Microsoft sign-in.")
    from .audit import log
    from .models import AppState

    s = _stored(db)
    if "tenant" in values:
        t = str(values.get("tenant") or "").strip()
        if t and (not TENANT_RX.match(t) or t.lower() in ("common", "organizations", "consumers")):
            raise ValueError("The tenant must be the company's own tenant id (a GUID) or its domain, e.g. contoso.onmicrosoft.com.")
        s["tenant"] = t
    if "client_id" in values:
        c = str(values.get("client_id") or "").strip()
        if c and not GUID_RX.match(c):
            raise ValueError("The client id (application id) is a GUID like 1b2c3d4e-….")
        s["client_id"] = c
    if values.get("secret"):
        sec = str(values["secret"]).strip()
        if len(sec) < 8:
            raise ValueError("That doesn't look like a client secret.")
        s["secret_sealed"], s["secret_hint"] = secretbox.seal(sec), secretbox.hint(sec)
    if "enabled" in values:
        s["enabled"] = bool(values["enabled"])
    row = _row(db)
    if row is None:
        row = AppState(key=STATE_KEY)
        db.add(row)
    from sqlalchemy import JSON

    row.value = s if isinstance(AppState.__table__.c.value.type, JSON) else json.dumps(s)
    db.flush()
    log(db, "microsoft_signin_settings", actor=user, target="microsoft",
        detail=f"enabled={bool(s.get('enabled'))} tenant={s.get('tenant', '')} client={s.get('client_id', '')}" + (" secret=changed" if values.get("secret") else ""))
    return card(db)


def base_url(db: Optional[Session] = None) -> str:
    """How people reach this server (the redirect address Microsoft sends them back to is built from it)."""
    from .config import settings

    if getattr(settings, "site_url", ""):
        return settings.site_url.rstrip("/")  # CBX-Connect 2
    try:
        from . import state  # the old CBX

        return state.public_url(db).rstrip("/")
    except Exception:  # noqa: BLE001
        return (getattr(settings, "public_url", "") or "").rstrip("/")


def redirect_uri(db: Optional[Session] = None) -> str:
    return base_url(db) + CALLBACK


# ---- the flow ----------------------------------------------------------------------------------------------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def start(db: Session, next_path: str = "/") -> tuple[str, str]:
    """(the Microsoft sign-in address, the sealed cookie value)."""
    if not on(db):
        raise SignInError("off")
    c = config(db)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    nxt = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    cookie = secretbox.seal(json.dumps({"s": state, "n": nonce, "v": verifier, "next": nxt[:300], "at": int(time.time())}))
    q = {"client_id": c["client_id"], "response_type": "code", "redirect_uri": redirect_uri(db), "response_mode": "query",
         "scope": "openid profile email", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
         "prompt": "select_account"}
    return f"{LOGIN}/{c['tenant']}/oauth2/v2.0/authorize?{urlencode(q)}", cookie


def _exchange(db: Session, cfg: dict, code: str, verifier: str) -> dict:
    """The code for tokens, at Microsoft's token endpoint (tests replace this)."""
    import httpx

    r = httpx.post(f"{LOGIN}/{cfg['tenant']}/oauth2/v2.0/token", timeout=20, data={
        "client_id": cfg["client_id"], "client_secret": cfg["secret"], "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri(db), "code_verifier": verifier, "scope": "openid profile email"})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or "id_token" not in body:
        raise SignInError("failed", f"token endpoint said {r.status_code}: {str(body.get('error_description') or body.get('error') or '')[:200]}")
    return body


def _payload(id_token: str) -> dict:
    try:
        part = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception as e:  # noqa: BLE001
        raise SignInError("failed", f"the ID token could not be read ({e})") from None


def claims_of(id_token: str, cfg: dict, nonce: str, now: Optional[float] = None) -> dict:
    """The ID token's claims, after the checks OIDC asks for of a token received from the token endpoint."""
    p = _payload(id_token)
    now = time.time() if now is None else now
    tid = str(p.get("tid") or "")
    if GUID_RX.match(cfg["tenant"]) and tid.lower() != cfg["tenant"].lower():
        raise SignInError("failed", "the token is from another tenant")
    if not str(p.get("iss") or "").lower() == f"{LOGIN}/{tid}/v2.0".lower():
        raise SignInError("failed", "the token's issuer is not this tenant")
    aud = p.get("aud")
    if (aud if isinstance(aud, str) else (aud or [None])[0]) != cfg["client_id"]:
        raise SignInError("failed", "the token is for another application")
    if not p.get("nonce") or not secrets.compare_digest(str(p["nonce"]), nonce):
        raise SignInError("failed", "the token's nonce does not match")
    if float(p.get("exp") or 0) < now - SKEW or float(p.get("nbf") or p.get("iat") or 0) > now + SKEW:
        raise SignInError("failed", "the token is out of date")
    if not p.get("oid"):
        raise SignInError("failed", "the token has no object id")
    return p


def email_of(claims: dict) -> str:
    for k in ("email", "preferred_username", "upn", "unique_name"):
        v = str(claims.get(k) or "").strip().lower()
        if "@" in v:
            return v
    return ""


def finish(db: Session, *, code: str, state: str, cookie: str, error: str = "") -> tuple[object, str]:
    """(the CBX user, where to go next). Raises SignInError with a code for the sign-in page."""
    if error:
        raise SignInError("denied", error[:200])
    try:
        saved = json.loads(secretbox.open_(cookie or ""))
    except Exception:  # noqa: BLE001
        raise SignInError("state", "no sign-in cookie, or it was tampered with") from None
    if not state or not secrets.compare_digest(str(saved.get("s") or ""), state) or time.time() - int(saved.get("at") or 0) > COOKIE_MAX_AGE:
        raise SignInError("state", "the state does not match or the sign-in took too long")
    if not on(db):
        raise SignInError("off")
    if not code:
        raise SignInError("denied", "no code came back")
    cfg = config(db)
    tokens = _exchange(db, cfg, code, saved["v"])
    claims = claims_of(tokens["id_token"], cfg, saved["n"])
    return match_user(db, claims), saved.get("next") or "/"


def match_user(db: Session, claims: dict):
    from .db import utcnow
    from .models import User

    email = email_of(claims)
    if not email:
        raise SignInError("nouser", "Microsoft gave no email for this account")
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None or getattr(user, "is_axl", False):
        raise SignInError("nouser", email)
    ends = getattr(user, "ends_at", None) or getattr(user, "expires_at", None)
    locked = getattr(user, "locked_until", None)
    if getattr(user, "locked", False):  # the old CBX: a lock, lifting by itself after a wait unless an admin set it
        from .security.auth import _lock_minutes_left

        left = _lock_minutes_left(user)
        if left is None or left > 0:
            raise SignInError("blocked", email)
    if not user.is_active or (ends and ends < utcnow()) or (locked and locked > utcnow()):
        raise SignInError("blocked", email)
    prefs = dict(user.prefs or {})
    oid = str(claims["oid"])
    if prefs.get("ms_oid") and prefs["ms_oid"] != oid:
        raise SignInError("other", email)
    if not prefs.get("ms_oid"):
        prefs["ms_oid"] = oid
        user.prefs = prefs
    return user


def test(db: Session) -> dict:
    """The owner's Test: Microsoft knows the tenant (its sign-in configuration answers). Never sends the secret."""
    c = config(db)
    if not c["tenant"] or not TENANT_RX.match(c["tenant"]):
        return {"ok": False, "detail": "Fill in the tenant first."}
    try:
        import httpx

        r = httpx.get(f"{LOGIN}/{c['tenant']}/v2.0/.well-known/openid-configuration", timeout=15)
        if r.status_code != 200:
            return {"ok": False, "detail": f"Microsoft doesn't know that tenant ({r.status_code})."}
        iss = r.json().get("issuer", "")
        return {"ok": True, "detail": f"Microsoft knows the tenant ({iss}). Next: a real sign-in once the app is on https."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"Couldn't reach Microsoft from the server: {str(e)[:200]}"}
