"""Which model for which bot (1.0.62). Not every agent needs the strongest model: a helper that writes a
README or a checker that counts rows does fine on a small one, and small ones answer in seconds. So the
agents come in four kinds, each with its own model:

    brain    C-EXA herself: plans, talks, decides           CBX_CEXA_MODEL (.env)
    worker   leads, developers, analysts, reviewers         a mid-size model that calls tools
    light    helpers, writers, translators, operators       a small model that calls tools
    eyes     the designer                                   a model that can see

What is picked (Agents → Now → Models) is kept in DATA_DIR/cexa/models.json and wins over .env. C-EXA reads
what is installed in Ollama (and what each model can do: tools, vision), the card's memory, and — at night,
when web search is on — what is new, and suggests; the owner presses Use it or Download. Downloads come from
Ollama's own library only (`ollama pull <name>`), started by the owner, never by an agent.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from typing import Any, Optional

import httpx

from ..config import settings

TIERS: dict[str, tuple[str, str, str]] = {
    # tier: (who, what it does, what it needs)
    "brain": ("C-EXA herself", "plans, talks, decides", "tools"),
    "worker": ("Leads, developers, analysts, reviewers", "reads, reasons and uses tools", "tools"),
    "light": ("Helpers, writers, translators, operators", "small jobs, fast", "tools"),
    "eyes": ("The designer", "looks at screenshots", "vision"),
}
ROLE_TIER = {
    "lead": "worker", "developer": "worker", "core": "worker", "analyst": "worker", "reviewer": "worker",
    "security": "worker", "correctness": "worker", "people": "worker", "scout": "worker",
    "designer": "eyes",
    "tester": "light", "helper": "light", "writer": "light", "translator": "light", "operator": "light", "checker": "light", "triager": "light",
}
# Starting suggestions (October 2026) for a 32 GB card. C-EXA checks the web for newer ones at night.
SHELF: list[dict] = [
    {"name": "qwen3:4b", "gb": 2.6, "tiers": ["light"], "caps": ["tools", "thinking"], "note": "small, fast, calls tools well for its size"},
    {"name": "qwen3:8b", "gb": 5.2, "tiers": ["light", "worker"], "caps": ["tools", "thinking"], "note": "a good all-rounder for small and medium jobs"},
    {"name": "qwen3.5:9b", "gb": 6.6, "tiers": ["worker", "eyes"], "caps": ["tools", "vision"], "note": "calls tools and can see; already the PDF worker"},
    {"name": "devstral:24b", "gb": 14.0, "tiers": ["worker"], "caps": ["tools"], "note": "for the developers: a coding model"},
]
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}(/[a-z0-9][a-z0-9._-]{0,60})?(:[a-z0-9][a-z0-9._-]{0,40})?$")

_cache: dict[str, Any] = {"at": 0.0, "models": [], "gpu": None}
_lock = threading.Lock()
PULLS: dict[str, dict] = {}


# ---- what is picked ----------------------------------------------------------------------------------

def _file():
    return settings.data_dir / "cexa" / "models.json"


def picked() -> dict[str, str]:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return {k: str(v) for k, v in d.items() if k in TIERS and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def pick(tier: str) -> str:
    """The model the owner picked for this kind of bot, or '' (then the .env fall-back applies)."""
    return picked().get(tier, "")


def set_pick(tier: str, model: str) -> None:
    if tier not in TIERS or tier == "brain":
        raise ValueError("C-EXA's own model is set in .env (CBX_CEXA_MODEL).")
    d = picked()
    if model:
        d[tier] = model
    else:
        d.pop(tier, None)
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


def tier_model(tier: str) -> str:
    """The model a kind of bot uses right now: the pick, else .env, else C-EXA's own."""
    if tier == "brain":
        return settings.cexa_model
    env = {"worker": settings.model_worker_tier or settings.model_developer, "light": settings.model_light_tier or settings.model_helper, "eyes": settings.model_designer}
    return pick(tier) or env.get(tier, "") or settings.cexa_model


def model_for_role(role: str) -> str:
    return tier_model(ROLE_TIER.get(role, "worker"))


# ---- what is installed -------------------------------------------------------------------------------

def installed(force: bool = False) -> list[dict]:
    """Models in Ollama with their size (GB) and what they can do. Cached ten minutes; [] if Ollama is down."""
    with _lock:
        if not force and time.time() - _cache["at"] < 600:
            return list(_cache["models"])
    if settings.model_mode == "stub":
        return []
    out: list[dict] = []
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=5).json().get("models") or []
    except Exception:  # noqa: BLE001
        tags = []
    for m in tags:
        name = str(m.get("name") or m.get("model") or "")
        if not name:
            continue
        caps: list[str] = []
        try:
            caps = list(httpx.post(f"{settings.ollama_url}/api/show", json={"model": name}, timeout=5).json().get("capabilities") or [])
        except Exception:  # noqa: BLE001
            pass
        det = m.get("details") or {}
        out.append({"name": name, "gb": round((m.get("size") or 0) / 1e9, 1), "params": str(det.get("parameter_size") or ""),
                    "family": str(det.get("family") or ""), "caps": caps})
    with _lock:
        _cache.update(at=time.time(), models=out)
    return list(out)


def gpu_gb() -> float:
    if settings.gpu_gb:
        return float(settings.gpu_gb)
    if _cache["gpu"] is None:
        gb = 0.0
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            gb = round(sum(float(x) for x in r.stdout.split() if x.strip()) / 1024, 1)
        except Exception:  # noqa: BLE001
            gb = 0.0
        _cache["gpu"] = gb
    return float(_cache["gpu"] or 0.0)


