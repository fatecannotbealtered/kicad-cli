"""Commands implemented in this process, on `kicad_cli.fileformat`.

Each module here replaces a payload that ran inside KiCad's interpreter on
the SWIG bindings KiCad 11 removes. A port lands when its output is identical
to the payload's on every board KiCad ships -- the payload is the reference
until it is deleted -- so behaviour a port changes on purpose arrives in a
separate change that says so.
"""

from __future__ import annotations

from .. import envelope
from ..fileformat.board import Board, BoardError
from ..fileformat.sexpr import SexprError


def load_board(path: str) -> Board:
    """Read a board, or fail saying why it is not one.

    The payloads could not do this: pcbnew returned nothing for a file it could
    not open, and the payload died on the next line, so the caller saw E_IO and
    a Python traceback. A file that is not a board is bad input, and says what
    is wrong with it.
    """
    try:
        return Board.load(path)
    except (SexprError, BoardError, UnicodeDecodeError) as exc:
        envelope.fail(
            "E_VALIDATION",
            "not a KiCad board file",
            {"path": path, "reason": str(exc)[:300], "write_state": "not_started"},
        )
        raise AssertionError("unreachable") from exc  # envelope.fail exits
