"""The designer's eyes (1.0.49): a screenshot of a draft page, drawn by the app's own renderer with
made-up rows, so the designer (a model that can see) compares it with how CBX-Connect looks.

    page_png(F, page, rows)  → (png bytes, "") or (None, why not)
    reference_png()          → a page of CBX-Connect as it looks today (Home, made-up numbers), cached

How: a one-time token (two minutes, used once) maps to what the page needs; headless Chromium (Playwright,
on this server) opens http://127.0.0.1:<port>/cexa-preview/<token>, the preview page draws the layout with
the real stylesheet and renderer, and the screenshot is taken. The data in the picture is only ever the
helper's made-up rows and made-up numbers — never a real row.

Playwright is optional. Without it the designer reads the layout as text instead, and the Agents page
offers Turn on screenshots (the owner's click installs it: pip install playwright + Chromium, ~150 MB).
"""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..config import ROOT, settings

TOKEN_S = 120
_TOKENS: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
_AVAIL: dict = {"at": 0.0, "ok": False, "why": ""}
SETUP: dict = {"running": False, "log": "", "ok": None, "at": 0.0}


def available(fresh: bool = False) -> tuple[bool, str]:
    if not settings.team_screenshots:
        return False, "screenshots are switched off (CBX_TEAM_SCREENSHOTS=false)"
    if not fresh and time.time() - _AVAIL["at"] < 120:
        return _AVAIL["ok"], _AVAIL["why"]
    ok, why = False, ""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        why = "Playwright is not installed on this server"
    else:
        try:
            with sync_playwright() as p:
                exe = p.chromium.executable_path
            ok = bool(exe) and Path(exe).exists()
            why = "" if ok else "Chromium for Playwright is not installed"
        except Exception as e:  # noqa: BLE001 — anything here means: no screenshots
            why = f"Playwright could not start ({str(e)[:120]})"
    _AVAIL.update(at=time.time(), ok=ok, why=why)
    return ok, why


def issue(payload: dict) -> str:
    tok = secrets.token_urlsafe(24)
    now = time.time()
    with _LOCK:
        for k in [k for k, (exp, _) in _TOKENS.items() if exp < now]:
            _TOKENS.pop(k, None)
        _TOKENS[tok] = (now + TOKEN_S, payload)
    return tok


def take(tok: str) -> Optional[dict]:
    with _LOCK:
        exp, payload = _TOKENS.pop(tok, (0.0, None))
    return payload if payload is not None and exp >= time.time() else None


def preview_html(payload: dict, version: str) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    v = f"/static/{version}"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Preview</title><link rel="stylesheet" href="{v}/styles.css"><link rel="stylesheet" href="{v}/app.css"></head>'
            f'<body class="cexa-shot"><div class="app" style="grid-template-columns:1fr"><main class="main" id="main"><div id="root"></div></main></div>'
            f'<script type="application/json" id="cexa-data">{data}</script><script type="module" src="{v}/cexa-preview.js"></script></body></html>')


def _metric_samples(keys: list[str]) -> dict:
    from ..engine import metrics as MX

    out = {}
    for i, k in enumerate(keys):
        m = MX.METRICS.get(k)
        out[k] = {"value": [3, 12, 0, 7, 24, 1, 5][i % 7], "label": m.label if m else k, "route": "", "tone": ""}
    return out


def payload_for(F: dict, page: str, rows: dict) -> dict:
    """What the preview page needs to draw one page: the layout, the words, what each block reads, the
    rows (made up) and the numbers (made up)."""
    from ..engine import providers
    from ..engine.loader import effective_layout, widgets_of

    p = F["pages"][page]
    en = dict(F["languages"].get("en") or {})
    info, wrows, keys = {}, {}, []
    provs = {x["name"]: x for x in providers.describe_all()}
    for w in widgets_of(page, p):
        src = w.get("source") or {}
        wid = str(w.get("id"))
        if src.get("data"):
            b = F["data"].get(src["data"]) or {"columns": []}
            info[wid] = {"block": {"name": src["data"], **b}, "can": {}}
            rs = [dict(r) for r in (rows.get(src["data"]) or [])]
            _label_links(rs, b.get("columns") or [], F["data"], rows)
            wrows[wid] = {"columns": b.get("columns") or [], "rows": rs, "total": len(rs)}
        elif src.get("provider"):
            info[wid] = {"provider": provs.get(src["provider"]) or {"columns": [], "filters": []}}
            wrows[wid] = {"columns": (provs.get(src["provider"]) or {}).get("columns") or [], "rows": [], "total": 0}
        if w.get("type") == "metrics":
            keys += [it.get("metric") for it in (w.get("items") or []) if isinstance(it, dict) and it.get("metric")]
        if w.get("type") == "button":
            fl = F["flows"].get(str(w.get("flow") or "")) or {}
            info[wid] = {"flow": {"name": w.get("flow"), "title": en.get(fl.get("title"), w.get("flow")), "enabled": True}}
    tabs = [{"name": n, "title": en.get(q.get("title"), n)} for n, q in sorted(F["pages"].items(), key=lambda kv: (kv[1].get("order") or 100, kv[0])) if q.get("menu", True)]
    return {"tool": en.get((F["manifest"] or {}).get("title"), "Tool"), "page": page, "title": en.get(p.get("title"), page), "intro": en.get(p.get("intro"), p.get("intro") or ""),
            "width": (p.get("style") or {}).get("width") or "normal", "layout": effective_layout(page, p), "strings": en, "info": info, "rows": wrows,
            "metrics": _metric_samples(keys), "tabs": tabs, "theme": (F["manifest"] or {}).get("theme") or {}}


