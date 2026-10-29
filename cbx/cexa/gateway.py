"""The one door to language models. Everything C-EXA says or reads passes through here so it is
logged, budgeted, schema-checked and swappable.

    gw = Gateway(db)
    out = gw.json(task="assess", system=..., user=..., schema={...})   # dict, validated
    txt = gw.text(task="chat", system=..., user=...)

Modes (settings.model_mode):
  live  → Ollama /api/chat (qwen3.8:27b by default; per-task model choice below)
  stub  → deterministic answers from cbx.cexa.stub, so tests and a fresh install work with no GPU
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..config import ROOT, settings
from ..db import utcnow
from ..models import ModelCall

DEFAULT_NUM_CTX = 32768


def holds_write_lock(db: Session) -> bool:
    """Has this session written something it has not committed yet? On SQLite that is the database's
    one write lock: every other writer waits behind it (30 s) and then fails with "database is locked".
    A model call takes seconds to minutes, so it must never be made while holding it (1.0.60)."""
    try:
        if not db.in_transaction() or db.get_bind().dialect.name != "sqlite":
            return False
        raw = db.connection().connection.dbapi_connection
        return bool(getattr(raw, "in_transaction", False))
    except Exception:  # noqa: BLE001 — a check, never a failure
        return False


def guard_write_lock(db: Session, what: str) -> None:
    """Before something slow. In the test suite a held write lock is a bug: raise, so the test names the
    caller. On a server the caller's work so far is saved first (it would have been saved at the end
    anyway) and the place is written to the log — a minute of everyone waiting behind one model call
    is the worse harm."""
    if not holds_write_lock(db):
        return
    if settings.strict_db_locks:
        raise AssertionError(f"{what} while holding the database write lock — commit first")
    import logging
    import traceback

    where = " < ".join(f"{f.filename.rsplit('cbx', 1)[-1]}:{f.lineno}" for f in traceback.extract_stack(limit=6)[:-2])
    logging.getLogger("cbx.gateway").warning("%s while holding the database write lock — saved first so others need not wait (%s)", what, where)
    db.commit()


def _log_call(db: Session, row: ModelCall) -> None:
    """Write the model-call row in its own short transaction, so the caller's session does not start
    holding the write lock just because a model answered (that held it through the next call — the
    "database is locked" of 30 Sep, during a PDF Studio batch). If the caller already holds the lock,
    a second connection would wait on it, so the row then goes with the caller's work instead."""
    if holds_write_lock(db):
        db.add(row)
        return
    from ..db import SessionLocal

    s = SessionLocal()
    try:
        s.add(row)
        s.commit()
    except Exception:  # noqa: BLE001 — the log row is bookkeeping; never fail the answer for it
        s.rollback()
    finally:
        s.close()


def num_ctx() -> int:
    """The context window handed to Ollama on every call (`options.num_ctx`).

    A 27B model at 32k context needs the whole 32 GB of a 5090; when Ollama cannot fit the KV cache it
    quietly spills layers to the CPU and every call takes a minute. So the owner can lower it with
    CBX_NUM_CTX — as an environment variable or a line in `.env` next to run.bat. Read here rather
    than in cbx.config.Settings because config.py is shared; the fallback is what C-EXA always used."""
    raw = os.environ.get("CBX_NUM_CTX", "")
    if not raw:
        try:
            for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("CBX_NUM_CTX="):
                    raw = line.split("=", 1)[1].split("#")[0].strip()
                    break
        except OSError:
            raw = ""
    try:
        return max(2048, int(raw or DEFAULT_NUM_CTX))
    except ValueError:
        return DEFAULT_NUM_CTX


# Rough local-electricity cost so the spend meter means something (R/1k tokens by model class).
_COST_PER_1K = {"axl": 0.006, "worker": 0.002, "ocr": 0.0005, "developer": 0.006, "designer": 0.006, "helper": 0.004}

