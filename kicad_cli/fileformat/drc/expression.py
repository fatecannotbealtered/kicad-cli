"""The expressions of a board's custom rules: `A.NetClass == 'HV*' &&
B.Type == 'Zone'`.

KiCad's rule language, as its documentation describes it and its DRC
answers to it (`tests/fixtures/drc/drcrules`): an expression compares the
properties of the items a rule is asked about -- `A`, and for a rule about
two items `B` -- with each other, with text, and with numbers with units,
joins the comparisons with `&&`, `||` and `!`, and calls functions of an
item: `A.intersectsArea('name')`, `A.inDiffPair('*')`. Text compared with
`==` or `!=` is a pattern, `*` and `?` its wildcards.

Nothing is evaluated that is not understood: an expression naming a property
or a function this does not know is `Unknown`, and a rule whose condition is
unknown is not applied -- the check it would decide is said not to be made.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

NM = 1_000_000
UNITS = {"mm": NM, "mil": 25_400, "mils": 25_400, "in": 25_400_000, "um": 1_000, "nm": 1}

_TOKEN = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d*)?|\.\d+)(?P<unit>mm|mils?|in|um|nm|deg)?"
    r"|(?P<string>'[^']*'|\"[^\"]*\")"
    r"|(?P<name>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<op>==|!=|<=|>=|&&|\|\||[<>!().,+\-*/]))"
)


class Unknown(Exception):
    """An expression names what this does not know."""


class ExpressionError(ValueError):
    """An expression that does not parse."""


@dataclass(frozen=True)
class Node:
    kind: str  # number, string, property, call, unary, binary
    value: object = None
    args: tuple = ()


def parse(text: str) -> Node:
    tokens = _tokens(text)
    node, rest = _or(tokens)
    if rest:
        raise ExpressionError(f"unexpected {rest[0][1]!r} in {text!r}")
    return node


def _tokens(text: str) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            raise ExpressionError(f"cannot read {text[pos:]!r}")
        pos = m.end()
        if m.group("number") is not None:
            number = float(m.group("number"))
            unit = m.group("unit")
            if unit == "deg":
                out.append(("number", number))
            else:
                out.append(("length" if unit else "number",
                            number * UNITS[unit] if unit else number))  # fmt: skip
        elif m.group("string") is not None:
            out.append(("string", m.group("string")[1:-1]))
        elif m.group("name") is not None:
            out.append(("name", m.group("name")))
        elif m.group("op") is not None:
            out.append(("op", m.group("op")))
    return out


def _or(tokens):
    left, tokens = _and(tokens)
    while tokens and tokens[0] == ("op", "||"):
        right, tokens = _and(tokens[1:])
        left = Node("binary", "||", (left, right))
    return left, tokens


def _and(tokens):
    left, tokens = _compare(tokens)
    while tokens and tokens[0] == ("op", "&&"):
        right, tokens = _compare(tokens[1:])
        left = Node("binary", "&&", (left, right))
    return left, tokens


def _compare(tokens):
    left, tokens = _sum(tokens)
    if tokens and tokens[0][0] == "op" and tokens[0][1] in ("==", "!=", "<", ">", "<=", ">="):
        op = tokens[0][1]
        right, tokens = _sum(tokens[1:])
        left = Node("binary", op, (left, right))
    return left, tokens


def _sum(tokens):
    left, tokens = _product(tokens)
    while tokens and tokens[0][0] == "op" and tokens[0][1] in ("+", "-"):
        op = tokens[0][1]
        right, tokens = _product(tokens[1:])
        left = Node("binary", op, (left, right))
    return left, tokens


def _product(tokens):
    left, tokens = _unary(tokens)
    while tokens and tokens[0][0] == "op" and tokens[0][1] in ("*", "/"):
        op = tokens[0][1]
        right, tokens = _unary(tokens[1:])
        left = Node("binary", op, (left, right))
    return left, tokens


def _unary(tokens):
    if tokens and tokens[0] == ("op", "!"):
        inner, tokens = _unary(tokens[1:])
        return Node("unary", "!", (inner,)), tokens
    if tokens and tokens[0] == ("op", "-"):
        inner, tokens = _unary(tokens[1:])
        return Node("unary", "-", (inner,)), tokens
    return _primary(tokens)


def _primary(tokens):
    if not tokens:
        raise ExpressionError("an expression ends too soon")
    kind, value = tokens[0]
    if (kind, value) == ("op", "("):
        inner, rest = _or(tokens[1:])
        if not rest or rest[0] != ("op", ")"):
            raise ExpressionError("a parenthesis is not closed")
        return inner, rest[1:]
    if kind in ("number", "length"):
        return Node(kind, value), tokens[1:]
    if kind == "string":
        return Node("string", value), tokens[1:]
    if kind == "name":
        rest = tokens[1:]
        if rest and rest[0] == ("op", ".") and len(rest) > 1 and rest[1][0] == "name":
            member = rest[1][1]
            rest = rest[2:]
            if rest and rest[0] == ("op", "("):
                args, rest = _arguments(rest[1:])
                return Node("call", (value, member), tuple(args)), rest
            return Node("property", (value, member)), rest
        return Node("name", value), rest
    raise ExpressionError(f"unexpected {value!r}")


def _arguments(tokens):
    args = []
    if tokens and tokens[0] == ("op", ")"):
        return args, tokens[1:]
    while True:
        arg, tokens = _or(tokens)
        args.append(arg)
        if tokens and tokens[0] == ("op", ","):
            tokens = tokens[1:]
            continue
        if tokens and tokens[0] == ("op", ")"):
            return args, tokens[1:]
        raise ExpressionError("a call's arguments are not closed")


# -- evaluating --------------------------------------------------------------------------------


def evaluate(node: Node, scope) -> object:
    """The value of an expression; `scope(name)` gives the item a name
    stands for ("A", "B"), which answers `.get(property)` and
    `.call(function, args)`. Raises `Unknown` for what it cannot answer."""
    kind = node.kind
    if kind in ("number", "length", "string"):
        return node.value
    if kind == "property":
        owner, name = node.value
        item = scope(owner)
        if item is None:
            raise Unknown(f"{owner}.{name}")
        return item.get(name)
    if kind == "call":
        owner, name = node.value
        item = scope(owner)
        if item is None:
            raise Unknown(f"{owner}.{name}()")
        args = [evaluate(arg, scope) for arg in node.args]
        return item.call(name, args)
    if kind == "unary":
        value = evaluate(node.args[0], scope)
        return (not _truth(value)) if node.value == "!" else -_number(value)
    if kind == "binary":
        op = node.value
        if op == "&&":
            return _truth(evaluate(node.args[0], scope)) and _truth(evaluate(node.args[1], scope))
        if op == "||":
            return _truth(evaluate(node.args[0], scope)) or _truth(evaluate(node.args[1], scope))
        left, right = evaluate(node.args[0], scope), evaluate(node.args[1], scope)
        if op in ("==", "!="):
            same = _equal(left, right)
            return same if op == "==" else not same
        if op in ("<", ">", "<=", ">="):
            a, b = _number(left), _number(right)
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        a, b = _number(left), _number(right)
        return {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else 0.0}[op]
    raise Unknown(kind)


def _truth(value) -> bool:
    if isinstance(value, str):
        return value != ""
    return bool(value)


def _number(value) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise Unknown(f"a number from {value!r}") from exc


def _equal(left, right) -> bool:
    if isinstance(left, (list, tuple, set, frozenset)):
        return any(_equal(item, right) for item in left)
    if isinstance(right, (list, tuple, set, frozenset)):
        return any(_equal(left, item) for item in right)
    if isinstance(left, str) or isinstance(right, str):
        a, b = str(left), str(right)
        return matches(b, a) or matches(a, b)
    return abs(_number(left) - _number(right)) < 1e-9


def matches(pattern: str, text: str) -> bool:
    """A pattern with `*` and `?`, matched against the whole text, letter
    case aside: KiCad's 'CASE' is net case."""
    pattern, text = pattern.casefold(), text.casefold()
    if "*" not in pattern and "?" not in pattern:
        return pattern == text
    return fnmatch.fnmatchcase(text, pattern.replace("[", "[[]"))
