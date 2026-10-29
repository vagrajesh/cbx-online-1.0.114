"""No model (tests, a GPU-less install): a deterministic agent for cbx/cexa/loop.py.

A work's detail may carry lines `>> tool_name {"arg": "value"}` — the stub calls them in order, one per
turn, then finishes with what the last result said. Without such lines it plans, looks at the pulse and
finishes. After an answer (the owner's, or colleagues') it finishes with that answer."""
from __future__ import annotations

import json
import re
from typing import Optional


def _directives(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*>>\s*([a-z_]+)\s*(\{.*\})?\s*$", line)
        if m:
            try:
                args = json.loads(m.group(2)) if m.group(2) else {}
            except ValueError:
                args = {}
            out.append({"name": m.group(1), "arguments": args})
    return out


def reply(messages: list[dict], tools: Optional[list[dict]], *, want_json: bool = False) -> dict:
    first = next((m.get("content") or "" for m in messages if m.get("role") == "user"), "")
    plan = _directives(first)
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    done = sum(1 for m in messages if m.get("role") == "assistant")
    names = {t["function"]["name"] for t in tools or []}
    last = messages[-1] if messages else {}
    later_user = len(users) > 1 and users[-1] == len(messages) - 1 and not str(last.get("content", "")).startswith("RESULT of ")

    def call(name, args):
        if want_json or tools is None:
            return {"content": json.dumps({"tool": name, "args": args}), "tool_calls": []}
        return {"content": "", "tool_calls": [{"name": name, "arguments": args}]}

    if later_user and done >= len(plan):
        return call("finish", {"result": "Thanks — going with: " + str(last.get("content", ""))[:300], "confidence": "85"})
    if done < len(plan):
        d = plan[done]
        return call(d["name"], d["arguments"])
    if not plan and done == 0:
        return call("plan", {"steps": "look at how things are; say what I found"})
    if not plan and done == 1 and "pulse" in names:
        return call("pulse", {})
    said = str(last.get("content", ""))
    return call("finish", {"result": "Done: " + re.sub(r"\s+", " ", said)[:240], "confidence": "80"})
