"""IFRS 16 ↔ the Sequentra API (1.0.80), the way the finance team's desktop IFRS 16 Workflow 5.16 links it.

The key is never in this tool: it sits in Settings → API connections (sealed, write-only, every change audited)
on a connection to https://api.excellerate.tech/sequentra/ that sends it in the `Clientkey` header and is ticked
for IFRS 16. This module finds that connection, hands the engine's own client (engine/sequentra_api.py,
unmodified) the address and the key for the length of one job, and never writes the key anywhere — the client
masks it even when a server echoes it back.

    config(db)                      → the client's APIConfig, or ApiProblem in plain words
    facts_and_steps(cfg, kind, wm)  → (lease facts, step payments or None, notes)       — before the engine runs
    cross_check(results, …)         → adds the "API Cross-Check" tab and its exceptions — after it ran
    check(cfg, month)               → what each of the ten reports returned (no data)
    download(cfg, month, to, dest)  → every report as CSV, zipped                       — to see what is there
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from . import ENGINE_DIR  # noqa: F401 — importing the package puts the engine on sys.path

BASE = "https://api.excellerate.tech/sequentra/"
HEADER = "Clientkey"
SETUP = ("Add it once in Settings → API connections → Add: name 'Sequentra', address " + BASE
         + ", key sent as a header named " + HEADER + ", paste the key you use in Power BI, and tick IFRS 16.")


class ApiProblem(ValueError):
    pass


def connection(db: Session):
    """The Sequentra connection ticked for IFRS 16: the one called 'sequentra', else any whose address is
    Sequentra's. Switched off, not ticked for IFRS 16 or without a key → ApiProblem saying which."""
    from ..models import ApiConnection

    rows = db.query(ApiConnection).all()
    c = next((x for x in rows if x.slug == "sequentra"), None) or next((x for x in rows if "/sequentra" in (x.base_url or "").lower()), None)
    if c is None:
        raise ApiProblem("There is no Sequentra connection yet. " + SETUP)
    if not c.is_active:
        raise ApiProblem(f"The connection '{c.name}' is switched off (Settings → API connections).")
    if "ifrs16" not in (c.tools or []):
        raise ApiProblem(f"The connection '{c.name}' is not ticked for IFRS 16 (Settings → API connections → Edit).")
    if not c.secret_sealed:
        raise ApiProblem(f"The connection '{c.name}' has no key. Paste it in Settings → API connections → Edit.")
    return c


def config(db: Session, *, use_charges: bool = False):
    import sequentra_api as sq  # the engine's client, unmodified

    from ..connections import service as CS

    c = connection(db)
    try:
        key = CS.secret_of(c)
    except CS.ConnectionError_ as e:
        raise ApiProblem(f"The key of '{c.name}' could not be opened ({e}). Paste it again in Settings → API connections.")
    header = (c.header_name or HEADER) if c.auth_kind == "header" else HEADER
    return sq.APIConfig(client_key=key, base_url=sq.normalise_base_url(c.base_url), header_name=header, use_charge_reports=bool(use_charges))


def facts_and_steps(cfg, kind: str, wm: date):
    import sequentra_api as sq

    try:
        return sq.fetch_for_run(cfg, kind, wm)
    except sq.SequentraAPIError as e:
        raise ApiProblem(f"Sequentra API: {e}") from None


def cross_check(results: dict, facts, cfg, kind: str, wm: date, notes: list) -> None:
    """The other reports check the run — they never change what was drafted (as the desktop does)."""
    import pandas as pd
    import sequentra_api as sq

    try:
        ref, ref_notes = sq.fetch_reference(cfg, kind, wm)
        notes += ref_notes
        cc, cc_exc = sq.cross_check(results, facts, ref, wm)
        results["API Cross-Check"] = cc
        if cc_exc:
            ex = results.get("Exceptions")
            results["Exceptions"] = pd.concat([ex, pd.DataFrame(cc_exc)], ignore_index=True) if ex is not None else pd.DataFrame(cc_exc)
        flagged = int(cc["Flags"].astype(str).ne("").sum()) if len(cc) else 0
        notes.append(f"API cross-check: {len(cc)} lease(s), {flagged} flagged")
    except Exception as e:  # noqa: BLE001 — a check that fails must not lose the month
        notes.append(f"API cross-check skipped - {str(e)[:160]}")
    rs = results.get("Recon Summary")
    if rs is not None and list(rs.columns[:2]) == ["Item", "Value"]:
        extra = [("Lease data source", f"Sequentra API ({kind}, {wm.strftime('%m/%Y')})")] + [("API", n) for n in notes]
        results["Recon Summary"] = pd.concat([rs, pd.DataFrame(extra, columns=["Item", "Value"])], ignore_index=True)


def check(cfg, month: Optional[date]) -> list[str]:
    """The desktop's Test connection + Check all reports: rows and fields per report, never the data."""
    import sequentra_api as sq

    cli = sq.SequentraClient(cfg)
    try:
        lines = cli.test_connection()
    except sq.SequentraAPIError as e:
        return [f"Not connected: {e}"]
    return lines + [""] + cli.check_all(month or sq.default_month())


def download(cfg, month: date, to_month: Optional[date], dest) -> tuple[list[str], bytes]:
    import sequentra_api as sq

    try:
        lines = sq.download_all(cfg, month, dest, to_month=to_month)
    except sq.SequentraAPIError as e:
        raise ApiProblem(f"Sequentra API: {e}") from None
    return lines, dest.with_suffix(".zip").read_bytes()


def is_sequentra(c) -> bool:
    return c is not None and (c.slug == "sequentra" or "/sequentra" in (c.base_url or "").lower())


def test(db: Session, c=None) -> dict:
    """The desktop's Test connection: one call to Building & Record tabs with the key — proves the key works and
    says whether the lease key came back. (A plain call to the address alone answers 404 even with a good key.)"""
    import sequentra_api as sq
    from datetime import datetime, timezone

    try:
        cfg = config(db)
        lines = sq.SequentraClient(cfg).test_connection()
        res = {"ok": True, "status": 200, "note": " ".join(lines[:2]), "lines": lines}
    except ApiProblem as e:
        res = {"ok": False, "status": 0, "note": str(e)}
    except sq.SequentraAPIError as e:
        res = {"ok": False, "status": getattr(e, "status", None) or 0, "note": str(e)}
    res["at"] = datetime.now(timezone.utc).isoformat()
    if c is not None:
        c.last_test = {k: v for k, v in res.items() if k != "lines"}
    return res