def _label_links(rs: list, cols: list, data: dict, rows: dict) -> None:
    """Made-up rows show their links as the made-up row they point at, as the live page would."""
    from ..engine.loader import link_show

    for c in cols:
        if c.get("type") != "link" or c.get("table") not in data:
            continue
        show = c.get("show") or link_show(data[c["table"]])
        by_id = {r.get("id"): r for r in rows.get(c["table"]) or []}
        for r in rs:
            v = r.get(c["key"])
            if isinstance(v, int):
                r.setdefault("_labels", {})[c["key"]] = str((by_id.get(v) or {}).get(show) or f"#{v}")


def _shoot(payload: dict) -> tuple[Optional[bytes], str]:
    ok, why = available()
    if not ok:
        return None, why
    from playwright.sync_api import sync_playwright

    tok = issue(payload)
    url = f"http://127.0.0.1:{settings.port}/cexa-preview/{tok}"
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": 1280, "height": 860})
                pg.goto(url, wait_until="networkidle", timeout=30000)
                pg.wait_for_selector("body[data-done=\"1\"]", state="attached", timeout=15000)  # (the CSP forbids evaluating a string)
                png = pg.screenshot(full_page=True)
            finally:
                b.close()
        return png, ""
    except Exception as e:  # noqa: BLE001 — no picture is not a failure of the job
        return None, f"the screenshot did not work ({str(e)[:160]})"


def page_png(F: dict, page: str, rows: dict) -> tuple[Optional[bytes], str]:
    return _shoot(payload_for(F, page, rows))


def reference_png() -> tuple[Optional[bytes], str]:
    """CBX-Connect as it looks today: the owner's Home, drawn the same way with made-up numbers. Kept for
    the version running (a new version may change the look)."""
    from .. import VERSION
    from ..engine import loader

    f = settings.data_dir / "cexa" / f"reference-{VERSION}.png"
    if f.exists():
        return f.read_bytes(), ""
    t = loader.registry().tools.get("home")
    if t is None or "owner" not in t.pages:
        return None, "no Home page to compare with"
    F = {"manifest": t.manifest, "pages": t.pages, "data": t.data, "flows": t.flows, "languages": t.languages}
    png, why = _shoot(payload_for(F, "owner", {}))
    if png:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(png)
    return png, why


def setup_screenshots() -> None:
    """The owner pressed Turn on screenshots: install Playwright and its Chromium into this Python. Runs in
    the background; the Agents page shows the log."""
    if SETUP["running"]:
        return
    SETUP.update(running=True, log="", ok=None, at=time.time())
    out = []
    ok = True
    for cmd in ([sys.executable, "-m", "pip", "install", "playwright>=1.45"], [sys.executable, "-m", "playwright", "install", "chromium"]):
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=1200)
            out.append(f"$ {' '.join(cmd[1:])}\n{(r.stdout or '')[-1500:]}{(r.stderr or '')[-800:]}")
            if r.returncode != 0:
                ok = False
                break
        except (OSError, subprocess.SubprocessError) as e:
            out.append(f"$ {' '.join(cmd[1:])}\n{e}")
            ok = False
            break
    SETUP.update(running=False, log="\n".join(out)[-4000:], ok=ok, at=time.time())
    available(fresh=True)


def status() -> dict[str, Any]:
    ok, why = available()
    return {"on": ok, "why": why, "setup": {k: SETUP[k] for k in ("running", "ok")}, "setup_log": SETUP["log"][-1500:]}
