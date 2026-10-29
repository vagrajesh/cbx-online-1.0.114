"""Site Schedules (1.0.89), the page CBX-Connect 2 has: drop the site's Swiftnet PDFs (quotes, the NTP, an earlier
agreement) → they are read (reader.py) into one agreement → a person checks the fields and the charge tables
(every quote comment its own row) beside the Word page, picks who signs → Word, from the template an admin
edits (template.py). Every read, save, Word file and download is kept on the agreement's history.

Who: anyone with the Site Schedules tool sees the agreements they made; admins see all. Admins edit the
template; every save is a version that can be gone back to. The people who sign (and their drawn signatures)
are one shared list.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from ..models import AppState, StoredFile, User
from ..models.schedules import SsAgreement
from ..warehouse import storage
from . import reader as R
from . import template as T

SLUG = "site-schedules"
MAX_MB = 25
MAX_FILES = 12
TPL_KEY, PEOPLE_KEY, CODES_KEY = "ss.template", "ss.people", "ss.co_codes"
LEASE_CODES = R.LEASE_CODES  # 1.0.108: one list (reader.py): the lease types and their file-name codes
CO_CODES = {"vodacom": "VDC", "mtn": "MTN", "telkom": "TKM", "cell c": "CELLC", "rain": "RAIN"}

# the form, top to bottom: (section, [(key, label, kind)]) — the same as CBX-Connect 2
FORM = [
    ("Document", [("lease_type", "Lease type", "lease"), ("company", "Co-user company", "text"), ("co_code", "Co-user code (for the file name)", "text"),
                  ("col_id", "Co-locate ID", "text"), ("soc_no", "Swiftnet SOC No", "text"), ("contract_no", "Contract No", "text"),
                  ("t100_ref", "T100 reference", "ref"), ("t000_ref", "T000 reference", "ref"), ("lease_code", "Lease code (for the file name)", "text")]),
    ("Represented by", [("swiftnet_rep", "Swiftnet representative", "text"), ("couser_rep", "Co-user representative", "text")]),
    ("Swiftnet site", [("site_name", "Site name", "text"), ("stac", "STAC code", "text"), ("pid", "Site PID", "text"), ("region", "Region", "text"),
                       ("lat", "Latitude", "text"), ("lon", "Longitude", "text"), ("struct_name", "Structure / mast name", "text"),
                       ("struct_cat", "Structure category", "text"), ("site_owner", "Site owner", "text")]),
    ("Co-user site", [("co_site_name", "Co-user site name", "text"), ("bs_site_no", "BS / site no", "text"), ("co_region", "Co-user region", "text")]),
    ("Duration", [("start_date", "Commencement date (01 August 2026)", "text"), ("term_years", "Term: years", "int"), ("term_months", "Term: months", "int"),
                  ("esc_year", "First escalation: 01 April of", "int")]),
    ("Infrastructure", [("mast_cat", "Mast category", "text"), ("mast_hagl", "Mast height HAGL (m)", "text"), ("inst_hagl", "Installation height HAGL (m)", "text")]),
    ("Scope of approved work", [("scope", "Scope of approved work (from the SOW / NTP)", "long"), ("equip_comments", "Equipment / accommodation comments", "long"),
                                ("current_install", "Client current installation on site", "long")]),  # 1.0.108: its own heading in clause 7
]
KNOWN = {k for _, items in FORM for k, _, _ in items}
REQUIRED = ["lease_type", "company", "col_id", "site_name", "pid", "region", "lat", "lon", "co_site_name", "bs_site_no", "co_region", "start_date",
            "mast_cat", "mast_hagl", "inst_hagl", "t100_ref", "t000_ref"]
SEED_PEOPLE = {"swift": [{"name": "Eddy Kgomongwe", "title": "Chief Commercial Officer for Swiftnet"}, {"name": "Gert Vorster", "title": "CFO for Swiftnet"},
                         {"name": "Paul Jansen van Rensburg", "title": "CEO for Swiftnet"}, {"name": "ET Kgomongwe", "title": "Authorised Representative"}],
               "couser": [{"name": "Eugene Roets", "title": "Financial Director"}, {"name": "Beverly Ngwenya", "title": "CTO"},
                          {"name": "Akshay Sukhlal", "title": "Authorised Representative"}]}


class Refused(Exception):
    pass


# ---- small settings kept in app_state (text there: JSON) ---------------------------------------------------
def _get(db: Session, key: str, default: Any) -> Any:
    st = db.get(AppState, key)
    if st is None or not st.value:
        return default
    try:
        return json.loads(st.value)
    except ValueError:
        return default


def _put(db: Session, key: str, value: Any) -> None:
    st = db.get(AppState, key)
    text = json.dumps(value)
    if st is None:
        db.add(AppState(key=key, value=text))
    else:
        st.value, st.updated_at = text, utcnow()
    db.flush()


def _by(me: User | None) -> str:
    return (me.name or me.email) if me else ""


def _note(a: SsAgreement, me: User | None, what: str) -> None:
    a.history = ([*(a.history or []), {"at": utcnow().isoformat(), "by": _by(me), "what": what[:300]}])[-200:]


# ---- the template and its versions ------------------------------------------------------------------------
def template_state(db: Session) -> dict:
    v = _get(db, TPL_KEY, {})
    if not isinstance(v, dict) or not v.get("versions"):
        v = {"current": 0, "versions": []}
    return v


def current_template(db: Session) -> tuple[int, dict]:
    v = template_state(db)
    for x in v["versions"]:
        if x["n"] == v["current"]:
            return x["n"], T.upgrade(x["template"])  # 1.0.107: the standard words that changed, and the Swiftnet signing setting
    return 0, T.upgrade(T.default())


def save_template(db: Session, me: User, tpl: dict, note: str = "") -> int:
    if not me.is_admin:
        raise PermissionError("Admins change the template.")
    probs = T.check(tpl)
    if probs:
        raise Refused(" ".join(probs[:5]))
    clean = {"font": str(tpl.get("font") or "Arial")[:40], "size": max(8, min(14, float(tpl.get("size") or 11))),
             "margin_cm": max(1.0, min(4.0, float(tpl.get("margin_cm") or 2.54))), "footer": str(tpl.get("footer") or "")[:300], "blocks": tpl["blocks"],
             "swift_sign": T.swift_sign(tpl)}  # 1.0.107: where Swiftnet signs and who for it
    v = template_state(db)
    n = max([x["n"] for x in v["versions"]] + [0]) + 1
    v["versions"] = ([*v["versions"], {"n": n, "at": utcnow().isoformat(), "by": _by(me), "note": (note or "")[:200], "template": clean}])[-40:]
    v["current"] = n
    _put(db, TPL_KEY, v)
    log(db, "schedule_template_saved", actor=me, target=SLUG, detail=f"version {n}: {note[:120]}")
    return n


def use_version(db: Session, me: User, n: int) -> int:
    if not me.is_admin:
        raise PermissionError("Admins change the template.")
    v = template_state(db)
    if n != 0 and not any(x["n"] == n for x in v["versions"]):
        raise Refused("That version isn't there.")
    if n == 0:  # the standard wording, as a new version so the history stays honest
        return save_template(db, me, T.default(), "Back to the standard wording")
    v["current"] = n
    _put(db, TPL_KEY, v)
    log(db, "schedule_template_version", actor=me, target=SLUG, detail=f"version {n} in use")
    return n


# ---- people who sign ------------------------------------------------------------------------------------
def people(db: Session) -> dict:
    v = _get(db, PEOPLE_KEY, None)
    if not v:
        return {k: [dict(p, sig=None) for p in x] for k, x in SEED_PEOPLE.items()}
    return v


def save_people(db: Session, me: User, data: dict) -> dict:
    out: dict = {"swift": [], "couser": []}
    for side in out:
        for p in (data.get(side) or [])[:60]:
            name = str((p or {}).get("name") or "").strip()[:120]
            if not name:
                continue
            sig = (p or {}).get("sig") or None
            if sig:
                if not isinstance(sig, str) or not sig.startswith("data:image/png;base64,") or len(sig) > 400_000:
                    raise Refused(f"{name}: a signature is a drawn picture (PNG) under 300 KB.")
                try:
                    base64.b64decode(sig.split(",", 1)[1], validate=True)
                except Exception:  # noqa: BLE001
                    raise Refused(f"{name}: that signature picture is damaged.") from None
            out[side].append({"name": name, "title": str((p or {}).get("title") or "").strip()[:160], "sig": sig})
    _put(db, PEOPLE_KEY, out)
    log(db, "schedule_people_saved", actor=me, target=SLUG, detail=f"{len(out['swift'])} Swiftnet, {len(out['couser'])} co-user")
    return out


def co_code_for(db: Session, company: str) -> str:
    codes = {**CO_CODES, **(_get(db, CODES_KEY, {}) or {})}
    low = (company or "").lower()
    return next((v for k, v in codes.items() if k and k in low), "")


def remember_co_code(db: Session, company: str, code: str) -> None:
    word = (company or "").split(" ")[0].lower()
    if word and code:
        _put(db, CODES_KEY, {**(_get(db, CODES_KEY, {}) or {}), word: code})


# ---- agreements -----------------------------------------------------------------------------------------
def can_see(me: User, a: SsAgreement) -> bool:
    return me.is_admin or a.user_id == me.id


def visible(db: Session, me: User) -> list[SsAgreement]:
    q = db.query(SsAgreement).filter(SsAgreement.status != "archived")
    if not me.is_admin:
        q = q.filter(SsAgreement.user_id == me.id)
    return q.order_by(SsAgreement.id.desc()).all()


def missing(a: SsAgreement) -> list[str]:
    f = R.fix_refs(a.fields)  # 1.0.108: "T100/" with no number is not filled in
    new = re.search(r"New Application", str(f.get("lease_type") or ""), re.I)
    return [k for k in REQUIRED if not str(f.get(k) or "").strip() and not (new and k in ("t100_ref", "t000_ref"))]


def ref_parts(f: dict) -> list[str]:
    """# 1.0.108: the two numbers at the front of the file name, each in its own place: '9130', '5183' when both are there
    (Swiftnet's naming); one alone keeps its prefix ('T000-5183') so reading the file back never makes it a T100."""
    t100, t000 = R.ref_number(f.get("t100_ref")), R.ref_number(f.get("t000_ref"))
    if t100 and t000:
        return [t100, t000]
    return [f"T100-{t100}" if t100 else "", f"T000-{t000}" if t000 else ""]


def file_name(a: SsAgreement) -> str:
    """'9130 - 5183 - 70204797 - TIERVLEI EXCHANGE - 01.08.2026 - 31.07.2031 - 7708 - R - VDC.docx' (empty parts left out)."""
    f = a.fields or {}
    v = T.values({"fields": f, "tables": a.tables or {}})
    d1, d2 = R.parse_long_date(f.get("start_date") or ""), R.parse_long_date(v.get("end_date") or "")
    parts = [*ref_parts(f), f.get("contract_no"), str(f.get("site_name") or "").upper(),
             d1.strftime("%d.%m.%Y") if d1 else "", d2.strftime("%d.%m.%Y") if d2 else "", f.get("col_id"),
             R.lease_code_for(f.get("lease_type"), f.get("lease_code")), f.get("co_code")]  # 1.0.108: the code follows the lease type
    name = " - ".join(re.sub(r"[\\/:*?\"<>|]+", " ", str(p)).strip() for p in parts if str(p or "").strip())
    return (name or f"Site Schedule {a.id}")[:200] + ".docx"


def downloads(a: SsAgreement) -> int:
    return sum(1 for h in (a.history or []) if str(h.get("what", "")).startswith("Downloaded"))


def out(db: Session, a: SsAgreement, full: bool = False) -> dict:
    f = a.fields or {}
    d = {"id": a.id, "title": a.title or f.get("site_name") or f"Agreement {a.id}", "status": a.status, "company": f.get("company", ""),
         "lease_type": f.get("lease_type", ""), "start_date": f.get("start_date", ""), "missing": missing(a), "notes": a.notes or [],
         "files": a.files or [], "file_name": file_name(a), "downloads": downloads(a),
         "updated": (a.updated_at or a.created_at).isoformat() if (a.updated_at or a.created_at) else "", "agreement": None}
    if a.agreement_file_id:
        sf = db.get(StoredFile, a.agreement_file_id)
        d["agreement"] = {"id": a.agreement_file_id, "name": sf.name if sf else "agreement.docx"}
    if full:
        f = R.fix_refs(f)  # 1.0.108: an agreement saved before shows "T100/<number>" too
        d.update(fields=f, tables=a.tables or {}, signing=a.signing or {}, history=list(reversed(a.history or []))[:100],
                 values=T.values({"fields": f, "tables": a.tables or {}, "signing": a.signing or {}}, tpl=current_template(db)[1]))
    return d


def _store(db: Session, me: User, a: SsAgreement, name: str, data: bytes) -> StoredFile:
    return storage.store(db, me, name, data, sub="Inbox", source=SLUG, mirror=False, meta={"purpose": SLUG, "agreement_id": a.id})


def _blank_signing() -> dict:
    # 1.0.107: Swiftnet's place and people come from the template's setting, not the agreement
    return {"swift": {"city": "", "date": "", "witnesses": ["", ""], "people": []}, "couser": {"city": "", "date": "", "witnesses": ["", ""], "people": []}}


def create(db: Session, me: User, uploads: list[tuple[str, bytes]]) -> SsAgreement:
    a = SsAgreement(user_id=me.id, fields={"lease_type": "New Application", "soc_no": "4320151972", "term_years": 5, "term_months": 0},
                    tables={"t1": [], "t2": [], "t3": [], "t4": [], "extra": []}, signing=_blank_signing(), history=[])
    db.add(a)
    db.flush()
    _note(a, me, "Started")
    if uploads:
        read(db, me, a, uploads)
    a.title = (a.fields or {}).get("site_name") or a.title
    log(db, "schedule_agreement_made", actor=me, target=f"ss_agreement:{a.id}", detail=(a.title or "")[:120])
    return a


def read(db: Session, me: User, a: SsAgreement, uploads: list[tuple[str, bytes]]) -> SsAgreement:
    """Add PDFs to the agreement and read ALL of its PDFs again — the fields and tables start over (the signing
    people and the representatives stay)."""
    if len(uploads) + len(a.files or []) > MAX_FILES:
        raise Refused(f"At most {MAX_FILES} PDFs on one agreement.")
    files = [dict(x) for x in (a.files or [])]
    for name, data in uploads:
        if not data:
            raise Refused(f"{name} is empty.")
        if len(data) > MAX_MB * 1024 * 1024:
            raise Refused(f"PDFs up to {MAX_MB} MB.")
        if not name.lower().endswith(".pdf") or not data.startswith(b"%PDF"):
            raise Refused(f"{name}: Swiftnet PDFs only (quotes, the NTP, the SOW, an earlier agreement).")
        sf = _store(db, me, a, name, data)
        files.append({"file_id": sf.id, "name": sf.name, "kind": "", "pages": 0, "at": utcnow().isoformat(), "by": _by(me)})
    blobs = []
    for x in files:
        sf = db.get(StoredFile, x["file_id"])
        if sf is not None:
            try:
                blobs.append((x["name"], storage.read_bytes(sf)))
            except OSError:
                pass
    r = R.read_pdfs(blobs)
    kinds = {i["name"]: i for i in r["files"]}
    for x in files:
        k = kinds.get(x["name"]) or {}
        x["kind"], x["pages"] = k.get("kind", ""), k.get("pages", 0)
    keep = {k: (a.fields or {}).get(k) for k in ("swiftnet_rep", "couser_rep", "soc_no") if (a.fields or {}).get(k)}
    fields = {"soc_no": "4320151972", **{k: v for k, v in r["fields"].items() if v not in (None, "")}, **keep}
    if not fields.get("co_code") and fields.get("company"):
        fields["co_code"] = co_code_for(db, fields["company"])
    if fields.get("start_date"):
        d = R.parse_long_date(fields["start_date"])
        if d:
            fields.setdefault("esc_year", R.first_escalation_year(d))  # 1.0.107: 01 Jan–31 Mar → 01 April that year
    a.fields = fields
    a.tables = {**r["tables"], "extra": (a.tables or {}).get("extra") or []}
    a.notes = r["notes"]
    a.files = files
    a.title = fields.get("site_name") or a.title
    a.status = "draft"
    a.updated_at = utcnow()
    added = ", ".join(n for n, _ in uploads)
    _note(a, me, (f"Added {added} and read " if added else "Read ") + f"{len(files)} PDF{'s' if len(files) != 1 else ''}")
    return a


def save(db: Session, me: User, a: SsAgreement, data: dict) -> SsAgreement:
    what = []
    if "fields" in data and isinstance(data["fields"], dict):
        f = dict(a.fields or {})
        old_start = R.parse_long_date(str(f.get("start_date") or ""))
        for k, v in data["fields"].items():
            if k in KNOWN:
                new = (int(v) if str(v).strip().lstrip("-").isdigit() else None) if k in ("term_years", "term_months", "esc_year") else str(v or "").strip()[:4000]
                if k in R.REF_PREFIX:
                    new = R.ref_text(k, new)  # 1.0.108: compared as it will be kept, so the history says only what changed
                if new != f.get(k):
                    what.append(k)
                f[k] = new
        f = R.fix_refs(f)  # 1.0.108: whatever was typed or saved: "T100/<number>", "T000/<number>" or nothing
        if f.get("start_date") and not R.parse_long_date(f["start_date"]):
            raise Refused("Commencement date: write it like 01 August 2026.")
        new_start = R.parse_long_date(str(f.get("start_date") or ""))
        if new_start and new_start != old_start and (not f.get("esc_year") or (old_start and f.get("esc_year") == R.first_escalation_year(old_start))):
            f["esc_year"] = R.first_escalation_year(new_start)  # 1.0.107: the first escalation follows a new date unless a person set their own
        a.fields = f
        if f.get("co_code") and f.get("company"):
            remember_co_code(db, f["company"], f["co_code"])
    if "tables" in data and isinstance(data["tables"], dict):
        t = {}
        for k in ("t1", "t2", "t3", "t4"):
            t[k] = [{c: str((r or {}).get(c) or "").strip()[:600] for c in ("item", "req", "desc", "amt")} for r in (data["tables"].get(k) or [])[:60]]
            for r in t[k]:
                if r["amt"]:
                    r["amt"] = R.fmt_zar(r["amt"])
        t["extra"] = []
        for x in (data["tables"].get("extra") or [])[:10]:
            cols = [str(c)[:60] for c in (x.get("cols") or T.COLUMNS)][:8] or T.COLUMNS
            rows = [[str(v or "")[:600] for v in (r or [])][:len(cols)] for r in (x.get("rows") or [])[:60]]
            for r in rows:
                r += [""] * (len(cols) - len(r))
                if r[-1]:
                    r[-1] = R.fmt_zar(r[-1])
            t["extra"].append({"title": str(x.get("title") or "")[:160], "cols": cols, "rows": rows})
        if t != {k: (a.tables or {}).get(k) for k in t}:
            what.append("charges")
        a.tables = t
    if "signing" in data and isinstance(data["signing"], dict):
        s = {}
        for side in ("swift", "couser"):
            x = data["signing"].get(side) or {}
            s[side] = {"city": str(x.get("city") or "")[:80], "date": str(x.get("date") or "")[:40],
                       "witnesses": [str(w or "")[:120] for w in (x.get("witnesses") or [])][:6],
                       "people": [{"name": str(p.get("name") or "")[:120], "title": str(p.get("title") or "")[:160]} for p in (x.get("people") or [])[:6] if (p or {}).get("name")]}
        if s != (a.signing or {}):
            what.append("who signs")
        a.signing = s
    a.title = (a.fields or {}).get("site_name") or a.title
    if what:
        if a.status == "built":
            a.status = "draft"  # changed after the Word file was made: make it again
        a.updated_at = utcnow()
        # one line per person per few minutes, not one per keystroke
        h = list(a.history or [])
        if h and h[-1].get("by") == _by(me) and str(h[-1].get("what", "")).startswith("Changed") and _recent(h[-1].get("at")):
            h[-1] = {**h[-1], "at": utcnow().isoformat(), "what": _merge_changed(h[-1]["what"], what)}
            a.history = h
        else:
            _note(a, me, "Changed " + ", ".join(_label(k) for k in what))
    return a


def _label(k: str) -> str:
    for _, items in FORM:
        for key, lab, _ in items:
            if key == k:
                return lab.split(" (")[0].lower()
    return k


def _merge_changed(old: str, keys: list[str]) -> str:
    have = [x.strip() for x in old[len("Changed "):].split(",") if x.strip()]
    for k in keys:
        if _label(k) not in have:
            have.append(_label(k))
    return ("Changed " + ", ".join(have))[:300]


def _recent(iso: Any, minutes: int = 10) -> bool:
    from datetime import datetime, timedelta, timezone

    try:
        t = datetime.fromisoformat(str(iso))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        now = utcnow()
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return now - t < timedelta(minutes=minutes)
    except (TypeError, ValueError):
        return False


def build(db: Session, me: User, a: SsAgreement) -> tuple[SsAgreement, StoredFile]:
    n, tpl = current_template(db)
    data = T.to_docx(tpl, {"fields": a.fields or {}, "tables": a.tables or {}, "signing": a.signing or {}}, people(db))
    owner = db.get(User, a.user_id) or me
    sf = storage.store(db, owner, file_name(a), data, sub="Results", source=SLUG, mirror=False, meta={"purpose": SLUG, "agreement_id": a.id, "built": True})
    a.agreement_file_id, a.status, a.template_version, a.updated_at = sf.id, "built", n, utcnow()
    _note(a, me, f"Made the Word file ({sf.name})" + (f" with template version {n}" if n else ""))
    log(db, "schedule_agreement_built", actor=me, target=f"ss_agreement:{a.id}", detail=sf.name[:200])
    return a, sf


def downloaded(db: Session, me: User, sf: StoredFile) -> None:
    """Someone took a file of an agreement: kept on its history (and the audit trail)."""
    aid = (sf.meta or {}).get("agreement_id")
    a = db.get(SsAgreement, int(aid)) if aid else None
    if a is None:
        return
    word = "the Word file" if (sf.meta or {}).get("built") else sf.name
    _note(a, me, f"Downloaded {word}")
    log(db, "schedule_file_downloaded", actor=me, target=f"ss_agreement:{a.id}", detail=sf.name[:200])


def archive(db: Session, me: User, a: SsAgreement) -> None:
    a.status, a.updated_at = "archived", utcnow()
    _note(a, me, "Put away")
    log(db, "schedule_agreement_archived", actor=me, target=f"ss_agreement:{a.id}", detail=(a.title or "")[:120])


def can_get_file(db: Session, me: User, sf: StoredFile) -> bool:
    meta = sf.meta or {}
    if meta.get("purpose") != SLUG or not meta.get("agreement_id"):
        return False
    a = db.get(SsAgreement, int(meta["agreement_id"]))
    return bool(a and can_see(me, a))
