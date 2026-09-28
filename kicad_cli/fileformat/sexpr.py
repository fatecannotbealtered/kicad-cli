"""Lossless reading and writing of KiCad's S-expression files.

Every write this tool makes has to change what it was asked to change and
nothing else. `sch relink` learnt that the hard way: saving a KiCad 9 board
through a KiCad 10 `pcbnew` quietly migrated the whole file, which the
integrity check caught as a change nobody asked for. So a document parsed
here remembers where every list came from, and writing it back splices:

- a list nobody touched comes back byte for byte, whatever its formatting;
- a list that changed is written the way KiCad writes it, so the next save in
  KiCad produces no diff of its own.

How KiCad writes was measured on files it saved, not taken from its source
(which is GPL; this tool is MIT). Re-rendering every list of a file KiCad
9.99 or 10.0 saved reproduces it exactly -- all of them among its demo
projects, its 223 symbol libraries and the 3,416 footprints it saved itself:

- one tab per level of nesting, and KiCad's platform newline -- CRLF from
  KiCad on Windows -- which a document keeps from the file it came from;
- items follow the head on the opening line. The next atom is appended while
  the line so far, tabs counted as one character each, is shorter than 72
  characters, and starts a continuation line one level deeper otherwise;
- every sub-list starts a line of its own one level deeper -- except a run of
  ``(xy ...)``, packed while the line is shorter than 99 characters;
- an atom that follows a sub-list is appended to the line that sub-list ended
  on, under the same 72-character rule;
- a list that took more than one line closes on a line of its own at its own
  depth; one that did not closes where it stands;
- inside a string KiCad escapes backslash, double quote and newline, and
  nothing else: a tab is written as a tab.

Atoms are kept as their raw text: ``"F.Cu"`` with its quotes, ``0.508`` as
written. A board can hold millions of them, and a string per atom is the
cheapest faithful representation. `text()` decodes one; `quote()`, `symbol()`
and `number()` make new ones.

A board is mostly things a given command never reads -- zone fills, the
silkscreen lines of a 700-pin connector -- so nothing is parsed before it is
read. KiCad starts every sub-list of a multi-line list on a line of its own,
indented one level deeper, and a string cannot hide a line break because
KiCad escapes it. So the children of a list are found by searching for that
line start, without tokenising what is inside them, and each is parsed the
first time something reads it. A list whose layout is not KiCad's is parsed
whole, and one nobody read is written back as it came.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

Atom = str

# Whitespace is matched too, so the pieces tile the text and each one's offset
# is the running sum of the lengths before it. A lone '"' is matched only to
# be reported: without it an unterminated string would pass as whitespace.
_PIECES = re.compile(r'\s+|"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+|"', re.S)
_BARE = re.compile(r'[^\s()"]+')
_ESCAPED = re.compile(r"\\(.)", re.S)
_UNESCAPE = {"n": "\n", "r": "\r", "t": "\t"}

ATOM_WRAP = 72
XY_WRAP = 99
# Deferring a list's children costs a few checks per child, which only pays
# when the list is large: a track is parsed whole faster than it is split.
DEFER_ABOVE = 4096


class SexprError(ValueError):
    """The text is not a well-formed S-expression."""


class List:
    """One parenthesised expression.

    ``items`` holds atoms (raw strings) and nested lists in order; the first
    item is the head -- ``footprint``, ``pad``, ``xy`` -- when there is one.
    Change a list through its methods, never through ``items``: they are what
    mark it for re-rendering instead of copying.
    """

    __slots__ = ("_items", "start", "end", "parent", "_source", "_depth", "_changed", "_touched")

    def __init__(self, items: list | None = None) -> None:
        self._items: list | None = []
        self.start = -1  # offsets into the source text; -1 for a new list
        self.end = -1
        self.parent: List | None = None
        self._source: str | None = None  # kept until the list is parsed
        self._depth = -1
        self._changed = False  # its own items differ from the source
        self._touched = False  # it, or something below it, has changed
        for item in items or ():
            self._adopt(item)
            self._items.append(item)

    @classmethod
    def new(cls, head: str, *items: Atom | List) -> List:
        """A list that did not come from a file: ``List.new("at", "1", "2")``."""
        return cls([symbol(head), *items])

    @classmethod
    def _unparsed(cls, source: str, start: int, end: int, parent: List | None, depth: int):
        node = cls.__new__(cls)
        node._items = None
        node.start, node.end = start, end
        node.parent = parent
        node._source = source
        node._depth = depth
        node._changed = node._touched = False
        return node

    @property
    def items(self) -> list:
        if self._items is None:
            self._parse()
        return self._items

    def _parse(self) -> None:
        source = self._source
        items = _children_unparsed(source, self.start, self.end, self._depth, self)
        if items is None:
            items = _parse_list(source, self.start, self.end)._items
            for item in items:
                if isinstance(item, List):
                    item.parent = self
        self._items = items
        self._source = None

    # -- reading ------------------------------------------------------------

    @property
    def head(self) -> str | None:
        if self._items is None:
            # Straight from the text: asking what an item is must not cost
            # parsing it.
            m = _BARE.match(self._source, self.start + 1)
            return m.group(0) if m else None
        first = self._items[0] if self._items else None
        if isinstance(first, str) and not first.startswith('"'):
            return first
        return None

    def find(self, name: str) -> List | None:
        """The first direct child list whose head is ``name``."""
        for item in self.items:
            if isinstance(item, List) and item.head == name:
                return item
        return None

    def find_all(self, name: str) -> list[List]:
        return [i for i in self.items if isinstance(i, List) and i.head == name]

    def lists(self) -> list[List]:
        return [i for i in self.items if isinstance(i, List)]

    def walk(self, name: str | None = None) -> Iterator[List]:
        """This list and every list below it, in document order."""
        stack = [self]
        while stack:
            node = stack.pop()
            if name is None or node.head == name:
                yield node
            stack.extend(i for i in reversed(node.items) if isinstance(i, List))

    def atom(self, index: int = 1) -> Atom | None:
        """The raw atom at ``index``, or None if there is none there."""
        items = self.items
        if index < len(items) and isinstance(items[index], str):
            return items[index]
        return None

    def value(self, index: int = 1) -> str | None:
        """The decoded atom at ``index``: quotes and escapes removed."""
        raw = self.atom(index)
        return None if raw is None else text(raw)

    def values(self) -> list[str]:
        """Every atom after the head, decoded."""
        return [text(i) for i in self.items[1:] if isinstance(i, str)]

    # -- changing -----------------------------------------------------------

    def append(self, item: Atom | List) -> None:
        items = self.items
        self._adopt(item)
        items.append(item)
        self._mark()

    def insert(self, index: int, item: Atom | List) -> None:
        items = self.items
        self._adopt(item)
        items.insert(index, item)
        self._mark()

    def remove(self, item: List) -> None:
        """Remove a sub-list, found by identity.

        Atoms go by position, through `pop`: equal atoms can be one and the
        same string object -- CPython shares every one-character string -- so
        "this atom" is not something identity can say.
        """
        self.pop(self._index_of(item))

    def replace(self, old: List, new: Atom | List) -> None:
        """Put ``new`` where the sub-list ``old`` is. Atoms: use `set`."""
        self.set(self._index_of(old), new)

    def pop(self, index: int) -> Atom | List:
        item = self.items.pop(index)
        if isinstance(item, List):
            item.parent = None
        self._mark()
        return item

    def set(self, index: int, item: Atom | List) -> None:
        items = self.items
        old = items[index]
        if old is item:
            return
        self._adopt(item)  # first: a refusal must leave everything as it was
        if isinstance(old, List):
            old.parent = None
        items[index] = item
        self._mark()

    def _index_of(self, item: List) -> int:
        if not isinstance(item, List):
            raise TypeError("find an atom by position, not by value")
        for index, existing in enumerate(self.items):
            if existing is item:
                return index
        raise ValueError("not an item of this list")

    def _adopt(self, item: Atom | List) -> None:
        if isinstance(item, List):
            if item.parent is not None:
                raise ValueError("list already belongs to another list; remove it first")
            item.parent = self
        elif not isinstance(item, str) or not item:
            raise TypeError("an item is a non-empty atom string or a List")

    def _mark(self) -> None:
        self._changed = True
        node: List | None = self
        while node is not None and not node._touched:
            node._touched = True
            node = node.parent

    def __repr__(self) -> str:
        return f"List({self.head or '?'})"


class Document:
    """A parsed file: the source text, and the tree that came from it."""

    __slots__ = ("source", "root", "lead", "trail", "newline")

    def __init__(self, source: str, root: List, lead: str, trail: str) -> None:
        self.source = source
        self.root = root
        self.lead = lead  # anything before the root: a byte-order mark, say
        self.trail = trail  # anything after it: normally one line break
        self.newline = "\r\n" if "\r\n" in source[: 1 << 16] else "\n"

    @classmethod
    def parse(cls, source: str, lazy: bool = True) -> Document:
        start, end = _root_span(source)
        if lazy:
            root = List._unparsed(source, start, end, None, 0)
        else:
            root = _parse_list(source, start, end)
        return cls(source, root, source[:start], source[end:])

    @classmethod
    def load(cls, path: str | Path, lazy: bool = True) -> Document:
        # Bytes, not text mode: universal-newline decoding would rewrite a
        # CRLF file on the way in, and every offset after it would be wrong.
        return cls.parse(Path(path).read_bytes().decode("utf-8"), lazy=lazy)

    def span(self, node: List) -> tuple[int, int] | None:
        """Where an untouched list lies in the source, or None once anything in
        it has changed. For readers that can take what they need straight from
        the text -- a run of coordinates, whether a word occurs -- without
        building a list for every item in it."""
        if node.start < 0 or node._changed or node._touched:
            return None
        return node.start, node.end

    def dumps(self) -> str:
        # Re-rendered text takes the file's newline as it is written; copied
        # text keeps whatever it had, down to a stray LF in a CRLF file.
        return self.lead + _emit(self.root, 0, self.source, self.newline) + self.trail

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.dumps().encode("utf-8"))


def render(node: List, newline: str = "\n") -> str:
    """A list built in memory, written out as KiCad writes a file of it."""
    return _render(node, 0, "", newline) + newline


# -- atoms --------------------------------------------------------------------


def text(atom: Atom) -> str:
    """Decode an atom: a quoted string loses its quotes and escapes."""
    if not atom.startswith('"'):
        return atom
    inner = atom[1:-1]
    if "\\" not in inner:
        return inner
    return _ESCAPED.sub(lambda m: _UNESCAPE.get(m.group(1), m.group(1)), inner)


def quote(value: str) -> Atom:
    """A string atom, escaped exactly as KiCad escapes one."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def symbol(token: str) -> Atom:
    """A bare atom -- a keyword such as ``smd`` or ``yes``, never user text."""
    if not _BARE.fullmatch(token):
        raise ValueError(f"{token!r} cannot be written as a bare atom; quote it")
    return token


