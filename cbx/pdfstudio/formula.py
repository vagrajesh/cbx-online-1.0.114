"""Formula columns: a value worked out from other columns, never read off the page.

    validate("material_excl_vat * 1.10", keys={"material_excl_vat": "money", ...}) → ["material_excl_vat"]
    evaluate("labour_hours * labour_rate", {"labour_hours": 3, "labour_rate": 450.0})   → 1350.0

Grammar (a small recursive-descent parser — no eval()):
    expr   := term (("+" | "-") term)*
    term   := factor (("*" | "/") factor)*
    factor := ("-" | "+") factor | number | key | func "(" expr ("," expr)* ")" | "(" expr ")"
    func   := round | abs | min | max
Numbers may use a decimal point or comma (1,10 and 1.10 are the same). Column keys are snake_case.
`evaluate` returns None when any referenced column is blank or not a number, or on division by zero
— a blank formula cell is the honest answer when an optional input is not on the document.
"""
from __future__ import annotations

import math
import re
from typing import Optional

FUNCS = ("round", "abs", "min", "max")
_TOKEN = re.compile(r"\s*(?:(\d+(?:[.,]\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(\*\*|[-+*/(),]))")
NUMERIC_KINDS = ("money", "number", "formula")


class FormulaError(ValueError):
    """A plain-English problem with an expression."""


def _tokens(expr: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    s = expr or ""
    while pos < len(s):
        if s[pos:].strip() == "":
            break
        m = _TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"I don't understand '{s[pos:pos+12].strip()}' — use column keys, numbers, + - * / ( ) and round(x, 2).")
        num, name, op = m.groups()
        if num is not None:
            out.append(("num", num.replace(",", ".")))
        elif name is not None:
            out.append(("name", name.lower()))
        else:
            if op == "**":
                raise FormulaError("Powers (**) aren't supported — use multiplication.")
            out.append(("op", op))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.t = tokens
        self.i = 0

    def peek(self) -> Optional[tuple[str, str]]:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise FormulaError("The formula ends too early — something is missing at the end.")
        self.i += 1
        return tok

    def expect(self, op: str) -> None:
        tok = self.peek()
        if tok != ("op", op):
            got = f"'{tok[1]}'" if tok else "the end of the formula"
            raise FormulaError(f"Expected '{op}' but found {got}.")
        self.i += 1

    def parse(self):
        if not self.t:
            raise FormulaError("Type a formula, e.g. material_excl_vat * 1.10.")
        node = self.expr()
        if self.peek() is not None:
            raise FormulaError(f"Unexpected '{self.peek()[1]}' after the end of the formula.")
        return node

    def expr(self):
        node = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.factor()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            node = ("bin", op, node, self.factor())
        return node

    def factor(self):
        tok = self.take()
        kind, val = tok
        if kind == "op" and val in ("-", "+"):
            return ("neg", self.factor()) if val == "-" else self.factor()
        if kind == "op" and val == "(":
            node = self.expr()
            self.expect(")")
            return node
        if kind == "num":
            return ("num", float(val))
        if kind == "name":
            if self.peek() == ("op", "("):
                if val not in FUNCS:
                    raise FormulaError(f"'{val}(' isn't a function I know. Use round, abs, min or max.")
                self.take()
                args = [self.expr()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr())
                self.expect(")")
                if val in ("abs",) and len(args) != 1:
                    raise FormulaError("abs() takes one value.")
                if val == "round" and len(args) not in (1, 2):
                    raise FormulaError("round() takes a value and the number of decimals, e.g. round(x, 2).")
                if val in ("min", "max") and len(args) < 2:
                    raise FormulaError(f"{val}() needs at least two values.")
                return ("call", val, args)
            return ("key", val)
        raise FormulaError(f"Unexpected '{val}'.")


def parse(expr: str):
    return _Parser(_tokens(expr)).parse()


def keys_in(node) -> list[str]:
    kind = node[0]
    if kind == "key":
        return [node[1]]
    if kind == "num":
        return []
    if kind == "neg":
        return keys_in(node[1])
    if kind == "bin":
        return keys_in(node[2]) + keys_in(node[3])
    if kind == "call":
        out: list[str] = []
        for a in node[2]:
            out += keys_in(a)
        return out
    return []


def validate(expr: str, columns: dict[str, str], *, own_key: str = "") -> list[str]:
    """Parse `expr` and check every key against `columns` ({key: ctype}). Returns the referenced keys
    in order. Raises FormulaError with the list of usable keys on an unknown one."""
    node = parse(expr)
    seen: list[str] = []
    usable = [k for k, ct in columns.items() if ct in NUMERIC_KINDS and k != own_key]
    for k in keys_in(node):
        if k == own_key:
            raise FormulaError("A formula can't use its own column.")
        if k not in columns:
            raise FormulaError(f"'{k}' isn't a column on this template. Columns you can use: {', '.join(usable) or 'none yet — add a money or number column first'}.")
        if columns[k] not in NUMERIC_KINDS:
            raise FormulaError(f"'{k}' is a {columns[k]} column — formulas can only use money, number and other formula columns ({', '.join(usable) or 'none yet'}).")
        if k not in seen:
            seen.append(k)
    return seen


def _eval(node, values: dict[str, float]) -> Optional[float]:
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "key":
        v = values.get(node[1])
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if kind == "neg":
        v = _eval(node[1], values)
        return None if v is None else -v
    if kind == "bin":
        a, b = _eval(node[2], values), _eval(node[3], values)
        if a is None or b is None:
            return None
        op = node[1]
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        return None if b == 0 else a / b
    if kind == "call":
        args = [_eval(a, values) for a in node[2]]
        if any(a is None for a in args):
            return None
        f = node[1]
        if f == "round":
            return round(args[0], int(args[1]) if len(args) > 1 else 0)
        if f == "abs":
            return abs(args[0])
        if f == "min":
            return min(args)
        return max(args)
    return None


def evaluate(expr: str, values: dict[str, float]) -> Optional[float]:
    """None when an input is blank / not a number / division by zero; else the rounded (2 dp) value."""
    try:
        v = _eval(parse(expr), values)
    except FormulaError:
        return None
    if v is None or isinstance(v, bool) or math.isnan(v) or math.isinf(v):
        return None
    return round(v, 2)


def missing_inputs(expr: str, values: dict[str, float]) -> list[str]:
    """Which referenced keys have no numeric value (for the cell's note)."""
    try:
        node = parse(expr)
    except FormulaError:
        return []
    out = []
    for k in keys_in(node):
        v = values.get(k)
        if (not isinstance(v, (int, float)) or isinstance(v, bool)) and k not in out:
            out.append(k)
    return out


def pretty(expr: str) -> str:
    """'material_excl_vat*1.10' → 'material_excl_vat × 1.10' for the sheet legend."""
    return re.sub(r"\s*\*\s*", " × ", re.sub(r"\s*/\s*", " ÷ ", (expr or "").strip()))
