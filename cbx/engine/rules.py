"""The Builder's rule shape, checked on the server (1.0.54) — the same test as web/builder/rules.js.

A flow the Builder can open as a rule is: its trigger, then optionally Find rows (+ For each over them),
then If steps that only have a "yes", then action steps one after the other (an approval's "yes" is the
next step and it has no "no"); with a For each, the steps after it run once at the end. Anything else
still runs, but the Builder opens it read-only — so C-EXA's team is held to this shape, and the loader
says so for a hand-written flow.
"""
from __future__ import annotations

from typing import Optional

LOOPS = ("if", "for_each", "find_rows")


def why_not(f: dict) -> Optional[str]:
    """None when the flow fits the rule shape, else a plain reason."""
    steps = f.get("steps") or {}
    if not isinstance(steps, dict) or not steps:
        return None
    seen: set = set()

    def take(i):
        if i in seen:
            raise ValueError("it loops back on itself")
        seen.add(i)
        s = steps.get(i)
        if not isinstance(s, dict):
            raise ValueError(f"a step points at '{i}', which is not there")
        return s

    def actions(i):
        while i:
            s = take(i)
            if s.get("type") in LOOPS:
                raise ValueError(f"a {str(s.get('type')).replace('_', ' ')} comes after an action")
            if s.get("type") == "approval":
                if s.get("no"):
                    actions(s["no"])  # its steps for "declined": actions only
                i = s.get("yes")
                continue
            i = s.get("next")

    try:
        cur = f.get("start")
        s = take(cur) if cur else None
        if s and s.get("type") == "find_rows":
            nx = steps.get(s.get("next")) if s.get("next") else None
            if isinstance(nx, dict) and nx.get("type") == "for_each" and nx.get("items") == (s.get("as") or "rows"):
                take(s["next"])
                if nx.get("next"):
                    actions(nx["next"])
                cur = nx.get("body")
            else:
                # once, with the whole list: If steps (only yes) first, then the steps
                x = s.get("next")
                sx = take(x) if x else None
                while sx and sx.get("type") == "if":
                    if sx.get("no"):
                        raise ValueError("an If has steps for 'no' as well as 'yes'")
                    x = sx.get("yes")
                    sx = take(x) if x else None
                if sx:
                    seen.discard(x)
                    actions(x)
                cur = None
            s = take(cur) if cur else None
        while s and s.get("type") == "if":
            if s.get("no"):
                raise ValueError("an If has steps for 'no' as well as 'yes'")
            cur = s.get("yes")
            s = take(cur) if cur else None
        if s:
            seen.discard(cur)
            actions(cur)
    except ValueError as e:
        return str(e)
    return None
