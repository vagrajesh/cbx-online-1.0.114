"""Scan uploaded IFRS 16 source files and report which months they contain.

The month list comes from the movement file's "Month" column — exactly the field the engine itself
uses to pick a workflow month — so what the dropdown offers is what the engine can actually run.
(Ported as-is from the old portal's ifrs16/scanning.py; only the engine import path changed.)
"""
from __future__ import annotations

from datetime import date

from . import ENGINE_DIR  # noqa: F401 — importing the package puts the engine on sys.path


def scan_sources(la_path, movements_path, sequentra_path=None, steps_path=None):
    """
    Returns (scan_dict, error_message). scan_dict:
      months:     [{value 'YYYY-MM', label 'August 2026', rows N}] newest first
      la_rows:    rows in the LA register
      move_rows:  rows in the movement file
      steps_rows / sequentra_rows: rows if those files were supplied
      movement_label: RR06 or ARO06, as the engine would label it
    """
    import lease_engine as le  # engine module, unmodified

    scan = {"months": [], "la_rows": 0, "move_rows": 0, "steps_rows": 0, "sequentra_rows": 0, "movement_label": ""}
    try:
        la = le.load_la_register(str(la_path))
        scan["la_rows"] = int(len(la))
    except Exception as exc:  # noqa: BLE001
        return scan, f"The LA register could not be read: {exc}"

    try:
        mv, steps = le.load_movements(str(movements_path))
        scan["move_rows"] = int(len(mv))
    except Exception as exc:  # noqa: BLE001
        return scan, f"The movement file could not be read: {exc}"

    scan["movement_label"] = "ARO06" if "aro" in str(movements_path).lower() else "RR06"

    # Count rows per month, using the engine's own date parser
    counts: dict[str, int] = {}
    if "Month" in getattr(mv, "columns", []):
        for v in mv["Month"]:
            d = le._to_date(v)
            if not d:
                continue
            key = date(d.year, d.month, 1).strftime("%Y-%m")
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return scan, ("No dated rows were found in the movement file's 'Month' column, so there is nothing to choose from. Check you uploaded the RR06 / ARO06 export.")

    for key in sorted(counts, reverse=True):
        y, m = int(key[:4]), int(key[5:7])
        scan["months"].append({"value": key, "label": date(y, m, 1).strftime("%B %Y"), "rows": counts[key]})

    # optional files, reported so the user can see what was picked up. 1.0.106: one that can't be read says so
    # here (it used to read as nothing, and the month failed later on it)
    if steps_path:
        try:
            scan["steps_rows"] = int(len(le.load_step_payments(str(steps_path))))
        except Exception as exc:  # noqa: BLE001
            return scan, f"The step payments report could not be read: {exc}"
    elif steps is not None:
        scan["steps_rows"] = int(len(steps))
    if sequentra_path:
        try:
            scan["sequentra_rows"] = int(len(le.load_sequentra(str(sequentra_path))))
        except Exception as exc:  # noqa: BLE001
            return scan, f"The Sequentra export could not be read: {exc}"
    return scan, ""
