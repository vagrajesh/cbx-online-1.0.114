"""The studio voice, from CBX's side (1.0.68): install it, start and stop it, ask it to speak.

The model (Chatterbox Turbo, MIT licence, on the graphics card) lives in its own Python environment under
DATA_DIR/voice/studio/ so its library versions never touch CBX's own. Set up the studio voice (the owner,
Agents → Now → Voice) does, in the background:

    1. a new Python environment (python -m venv) in DATA_DIR/voice/studio/venv
    2. PyTorch for the graphics card (CUDA 12.8 build — the RTX 6000 needs it) from download.pytorch.org
    3. chatterbox-tts and the libraries it needs, from PyPI
    4. a check that the card is seen
    5. the voice prints: about twelve seconds of each made-up British voice, spoken by Kokoro (voices.py)
    6. it starts the service; the model (~1.5 GB) downloads from Hugging Face on its first start

The service listens on 127.0.0.1:CBX_VOICE_STUDIO_PORT only, and answers only with the token in
DATA_DIR/voice/studio/token. CBX starts it when it starts (if installed), and again if it has stopped.
"""
from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from . import voices as V

log = logging.getLogger("cbx.voice.studio")
TORCH = ["torch==2.7.1", "torchaudio==2.7.1"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
DEPS = ["librosa==0.11.0", "s3tokenizer", "transformers==5.2.0", "diffusers==0.29.0", "resemble-perth>=1.0.0", "conformer==0.3.2",
        "safetensors==0.5.3", "pyloudnorm", "omegaconf", "huggingface_hub", "numpy<2.0.0" if sys.version_info < (3, 13) else "numpy>=2.0.0"]

SETUP: dict[str, Any] = {"running": False, "ok": None, "log": "", "at": 0.0, "step": ""}
_proc: dict[str, Any] = {"p": None, "started": 0.0}
_lock = threading.Lock()


def home() -> Path:
    # absolute: the setup runs its commands from inside this folder, so a relative data folder (CBX_DATA_DIR=data)
    # would point the new environment's python at a path that doesn't exist from there (1.0.72 fix)
    return (settings.path("voice") / "studio").resolve()


def prints() -> Path:
    return (settings.path("voice") / "prints").resolve()


def venv_python() -> Path:
    v = home() / "venv"
    return v / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def installed() -> bool:
    return (home() / "installed.ok").is_file() and venv_python().is_file()


def port() -> int:
    return int(settings.voice_studio_port or 8099)


def token() -> str:
    p = home() / "token"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def _python_for_service() -> Optional[str]:
    if os.environ.get("CBX_STUDIO_FAKE") == "1":
        return sys.executable  # the suite: CBX's own Python runs the service in its fake mode
    return str(venv_python()) if installed() else None


def health(timeout: float = 1.5) -> Optional[dict]:
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port()}/health", headers={"X-Token": token()}, timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def start() -> bool:
    """Start the service if it is installed and not running. True when it answers."""
    if not settings.voice or not settings.voice_studio:
        return False
    with _lock:
        if health():
            return True
        py = _python_for_service()
        if py is None:
            return False
        p = _proc["p"]
        if p is not None and p.poll() is None and time.time() - _proc["started"] < 120:
            return False  # still coming up
        env = {**os.environ, "CBX_STUDIO_TOKEN": token(), "CBX_STUDIO_PORT": str(port()), "CBX_STUDIO_PRINTS": str(prints()),
               "HF_HOME": str(home() / "hf"), "PYTHONIOENCODING": "utf-8"}
        logf = open(home() / "service.log", "ab")  # noqa: SIM115 — the child keeps it
        kw: dict = {"stdout": logf, "stderr": subprocess.STDOUT, "env": env, "cwd": str(home())}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        _proc["p"] = subprocess.Popen([py, str(Path(__file__).with_name("studio_service.py"))], **kw)
        _proc["started"] = time.time()
    for _ in range(20):
        time.sleep(0.25)
        if health(0.5):
            return True
    return False


def stop() -> None:
    p = _proc.get("p")
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
    _proc["p"] = None


def print_path(vid: str) -> Path:
    return prints() / f"{vid.split(':', 1)[1]}.wav"


