# 1.0.98: from CBX-Connect 2.10.3 cbx/xlsafe.py
"""2.9.3: text from outside (a mail subject, a PDF's name, a cell someone typed, an uploaded sheet) goes into Excel
and CSV files as plain text, never as a live formula. Numbers stay numbers."""
from __future__ import annotations

RISKY = ("=", "+", "-", "@", "\t", "\r")


def risky(v) -> bool:
    return isinstance(v, str) and v.startswith(RISKY)


def cell(c):
    """An openpyxl cell: a text starting with = + - @ tab or CR keeps its value and is stored as text."""
    if risky(c.value):
        c.data_type = "s"
    return c


def sheet(ws) -> None:
    for row in ws.iter_rows():
        for c in row:
            cell(c)


def book(wb) -> None:
    for ws in wb.worksheets:
        sheet(ws)


def csv_text(v):
    """For a CSV cell: such a text gets a leading ' so a spreadsheet shows it instead of running it."""
    return "'" + v if risky(v) else v
