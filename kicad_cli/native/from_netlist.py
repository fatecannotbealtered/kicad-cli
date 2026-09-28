"""``board from-netlist``, in this process: a board from a netlist.

A port of `payload/board_build.py`, which ran on pcbnew. KiCad's "Update PCB
from Schematic" has no headless entry point; what it does is load each part's
footprint, place it, and join its pads into nets, and a footprint is a file
this tool reads like any other. `kicad_cli/boardgen.py` plans the board
without KiCad, as before; this builds it.

What pcbnew reads from the board is what it read from the pcbnew version's
-- every footprint, pad and net (`tests/test_native_from_netlist.py`) -- with
two differences, both on purpose:

- The outline is fitted to the parts' copper, drawings and courtyards. The
  pcbnew version fitted it to their text as well, which takes the font's
  measurements; this outline is provisional anyway -- `board place` moves
  the parts and it no longer fits either way.
- Every uuid is derived from the netlist and the part it belongs to, so the
  same netlist gives the same board, byte for byte. pcbnew drew them at
  random, and the same netlist gave a different file every time.

The board comes with the project files pcbnew's Save wrote beside it --
`NAME.kicad_pro`, where the design rules and net classes live, and
`NAME.kicad_prl` -- the same bytes, but only where there are none yet. pcbnew
wrote both every time, over whatever was there, and a project the schematic
already had came back as KiCad's defaults, its net classes gone.
"""

from __future__ import annotations

import hashlib
import os
import uuid as uuidlib
from pathlib import Path
from typing import Any

from .. import envelope
from ..fileformat import new_project
from ..fileformat.board import Board
from ..fileformat.new_board import EMPTY
from ..fileformat.sexpr import Document, List, copy, number, quote, symbol
from . import write

NM = 1_000_000
# Between the outline and the outermost part: enough that DRC does not
# complain of the edge, not so much that board is wasted.
EDGE_MARGIN_MM = 2.0
OUTLINE_WIDTH = "0.1"
# Items that carry a uuid of their own in a board's footprint.
WITH_UUID = {
    "property", "fp_text", "fp_text_box", "fp_line", "fp_arc", "fp_circle", "fp_rect",
    "fp_poly", "fp_curve", "pad", "zone", "dimension", "group", "image",
}  # fmt: skip
DROPPED = {"version", "generator", "generator_version"}


def mm(value: float) -> float:
    return value / NM


def from_mm(value: float) -> int:
    """As pcbnew's FromMM: truncated to the nanometre (see native/move.py)."""
    return int(float(value) * float(NM))


class _Ids:
    """Uuids that follow from what the board is made of, not from chance."""

    def __init__(self, seed: str) -> None:
        self.namespace = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "kicad-cli/board/" + seed)

    def __call__(self, *parts: object) -> str:
        return quote(str(uuidlib.uuid5(self.namespace, "/".join(str(p) for p in parts))))


def run(plan: dict[str, Any], out: str, netlist: str, confirm: str | None) -> None:
    write.start()
    preview = {
        "out": out,
        "components": len(plan["components"]),
        "nets": len(plan["nets"]),
        "placement": plan.get("placement", "grid"),
        "will": "新建一块板：按计划摆放全部封装、建立网络、画矩形板框",
    }
    # Bound to the netlist, not to the plan: the board does not exist yet, and
    # the plan is a new temporary file on every call. The netlist is the real
    # input; if it changes, the plan is stale, which is what the token says.
    envelope.check_confirm(confirm, "board from-netlist:" + os.path.basename(out), preview, netlist)

    document = Document.parse(EMPTY)
    root = document.root
    ids = _Ids(hashlib.sha256(Path(netlist).read_bytes()).hexdigest())
    footprints: dict[str, List] = {}
    placed = []
    for item in plan["components"]:
        node = _footprint(item, ids)
        _insert_before_tail(root, node)
        footprints[item["ref"]] = node
        placed.append(item["ref"])

    joined, missing = _wire(footprints, plan["nets"])
    size = _outline(document, ids)

    write.begin(out)
    Path(out).write_bytes(document.dumps().encode("utf-8"))
    project = _project_files(Path(out))
    write.done(
        {
            "board": out,
            "project_files": project,
            "footprints": len(placed),
            "nets": len(plan["nets"]),
            "pads_connected": joined,
            "unmatched_nodes": missing,
            "board_mm": size,
            "placement": plan.get("placement", "grid"),
            "note": "布局是按参考编号排的网格，不是按电路关系摆的——先跑 board place "
            "按连接关系重排（实测走线铜长可减一半），再跑 board route 布线，"
            "之后按 verify.width_regressed 决定要不要再 widen",
        }
    )


def _project_files(board: Path) -> dict[str, list[str]]:
    """`NAME.kicad_pro` and `NAME.kicad_prl` beside the board, where missing.

    A file already there is the project's -- a schematic's net classes and
    ERC settings live in its `.kicad_pro` -- and stays as it is. A file made
    here is part of the write: a failure removes it with the board.
    """
    created, kept = [], []
    for path, text in (
        (board.with_suffix(".kicad_pro"), new_project.project(board.stem)),
        (board.with_suffix(".kicad_prl"), new_project.local(board.stem)),
    ):
        if path.exists():
            kept.append(str(path))
            continue
        write.begin(path)
        path.write_bytes(text.encode("utf-8"))
        created.append(str(path))
    return {"created": created, "kept": kept}


def _insert_before_tail(root: List, node: List) -> None:
    """Before the board's closing (embedded_fonts no), where KiCad puts items."""
    for index, item in enumerate(root.items):
        if isinstance(item, List) and item.head == "embedded_fonts":
            root.insert(index, node)
            return
    root.append(node)