TASK_MODEL = {
    # task → which model class does it
    "profile_author": "axl",
    "profile_repair": "axl",
    "assess": "axl",
    "interview": "axl",
    "chat": "axl",
    "dev": "axl",
    "brief": "axl",
    "residual_read": "worker",
    "vision_read": "worker",
    "ocr": "ocr",
    "call": "helper",  # 1.0.64: a voice call wants a quick model (the light bots' pick)
    "mail_read": "helper", "mail_draft": "axl",  # 1.0.69 My mail: reading is quick, a draft gets the brain
    "support_relay": "helper",  # 1.0.74: a support person's question, tidied for the requester
    "support_new_problem": "helper",  # 1.0.109: is a requester's mail in an old ticket's thread a different problem?
    "support_ask": "axl",  # 1.0.109 (round 2): a desk person asks AXL about a ticket (cbx/support/askaxl.py) — a person asked, so AXL answers
    "problem_check": "helper",
    "ap_read": "helper", "ap_reply": "helper", "ap_axl": "axl",
    # 1.0.98: from CBX-Connect 2.10.x — AP capture (Sol sorts, Mia matches, Tess checks: quick; Cara/Dex/Eli read invoices
    # with the eyes; Seth, the senior, on C-EXA's model) and the support desk (readers quick, Ada and C-EXA on the brain,
    # Iris reads pictures with the eyes)
    "ap_sort": "helper", "ap_match": "helper", "ap_sap_check": "helper", "ap_capture": "designer", "ap_capture_senior": "designer",  # 1.0.110: Seth reads page pictures — the eyes model (C-EXA's own can't see: 96 of 96 failed on 19 Oct)
    "support_read": "helper", "support_learn": "helper", "support_lead": "axl", "support_axl": "axl", "support_see": "designer",  # 1.0.95: the AP Tool's chain — Paula reads (quick), C-EXA suggests; Quinn drafts  # 1.0.93: the AI check on something that failed (cbx/aicheck.py) — the quick model
    # C-EXA's team (cbx/cexa/ceo.py): she plans; developers write the folder; the designer looks; the helper words it
    "ceo_intake": "axl", "ceo_plan": "axl", "ceo_revise": "axl", "ceo_summary": "axl",
    "team_data": "developer", "team_page": "developer", "team_flow": "developer", "team_fix": "developer",
    "team_look": "designer",
    "team_words": "helper", "team_readme": "helper", "team_sample": "helper",
}


# Which temperature a task runs at (see Settings.temp_*). Anything not listed is "data".
TASK_TEMP = {
    "voice": "voice", "brief": "voice", "aside": "voice", "journal": "voice",
    "dev": "code",
    "team_data": "code", "team_page": "code", "team_flow": "code", "team_fix": "code", "ceo_plan": "code", "ceo_revise": "code",
}


def temperature_for(task: str) -> float:
    cls = TASK_TEMP.get(task, "data")
    t = {"voice": settings.temp_voice, "code": settings.temp_code, "data": settings.temp_data}[cls]
    return max(0.0, min(2.0, float(t)))  # Ollama's range


# ---- 1.0.85: the card is shared — one gate for every model call -------------------------------------------
# Agents, support reading, the heartbeat, mail agents and C-EXA's chat all ask the same GPU. Without a gate they
# all land on Ollama at once, the models fight for the card and calls time out. Every real call now passes this
# gate: at most CBX_GPU_SLOTS at a time (default 1), and a person waiting (chat, voice, a call) goes before
# background work.
# 1.0.101: the AP capture readers (Cara, Dex, Eli, Seth; Mia's bill-to question) and the support desk's readers (Mo, Lu,
# Kit, Iris, Ada) read what people are waiting for: they count as people's work, so they go before C-EXA's background team
# work (the council's team_lens held the one slot ~3 min a call while invoices showed "Being read")
READERS = frozenset({"ap_capture", "ap_capture_senior", "ap_match", "support_read", "support_see", "support_lead"})
FOREGROUND = frozenset({"chat", "voice", "call", "aside", "interview", "interview_suggest", "dev", "brief"}) | READERS


