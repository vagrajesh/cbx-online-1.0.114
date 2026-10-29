"""Node for the script checks, on any Node the machine has (or none). 1.0.110 / 2.10.13: the CBX PC's Node is older than
the `--experimental-default-type` flag (Node 20.10+), so every check made Inject fail with "bad option". A script is checked
as an ES module by copying it to a .mjs file (understood since Node 12); a snippet runs with --input-type=module."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

NODE = shutil.which("node")


def _need() -> str:
    if not NODE:
        pytest.skip("no Node on this machine")
    return NODE


def check_module(path: Path) -> str:
    """'' when the script parses as an ES module, else Node's message."""
    node = _need()
    with tempfile.TemporaryDirectory(prefix="cbx-node-") as d:
        p = Path(d) / (Path(path).stem + ".mjs")
        p.write_bytes(Path(path).read_bytes())
        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode == 0:
        return ""
    lines = [x.strip() for x in (r.stderr or "").strip().splitlines() if x.strip()]
    said = next((x for x in lines if "Error" in x and not x.startswith("at ")), "")
    return said or (lines[-1] if lines else f"exit {r.returncode}")


def run_module(code: str) -> str:
    """Run a snippet as an ES module and give back what it printed."""
    node = _need()
    # 1.0.111 / 2.10.14: through a utf-8 .mjs file, not -e: a command-line argument is re-encoded in the machine's code page
    # (cp1252 on the CBX PC), which turned an em dash into '?' before Node saw it
    with tempfile.TemporaryDirectory(prefix="cbx-node-") as d:
        p = Path(d) / "snippet.mjs"
        p.write_text(code, encoding="utf-8")
        r = subprocess.run([node, str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()
