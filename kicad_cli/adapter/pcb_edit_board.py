"""Shared pieces of the board edits: loading a board for a write, keys, nets and placement.

Only boards in KiCad 10's format are written (version 20250000 and later, where every item
names its net); an older board is read-only until KiCad 10 has opened and saved it once.
"""
from __future__ import annotations

import hashlib
import re
import uuid as uuidlib
from pathlib import Path

from . import base
from .fileformat.board import Board, BoardError, Footprint
from .fileformat.sexpr import List, SexprError

NM = 1_000_000
KICAD10 = 20250000


def load(path: Path) -> Board:
    """The board as saved, or E_VALIDATION when it is not one KiCad can open."""
    try:
        return Board.load(path)
    except (BoardError, SexprError, UnicodeDecodeError, OSError) as exc:
        raise base.invalid(f"{path.name} cannot be read as a KiCad board: {exc}.") from None


def load_for_write(path: Path) -> Board:
    """The board, which must be in KiCad 10's format, since kicad-cli writes only that."""
    board = load(path)
    if board.version < KICAD10:
        raise base.unsupported(f"{path.name} is in an older KiCad format (version {board.version}); open it in "
                               "KiCad 10 and save it once, then run the command again.")
    return board


def fp_key(path: Path, ref: str) -> str:
    return base.key(path, f"/footprints/{ref}")


def track_key(path: Path, identity: str) -> str:
    return base.key(path, f"/tracks/{identity}")


def via_key(path: Path, identity: str) -> str:
    return base.key(path, f"/vias/{identity}")


def pad_key(path: Path, ref: str, number: str) -> str:
    return base.key(path, f"/footprints/{ref}/pads/{number}")


def net_key(path: Path, name: str) -> str:
    return base.key(path, f"/nets/{name}")


def identity(node: List) -> str | None:
    found = node.find("uuid")
    return found.value(1) if found is not None else None


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derived_uuid(*parts: object) -> str:
    """A uuid that follows from its inputs, so a preview and its apply make the same one."""
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, "kicad-cli/" + "/".join(str(p) for p in parts)))


UUID = re.compile(r'\(uuid\s+"?([0-9a-fA-F-]{36})')


def fresh_uuid(board: Board, *parts: object) -> str:
    """``derived_uuid(*parts)``, or the first ``derived_uuid(*parts, n)`` the board does not hold yet:
    an item made the same way before and since moved in KiCad keeps its uuid."""
    taken = set(UUID.findall(board.document.source))
    out, n = derived_uuid(*parts), 0
    while out in taken:
        n += 1
        out = derived_uuid(*parts, n)
    return out


def footprint(board: Board, ref: str) -> Footprint:
    """The one footprint with a reference, or E_NOT_FOUND / E_AMBIGUOUS."""
    found = [fp for fp in board.footprints if fp.reference == ref]
    if not found:
        raise base.not_found(f"The board has no footprint {ref}.")
    if len(found) > 1:
        raise base.ambiguous(f"The board has {len(found)} footprints named {ref}; renumber them in KiCad first.",
                             [{"id": identity(fp.node) or "", "reference": ref, "x_mm": base.mm(fp.position[0]),
                               "y_mm": base.mm(fp.position[1])} for fp in found if fp.node is not None])
    return found[0]


def insert_before_tail(root: List, node: List) -> None:
    """Before the board's closing (embedded_fonts ...), where KiCad puts new items."""
    for index, item in enumerate(root.items):
        if isinstance(item, List) and item.head == "embedded_fonts":
            root.insert(index, node)
            return
    root.append(node)


def to_nm(value_mm: float) -> int:
    """Millimetres to the nearest nanometre, the file's resolution."""
    return int(round(float(value_mm) * NM))


def board_nets(board: Board) -> set[str]:
    names = {pad.net for fp in board.footprints for pad in fp.pads if pad.net}
    names |= {t.net for t in board.tracks if t.net} | {v.net for v in board.vias if v.net}
    names |= {z.net for z in board.zones if z.net}
    return names