def number(value: float, places: int = 6) -> Atom:
    """A number as KiCad writes one: fixed point, no trailing zeros, no -0.

    Board and footprint files carry six decimal places (one nanometre);
    schematic and symbol files carry four.
    """
    formatted = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return "0" if formatted in ("", "-0") else formatted


# -- parsing -----------------------------------------------------------------


def _root_span(source: str) -> tuple[int, int]:
    start = source.find("(")
    end = source.rfind(")")
    if start < 0 or end < start:
        raise SexprError("no expression in the text")
    if source[:start].strip("\ufeff \t\r\n") or source[end + 1 :].strip():
        raise SexprError("text outside the top-level expression")
    return start, end + 1


def _parse_list(source: str, start: int, end: int) -> List:
    """Parse ``source[start:end]`` whole; it must be exactly one list."""
    stack: list[List] = []
    root: List | None = None
    pos = start
    for piece in _PIECES.findall(source, start, end):
        first = piece[0]
        if first == "(":
            node = List.__new__(List)
            node._items = []
            node.start, node.end = pos, -1
            node._source = None
            node._depth = -1
            node._changed = node._touched = False
            if stack:
                node.parent = stack[-1]
                stack[-1]._items.append(node)
            elif root is None:
                node.parent = None
                root = node
            else:
                raise SexprError(f"a second expression at offset {pos}")
            stack.append(node)
        elif first == ")":
            if not stack:
                raise SexprError(f"unbalanced ')' at offset {pos}")
            stack.pop().end = pos + 1
        elif first.isspace():
            pass
        elif piece == '"':
            # The string pattern failed from here, so nothing closes it.
            raise SexprError(f"unterminated string at offset {pos}")
        elif stack:
            stack[-1]._items.append(piece)
        else:
            raise SexprError(f"an atom outside any expression at offset {pos}")
        pos += len(piece)
    if stack:
        raise SexprError(f"unbalanced '(' at offset {stack[-1].start}")
    if root is None or root.start != start or root.end != end:
        raise SexprError(f"not exactly one expression between offsets {start} and {end}")
    return root