class GpuGate:
    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.busy = 0
        self.people = 0  # people waiting for the card

    def slots(self) -> int:
        try:
            return max(1, int(settings.gpu_slots or 1))
        except (TypeError, ValueError):
            return 1

    @contextmanager
    def use(self, task: str):
        person = task in FOREGROUND
        with self.cv:
            if person:
                self.people += 1
            try:
                while self.busy >= self.slots() or (not person and self.people > 0):
                    self.cv.wait(timeout=5)
            finally:
                if person:
                    self.people -= 1
            self.busy += 1
        try:
            yield
        finally:
            with self.cv:
                self.busy -= 1
                self.cv.notify_all()

    def state(self) -> dict:
        return {"slots": self.slots(), "busy": self.busy, "people_waiting": self.people}


GPU = GpuGate()


class BudgetExhausted(Exception):
    pass


class ToolsUnsupported(Exception):
    """The model has no tool calling (Ollama says "does not support tools"): the loop asks for JSON instead."""


class ModelError(Exception):
    """The model could not be used. `raw` carries the last reply text (if any) so a caller can
    fall back to it when the model answered in prose instead of the JSON it was asked for."""

    raw: str = ""


class Gateway:
    def __init__(self, db: Session, *, batch_id: Optional[int] = None, ceiling_zar: Optional[float] = None, spent_zar: float = 0.0):
        self.db = db
        self.batch_id = batch_id
        self.ceiling = ceiling_zar
        self.spent = spent_zar
        self.mode = settings.model_mode

    # ------------------------------------------------------------------ public
    def model_for(self, task: str) -> str:
        cls = TASK_MODEL.get(task, "axl")
        from .models_fit import pick  # 1.0.62: the owner's pick per kind of bot wins

        chosen = pick({"developer": "worker", "designer": "eyes", "helper": "light"}.get(cls, ""))
        if chosen:
            return chosen
        return {"axl": settings.cexa_model, "worker": settings.worker_model, "ocr": settings.ocr_model,
                "developer": settings.model_developer or settings.cexa_model, "designer": settings.model_designer or settings.cexa_model,
                "helper": settings.model_helper or settings.cexa_model}[cls]

    def text(self, task: str, system: str, user: str, *, images: Optional[list[bytes]] = None, temperature: Optional[float] = None, model: str = "") -> str:
        return self._call(task, system, user, images=images, temperature=temperature_for(task) if temperature is None else temperature, want_json=False, model=model)

    def json(self, task: str, system: str, user: str, schema: dict, *, images: Optional[list[bytes]] = None, retries: int = 2, required: Optional[list] = None, model: str = "") -> dict:
        """Ask for JSON matching `schema` (a light {key: type} description). Retries on bad output.
        `required`: the keys that must be there (default: every key of the schema)."""
        sys_full = system + "\n\nReply with ONLY a JSON object with these keys: " + json.dumps(schema) + ". No prose, no markdown."
        last_err, raw = "", ""
        for attempt in range(retries + 1):
            raw = self._call(task, sys_full, user if attempt == 0 else user + f"\n\nYour previous reply was not valid JSON ({last_err}). Reply with only the JSON object.", images=images, temperature=temperature_for(task) if attempt == 0 else 0.0, want_json=True, retry_no=attempt, model=model)
            try:
                data = _extract_json(raw)
                missing = [k for k in (schema if required is None else required) if k not in data]
                if missing:
                    raise ValueError(f"missing keys {missing}")
                return data
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
        err = ModelError(f"model did not return valid JSON: {last_err}")
        err.raw = (raw or "").strip()
        raise err

    def chat(self, task: str, model: str, messages: list[dict], *, tools: Optional[list[dict]] = None, temperature: float = 0.2, want_json: bool = False, timeout: int = 300) -> dict:
        """One turn of an agent's conversation (1.0.62, cbx/cexa/loop.py): the messages so far, the tools it
        may call (Ollama's native tool calling). Returns {"content": str, "tool_calls": [{"name", "arguments"}]}.
        Raises ToolsUnsupported when the model cannot call tools, so the loop can fall back to JSON."""
        guard_write_lock(self.db, f"model call '{task}'")
        t0, ok, out = time.time(), True, {"content": "", "tool_calls": []}
        tokens_in = sum(len(str(m.get("content") or "")) for m in messages) // 4
        try:
            if self.mode == "stub":
                from . import loop_stub

                out = loop_stub.reply(messages, tools, want_json=want_json)
            else:
                with GPU.use(task):
                    out = self._ollama_chat(model, messages, tools, temperature, want_json, timeout)
        except ToolsUnsupported:
            ok = False
            raise
        except Exception:
            ok = False
            raise
        finally:
            ms = int((time.time() - t0) * 1000)
            tokens_out = (len(out.get("content") or "") + len(json.dumps(out.get("tool_calls") or []))) // 4
            cost = (tokens_in + tokens_out) / 1000 * _COST_PER_1K["helper"]
            self.spent += cost
            _log_call(self.db, ModelCall(task=task, model=model, batch_id=self.batch_id, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=ms, cost_zar=cost, ok=ok, schema_retries=0))
        return out

    def _ollama_chat(self, model: str, messages: list[dict], tools, temperature: float, want_json: bool, timeout: int) -> dict:
        body: dict[str, Any] = {"model": model, "stream": False, "messages": messages, "options": {"temperature": temperature, "num_ctx": num_ctx()}}
        if tools:
            body["tools"] = tools
        if want_json:
            body["format"] = "json"
        try:
            r = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise ModelError(f"Ollama not reachable at {settings.ollama_url}: {e}") from e
        if r.status_code >= 400:
            if "support tools" in r.text:
                raise ToolsUnsupported(model)
            raise ModelError(f"Ollama error {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:
            raise ModelError(f"Ollama returned something that is not JSON: {r.text[:120]!r}") from e
        if not isinstance(data, dict) or data.get("error"):
            err = str((data or {}).get("error") if isinstance(data, dict) else data)
            if "support tools" in err:
                raise ToolsUnsupported(model)
            raise ModelError(f"Ollama error: {err[:200]}")
        msg = data.get("message") or {}
        calls = []
        for c in (msg.get("tool_calls") or []) if isinstance(msg, dict) else []:
            f = (c or {}).get("function") or {}
            args = f.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if f.get("name"):
                calls.append({"name": str(f["name"]), "arguments": args if isinstance(args, dict) else {}})
        return {"content": str(msg.get("content") or "") if isinstance(msg, dict) else "", "tool_calls": calls}

    # ----------------------------------------------------------------- private
    def _call(self, task: str, system: str, user: str, *, images, temperature, want_json, retry_no: int = 0, model: str = "") -> str:
        model = model or self.model_for(task)  # 1.0.98: a tool may name its own model (AP Settings: the capture and senior models)
        guard_write_lock(self.db, f"model call '{task}'")
        t0 = time.time()
        ok = True
        tokens_in = len(system + user) // 4
        try:
            if self.mode == "stub":
                from . import stub

                out = stub.answer(task, system, user, want_json=want_json)
            else:
                with GPU.use(task):
                    out = self._ollama(model, system, user, images, temperature, want_json, **({"timeout": 420} if task.startswith(("ceo_", "team_")) else {}))  # the team writes long replies
        except Exception:
            ok = False
            raise
        finally:
            ms = int((time.time() - t0) * 1000)
            tokens_out = len(locals().get("out", "") or "") // 4
            cls = TASK_MODEL.get(task, "axl")
            cost = (tokens_in + tokens_out) / 1000 * _COST_PER_1K[cls]
            self.spent += cost
            _log_call(self.db, ModelCall(task=task, model=model, batch_id=self.batch_id, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=ms, cost_zar=cost, ok=ok, schema_retries=retry_no))
            if self.ceiling is not None and self.spent > self.ceiling:
                raise BudgetExhausted(f"batch spend R{self.spent:.2f} exceeded ceiling R{self.ceiling:.2f}")
        return out

    def _ollama(self, model: str, system: str, user: str, images, temperature: float, want_json: bool, timeout: int = 180) -> str:
        msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            import base64

            msg["images"] = [base64.b64encode(b).decode() for b in images]
        body: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, msg],
            "options": {"temperature": temperature, "num_ctx": num_ctx()},  # CBX_NUM_CTX, see num_ctx()
        }
        if want_json:
            body["format"] = "json"
        try:
            r = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ModelError(f"Ollama not reachable at {settings.ollama_url}: {e}") from e
        try:
            data = r.json()
        except ValueError as e:
            raise ModelError(f"Ollama at {settings.ollama_url} returned something that is not JSON: {r.text[:120]!r}") from e
        if not isinstance(data, dict):
            raise ModelError(f"Ollama returned an unexpected reply: {str(data)[:120]!r}")
        if data.get("error"):
            raise ModelError(f"Ollama error: {data['error']}")
        msg = data.get("message") or {}
        return str(msg.get("content") or "") if isinstance(msg, dict) else ""


