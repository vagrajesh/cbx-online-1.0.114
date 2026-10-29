"""Run a staged version so a person can click through it before promoting.

Each sandbox slot (sandbox-a / sandbox-b) can host one running instance of the staged package:
  start(v)   → launches `python -m cbx.main` from the package dir on its own port
               (default 8081 for sandbox-a, 8082 for sandbox-b; CBX_SANDBOX_A_PORT / _B_PORT override)
  stop(slot) → stops that slot's instance
  status()   → what's running, on which port, healthy or not

The sandbox instance uses its own throwaway data directory (data/sandboxes/<slot>-data), so it can
never touch the live database, uploads or keys. C-EXA's builds land here — the live tree stays hands-off.

Signing in. The sandbox database starts empty, so the live password cannot work there — and it must
not: C-EXA's own code runs in that instance, and the live password hashes never leave the live database.
Instead `start()` seeds the sandbox with every live owner's email and a fresh sandbox-only password
(the package's own bootstrap does the writing, so the schema is the package's own). The passwords are
kept in this process's memory, shown on the Versions page to the owner, and forgotten when the slot
stops. New start, new password.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

import httpx

from ..config import settings
from .safe_env import minimal_env

_proc: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()
# slot → [{"email", "password", "at"}] — the sandbox's own sign-ins; never written to disk or the database
LOGINS: dict[str, list[dict]] = {}
# slot → {"kind": version|proposal|playground, "title", "path"} — what is running there
RUNNING: dict[str, dict] = {}

DEFAULT_PORTS = {"sandbox-a": 8081, "sandbox-b": 8082}
# 1.0.80: the work areas — "test" and "dev-<user id>" — registered with their ports (cbx/versions/areas.py)
AREA_PORTS: dict[str, int] = {}
SLOT_RE = re.compile(r"^(sandbox-[ab]|test|dev-\d{1,9})$")


def register(slot: str, port: int) -> None:
    if SLOT_RE.match(slot or "") and slot not in DEFAULT_PORTS and port:
        AREA_PORTS[slot] = int(port)


def ports() -> dict[str, int]:
    return {**{s: _port_for(s) for s in DEFAULT_PORTS}, **AREA_PORTS}


def known(slot: str) -> bool:
    return slot in DEFAULT_PORTS or slot in AREA_PORTS
SEED_TIMEOUT_S = 150  # the package's bootstrap: imports + create tables + one owner, on a busy Windows box


def _port_for(slot: str) -> int:
    if slot in AREA_PORTS:
        return AREA_PORTS[slot]
    env = os.environ.get(f"CBX_SANDBOX_{slot[-1].upper()}_PORT")
    return int(env) if env else DEFAULT_PORTS.get(slot, 8081 + (0 if slot == "sandbox-a" else 1))


def _data_dir(slot: str) -> Path:
    d = settings.data_dir / "sandboxes" / f"{slot}-data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _base(slot: str) -> str:
    return f"http://127.0.0.1:{_port_for(slot)}"


def healthy(slot: str, timeout: float = 1.5) -> bool:
    try:
        r = httpx.get(f"{_base(slot)}/api/health", timeout=timeout, trust_env=False)
        return r.status_code == 200
    except Exception:
        return False


def child_env(slot: str) -> dict:
    """The environment a sandbox process runs in: its own data dir and port, loopback only, no GPU
    model. 1.0.114: only an approved short list of the server's environment is passed on (safe_env.minimal_env) —
    the code running here may be an uploaded zip nobody has read, so the server's secrets stay out. A package
    carries no .env (refused at upload), so these values are all the settings it gets."""
    env = minimal_env()
    if os.environ.get("CBX_SANDBOX_MODEL_MODE", "stub") != "stub":
        env["CBX_OLLAMA_URL"] = settings.ollama_url  # a sandbox told to use the real model needs to know where it is
    env["CBX_DATA_DIR"] = str(_data_dir(slot))
    env["CBX_PORT"] = str(_port_for(slot))
    env["CBX_HOST"] = "127.0.0.1"
    env["CBX_SANDBOX_RUN"] = "1"          # marks nested runs; tests skip themselves, bootstrap makes throwaway owners
    env["CBX_MODEL_MODE"] = os.environ.get("CBX_SANDBOX_MODEL_MODE", "stub")  # sandbox never loads the GPU model
    env["CBX_DATABASE_URL"] = ""          # never the live Postgres, if one is configured
    env["CBX_MAIL_ENABLED"] = "0"         # a sandbox never emails anyone
    env["CBX_FLOWS_SCHEDULE"] = "false"   # a copy must not run the live flows on its own schedule
    env["CBX_TEAM_SCREENSHOTS"] = "false"  # nor take screenshots on the live port
    env["CBX_TEAMS_AUTO"] = "false"  # nor run C-EXA's standing teams a second time
    env["CBX_BACKUPS"] = "false"  # nor back up a throwaway copy every night
    env["CBX_CODE_FALLBACK"] = "let_in"   # a sandbox has no mailer and nobody can see its window
    env["CBX_MFA_REQUIRED"] = "none"      # the sandbox sign-in on the card is the whole key to a throwaway copy
    return env


def seed_owners(slot: str, package_path: Path, owners: Iterable[tuple[str, str]]) -> tuple[list[dict], str]:
    """Create (or reset) each owner in the sandbox's own database with a fresh sandbox-only password,
    by running the PACKAGE's `python -m cbx.bootstrap` against the sandbox data dir — its models, its
    schema, whatever version it is. Returns (logins, note). Nothing of the live accounts is copied but
    the email and the display name."""
    python = sys.executable or "python"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    logins: list[dict] = []
    notes: list[str] = []
    env = child_env(slot)
    env["CBX_SANDBOX_SEED"] = "1"  # bootstrap: throwaway owner, no forced password change
    for email, name in owners:
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            continue
        try:
            r = subprocess.run(
                [python, "-m", "cbx.bootstrap", "--owner", email, "--name", name or "Owner"],
                cwd=str(package_path), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=SEED_TIMEOUT_S, creationflags=flags,
            )
        except subprocess.TimeoutExpired:
            notes.append(f"{email}: bootstrap took longer than {SEED_TIMEOUT_S} s")
            continue
        except OSError as e:
            notes.append(f"{email}: {e}")
            continue
        m = re.search(r"Temporary password:\s*(\S+)", r.stdout or "")
        if r.returncode != 0 or not m:
            tail = ((r.stderr or r.stdout or "").strip().splitlines() or ["no output"])[-1]
            notes.append(f"{email}: the package's bootstrap did not create the account ({tail[:160]})")
            continue
        logins.append({"email": email, "password": m.group(1), "at": time.strftime("%Y-%m-%d %H:%M")})
    LOGINS[slot] = logins
    return logins, "; ".join(notes)


def logins(slot: str) -> list[dict]:
    """The sandbox's sign-ins — for the owner's Versions page only, never for C-EXA's tools or the log."""
    return list(LOGINS.get(slot, []))