_LINE_STARTS: dict[int, str] = {}


def _children_unparsed(source: str, start: int, end: int, depth: int, parent: List):
    """The items of the list at ``source[start:end]``, its sub-lists unparsed.

    None when the list is not laid out the way KiCad lays one out -- on one
    line, a sub-list sharing a line, anything between two sub-lists -- and
    the caller parses it whole instead.
    """
    if depth < 0 or end - start < DEFER_ABOVE or source.find("\n", start, end) < 0:
        return None
    needle = _LINE_STARTS.get(depth)
    if needle is None:
        needle = _LINE_STARTS[depth] = "\n" + "\t" * (depth + 1) + "("
    offset = len(needle) - 1
    starts = []
    at = source.find(needle, start, end)
    while at >= 0:
        starts.append(at + offset)
        at = source.find(needle, at + offset, end)
    if not starts:
        return None  # atoms only, over several lines

    items: list = []
    for piece in _PIECES.findall(source, start + 1, starts[0]):
        if piece[0] in '()"' and (piece[0] != '"' or len(piece) == 1):
            return None  # a sub-list on the opening line
        if not piece[0].isspace():
            items.append(piece)
    close = end - 1
    closing = "\t" * (depth + 1) + ")"
    for index, begin in enumerate(starts):
        limit = starts[index + 1] if index + 1 < len(starts) else close
        child_end = source.rfind(")", begin, limit) + 1
        gap = source[child_end:limit]
        if child_end <= begin or (gap and not gap.isspace()):
            return None  # an atom after a sub-list
        # The last ')' before the next line proves nothing on its own: a line
        # of packed (xy ...) ends in one too. A child must open alone on its
        # line, and a child of several lines must close alone on its last.
        # Only the first line is tokenised, so a child's own contents are
        # still not read.
        first_break = source.find("\n", begin, child_end)
        opens = 0
        for piece in _PIECES.findall(source, begin, child_end if first_break < 0 else first_break):
            if piece == "(":
                opens += 1
            elif piece == '"':
                return None
        if opens != 1:
            return None
        if first_break >= 0 and not (
            source.endswith(closing, begin, child_end)
            and source[child_end - len(closing) - 1] == "\n"
        ):
            return None
        # And a child must be whole. KiCad 9.0 wrote some footprints' pads a
        # tab too shallow, so a pad can look like a sibling of its own
        # footprint, and the footprint like a list that ends early. Only the
        # count says otherwise. A ')' inside a string can unbalance it too;
        # that costs a whole parse, never a wrong one.
        if source.count("(", begin, child_end) != source.count(")", begin, child_end):
            return None
        items.append(List._unparsed(source, begin, child_end, parent, depth + 1))
    return items


