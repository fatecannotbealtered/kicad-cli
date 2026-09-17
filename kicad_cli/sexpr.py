"""Minimal S-expression reader for KiCad files.

KiCad's ``.kicad_pcb`` / ``.kicad_sch`` / ``.kicad_mod`` are all S-expressions.
Reading them directly is the most durable way to get at a board: it needs no
KiCad install, and it survives the removal of the SWIG bindings in KiCad 11.
Anything that can be answered from the file should be answered from the file.

The parser is deliberately small. It does not interpret KiCad semantics, it
only turns text into nested lists so callers can walk them.
"""

from __future__ import annotations

from typing import Any

Node = list[Any]  # a node is [tag, *children]; atoms are str


def parse(text: str) -> Node:
    """Parse a whole file into one root node. Raises ValueError on malformed input."""
    pos = 0
    n = len(text)
    stack: list[Node] = []
    root: Node | None = None

    while pos < n:
        ch = text[pos]

        if ch.isspace():
            pos += 1
            continue

        if ch == "(":
            node: Node = []
            if stack:
                stack[-1].append(node)
            elif root is None:
                root = node
            else:
                raise ValueError("more than one top-level expression")
            stack.append(node)
            pos += 1
            continue

        if ch == ")":
            if not stack:
                raise ValueError(f"unbalanced ')' at offset {pos}")
            stack.pop()
            pos += 1
            continue

        if ch == '"':
            pos += 1
            out = []
            while pos < n:
                c = text[pos]
                if c == "\\" and pos + 1 < n:
                    nxt = text[pos + 1]
                    out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    pos += 2
                    continue
                if c == '"':
                    pos += 1
                    break
                out.append(c)
                pos += 1
            else:
                raise ValueError("unterminated string")
            if not stack:
                raise ValueError("string outside any expression")
            stack[-1].append("".join(out))
            continue

        # bare atom
        start = pos
        while pos < n and not text[pos].isspace() and text[pos] not in '()"':
            pos += 1
        if pos == start:
            raise ValueError(f"unexpected character {text[start]!r} at offset {start}")
        if not stack:
            raise ValueError("atom outside any expression")
        stack[-1].append(text[start:pos])

    if stack:
        raise ValueError("unbalanced '(' - file ended inside an expression")
    if root is None:
        raise ValueError("empty file")
    return root


def tag(node: Any) -> str | None:
    """The head symbol of a node, or None for atoms and empty nodes."""
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def children(node: Node, name: str) -> list[Node]:
    """Direct children whose head symbol is ``name``."""
    return [c for c in node[1:] if isinstance(c, list) and tag(c) == name]


def child(node: Node, name: str) -> Node | None:
    for c in node[1:]:
        if isinstance(c, list) and tag(c) == name:
            return c
    return None


def value(node: Node | None, index: int = 1, default: Any = None) -> Any:
    """Positional value of a node, e.g. ``value(child(fp, "uuid"))``."""
    if node is None or len(node) <= index:
        return default
    return node[index]


def walk(node: Node, name: str):
    """Every descendant node with head symbol ``name``, at any depth."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, list):
            continue
        if tag(cur) == name:
            yield cur
        for c in reversed(cur[1:]):
            if isinstance(c, list):
                stack.append(c)


def prop(node: Node, name: str) -> str | None:
    """A KiCad ``(property "Name" "Value" ...)`` lookup on direct children."""
    for c in children(node, "property"):
        if len(c) >= 3 and c[1] == name:
            return c[2]
    return None
