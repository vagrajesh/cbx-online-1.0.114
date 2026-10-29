"""IFRS 16 tool: monthly Lease Accelerator imports from the RR06/ARO06 movements and the LA register.

`engine/` is the finance team's validated accounting engine — lease_engine.py, ledger_check.py and the
Sequentra API client sequentra_api.py of their desktop IFRS 16 Workflow 5.16.0 (1.0.80), copied UNMODIFIED with
its LA templates. It is used as a library exactly as the desktop uses it — put on sys.path and imported by
module name (`import lease_engine`). Never edit anything under engine/; a new desktop version is copied in whole.

1.0.106 (CBX-Connect 2.10.9 / old CBX 1.0.106, the same engine files in both): the readers were changed so the team's real
exports run - lease_engine.py: whole books read once (kept 15 minutes), calamine first, files read by what they really
are (an .xls that is an .xlsx, a web page, a CSV), title blocks up to 30 rows, the RR06 move type column with no heading,
text numbers ('1 234,56'), Excel serial dates, NaT, 2026/07/01, wrong-slot hints (what_file), the Sequentra address
from Building Number + Street Name, a fractional Discount Rate as a percentage; rr06_recon.py: Current Charges under
their merged banner, 'Current Lease Term (Years)'; ledger_check.py: a title block above the headings, text amounts.
A new desktop copy must carry these (or the same fixes) - tests/test_ifrs16_inputs_*.py fail without them.
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent / "engine"
DATA_DIR = ENGINE_DIR / "data"
CODES_PATH = DATA_DIR / "codes.xlsx"
RATES_PATH = DATA_DIR / "discount_rates.xlsx"

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
