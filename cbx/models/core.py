"""Users, teams, roles, tool access, row-level rules, sessions, audit."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, UTCDateTime, utcnow

ROLES = ("owner", "admin", "manager", "user")
# owner   — everything, incl. making owners/admins/managers and promoting versions.
# admin   — builds for everyone: requests, workflows, warehouse, users; may make managers and users.
# manager — oversees ONE team: Users & roles, Audit trail and Entity Compliance limited to that team;
#           may add and manage users on their own team only; needs tool grants like a user.
# user    — their tools, their requests, their results.
# C-EXA is a real account (role "admin" since 1.0.114; "owner" before) flagged is_axl, so every one of her actions is
# attributable, but the code below refuses to let her grant access or promote versions.
GRANTABLE = {"owner": ROLES, "admin": ("manager", "user"), "manager": ("user",), "user": ()}


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    # Sub-teams: a team may sit under another (CEO's team → three managers' teams → their leads').
    # Depth is not limited; cbx.org.tree guards against a team becoming its own ancestor.
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    users: Mapped[list["User"]] = relationship(back_populates="team", foreign_keys="User.team_id")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team: Mapped[Optional[Team]] = relationship(back_populates="users", foreign_keys=[team_id])
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_axl: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    # Who this person reports to. This is org STRUCTURE, not permission: what it grants is
    # decided in cbx/org/access.py, and sensitive kinds (To-do) need an explicit grant on top.
    reports_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # When the lock went on. A lock that came from wrong passwords lifts by itself after
    # CBX_LOGIN_LOCKOUT_MINUTES; a lock an admin set by hand has no locked_at and stays until lifted.
    locked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reset_requested_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # ---- two-step verification (1.0.45) ------------------------------------
    # "" = not set up yet | totp = an authenticator app | email = a code to their inbox (the 1.0.3x way, chosen)
    mfa_method: Mapped[str] = mapped_column(String(16), default="")
    mfa_secret: Mapped[str] = mapped_column(Text, default="")  # sealed (secretbox), never the raw secret
    mfa_enrolled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    mfa_recovery: Mapped[list] = mapped_column(JSON, default=list)  # hashes of the unused recovery codes
    mfa_failed: Mapped[int] = mapped_column(Integer, default=0)  # wrong codes in a row; the lock comes at CBX_MFA_MAX_FAILED
    # 1.0.113: the person's learned clock difference (steps: the step their app's code matched minus the server's step),
    # the last step a code was accepted for (that step and any earlier one are refused: no replay), and when their
    # second step was proven with a code — email codes chosen before 1.0.113 never were (cbx/migrate.py 1113_mfa_email_unproven)
    mfa_drift: Mapped[int] = mapped_column(Integer, default=0)
    mfa_last_step: Mapped[int] = mapped_column(Integer, default=0)
    mfa_proven_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # 1.0.113: when /mfa/setup last made a new secret — the wide (±5 min) search at /mfa/confirm is open only 10 minutes after it
    mfa_setup_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # An outside person (a vendor, a client) is let in until this date and then not — the Access Control
    # policy's third-party rule. Empty for staff.
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # R8: sees only their own team, whatever their role says. Set when a team-limited person (a manager) made the
    # account, so a Viewer a manager makes sees that manager's team and not the company. Only an owner or an admin
    # takes it off (cbx/api/users.py patch_user).
    team_limited: Mapped[bool] = mapped_column(Boolean, default=False)

    # ---- role helpers -------------------------------------------------------
    # `role` is still the slug, and every one of the 152 checks across the app still reads these
    # three properties. What changed is what is UNDERNEATH them: a capability set, so a role invented
    # in Settings lights up exactly the same code paths a built-in does. Nothing else had to move.
    def caps(self) -> set[str]:
        """This person's capabilities. Falls back to the built-in definition when the roles table has
        not been seeded yet (a database from before this release, mid-migration)."""
        from ..security import caps as C

        role = getattr(self, "_role_row", None)
        if role is not None:
            got = C.expand(role.caps or [])
        else:
            builtin = C.BUILTIN.get(self.role) or C.PRESETS.get(self.role)
            got = C.expand(builtin["caps"]) if builtin else set()
        if getattr(self, "team_limited", False):  # R8: the account's own limit, whatever the role says
            got = got | {"team.scope"}
        return got

    def can(self, capability: str) -> bool:
        """The one question worth asking. `is_admin` and friends are shorthands over this."""
        if self.is_axl and capability in NEVER_ALONE_CAPS:  # 1.0.114: never-alone is code, whatever her role row says
            return False
        return capability in self.caps()

    @property
    def is_owner(self) -> bool:
        return self.can("everything")

    @property
    def is_admin(self) -> bool:
        """Owner or admin: someone who builds for everyone. This is deliberately ONE coarse
        capability rather than something inferred from a finer set, because 152 checks in the app ask
        exactly this question — so a role you invent becomes an admin only if you tick it on purpose,
        never as a side effect of holding some other permission."""
        return self.can("admin.all")

    @property
    def is_team_limited(self) -> bool:
        """R8: what they SEE — people, the audit trail, the org chart — is narrowed to their own team. Every check
        that narrows asks this, not `is_manager`: a viewer a manager made is limited without being a manager."""
        return self.can("team.scope")

    @property
    def is_manager(self) -> bool:
        """What they DO: a manager's powers over their own team (its rows in tools, its companies' people, its
        approvals, chat rooms). Before R8 this was `team.scope` alone, so anything team-limited became a manager."""
        return self.can("team.lead") and self.can("team.scope")

    @property
    def manages_people(self) -> bool:
        """May open Users & roles and the Audit trail (each within their own scope)."""
        return self.can("people.view")

    def initials(self) -> str:
        parts = [p for p in self.name.split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or self.email[:2].upper()

    def can_manage_role(self, role: str) -> bool:
        """Which roles this person may hand out. Ranked, not a fixed table, so a role invented in
        Settings slots in wherever it is ranked: you may grant anything strictly below your own rank,
        and the owner may grant anything at all. C-EXA: nobody, ever.

        Falls back to the old fixed table when the roles row isn't attached (a request that never
        went through `current_user`, or a database from before this release)."""
        if self.is_axl:
            return False
        if not self.can("people.roles"):
            return False
        mine = getattr(self, "_role_row", None)
        if mine is None:
            return role in GRANTABLE.get(self.role, ())
        from ..security import roles as _R

        other = getattr(self, "_roles_by_slug", {}).get(role)
        if other is None:
            return role in GRANTABLE.get(self.role, ()) or self.is_owner
        return bool(self.is_owner or other.rank < mine.rank)

    def can_grant_role(self, role: str) -> bool:
        """R8: may this person GIVE someone this role? Rank (`can_manage_role`), and powers no wider than their own
        (cbx/security/roles.py may_grant). The team limit travels on the account they make, not on the role.
        `can_manage_role` stays the rank question, which also decides whose existing account they may manage."""
        if not self.can_manage_role(role):
            return False
        other = getattr(self, "_roles_by_slug", {}).get(role)
        if other is None:
            return True  # roles not attached: can_manage_role's fixed table answered, and it gives a manager only "user"
        from ..security import roles as _R

        return _R.may_grant(self, other)[0]

    def same_team(self, other: "User") -> bool:
        return bool(self.team_id) and other.team_id == self.team_id

    def can_manage_user(self, other: "User") -> bool:
        """Role AND scope: a team-limited person only reaches people on their own team; nobody edits C-EXA."""
        if other.is_axl or not self.can_manage_role(other.role):
            return False
        if self.is_team_limited:
            return self.same_team(other)
        return True


class Tool(Base):
    """A tool users can be granted. Seeded from cbx.tools.REGISTRY on startup."""

    __tablename__ = "tools"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(32), default="tool")
    admin_only: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=100)
    # "builtin" = shipped in cbx.tools.REGISTRY; "workflow" = a Node-RED workflow exposed before 1.0.48 (kept, not shown)
    kind: Mapped[str] = mapped_column(String(16), default="builtin")
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # workflow tools: {accepts_files: bool}


class ToolAccess(Base):
    __tablename__ = "tool_access"
    __table_args__ = (UniqueConstraint("tool_id", "user_id", name="uq_tool_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    granted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # How far the tool reaches for this person. "all" = the tool's ordinary rule (Entity Compliance: the
    # companies open to everyone plus their team's, plus any they were given). "granted" = ONLY the
    # things they were given one by one — the Mozambique controller who must see Mozambique and nothing else.
    scope: Mapped[str] = mapped_column(String(16), default="all")


class RowRule(Base):
    """Row-level rule: user or team sees only rows of `table` matching `predicate`.
    Predicates are structured (field/op/value) so they are validated, never raw SQL."""

    __tablename__ = "row_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    table: Mapped[str] = mapped_column(String(64))  # e.g. "master_rows", "stored_files"
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    field: Mapped[str] = mapped_column(String(64))
    op: Mapped[str] = mapped_column(String(16), default="eq")  # eq, in, startswith
    value: Mapped[str] = mapped_column(String(255))
    note: Mapped[str] = mapped_column(String(255), default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    agent: Mapped[str] = mapped_column(String(255), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # True between the password and the second step (setting it up, or passing it): every API but
    # /api/auth/* refuses such a session — a stolen password alone opens nothing
    pending_mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    # "View as" (cbx/security/viewas.py): while set, this session sees the app as that person, read-only
    view_as_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    view_as_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class TrustedDevice(Base):
    """A browser this person has proved is theirs, by entering a code we emailed them.

    The unit of trust is the DEVICE, not the IP. Every person in the 17 Sep logs appeared from more
    than one address inside 48 hours — Edwin from two, Otsile from a rotating IPv6 mobile address —
    so an IP allowlist would have asked the owner to approve something several times a day and left
    people at the door in between. A device answers the question that was actually being asked
    ("is this someone we know?") and asks it once a month instead of once a cell tower.
    """

    __tablename__ = "trusted_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # the cookie value
    label: Mapped[str] = mapped_column(String(120), default="")  # "Chrome on Windows", for the list
    first_ip: Mapped[str] = mapped_column(String(64), default="")
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginCode(Base):
    """A one-time code emailed to someone signing in from a device we have not seen.

    Stored as a hash: a database someone can read must not be a list of working sign-in codes.
    """

    __tablename__ = "login_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    agent: Mapped[str] = mapped_column(String(255), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # 1.0.113: what the code is for — "signin" (/api/auth/verify) or "use_email" (/mfa/use-email/confirm); one never works for the other
    purpose: Mapped[str] = mapped_column(String(16), default="signin")


class SignInTicket(Base):
    """1.0.113: "this browser passed the password". /api/auth/login hands one out (an HttpOnly cookie, the row holds only its
    hash) when a second step is still to come; /api/auth/verify accepts a code only with a live ticket for that same person,
    counts wrong codes on it, and spends it on success. Before this, /verify looked the person up by email alone, so an
    email address and one current app code (or a recovery code) opened a full session with no password at all."""

    __tablename__ = "sign_in_tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(16), default="second_step")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


# 1.0.114 (CBX 2's NEVER_ALONE_CAPS): what the AI's account may never do by itself, whatever role it carries — the owner's
# everything, handing out roles, granting tools, reviewing code, approving what she learned, deleting companies. Since
# 1.0.114 her account is an admin, not an owner (cbx/bootstrap.py, cbx/migrate.py 1114_axl_not_owner).
NEVER_ALONE_CAPS = frozenset({"everything", "people.roles", "tools.grant", "code.review", "profiles.approve", "entities.delete"})


class AuditLog(Base):
    """Append-only. No update or delete endpoint exists for this table."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_label: Mapped[str] = mapped_column(String(120), default="")  # "axl", "axl→agent:harvester-02", "thandi.m"
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(16), default="ok")  # ok | blocked | failed | pending
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


class PulseState(Base):
    """One row per thing the pulse (cbx/cexa/pulse.py) checks every minute: is Ollama up, are batches
    stuck, is the scheduler alive, disk, drift… `bad_streak` counts consecutive bad checks; an alert goes
    out at two, once, and a recovery note when it clears. This table IS the snapshot "what changed since
    yesterday" reads from."""

    __tablename__ = "pulse_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    value: Mapped[str] = mapped_column(String(200), default="")     # the short reading, e.g. "online · 27b on GPU"
    detail: Mapped[str] = mapped_column(Text, default="")           # the sentence for a person
    bad_streak: Mapped[int] = mapped_column(Integer, default=0)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)   # an alert went out for the current bad spell
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)  # when ok last flipped


class AppState(Base):
    """One row per fact this server has observed about itself (see cbx/state.py).

    Deliberately not settings: `.env` says what someone intended, this says what actually happened.
    """

    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
