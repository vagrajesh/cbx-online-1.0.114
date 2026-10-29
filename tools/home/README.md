# Home — the dashboard

**What.** The first page everyone sees: rows of numbers (open requests, documents due, failed sign-ins,
cells to check…) and one or two short lists. Click a number to open the page behind it.

**Who.** Everyone (`access.everyone`). It is not in the menu (`menu.hidden`) — it *is* Home. Each person
gets the page for their role: `owner`, `admin`, `manager`, `user`. A role invented in Users & roles
gets the admin page if it is an admin-level role, the manager page if it manages people, else `user`
— or its own page, if you add one named after it.

**Where.** `pages/<role>.json`. Each **Numbers** block (`type: metrics`) lists metric keys; the
catalogue is `cbx/engine/metrics.py` (the Builder lists it with a line on each). A person only sees
the numbers their role and tools allow — the others are not shown to them, not shown as zero.

**How to change it.** Admin → Builder → Home: tick numbers in a Numbers block, add blocks, wire them to
a role's page, Save draft → Preview → Release → Promote. That changes the starting design for the
role. Each person can then rearrange their own Home (Home → Customise: add, remove and drag numbers,
hide a block, Reset); that is kept with them, in their settings, and never touches this folder.

**Owner.** edwin.botha@cbreexcellerate.com