def _extract_json(raw: str) -> dict:
    """The JSON object in a model reply — tolerating code fences and prose around it. Always a dict."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    return data


def _gb(n: Any) -> float:
    try:
        return round(float(n or 0) / (1024 ** 3), 1)
    except (TypeError, ValueError):
        return 0.0


def _ollama_loaded() -> list[dict]:
    """What Ollama has in memory right now (`GET /api/ps`), with how much of each model sits on the GPU.
    `size` is the whole model, `size_vram` the part in video memory; equal means 100 % GPU. Older
    Ollama builds have no /api/ps — then this is simply an empty list."""
    try:
        r = httpx.get(f"{settings.ollama_url}/api/ps", timeout=2)
        r.raise_for_status()
        rows = r.json().get("models", []) if isinstance(r.json(), dict) else []
    except Exception:  # noqa: BLE001 — absence of the diagnostic is not an error
        return []
    out = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        size = int(m.get("size") or 0)
        vram = int(m.get("size_vram") or 0)
        pct = 100 if size and vram >= size else (int(round(100 * vram / size)) if size else 0)
        out.append({"name": str(m.get("name") or m.get("model") or "?"), "size": size, "size_vram": vram, "gpu_pct": max(0, min(100, pct)), "context": int(m.get("context_length") or 0), "size_gb": _gb(size), "vram_gb": _gb(vram)})
    return out


def placement_advice(loaded: list[dict], ctx: Optional[int] = None) -> str:
    """One plain sentence per loaded model that is not fully on the GPU — the usual reason C-EXA is slow."""
    ctx = ctx or num_ctx()
    lines = []
    for m in loaded:
        pct = m.get("gpu_pct", 100)
        if pct >= 100:
            continue
        smaller = "use the 27b-nvfp4 tag" if "27b" in m["name"] else "pull a smaller quantisation of it"
        lines.append(f"{m['name']} is {pct}% on the GPU — {100 - pct}% is running on the CPU, which is why C-EXA is slow. Free VRAM: close other GPU apps, or lower CBX_NUM_CTX (now {ctx}), or {smaller}.")
    return " ".join(lines)


def latency_by_task(db: Session, hours: int = 24) -> list[dict]:
    """Average model latency per task over the last `hours`, from ModelCall — slowest first."""
    since = utcnow() - timedelta(hours=hours)
    failed = case((ModelCall.ok.is_(False), 1), else_=0)
    rows = (
        db.query(ModelCall.task, func.count(ModelCall.id), func.avg(ModelCall.latency_ms), func.max(ModelCall.latency_ms), func.sum(failed))
        .filter(ModelCall.at >= since)
        .group_by(ModelCall.task)
        .all()
    )
    out = [{"task": t, "calls": int(n or 0), "avg_s": round((avg or 0) / 1000, 1), "max_s": round((mx or 0) / 1000, 1), "failed": int(f or 0)} for t, n, avg, mx, f in rows]
    out.sort(key=lambda x: -x["avg_s"])
    return out


SLOW_S = 30.0  # a C-EXA call slower than this, on average, means something is wrong with the model placement


def ollama_status(db: Optional[Session] = None) -> dict:
    """Used by the health page, the Talk-to-C-EXA header, `/api/cexa/status` and the owner's Home.

    Always: online, mode, models, num_ctx. In live mode also `loaded` (what Ollama holds in memory and how
    much of each model is on the GPU) and `advice` — a plain-English sentence when a model is partly on the
    CPU. With a `db`, `latency` per task over the last 24 h and `slow` (average C-EXA call above SLOW_S)."""
    ctx = num_ctx()
    if settings.model_mode == "stub":
        out: dict[str, Any] = {"online": True, "mode": "stub", "models": [settings.cexa_model], "loaded": [], "advice": "", "num_ctx": ctx}
    else:
        try:
            r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            loaded = _ollama_loaded()
            out = {"online": True, "mode": "live", "models": names, "cexa_ready": any(n.startswith(settings.cexa_model.split(":")[0]) for n in names), "loaded": loaded, "advice": placement_advice(loaded, ctx), "num_ctx": ctx}
        except Exception as e:  # noqa: BLE001
            out = {"online": False, "mode": "live", "error": str(e), "models": [], "loaded": [], "advice": f"Ollama is not answering at {settings.ollama_url}. C-EXA falls back to fixed sentences and PDF Studio runs on rules only until it is back.", "num_ctx": ctx}
    if db is not None:
        try:
            lat = latency_by_task(db)
        except Exception:  # noqa: BLE001 — a diagnostic must never break the page that shows it
            lat = []
        out["latency"] = lat
        cexa_tasks = [x for x in lat if TASK_MODEL.get(x["task"], "axl") == "axl" and x["calls"]]
        worst = max((x["avg_s"] for x in cexa_tasks), default=0.0)
        out["slow"] = bool(out["mode"] == "live" and worst > SLOW_S)
        if out["slow"] and not out["advice"]:
            out["advice"] = f"C-EXA's calls averaged {worst:.0f} s over the last 24 h (anything over {SLOW_S:.0f} s is slow). Nothing is loaded in Ollama right now, so ask C-EXA something and look again — or lower CBX_NUM_CTX (now {ctx})."
    return out


def model_health_text(db: Session) -> str:
    """The same diagnostic as plain text, for C-EXA's `model_health` tool and the owner's question 'why are you slow'."""
    st = ollama_status(db)
    lines = [f"Model: {settings.cexa_model} (worker {settings.worker_model}, OCR {settings.ocr_model}) · mode {st['mode']} · Ollama {'online' if st.get('online') else 'OFFLINE'} at {settings.ollama_url} · context window {st['num_ctx']} tokens (CBX_NUM_CTX)."]
    if st["mode"] == "stub":
        lines.append("Stub mode: no model is used; every answer is a fixed sentence. Set CBX_MODEL_MODE=live once Ollama has the models.")
    if st.get("loaded"):
        for m in st["loaded"]:
            lines.append(f"Loaded: {m['name']} — {m['gpu_pct']}% on the GPU ({m['vram_gb']} of {m['size_gb']} GB)" + (f", context {m['context']}" if m.get("context") else "") + ".")
    elif st["mode"] == "live" and st.get("online"):
        lines.append("Nothing is loaded in Ollama right now (it unloads idle models after a few minutes).")
    if st.get("advice"):
        lines.append("Advice: " + st["advice"])
    lat = st.get("latency") or []
    if lat:
        lines.append("Last 24 h: " + "; ".join(f"{x['task']} {x['calls']} call{'s' if x['calls'] != 1 else ''}, avg {x['avg_s']} s (max {x['max_s']} s)" + (f", {x['failed']} failed" if x["failed"] else "") for x in lat) + ".")
    else:
        lines.append("Last 24 h: no model calls.")
    if st.get("slow"):
        lines.append("Verdict: slow. A healthy 27B model fully on a 5090 answers most calls in under 30 s.")
    elif st["mode"] == "live" and st.get("online"):
        lines.append("Verdict: healthy.")
    return "\n".join(lines)
