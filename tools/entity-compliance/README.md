# Entity Compliance (tool folder)

**What it is.** The compliance register drawn by the engine: companies down the side, document types across the top, one dot per cell; and a flat list of every live document. Same data, same rules as the built-in Entity Compliance screens. Since 1.0.56 it is the one Entity Compliance menu row: clicking a dot opens the built-in screen to file or renew that document, and *Companies and settings* opens the rest (companies, reminders, archive).

**Who sees it.** Owners and admins always (`access.roles`); anyone else who has been granted the Entity Compliance tool in Users & roles (`access.tool` = `doc-watch`, the grant's technical name). What a person sees inside is still filtered by the same rules as before: their companies, their team's, or everything for admins.

**Where the data lives.** Nowhere new. Both pages use providers (`compliance.grid`, `compliance.documents`) that read the compliance tables Core already keeps. This folder has no `data/` blocks of its own yet.

**Pages.**

| Page | Type | Source | Shows |
| --- | --- | --- | --- |
| `grid` | grid | provider `compliance.grid` | the dot matrix, five summary tiles, the legend |
| `documents` | table | provider `compliance.documents` | one row per live document; search; filter by status |

**Export (1.0.99).** The grid page's *Export* button (a `links` block, `w_export`) downloads `Entity Compliance <date>.xlsx`
from Core's `GET /api/ec/export` (`cbx/api/ec_export_api.py`, book in `cbx/docwatch/ec_export.py`): only the companies the
person may see, through these tables' own scopes; archived ones on their own sheet, for admins only. Sheets: Overview,
Documents, Expiring and expired, Entities, Document types, About. Audited as `ec_export` (counts only).

**Words.** Every label is a key in `languages/en.json`; `ar`, `pt` and `fr` carry the same keys. A key a language lacks falls back to English. Arabic pages are drawn right-to-left by the renderer.

**How to change it.** Admin → Builder → Entity Compliance. Change a label, add a column to the documents table, or change who sees it; preview; save the draft; release. The release runs the tests, you sign it off on Versions, and the ten-minute notice goes out. Nothing here is code.

**Tests.** `tests/test_entity_compliance.py` in this folder (one file per tool, named after it): the folder is sound, the pages answer for an owner, and a person without the grant is refused.