def start(slot: str, package_path: str, owners: Optional[Iterable[tuple[str, str]]] = None, *, kind: str = "version", title: str = "") -> dict:
    """Launch the staged package as a server instance. Returns {ok, url, note, login}.
    `owners` = [(email, name)] of the live owners who should be able to sign in to it;
    `kind`/`title` say what this is on the Versions page (a version, a proposal's build, a playground)."""
    src = Path(package_path)
    if not known(slot):
        return {"ok": False, "note": f"There is no sandbox slot called {slot}."}
    if not (src / "cbx" / "main.py").exists():
        return {"ok": False, "note": "That package has no cbx/main.py — nothing to run."}
    with _lock:
        old = _proc.get(slot)
        if old is not None and old.poll() is None:
            old.terminate()
            try:
                old.wait(timeout=8)
            except subprocess.TimeoutExpired:
                old.kill()
            _proc.pop(slot, None)
        seeded, seed_note = ([], "")
        if owners:
            # before the app starts: bootstrap and the server must not both create the tables at once
            seeded, seed_note = seed_owners(slot, src, owners)
        python = sys.executable or "python"
        env = child_env(slot)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
        try:
            p = subprocess.Popen(
                [python, "-m", "cbx.main"],
                cwd=str(src), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "note": f"could not start: {e}"}
        _proc[slot] = p
        RUNNING[slot] = {"kind": kind, "title": title or src.name, "path": str(src)}
    out = {"url": _base(slot), "pid": p.pid, "port": _port_for(slot), "door": f"/sandbox/{slot}/enter", "login": [{"email": s["email"], "password": s["password"]} for s in seeded]}
    if owners and not seeded:
        out["note"] = "the sandbox has no sign-in: " + (seed_note or "bootstrap gave no password")
    elif seed_note:
        out["note"] = seed_note
    # give it a moment, then check health (sandbox boots in a few seconds)
    deadline = time.time() + 30
    while time.time() < deadline:
        if healthy(slot):
            return {"ok": True, **out}
        if p.poll() is not None:
            return {"ok": False, "note": f"sandbox exited immediately (code {p.returncode}) — check the package"}
        time.sleep(1.0)
    out["note"] = (out.get("note", "") + " · " if out.get("note") else "") + "started but not healthy yet — give it a few seconds and refresh"
    return {"ok": True, **out}


def is_running(slot: str) -> bool:
    p = _proc.get(slot)
    return p is not None and p.poll() is None


def stop(slot: str) -> dict:
    with _lock:
        p = _proc.pop(slot, None)
        LOGINS.pop(slot, None)  # a stopped sandbox forgets its sign-ins; the next start makes new ones
        RUNNING.pop(slot, None)
    if p is None:
        return {"ok": True, "note": "that slot wasn't running"}
    try:
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
    except Exception:
        pass
    return {"ok": True, "note": "sandbox stopped"}


def stop_all() -> None:
    for slot in list(_proc):
        stop(slot)


def status() -> list[dict]:
    out = []
    for slot, port in ports().items():
        p = _proc.get(slot)
        running = p is not None and p.poll() is None
        what = RUNNING.get(slot, {}) if running else {}
        out.append({
            "slot": slot,
            "port": port,
            "url": _base(slot),
            "running": running,
            "healthy": healthy(slot) if running else False,
            "pid": p.pid if running else None,
            "kind": what.get("kind"),
            "title": what.get("title"),
        })
    return out

