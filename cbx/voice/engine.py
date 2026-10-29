"""C-EXA's ears and voice (1.0.64), on this server — no audio leaves the building.

    ears   faster-whisper (Whisper, CTranslate2): the graphics card when it can (float16), else the CPU
           (int8, a smaller model). CBX_VOICE_STT_MODEL / CBX_VOICE_STT_CPU_MODEL. The model downloads once
           (Hugging Face) the first time she listens.
    voice  Kokoro-82M through ONNX Runtime (kokoro-onnx): fast on the CPU. English (British voice by
           default), Portuguese and French; Arabic is not one of Kokoro's languages — the browser speaks it.
           The two model files (~340 MB) are fetched by the owner's Set up voice.

Nothing is installed or downloaded on its own: the owner presses Set up voice (Agents → Now → Voice), which
pip-installs the two packages into this Python and fetches Kokoro's files into DATA_DIR/voice/. Audio is
handled in memory and never kept; what is said is kept only as text where a feature keeps text (a call's
note, a chat message).

In the test suite (model mode stub) both are stand-ins: listening returns what the test put in the audio's
first bytes, speaking returns a short silent WAV.
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Optional

from ..config import ROOT, settings

log = logging.getLogger("cbx.voice")

KOKORO_FILES = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
}
# language → (Kokoro voice, Kokoro language). None: the browser speaks it.
VOICES = {"en": ("bf_emma", "en-gb"), "pt": ("pf_dora", "pt-br"), "fr": ("ff_siwis", "fr-fr"), "ar": None}
MAX_AUDIO = 10 * 1024 * 1024
MAX_SAY = 800

_lock = threading.Lock()
_stt: dict[str, Any] = {"model": None, "device": "", "error": ""}
_tts: dict[str, Any] = {"model": None, "error": ""}
SETUP: dict[str, Any] = {"running": False, "ok": None, "log": "", "at": 0.0}


def folder() -> Path:
    return settings.path("voice")


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


def status() -> dict:
    stub = settings.model_mode == "stub"
    files = all((folder() / n).is_file() for n in KOKORO_FILES)
    ears = stub or _has("faster_whisper")
    mouth = stub or (_has("kokoro_onnx") and files)
    return {"ears": {"ready": ears, "engine": "Whisper" + (f" · {_stt['device']}" if _stt["device"] else ""), "model": settings.voice_stt_model,
                     "why": "" if ears else "Set up voice installs Whisper (faster-whisper)."},
            "voice": {"ready": mouth, "engine": "Kokoro", "voice": _chosen_name(),
                      "why": "" if mouth else ("Set up voice installs Kokoro (kokoro-onnx)." if not _has("kokoro_onnx") else "Set up voice fetches Kokoro's two model files.")},
            "on": settings.voice and ears and mouth, "enabled": settings.voice, "setup": {k: SETUP[k] for k in ("running", "ok")}, "setup_log": SETUP["log"][-1500:],
            "languages": {k: bool(v) for k, v in VOICES.items()}}


def _chosen_name() -> str:
    from . import voices as V

    vid = V.chosen()
    return f"{V.CATALOG[vid][0]} ({'studio' if V.CATALOG[vid][1] == 'studio' else 'quick'})"


# ---- ears --------------------------------------------------------------------------------------------

def _load_stt():
    with _lock:
        if _stt["model"] is not None:
            return _stt["model"]
        from faster_whisper import WhisperModel

        try:
            m = WhisperModel(settings.voice_stt_model, device="cuda", compute_type="float16", download_root=str(folder() / "whisper"))
            _stt.update(model=m, device="graphics card", error="")
        except Exception as e:  # noqa: BLE001 — no card, no CUDA libraries: the CPU does it
            log.info("voice: Whisper on the CPU (%s)", e)
            m = WhisperModel(settings.voice_stt_cpu_model, device="cpu", compute_type="int8", download_root=str(folder() / "whisper"))
            _stt.update(model=m, device="CPU", error="")
        return _stt["model"]


def transcribe(audio: bytes, lang: str = "") -> dict:
    """What was said: {text, language, seconds}. Raises VoiceError with a sentence when it cannot."""
    if not audio:
        raise VoiceError("No sound came through.")
    if len(audio) > MAX_AUDIO:
        raise VoiceError("That recording is too long — keep it under a minute.")
    if settings.model_mode == "stub":
        # the suite's stand-in: the text is in the "audio" (b"say:<text>")
        txt = audio[4:].decode("utf-8", "ignore") if audio.startswith(b"say:") else ""
        return {"text": txt.strip(), "language": lang or "en", "seconds": 1.0}
    if not settings.voice or not _has("faster_whisper"):
        raise VoiceError("Voice is not set up on this server yet (Settings → C-EXA voice & models → Set up voice).")
    m = _load_stt()
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
        f.write(audio)
        p = f.name
    try:
        t0 = time.time()
        segs, info = m.transcribe(p, language=(lang or None) if lang in ("en", "pt", "fr", "ar") else None, vad_filter=True, beam_size=1)
        text = " ".join(s.text.strip() for s in segs).strip()
        return {"text": text, "language": info.language, "seconds": round(time.time() - t0, 2)}
    finally:
        Path(p).unlink(missing_ok=True)


# ---- voice -------------------------------------------------------------------------------------------

def _load_tts():
    with _lock:
        if _tts["model"] is None:
            from kokoro_onnx import Kokoro

            _tts["model"] = Kokoro(str(folder() / "kokoro-v1.0.onnx"), str(folder() / "voices-v1.0.bin"))
        return _tts["model"]


def _wav(samples, rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _silence(ms: int = 200, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * (rate * ms // 1000))
    return buf.getvalue()


def plain(text: str) -> str:
    """Words to say: no markdown, links read as 'a link', at most MAX_SAY characters."""
    import re

    t = re.sub(r"```[\s\S]*?```", " ", str(text or ""))
    t = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[*_`#>|]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_SAY]


_cache: dict[str, bytes] = {}


def kokoro_wav(text: str, vid: str, *, kokoro_lang: str = "en-gb", speed: float = 1.0) -> bytes:
    """Kokoro speaking `text` in a catalogue voice (its own, or a mix)."""
    from . import voices as V

    if settings.model_mode == "stub":
        return _silence()
    if not _has("kokoro_onnx") or not all((folder() / n).is_file() for n in KOKORO_FILES):
        raise VoiceError("Her voice is not set up on this server yet (Settings → C-EXA voice & models → Set up voice).")
    k = _load_tts()
    lang = "en-us" if V.CATALOG[vid][2] == "American" else kokoro_lang
    samples, rate = k.create(text, voice=V.style(vid, k), lang=lang, speed=speed)
    return _wav(samples, rate)


def speak(text: str, lang: str = "en", voice: str = "") -> Optional[bytes]:
    """A WAV of her saying it, or None when the browser should say it (a language Kokoro lacks).
    English: the owner's chosen voice (studio on the graphics card when it runs, else its quick twin)."""
    from . import studio as S
    from . import voices as V

    words = plain(text)
    if not words:
        raise VoiceError("Nothing to say.")
    l2 = (lang or "en")[:2]
    v = VOICES.get(l2, VOICES["en"])
    if v is None:
        return None
    if settings.model_mode == "stub" and os.environ.get("CBX_STUDIO_FAKE") != "1":
        return _silence()
    if not settings.voice:
        raise VoiceError("Voice is switched off on this server (CBX_VOICE).")
    vid = voice if voice in V.CATALOG else V.chosen()
    key = f"{vid}|{l2}|{words}"
    if key in _cache:
        return _cache[key]
    out = None
    if l2 != "en":  # Kokoro's own voice for the language
        if not _has("kokoro_onnx") or not all((folder() / n).is_file() for n in KOKORO_FILES):
            raise VoiceError("Her voice is not set up on this server yet (Settings → C-EXA voice & models → Set up voice).")
        samples, rate = _load_tts().create(words, voice=v[0], lang=v[1], speed=1.0)
        out = _wav(samples, rate)
    elif V.CATALOG[vid][1] == "studio":
        try:
            if S.health() or S.start():
                out = S.speak(words, vid)
        except Exception as e:  # noqa: BLE001 — the quick twin says it instead
            log.info("studio voice: %s", e)
        if out is None:
            out = kokoro_wav(words, V.kokoro_twin(vid))
    else:
        out = kokoro_wav(words, vid)
    if len(_cache) > 200:
        _cache.pop(next(iter(_cache)))
    _cache[key] = out
    return out


