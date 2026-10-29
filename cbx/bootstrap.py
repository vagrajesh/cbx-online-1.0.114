"""First-run setup: create tables, seed tools, create the AI (C-EXA) account and the first owner.
Run by `python -m cbx.bootstrap --owner edwin.botha@cbreexcellerate.com --name "Edwin Botha"`
(setup.bat does this) or automatically on first start if CBX_FIRST_OWNER is set."""
from __future__ import annotations

import argparse
import os
import sys

from .audit import log
from .branding import ai_name, product_name
from .db import init_db, session_scope
from .models import Folder, MasterSheet, Team, User
from .security.auth import hash_password, temp_password
from .tools import seed_tools


def ensure_cexa(db) -> User:
    """The AI's service account. Its display name follows branding (CBX_AI_NAME / Settings → Branding):
    when the owner renames the AI, the account is renamed here at the next start (and immediately by the
    branding endpoint), so Users & roles and the audit trail agree with the screen."""
    name = ai_name()
    cexa = db.query(User).filter(User.is_axl.is_(True)).one_or_none()
    if cexa is None:
        # 1.0.114: an admin, never an owner — her own routine work goes through cbx/cexa/policy.py; never-alone caps are refused in code
        cexa = User(email="cexa@cbx.local", name=name, title="Controller", role="admin", is_axl=True, must_change_password=False, password_hash="")
        db.add(cexa)
        db.flush()
        log(db, "account_created", actor_label="system", target="axl", detail=f"{name} service account")
    elif cexa.name != name:
        old = cexa.name
        cexa.name = name
        db.flush()
        log(db, "account_changed", actor_label="system", target="axl", detail=f"AI renamed {old} → {name} (branding)")
    if cexa.role == "owner":  # 1.0.114: never an owner (cbx/migrate.py 1114_axl_not_owner does it once; this holds it)
        cexa.role = "admin"
        db.flush()
        log(db, "account_changed", actor_label="system", target="axl", detail="the AI's service account: role owner → admin (1.0.114)")
    if cexa.email == "axl@cbx.local" and not db.query(User).filter(User.email == "cexa@cbx.local").count():
        cexa.email = "cexa@cbx.local"  # AXL became C-EXA in 1.0.49; the account never signs in, so nothing else changes
        db.flush()
        log(db, "account_changed", actor_label="system", target="axl", detail="service account address axl@cbx.local → cexa@cbx.local (1.0.49)")
    return cexa


def ensure_library(db) -> None:
    roots = [("library", "Shared library", "Shared library", None)]
    subs = ["PDFs", "Workflows", "Master sheets"]
    if not db.query(Folder).filter(Folder.path == "Shared library").one_or_none():
        lib = Folder(kind="library", name="Shared library", path="Shared library")
        db.add(lib)
        db.flush()
        for s in subs:
            db.add(Folder(kind="library", name=s, path=f"Shared library/{s}", parent_id=lib.id))
        db.add(Folder(kind="team", name="Teams", path="Teams"))
        db.flush()
    if not db.query(MasterSheet).count():
        db.add(
            MasterSheet(
                name="Municipal accounts",
                slug="municipal-accounts",
                description="One row per municipal / Eskom bill, added by PDF Studio and workflows.",
                columns=[
                    {"key": "account_no", "label": "Account", "ctype": "text"},
                    {"key": "municipality", "label": "Municipality", "ctype": "text"},
                    {"key": "period", "label": "Period", "ctype": "text"},
                    {"key": "kwh", "label": "kWh", "ctype": "number"},
                    {"key": "demand_kva", "label": "Demand kVA", "ctype": "number"},
                    {"key": "nmd_kva", "label": "NMD kVA", "ctype": "number"},
                    {"key": "excl_vat", "label": "Excl. VAT", "ctype": "money"},
                    {"key": "vat", "label": "VAT", "ctype": "money"},
                    {"key": "total", "label": "Total", "ctype": "money"},
                ],
            )
        )
        db.add(MasterSheet(name="Vendor payments", slug="vendor-payments", description="Vendor invoices and payment status.", columns=[
            {"key": "vendor", "label": "Vendor", "ctype": "text"}, {"key": "invoice_no", "label": "Invoice", "ctype": "text"},
            {"key": "date", "label": "Date", "ctype": "date"}, {"key": "excl_vat", "label": "Excl. VAT", "ctype": "money"},
            {"key": "vat", "label": "VAT", "ctype": "money"}, {"key": "total", "label": "Total", "ctype": "money"}]))
        db.flush()


def create_owner(db, email: str, name: str) -> tuple[User, str]:
    from .warehouse.folders import ensure_person_folders

    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    pw = temp_password()
    if user is None:
        team = db.query(Team).filter(Team.slug == "digital-transformation").one_or_none()
        if team is None:
            team = Team(name="Digital & Transformation", slug="digital-transformation")
            db.add(team)
            db.flush()
        user = User(email=email, name=name, role="owner", team_id=team.id, title="Owner")
        db.add(user)
        db.flush()
        log(db, "account_created", actor_label="setup", target=email, detail="first owner")
    user.password_hash = hash_password(pw)
    # Seeding a sandbox instance (CBX_SANDBOX_SEED, set by sandbox_runner.seed_owners and nothing else —
    # CBX_SANDBOX_RUN is also set for a version's test run, where the owner must keep its normal
    # first-sign-in flow): the owner signs in with the password the Versions page shows and is not made
    # to choose another one for a database that lives an afternoon.
    user.must_change_password = not os.environ.get("CBX_SANDBOX_SEED")
    user.locked = False
    user.failed_logins = 0
    db.flush()
    ensure_person_folders(db, user)
    return user, pw


def bootstrap(owner_email: str | None = None, owner_name: str = "Owner") -> str | None:
    init_db()
    with session_scope() as db:
        seed_tools(db)
        from .security.roles import seed as seed_roles

        seed_roles(db)
        ensure_cexa(db)
        ensure_library(db)
        if owner_email:
            _, pw = create_owner(db, owner_email, owner_name)
            return pw
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", help="email of the first owner (or an existing one to reset)")
    ap.add_argument("--name", default="Owner")
    a = ap.parse_args()
    pw = bootstrap(a.owner, a.name)
    if pw:
        print(f"\n  {product_name()} is ready.")
        print(f"  Sign in as {a.owner}")
        print(f"  Temporary password: {pw}")
        print("  You will be asked to choose your own password on first sign-in.\n")
    else:
        print("Database ready. No owner created (pass --owner email).")
    sys.exit(0)
