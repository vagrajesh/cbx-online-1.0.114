"""AXL's studio voice — a tiny speech service on this server's graphics card (1.0.68).

Runs in its OWN Python (DATA_DIR/voice/studio/venv, made by Set up the studio voice), never in CBX's: the
speech model needs its own versions of torch and friends, and they must not touch CBX's. CBX starts it,
talks to it on 127.0.0.1 only, with a token, and stops it when CBX stops. Standard library only here, plus
torch and chatterbox inside that Python. Not imported by CBX.

    GET  /health                     {"ok", "loaded", "device", "gpu", "error"}
    POST /speak {"text", "print"}    audio/wav — "print" is a file in CBX_STUDIO_PRINTS

CBX_STUDIO_FAKE=1 (the test suite) answers with a short silent WAV and loads nothing.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = os.environ.get("CBX_STUDIO_TOKEN", "")
PORT = int(os.environ.get("CBX_STUDIO_PORT", "8099"))
PRINTS = Path(os.environ.get("CBX_STUDIO_PRINTS", ".")).resolve()
FAKE = os.environ.get("CBX_STUDIO_FAKE") == "1"
MAX_TEXT = 600

state = {"model": None, "device": "", "gpu": "", "error": "", "print": None}
lock = threading.Lock()


def load() -> None:
    if FAKE:
        state.update(model="fake", device="fake")
        return
    try:
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        gpu = torch.cuda.get_device_name(0) if dev == "cuda" else ""
        m = ChatterboxTurboTTS.from_pretrained(device=dev)
        state.update(model=m, device=dev, gpu=gpu, error="")
    except Exception as e:  # noqa: BLE001 — reported on /health
        state["error"] = f"{type(e).__name__}: {e}"[:500]


def wav_bytes(samples, rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def silence() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 2400)
    return buf.getvalue()


def speak(text: str, print_name: str) -> bytes:
    p = (PRINTS / Path(print_name).name).resolve()
    if p.parent != PRINTS or not p.is_file():
        raise ValueError("unknown voice print")
    if FAKE:
        return silence()
    m = state["model"]
    if m is None:
        raise RuntimeError(state["error"] or "the studio voice is still loading")
    with lock:  # one at a time on the card; the voice is prepared once per print
        if state["print"] != str(p):
            m.prepare_conditionals(str(p))
            state["print"] = str(p)
        wav = m.generate(text)
    return wav_bytes(wav.squeeze(0).numpy(), m.sr)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self) -> bool:
        if not TOKEN or self.headers.get("X-Token") != TOKEN:
            self._send(403, b'{"error":"token"}')
            return False
        return True

    def do_GET(self):
        if self.path != "/health":
            return self._send(404, b"{}")
        if not self._ok():
            return None
        self._send(200, json.dumps({"ok": True, "loaded": state["model"] is not None, "device": state["device"], "gpu": state["gpu"], "error": state["error"]}).encode())

    def do_POST(self):
        if self.path != "/speak":
            return self._send(404, b"{}")
        if not self._ok():
            return None
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 20000)
            body = json.loads(self.rfile.read(n) or b"{}")
            text = str(body.get("text") or "").strip()[:MAX_TEXT]
            if not text:
                return self._send(400, b'{"error":"no text"}')
            self._send(200, speak(text, str(body.get("print") or "")), "audio/wav")
        except ValueError as e:
            self._send(400, json.dumps({"error": str(e)}).encode())
        except Exception as e:  # noqa: BLE001
            self._send(503, json.dumps({"error": f"{type(e).__name__}: {e}"[:400]}).encode())


def main() -> None:
    threading.Thread(target=load, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"studio voice on 127.0.0.1:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
