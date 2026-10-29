"""PDF Studio engine: read PDFs into positional text, learn a layout profile (rules), extract
typed cells with provenance, score, repair, and learn from user corrections.

Modules
  money       – SA number / money / date parsing
  spans       – PDF → words with x/y/page (pdfplumber; OCR fallback), cached on disk
  profile     – ruleset schema, layout fingerprints, heuristic authoring, C-EXA prompts
  extract     – run a ruleset over one document → cells with quote + bbox
  scoring     – field / row / column / batch scores and gates
  repair      – diagnose failing columns and patch the ruleset
  corrections – verify a user-typed value against the PDF and learn a rule from it
  formula     – safe evaluator for `formula` columns (material_excl_vat * 1.10), no eval()
  pipeline    – the background job for a batch (filename / optional / formula column kinds,
                sibling-profile rule reuse, optional-by-evidence, re-runs with history)
  export      – xlsx workbook and master-sheet append; `columns_of` is the one column-dict builder
  profiles    – small helpers used by other features (pending_count) and the bench
  housekeeping– hourly routine: prune empty batches, fail uploads that never finished
"""
