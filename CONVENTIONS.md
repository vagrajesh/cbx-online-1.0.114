# CBX-Online — build conventions

Read this before adding anything. The product is for ~1 900 non-technical staff; the owner (Edwin) is
non-technical too. "Less is more" for users; depth lives on admin/owner pages.

## Layout
- `cbx/` FastAPI backend. `cbx/main.py` boots; feature routers are listed in `cbx/api/registry.py`
  (already registered — just create the module with a `router`). Views are listed in `web/views/index.js`
  (already registered — replace the stub file).
- `cbx/models/*.py` SQLAlchemy models. Use `UTCDateTime` (from `cbx.db`) for datetimes, `JSON` for
  structured blobs. Only edit the model file for your feature.
- `cbx/security/deps.py`: `current_user`, `require_admin`, `require_owner`, `require_human` (blocks C-EXA),
  `require_tool("pdf-studio")`. **Every endpoint declares one.** Deny by default.
- `cbx/security/rls.py`: `scope(q, Model, user)` for owned rows; `filter_rows(db, user, table, rows)` for
  shared row data (master sheets, docs). Users see their own things; admins/owners see all.
- `cbx/audit.py::log(db, action, actor=user, target=, detail=, outcome=)` — call it for anything that
  changes state or is denied. C-EXA acts as the `User` with `is_axl=True` (get it via
  `db.query(User).filter(User.is_axl.is_(True)).one()`); label sub-agents as `actor_label="axl→agent:<name>"`.
- `cbx/cexa/gateway.py::Gateway(db).json(task, system, user, schema)` / `.text(...)` — the only way to call a
  model. Never call Ollama directly. Tests run with `CBX_TESTING=1` → stub mode (`cbx/cexa/stub.py`) — extend
  the stub if your task needs deterministic answers.
- `cbx/jobs.py::submit(fn, *args)` for background work; `@routine(name, seconds)` for periodic C-EXA routines.
  Tests set `jobs.set_inline(True)`.
- `cbx/warehouse/storage.py::store(db, user, filename, bytes, sub="Inbox"|"Work"|"Results", source=...)`
  stores files (person folder + shared-library mirror). Results use `source="result"` (not mirrored).
- `cbx/pdfstudio/harvest.py`: `list_files(kind, path, subfolders)` lists PDFs in a local/UNC folder or a
  OneDrive folder (plain-English `HarvestError` when it can't); `parse_doc_date(value)` turns any SA date
  shape into a `date`; `run_source(id)` / `rerun_range(...)` reuse `pipeline.run_batch` — never fork the
  extraction. `pipeline.author_missing_columns(db, template, profile)` adds/re-authors ONE column's rules
  into a new version with the other fields copied unchanged — use it instead of re-drafting a profile.
- Sessions: `cbx.db.SessionLocal()` / `session_scope()` in background jobs; `get_db` dependency in routers.

## Tool folders (1.0.43 →)
- A new work tool is a **folder** under `tools/<slug>/`, not a view and a router: `tool.json`, `pages/*.json`,
  `data/*.json`, `languages/{en,ar,pt,fr}.json`, `flows/`, `tests/test_<slug>.py`, `README.md`. Build it in
  Admin → Builder or by hand; `docs/tool-folders.md` is the reference. Blocks hold **keys**, never words.
- A page is `layout` → sections → columns → **blocks** (`cbx/engine/loader.py::WIDGET_TYPES`); a new kind of block
  is a case in `web/views/tool.js::renderWidget`, a palette entry and its questions in `web/builder/ask.js`
  (`ADD`, `TYPE_NAME`, the right-hand questions), and its checks in `loader.validate`. A page never holds code: a Code block is
  served as its own sandboxed document (`engine_api.code_response`) — never inline it into the app's page.
- Data a page shows that Core owns comes through a **provider** (`cbx/engine/providers.py`, `@provider(...)`,
  returning the grid or table shape). A tool's own tables are Data blocks in its own SQLite file
  (`cbx/engine/store.py`). Never read another tool's file.
- Code is for Core: the engine, providers, security, releases. If a request can be met with a folder, it is
  a folder. Keep a tool folder under 40k tokens (`tests/test_engine_tools.py`).
- A promotion is announced (`cbx/versions/rollout.py`): ten minutes' notice by default, `minutes=0` to switch
  now. Never call `injector.promote` from a request without going through `versions_api.promote`.

## API shape
- JSON in/out, `/api/<feature>/...`. Pydantic `BaseModel` for bodies. Return plain dicts.
- Errors: `raise HTTPException(4xx, "Plain-English sentence the user can act on.")`.
- Dates as ISO strings (`cbx.api.schemas.iso`). Money as numbers (ZAR), not strings.

## Frontend (no build step)
- ES modules in `web/`. `import { api } from '../api.js'` (get/post/patch/del/upload).
- `import { esc, toast, modal, confirmBox, pill, statusPill, fmtDate, fmtNum, fmtMoney, pct, fileDrop, avatar, aiAvatar, md, el } from '../ui.js'`.
- A view: `export default { title, navRoute?, async render(rootEl, ctx) { ... return disposeFn? } }`.
  `ctx = { me, tools, params, go(route, params), toast, refreshCounts, api }`. Navigate with `ctx.go('flow', {id})`; read `ctx.params.id`.
- Always escape user data with `esc()`. Use the existing CSS classes from `web/styles.css` (the approved
  design: `.card .hd .bd`, `.stat`, `.pill good|warn|crit|info|ai|neutral [run]`, `.list`, `.tbl table`, `.stepper`,
  `.score .bar`, `.pdfgrid`, `.sheet td.miss|filled|flag`, `.msg me|ai`, `.thinking`, `.rule[.user]`, `.kan .task`,
  `.board .tcard`, `.tree`, `.drop`, `.gauge .ring`, `.imp`). White only. No emojis. Users never see model
  names, scores or logs — put those behind `ctx.me.role !== 'user'`.
- Role split: the same view may show less for users. Check `ctx.me.role`.

## Tests
- `tests/test_<feature>.py`, pytest, using `tests/conftest.py` fixtures (`client`, `owner`, `admin`, `user`,
  `login(client, user)`). Tests must pass with no GPU and no network. Run `python -m pytest -q tests tools`:
  each tool folder's `tests/` is collected too (`tools/conftest.py` lends it the fixtures).

## Do not
- Edit `cbx/main.py`, `cbx/api/registry.py`, `web/views/index.js`, `web/app.js`, `web/styles.css`, or other
  features' files. Add your own CSS to `web/app.css` **only by appending** a clearly commented block.
- Store secrets in code. Log passwords. Let C-EXA grant access or promote to production (`require_human`).