def _footprint(item: dict[str, Any], ids: _Ids) -> List:
    """A library footprint as a placed one: named, positioned, given uuids.

    Named by the file's own name, as the pcbnew version left it -- without
    its library, which DEVELOPMENT_STATUS.md lists to be fixed.
    """
    try:
        source = Document.load(item["file"]).root
    except (OSError, ValueError) as exc:
        # The plan checked the file exists; one that exists and does not read
        # is damaged, unreadable or from another version -- not a typo.
        envelope.fail(
            "E_IO",
            "封装文件存在但 KiCad 读不了",
            {"ref": item["ref"], "file": item["file"], "reason": str(exc)[:200]},
        )
        raise AssertionError("unreachable") from exc
    ref = item["ref"]
    node = List.new("footprint", source.items[1])
    children = [c for c in source.items[2:] if not (isinstance(c, List) and c.head in DROPPED)]
    have = {c.value(1) for c in children if isinstance(c, List) and c.head == "property"}
    at = List.new("at", number(mm(from_mm(item["x"]))), number(mm(from_mm(item["y"]))))
    counters: dict[str, int] = {}
    for child in children:
        if not isinstance(child, List):
            node.append(child)
            continue
        placed = copy(child)
        head = placed.head
        if head == "property":
            name = placed.value(1)
            if name == "Reference":
                placed.set(2, quote(ref))
            elif name == "Value" and item.get("value"):
                placed.set(2, quote(str(item["value"])))
        if head in WITH_UUID:
            counters[head] = counters.get(head, 0) + 1
            _set_uuid(placed, ids(ref, head, counters[head]))
        node.append(placed)
        if head == "layer":
            node.append(List.new("uuid", ids(ref)))
            node.append(at)
        if head == "property" and placed.value(1) == "Value":
            for missing in ("Datasheet", "Description"):
                if missing not in have:
                    node.append(_empty_property(missing, ids(ref, "property", missing)))
    return node


def _set_uuid(node: List, value: str) -> None:
    """Give an item its uuid: replace one it has, or add one where KiCad puts
    it -- before a property's effects, last on anything else."""
    for index, item in enumerate(node.items):
        if isinstance(item, List) and item.head == "uuid":
            node.set(index, List.new("uuid", value))
            return
    if node.head == "property":
        for index, item in enumerate(node.items):
            if isinstance(item, List) and item.head == "effects":
                node.insert(index, List.new("uuid", value))
                return
    node.append(List.new("uuid", value))


def _empty_property(name: str, uuid: str) -> List:
    """A mandatory field the library footprint does not have, as pcbnew adds it."""
    return List.new(
        "property",
        quote(name),
        quote(""),
        List.new("at", "0", "0", "0"),
        List.new("layer", quote("F.Fab")),
        List.new("hide", symbol("yes")),
        List.new("uuid", uuid),
        List.new("effects", List.new("font", List.new("size", "1.27", "1.27"))),
    )


def _wire(footprints: dict[str, List], nets: list[dict[str, Any]]) -> tuple[int, list]:
    """Join each net's pads. A node naming a pad the footprint lacks is
    reported -- usually a symbol and footprint that number pins differently --
    rather than skipped: skipping it delivers a board missing a connection."""
    # By number, the last pad of a number winning, as the pcbnew version had
    # it: a footprint with several pads of one number -- a crystal's case, a
    # shield -- gets the net on one of them. KiCad's own update puts it on
    # all of them; DEVELOPMENT_STATUS.md lists this to be fixed.
    pads: dict[str, dict[str, List]] = {}
    for ref, node in footprints.items():
        pads[ref] = {pad.value(1) or "": pad for pad in node.find_all("pad")}
    joined, missing = 0, []
    for net in nets:
        for member in net["nodes"]:
            pad = pads.get(member["ref"], {}).get(member["pin"])
            if pad is None:
                missing.append({"net": net["name"], "node": member["ref"] + "." + member["pin"]})
                continue
            _set_net(pad, net["name"])
            joined += 1
    return joined, missing


def _set_net(pad: List, name: str) -> None:
    """(net "NAME") on a pad, where KiCad 10 writes it: before its uuid."""
    entry = List.new("net", quote(name))
    for index, item in enumerate(pad.items):
        if isinstance(item, List) and item.head == "net":
            pad.set(index, entry)
            return
    for index, item in enumerate(pad.items):
        if isinstance(item, List) and item.head == "uuid":
            pad.insert(index, entry)
            return
    pad.append(entry)


def _outline(document: Document, ids: _Ids) -> list:
    """A rectangle on Edge.Cuts around the parts, a margin out: around each
    footprint's pads and drawings, courtyard included, text left out."""
    boxes = [box for fp in Board(document).footprints if (box := fp.bbox()) is not None]
    if not boxes:
        return [0.0, 0.0]
    margin = from_mm(EDGE_MARGIN_MM)
    left = min(b[0] for b in boxes) - margin
    top = min(b[1] for b in boxes) - margin
    right = max(b[2] for b in boxes) + margin
    bottom = max(b[3] for b in boxes) + margin
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    for index in range(4):
        (x1, y1), (x2, y2) = corners[index], corners[(index + 1) % 4]
        line = List.new(
            "gr_line",
            List.new("start", number(mm(x1)), number(mm(y1))),
            List.new("end", number(mm(x2)), number(mm(y2))),
            List.new("stroke", List.new("width", OUTLINE_WIDTH), List.new("type", "default")),
            List.new("layer", quote("Edge.Cuts")),
            List.new("uuid", ids("outline", index)),
        )
        _insert_before_tail(document.root, line)
    return [round(mm(right - left), 2), round(mm(bottom - top), 2)]
