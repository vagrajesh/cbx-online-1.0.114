# 1.0.104: new (Builder U) — V07 item 3: Settings → Tariffs (admins, upload/download)
"""The tariff table the rate check uses before an account's own history: one row per profile or municipality, tariff
name and effective period, with c/kWh (optionally blocks or time-of-use rates) and the fixed charges per month (excl. VAT).

    rows(db) / out(t)          the table
    save(db, user, data, id)   add or change one row (admins; audited ap_tariff)
    delete(db, user, id)
    upload(db, user, data, name)  an .xlsx or .csv replaces the table (audited ap_tariff, with the counts)
    download(db)               the table as .xlsx (the same columns the upload takes)
    match(db, profile, municipality, tariff, on)  the row for a bill (None when the table has none)
    expected(t, kwh)           the electricity charge excl. VAT the row gives for that many kWh

Blocks are written "upto=c/kWh" separated by ";" with the last one open ("500=150.2; =180.4"); time-of-use rates
"peak=250; standard=180; offpeak=120" are kept but the check uses c/kWh for them (the bill history has no TOU split)."""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..models import User
from ..models.ap import ApTariff
from .bill_history import iso, num

COLUMNS = [("profile", "Profile"), ("municipality", "Municipality"), ("tariff", "Tariff"), ("effective_from", "Effective from"),
           ("effective_to", "Effective to"), ("c_per_kwh", "c/kWh"), ("blocks", "Blocks or TOU"), ("fixed_r", "Fixed R per month"), ("note", "Note")]
MAX_ROWS = 5000


class Refused(Exception):
    pass


def _who(u: Optional[User]) -> str:
    return (u.name or u.email) if u else ""