def _base(name: str) -> str:
    return name if ":" in name else name + ":latest"


def _find(models: list[dict], name: str) -> Optional[dict]:
    return next((m for m in models if _base(m["name"]) == _base(name)), None)


def _good_for(m: dict, tier: str, gpu: float) -> bool:
    need = TIERS[tier][2]
    if m["caps"] and need not in m["caps"]:
        return False
    if "embedding" in m["caps"]:
        return False
    gb = m["gb"] or 0
    if tier == "light":
        return 1.5 <= gb <= 7
    if tier == "worker":
        return 4 <= gb <= (gpu * 0.7 if gpu else 22)
    if tier == "eyes":
        return gb <= (gpu * 0.7 if gpu else 22)
    return True


def advise(force: bool = False) -> dict:
    """For each kind of bot: what it uses now, whether that is installed and able, and the suggestion."""
    models = installed(force)
    gpu = gpu_gb()
    tiers = []
    for tier, (who, what, need) in TIERS.items():
        cur = tier_model(tier)
        have = _find(models, cur)
        ok = bool(have) and (not have["caps"] or need in have["caps"])
        cands = sorted([m for m in models if _good_for(m, tier, gpu)], key=lambda m: m["gb"])
        suggest, why, download = "", "", None
        if tier == "light" and cands and (not have or have["gb"] > cands[0]["gb"] * 1.5):
            suggest, why = cands[0]["name"], f"installed, calls tools, {cands[0]['gb']} GB — enough for small jobs and much quicker"
        elif tier == "worker" and cands and (not ok or have["gb"] > (gpu * 0.7 if gpu else 22)):
            best = cands[-1]
            suggest, why = best["name"], f"installed, calls tools, {best['gb']} GB"
        elif tier == "eyes" and not ok and cands:
            suggest, why = cands[0]["name"], f"installed and can see, {cands[0]['gb']} GB"
        if tier != "brain" and not cands:
            for s in SHELF:
                if tier in s["tiers"] and not _find(models, s["name"]) and (not gpu or s["gb"] <= gpu * 0.7):
                    download = {"name": s["name"], "gb": s["gb"], "why": s["note"]}
                    break
        tiers.append({"tier": tier, "who": who, "what": what, "needs": need, "model": cur, "picked": bool(pick(tier)),
                      "installed": bool(have), "able": ok, "gb": have["gb"] if have else None,
                      "suggest": suggest if suggest and _base(suggest) != _base(cur) else "", "why": why, "download": download})
    loaded = sum(t["gb"] or 0 for t in tiers if t["installed"])
    note = ""
    if gpu and loaded > gpu:
        note = f"Together they need about {loaded:.0f} GB and the card has {gpu:.0f} GB: Ollama swaps them in and out (a few seconds each time). That is fine — the agents take turns anyway."
    return {"gpu_gb": gpu, "installed": models, "tiers": tiers, "note": note, "pulls": dict(PULLS), "ollama_up": bool(models) or settings.model_mode == "stub"}


def advice_text(force: bool = False) -> str:
    a = advise(force)
    lines = [f"Graphics card: {a['gpu_gb'] or 'unknown'} GB. Installed: " + (", ".join(f"{m['name']} ({m['gb']} GB; {'/'.join(m['caps']) or '?'})" for m in a["installed"]) or "nothing I can see (is Ollama running?)")]
    for t in a["tiers"]:
        s = f"- {t['who']} ({t['tier']}): {t['model']}" + ("" if t["installed"] else " — NOT INSTALLED") + ("" if t["able"] or not t["installed"] else f" — cannot do {t['needs']}")
        if t["suggest"]:
            s += f"; better: {t['suggest']} ({t['why']})"
        if t["download"]:
            s += f"; could download {t['download']['name']} ({t['download']['gb']} GB — {t['download']['why']})"
        lines.append(s)
    if a["note"]:
        lines.append(a["note"])
    return "\n".join(lines)


# ---- downloads (the owner presses Download) ------------------------------------------------------------

def name_ok(name: str) -> bool:
    n = (name or "").strip().lower()
    if not NAME_OK.match(n):
        return False
    return "/" not in n or "." not in n.split("/")[0]  # Ollama's library only — never another host


def pull(name: str) -> bool:
    """Start `ollama pull <name>` in the background. False if one is already running for it."""
    n = name.strip().lower()
    if not name_ok(n):
        raise ValueError("That is not a model name from Ollama's library (e.g. qwen3:8b).")
    with _lock:
        if PULLS.get(n, {}).get("state") == "downloading":
            return False
        PULLS[n] = {"state": "downloading", "pct": 0, "status": "starting", "error": ""}

    def run():
        try:
            if settings.model_mode == "stub":
                PULLS[n].update(state="done", pct=100, status="success")
                return
            with httpx.stream("POST", f"{settings.ollama_url}/api/pull", json={"model": n, "stream": True}, timeout=None) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("error"):
                        raise RuntimeError(str(d["error"])[:200])
                    tot, done = d.get("total") or 0, d.get("completed") or 0
                    PULLS[n].update(status=str(d.get("status") or "")[:80], pct=int(done * 100 / tot) if tot else PULLS[n]["pct"])
            PULLS[n].update(state="done", pct=100, status="success")
        except Exception as e:  # noqa: BLE001
            PULLS[n].update(state="failed", error=str(e)[:200])
        finally:
            with _lock:
                _cache["at"] = 0.0

    if settings.model_mode == "stub":
        run()
    else:
        threading.Thread(target=run, name=f"pull-{n}", daemon=True).start()
    return True
