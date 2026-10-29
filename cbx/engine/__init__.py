"""The tool engine: work tools as folders, not code.

    tools/<slug>/
      tool.json            the manifest — name, menu place, who may use it, its home page
      pages/<name>.json    Page blocks: what a screen shows (a table, a grid, a form) and from where
      data/<name>.json     Data blocks: the tool's own tables (one SQLite file per tool), or a named provider
      languages/<xx>.json  every word the tool shows, in en / ar / pt / fr — a key, never text, in the blocks
      flows/               step lists (a later release)
      tests/               the tool's own tests, collected with the suite
      README.md            the tool's page in the owner's document

The engine (loader.py) reads the folders at start-up, checks them, and serves them to one renderer
(web/views/tool.js) through cbx/api/engine_api.py. Nothing here knows what a tool is for: a tool is
what its folder says. The Builder (cbx/api/builder_api.py, web/views/builder.js) edits a copy of a
folder in the data dir, previews it against live data, and packages it as a version that goes through
the ordinary pipeline — tests, sign-off, the ten-minute notice — before it is live.
"""