def key(v: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def blocks_in(v: Any) -> list[dict]:
    """"500=150.2; =180.4" → [{upto_kwh: 500, c_per_kwh: 150.2}, {upto_kwh: None, c_per_kwh: 180.4}];
    "peak=250; offpeak=120" → [{tou: "peak", c_per_kwh: 250}, …]. A list is taken as it is (cleaned)."""
    out: list[dict] = []
    if isinstance(v, list):
        for b in v:
            if isinstance(b, dict) and num(b.get("c_per_kwh")) is not None:
                if b.get("tou"):
                    out.append({"tou": str(b["tou"])[:20], "c_per_kwh": num(b["c_per_kwh"])})
                else:
                    out.append({"upto_kwh": num(b.get("upto_kwh")), "c_per_kwh": num(b["c_per_kwh"])})
        return out
    for part in re.split(r"[;\n]", str(v or "")):
        if "=" not in part:
            continue
        a, b = (x.strip() for x in part.split("=", 1))
        rate = num(b)
        if rate is None:
            raise Refused(f'"{part.strip()}" is not upto=c/kWh (e.g. 500=150.2; =180.4).')
        if a and num(a) is None:
            out.append({"tou": a.lower()[:20], "c_per_kwh": rate})
        else:
            out.append({"upto_kwh": num(a), "c_per_kwh": rate})
    return out


def blocks_text(bs: Optional[list]) -> str:
    parts = []
    for b in bs or []:
        if b.get("tou"):
            parts.append(f"{b['tou']}={b['c_per_kwh']:g}")
        else:
            parts.append(f"{'' if b.get('upto_kwh') is None else format(b['upto_kwh'], 'g')}={b['c_per_kwh']:g}")
    return "; ".join(parts)


def out(t: ApTariff) -> dict:
    return {"id": t.id, "profile": t.profile, "municipality": t.municipality, "tariff": t.tariff, "effective_from": t.effective_from,
            "effective_to": t.effective_to, "c_per_kwh": t.c_per_kwh, "blocks": blocks_text(t.blocks), "fixed_r": t.fixed_r, "note": t.note,
            "updated_by": t.updated_by}


def rows(db: Session) -> list[ApTariff]:
    return db.query(ApTariff).order_by(ApTariff.municipality, ApTariff.profile, ApTariff.tariff, ApTariff.effective_from).all()


def _clean(data: dict) -> dict:
    v = {k: str(data.get(k) or "").strip() for k in ("profile", "municipality", "tariff", "note")}
    v["profile"], v["municipality"], v["tariff"], v["note"] = v["profile"][:64], v["municipality"][:160], v["tariff"][:120], v["note"][:300]
    for k in ("effective_from", "effective_to"):
        raw = data.get(k)
        if isinstance(raw, date):
            raw = raw.isoformat()
        d = iso(raw) if str(raw or "").strip() else ""
        if str(raw or "").strip() and not d:
            raise Refused(f"{dict(COLUMNS)[k]} is not a date (yyyy-mm-dd).")
        v[k] = d
    if v["effective_from"] and v["effective_to"] and v["effective_to"] < v["effective_from"]:
        raise Refused("Effective to is before effective from.")
    v["c_per_kwh"] = num(data.get("c_per_kwh")) if str(data.get("c_per_kwh") or "").strip() else None
    v["fixed_r"] = num(data.get("fixed_r")) if str(data.get("fixed_r") or "").strip() else None
    v["blocks"] = blocks_in(data.get("blocks"))
    if not (v["profile"] or v["municipality"]):
        raise Refused("A tariff row names the profile or the municipality.")
    if v["c_per_kwh"] is None and not v["blocks"]:
        raise Refused("A tariff row needs c/kWh or blocks.")
    if any(x is not None and x < 0 for x in (v["c_per_kwh"], v["fixed_r"])):
        raise Refused("Rates and fixed charges are not negative.")
    return v


def save(db: Session, user: User, data: dict, tid: Optional[int] = None) -> ApTariff:
    if not user.is_admin:
        raise PermissionError("Only an admin changes the tariffs.")
    v = _clean(data or {})
    t = db.get(ApTariff, tid) if tid else None
    if tid and t is None:
        raise LookupError("No such tariff row.")
    if t is None:
        t = ApTariff()
        db.add(t)
    for k, x in v.items():
        setattr(t, k, x)
    t.updated_by = _who(user)
    db.flush()
    log(db, "ap_tariff", actor=user, target=f"ap_tariff:{t.id}", detail=f"{'changed' if tid else 'added'}: {t.municipality or t.profile} · {t.tariff or 'any tariff'} "
        f"from {t.effective_from or 'always'}: {t.c_per_kwh if t.c_per_kwh is not None else blocks_text(t.blocks)} c/kWh, fixed R {t.fixed_r or 0}"[:400])
    return t


def delete(db: Session, user: User, tid: int) -> None:
    if not user.is_admin:
        raise PermissionError("Only an admin changes the tariffs.")
    t = db.get(ApTariff, tid)
    if t is None:
        raise LookupError("No such tariff row.")
    log(db, "ap_tariff", actor=user, target=f"ap_tariff:{t.id}", detail=f"deleted: {t.municipality or t.profile} · {t.tariff or 'any tariff'} from {t.effective_from or 'always'}"[:400])
    db.delete(t)


# ---- upload / download ------------------------------------------------------------------------------------------------------
def _table(data: bytes, name: str) -> list[list]:
    if name.lower().endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [r for r in csv.reader(io.StringIO(text))]
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        raise Refused("That file is not an Excel workbook (.xlsx) or a CSV.") from None
    try:
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def upload(db: Session, user: User, data: bytes, name: str) -> dict:
    """The file's rows replace the table (first sheet; a header row with the download's column names)."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the tariffs.")
    grid = _table(data, name or "")
    head = next((i for i, r in enumerate(grid[:20]) if r and any(key(c) in ("tariff", "ckwh", "municipality") for c in r)), None)
    if head is None:
        raise Refused("No header row: the columns are " + ", ".join(lbl for _k, lbl in COLUMNS) + ".")
    want = {key(lbl): k for k, lbl in COLUMNS} | {key(k): k for k, _l in COLUMNS} | {"centsperkwh": "c_per_kwh", "fixed": "fixed_r", "blocks": "blocks", "tou": "blocks"}
    cols = {i: want[key(c)] for i, c in enumerate(grid[head]) if key(c) in want}
    new, bad = [], []
    for n, r in enumerate(grid[head + 1:], head + 2):
        if not r or all(c in (None, "") for c in r):
            continue
        rec = {k: r[i] if i < len(r) else None for i, k in cols.items()}
        try:
            new.append(_clean(rec))
        except Refused as e:
            bad.append(f"row {n}: {e}")
        if len(new) > MAX_ROWS:
            raise Refused(f"More than {MAX_ROWS} tariff rows.")
    if bad:
        raise Refused("Nothing was changed. " + "; ".join(bad[:8]) + (f" (and {len(bad) - 8} more)" if len(bad) > 8 else ""))
    old = db.query(ApTariff).count()
    db.query(ApTariff).delete()
    for v in new:
        db.add(ApTariff(**v, updated_by=_who(user)))
    db.flush()
    log(db, "ap_tariff", actor=user, target="ap_tariffs", detail=f"uploaded {name or 'a file'}: {len(new)} rows replace {old}"[:400], count=len(new))
    return {"rows": len(new), "replaced": old}


def download(db: Session) -> bytes:
    import openpyxl

    from .. import xlsafe

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tariffs"
    ws.append([lbl for _k, lbl in COLUMNS])
    for t in rows(db):
        o = out(t)
        ws.append([o[k] if o[k] is not None else "" for k, _l in COLUMNS])
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- the rate check's row ----------------------------------------------------------------------------------------------------
def match(db: Session, profile: str, municipality: str, tariff: str, on: str) -> Optional[ApTariff]:
    """The tariff row for a bill: same profile (or municipality), same tariff name (a row with no name fits any), in
    force on the day (yyyy-mm-dd). A named row beats an unnamed one; the latest effective-from wins."""
    pk, mk, tk = key(profile), key(municipality), key(tariff)
    best, rank = None, None
    for t in db.query(ApTariff).all():
        if not ((pk and key(t.profile) == pk) or (mk and key(t.municipality) and (key(t.municipality) in mk or mk in key(t.municipality)))):
            continue
        if key(t.tariff) and key(t.tariff) != tk:
            continue
        if on and ((t.effective_from and on < t.effective_from) or (t.effective_to and on > t.effective_to)):
            continue
        r = (bool(key(t.tariff)), bool(pk and key(t.profile) == pk), t.effective_from or "", t.id)
        if rank is None or r > rank:
            best, rank = t, r
    return best


def expected(t: ApTariff, kwh: Optional[float]) -> Optional[dict]:
    """{energy, fixed, total (R excl. VAT), c_per_kwh_used, how} for that many kWh, else None."""
    if kwh is None or kwh <= 0:
        return None
    steps = [b for b in t.blocks or [] if not b.get("tou")]
    if steps:
        energy, left, last = 0.0, kwh, 0.0
        for b in steps:
            top = b.get("upto_kwh")
            take = left if top is None else max(0.0, min(left, top - last))
            energy += take * b["c_per_kwh"] / 100
            left -= take
            last = top if top is not None else last
            if left <= 0:
                break
        if left > 0:
            energy += left * steps[-1]["c_per_kwh"] / 100
        how = "blocks " + blocks_text(steps)
    elif t.c_per_kwh is not None:
        energy, how = kwh * t.c_per_kwh / 100, f"{t.c_per_kwh:g} c/kWh"
    else:
        return None
    fixed = t.fixed_r or 0.0
    return {"energy": round(energy, 2), "fixed": round(fixed, 2), "total": round(energy + fixed, 2), "how": how + (f" + R {fixed:,.2f} fixed" if fixed else "")}
