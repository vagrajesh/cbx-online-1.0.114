"""Registry of tools users can be granted. Seeded into the tools table on startup."""
from __future__ import annotations

REGISTRY = [
    dict(slug="pdf-studio", name="PDF Studio", icon="pdf", sort=10, description="Drop in a template and your PDFs. You get a filled sheet back."),
    dict(slug="master-sheets", name="Master Sheets", icon="sheet", sort=20, description="Living workbooks the warehouse keeps up to date."),
    # slug stays "doc-watch" (grants, routes and row rules key on it); the name people see is Entity Compliance
    dict(slug="doc-watch", name="Entity Compliance", icon="shield", sort=30, description="The compliance register: certificates, permits and contracts per site or client, with the date each runs out."),
    dict(slug="ifrs16", name="IFRS 16", icon="ledger", sort=35, description="Monthly Lease Accelerator imports from the RR06 movements and LA register."),
    dict(slug="ap", name="AP Tool", icon="inbox", sort=37, description="Drop a folder of AP files: the AP team reads, checks and sorts them; what it doesn't know comes to you."),
    dict(slug="site-schedules", name="Site Schedules", icon="tower", sort=40, admin_only=False, description="The site's Swiftnet PDFs in, the Site Schedule Agreement (Word) out."),
    # 1.0.96 (Edwin: "if i need to assign someone to support, there needs to be an option, they will work with the
    # mailers and tickets"): not a page — a grant. Holders get the Support menu, every ticket, the support mailboxes
    # in the Mailer, and can be assigned tickets. Assigning and Support set-up stay with the owner and admins.
    dict(slug="support-desk", name="Support desk", icon="headset", sort=60, description="Works the support tickets and the support mailboxes in the Mailer."),
    dict(slug="todo", name="To-do", icon="check-square", sort=50, description="Your own to-do list with steps, the team's open tasks to take, and the goals the team is working towards."),
]


def seed_tools(db) -> None:
    from .models import Tool

    existing = {t.slug: t for t in db.query(Tool).all()}
    for spec in REGISTRY:
        t = existing.get(spec["slug"])
        if t is None:
            db.add(Tool(**spec))
        else:
            for k, v in spec.items():
                setattr(t, k, v)
    db.flush()