# -- writing -----------------------------------------------------------------


def _emit(node: List, depth: int, source: str, newline: str = "\n") -> str:
    if node.start < 0 or node._changed:
        return _render(node, depth, source, newline)
    if not node._touched:
        return source[node.start : node.end]
    # Unchanged itself, changed below: keep its own text and splice.
    out = []
    pos = node.start
    for item in node.items:
        if isinstance(item, List) and (item._touched or item._changed):
            out.append(source[pos : item.start])
            out.append(_emit(item, depth + 1, source, newline))
            pos = item.end
    out.append(source[pos : node.end])
    return "".join(out)


def _is_xy(item: Atom | List) -> bool:
    return (
        isinstance(item, List) and item.head == "xy" and all(isinstance(i, str) for i in item.items)
    )


def _render(node: List, depth: int, source: str, newline: str = "\n") -> str:
    inner = newline + "\t" * (depth + 1)
    width = depth + 1  # of the indentation that starts each inner line
    out = ["("]
    line = depth + 1  # length of the current line so far, tabs included
    multiline = False
    after_xy = False
    for index, item in enumerate(node.items):
        if isinstance(item, str):
            if index == 0:
                out.append(item)
                line += len(item)
            elif line < ATOM_WRAP:
                out.append(" " + item)
                line += 1 + len(item)
            else:
                out.append(inner + item)
                line = width + len(item)
                multiline = True
            after_xy = False
            continue
        rendered = _emit(item, depth + 1, source, newline)
        xy = _is_xy(item)
        if xy and after_xy and line < XY_WRAP:
            out.append(" " + rendered)
            line += 1 + len(rendered)
        else:
            out.append(inner + rendered)
            tail = rendered.rfind("\n")
            line = width + len(rendered) if tail < 0 else len(rendered) - tail - 1
            multiline = True
        after_xy = xy
    if multiline:
        out.append(newline + "\t" * depth + ")")
    else:
        out.append(")")
    return "".join(out)


def mark_all(node: List) -> None:
    """Treat every list below ``node`` as changed, so all of it is re-rendered.

    For measuring the renderer against files KiCad wrote: a document whose
    every list is re-rendered must come back identical to the one KiCad saved.
    """
    for each in node.walk():
        each._changed = True
        each._touched = True
