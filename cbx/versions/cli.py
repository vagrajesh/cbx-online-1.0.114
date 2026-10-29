"""Command-line side of the version injector (used by update.bat and to build release zips).

    python -m cbx.versions.cli build                 → dist/cbx-online-<VERSION>.zip from this tree
    python -m cbx.versions.cli stage-and-promote X.zip  → stage, test, promote as the first owner
"""
from __future__ import annotations

import sys
from pathlib import Path

from .. import VERSION
from ..config import ROOT
from ..db import init_db, session_scope
from ..models import User
from . import injector


def build(out_dir: Path | None = None) -> Path:
    out_dir = out_dir or ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    import io
    import zipfile

    flat = injector.build_zip(ROOT, VERSION)
    # Wrap in a single folder so the same zip unzips cleanly for a fresh install AND uploads to the injector.
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(flat)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(f"cbx-online-{VERSION}/" + info.filename, src.read(info))
    p = out_dir / f"cbx-online-{VERSION}.zip"
    p.write_bytes(buf.getvalue())
    return p


def stage_and_promote(zip_path: Path) -> None:
    if not zip_path.is_file():
        raise SystemExit(f"No such file: {zip_path}")
    init_db()
    with session_scope() as db:
        owner = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).order_by(User.id).first()
        if owner is None:
            raise SystemExit("No owner account exists yet — run setup.bat first.")
        injector.RESTART_ENABLED = False
        try:
            v = injector.stage(db, zip_path.read_bytes(), owner, notes="applied with update.bat")
            if v.status != "sandbox":
                fails = (v.tests or {}).get("failures") or []
                why = ("\n  " + "\n  ".join(fails[:10])) if fails else (v.tests or {}).get("output", "")[-1500:]
                raise SystemExit(f"Tests failed in the staged version:{why}\nNothing was changed. The version is kept on the Versions page, where the owner can see the failures, re-test, delete it, or promote it anyway.")
            injector.promote(db, v, owner)
        except injector.VersionError as e:
            raise SystemExit(f"Update refused: {e} Nothing was changed.") from e
        print(f"Promoted {v.tag}. If run.bat is open, close it and start it again so the new code runs.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        print(build())
    elif cmd == "stage-and-promote" and len(sys.argv) > 2:
        stage_and_promote(Path(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(1)