class VoiceError(Exception):
    pass


# ---- the owner's Set up voice --------------------------------------------------------------------------

def setup() -> None:
    """pip install the two engines into this Python, then fetch Kokoro's files. Runs in the background."""
    if SETUP["running"]:
        return
    SETUP.update(running=True, ok=None, log="", at=time.time())
    out: list[str] = []
    ok = True
    pkgs = ["faster-whisper>=1.1", "kokoro-onnx>=0.4"]
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and "GPU" in r.stdout:
            pkgs += ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]  # Whisper on the graphics card
    except Exception:  # noqa: BLE001
        pass
    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=2400)
        out.append(f"$ pip install {' '.join(pkgs)}\n{(r.stdout or '')[-1500:]}{(r.stderr or '')[-800:]}")
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        out.append(f"$ pip install …\n{e}")
        ok = False
    if ok:
        import httpx

        for name, url in KOKORO_FILES.items():
            dest = folder() / name
            if dest.is_file() and dest.stat().st_size > 1_000_000:
                out.append(f"{name}: already here")
                continue
            try:
                tmp = dest.with_suffix(dest.suffix + ".part")
                with httpx.stream("GET", url, follow_redirects=True, timeout=None) as resp:
                    resp.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_bytes(1 << 20):
                            f.write(chunk)
                tmp.replace(dest)
                out.append(f"{name}: fetched ({dest.stat().st_size // 1_000_000} MB)")
            except Exception as e:  # noqa: BLE001
                out.append(f"{name}: could not fetch — {e}")
                ok = False
    import importlib

    importlib.invalidate_caches()  # the packages pip just installed are found without a restart
    SETUP.update(running=False, ok=ok, log="\n".join(out)[-4000:], at=time.time())
    _stt.update(model=None, device="")
    _tts.update(model=None)