def make_prints(force: bool = False) -> list[str]:
    """Each studio voice's print, spoken by Kokoro from its own or mixed voices (made-up voices only)."""
    from . import engine as E

    made = []
    prints().mkdir(parents=True, exist_ok=True)
    for vid, row in V.CATALOG.items():
        if row[1] != "studio":
            continue
        p = print_path(vid)
        if p.is_file() and not force:
            continue
        p.write_bytes(E.kokoro_wav(V.PRINT_TEXT, vid, speed=0.95))
        made.append(vid)
    return made


def speak(text: str, vid: str) -> bytes:
    import httpx

    p = print_path(vid)
    if not p.is_file():
        make_prints()
    r = httpx.post(f"http://127.0.0.1:{port()}/speak", json={"text": text, "print": p.name}, headers={"X-Token": token()}, timeout=60)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("audio/"):
        try:
            why = r.json().get("error", "")
        except ValueError:
            why = r.text[:200]
        raise RuntimeError(why or f"studio voice answered {r.status_code}")
    return r.content


def status() -> dict:
    h = health() if installed() or os.environ.get("CBX_STUDIO_FAKE") == "1" else None
    tail = ""
    lf = home() / "service.log"
    if lf.is_file():
        try:
            tail = lf.read_bytes()[-1200:].decode("utf-8", "replace")
        except OSError:
            pass
    return {"enabled": bool(settings.voice_studio), "installed": installed(), "running": bool(h), "loaded": bool(h and h.get("loaded")),
            "device": (h or {}).get("device", ""), "gpu": (h or {}).get("gpu", ""), "error": (h or {}).get("error", ""),
            "setup": {k: SETUP[k] for k in ("running", "ok", "step")}, "setup_log": SETUP["log"][-2500:], "service_log": tail}


def _run(cmd: list[str], out: list[str], timeout: int = 3600) -> bool:
    SETUP["step"] = " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd))[:160]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(home()))
        out.append(f"$ {SETUP['step']}\n{(r.stdout or '')[-800:]}{(r.stderr or '')[-600:]}")
        SETUP["log"] = "\n".join(out)[-6000:]
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        out.append(f"$ {SETUP['step']}\n{e}")
        SETUP["log"] = "\n".join(out)[-6000:]
        return False


def setup() -> None:
    """Install the studio voice. Runs in the background; the Voice card shows each step."""
    if SETUP["running"]:
        return
    SETUP.update(running=True, ok=None, log="", at=time.time(), step="starting")
    out: list[str] = []
    ok = True
    try:
        home().mkdir(parents=True, exist_ok=True)
        stop()
        stray = home() / "data"  # 1.0.68–1.0.71 made the environment here by mistake (relative data folder)
        if (stray / "voice" / "studio" / "venv").is_dir():
            import shutil

            shutil.rmtree(stray, ignore_errors=True)
        py = str(venv_python())
        if not venv_python().is_file():
            ok = _run([sys.executable, "-m", "venv", str(home() / "venv")], out, 600)
        ok = ok and _run([py, "-m", "pip", "install", "--upgrade", "pip"], out, 600)
        ok = ok and _run([py, "-m", "pip", "install", *TORCH, "--index-url", TORCH_INDEX], out)
        ok = ok and _run([py, "-m", "pip", "install", "chatterbox-tts", "--no-deps"], out, 900)
        ok = ok and _run([py, "-m", "pip", "install", *DEPS], out)
        # the libraries above may have pulled a different torch: put the graphics-card build back
        ok = ok and _run([py, "-m", "pip", "install", *TORCH, "--index-url", TORCH_INDEX, "--force-reinstall", "--no-deps"], out)
        ok = ok and _run([py, "-c", "import torch, chatterbox.tts_turbo; print('graphics card:', torch.cuda.is_available() and torch.cuda.get_device_name(0))"], out, 600)
        if ok:
            (home() / "installed.ok").write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
            try:
                made = make_prints(force=True)
                out.append(f"voice prints: {', '.join(made) or 'already there'}")
            except Exception as e:  # noqa: BLE001 — Kokoro missing: Set up voice first
                out.append(f"voice prints not made yet — {e}. Set up voice (Kokoro) first, then press this again.")
                ok = False
        if ok:
            SETUP["step"] = "starting the studio voice (the model downloads the first time)"
            started = start()
            out.append("studio voice started" if started else "installed — the studio voice is starting (the first start downloads the model)")
    except Exception as e:  # noqa: BLE001
        out.append(f"failed: {e}")
        ok = False
    SETUP.update(running=False, ok=ok, log="\n".join(out)[-6000:], at=time.time(), step="")
