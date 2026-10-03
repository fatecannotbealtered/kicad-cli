"""``board pour``, in this process: pour copper for one net over a layer, and
fill it.

A port of `payload/pour.py`, which ran on pcbnew; what it reports is the
payload's, field for field, but for the filled area, which this tool's zone
filler (`fileformat/zonefill.py`) makes a tenth of a percent different from
pcbnew's (`tests/test_native_pour.py`).

`board stitch` joins the islands of a pour and `board plane` audits what is
under each track; both assume a pour exists and neither can make one. This
makes it: a zone over the whole board but a margin from its edge, on one
layer, for one net -- then, as pcbnew did, every zone on the board filled
again and the board saved.
"""

from __future__ import annotations

import math
import uuid as uuidlib

from .. import envelope
from ..fileformat import polygons, zonefill
from ..fileformat.board import Board
from ..fileformat.drc.settings import Settings
from ..fileformat.sexpr import List, number, quote, symbol
from . import load_board, write

NM = 1_000_000
# How far the pour keeps from the board's edge: copper at the edge is cut
# into when the board is milled, and bare copper on the edge is the result.
DEFAULT_MARGIN_MM = 0.5
# The pour's own clearance and its thinnest copper: thinner than that does
# not etch.
DEFAULT_CLEARANCE_MM = 0.3
DEFAULT_MIN_WIDTH_MM = 0.25
# Thermal relief: a pad joined solid to a whole plane cannot be soldered by
# hand, the plane takes the heat; spokes carry current and keep the heat in.
THERMAL_GAP_MM = 0.5
THERMAL_SPOKE_MM = 0.5
NOTE = (
    "敷铜已灌。接着跑 board plane 看参考平面覆盖到了多少（这正是它要回答的），"
    "再跑 board stitch 用过孔把同网络的孤岛连起来——灌出来的铜被走线切开之后，"
    "看着连成一片的其实不是一片。DRC 不检查参考平面，所以 ok_to_fabricate 为真"
    "也不代表这块板的回流路径是好的。"
)


def from_mm(value: float) -> int:
    """Millimetres to nanometres as pcbnew's FromMM: rounded, halves away
    from nought."""
    scaled = value * NM
    return int(math.floor(scaled + 0.5)) if scaled >= 0 else -int(math.floor(-scaled + 0.5))


def layer_named(board: Board, name: str):
    """The board's layer by its standard name or the name someone gave it."""
    for layer in board.layers:
        if name in (layer.name, layer.user_name):
            return layer
    return None


def nets_of(board: Board) -> set[str]:
    names = {pad.net for fp in board.footprints for pad in fp.pads}
    names |= {t.net for t in board.tracks} | {v.net for v in board.vias}
    names |= {z.net for z in board.zones}
    names |= set(board._net_table().values())
    return names


def board_rect(board: Board, margin_mm: float) -> tuple[int, int, int, int]:
    """The box round the board's outline, brought in by the margin."""
    box = board.edge_bbox()
    if box is None or box[2] == box[0] or box[3] == box[1]:
        envelope.fail(
            "E_VALIDATION",
            "板上没有 Edge.Cuts 板框，敷铜没有边界",
            {"hint": "board from-netlist 会画一圈板框；手工板请先在 KiCad 里画 Edge.Cuts"},
        )
    margin = from_mm(margin_mm)
    left, top = box[0] + margin, box[1] + margin
    right, bottom = box[2] - margin, box[3] - margin
    if right <= left or bottom <= top:
        envelope.fail(
            "E_VALIDATION",
            "板框收掉 margin 之后不剩面积",
            {
                "margin_mm": margin_mm,
                "board_mm": [(box[2] - box[0]) / NM, (box[3] - box[1]) / NM],
            },
        )
    return left, top, right, bottom


def zone_node(net: str, layer: str, rect, options: dict) -> List:
    """A zone as pcbnew writes the one it made: over `rect`, its own
    clearance, minimum thickness and pad connection."""
    left, top, right, bottom = rect
    connect = List.new("connect_pads", List.new("clearance", number(options["clearance"])))
    if options["connect"] == "solid":
        connect.insert(1, symbol("yes"))
    corners = ((left, top), (right, top), (right, bottom), (left, bottom))
    return List.new(
        "zone",
        List.new("net", quote(net)),
        List.new("layer", quote(layer)),
        List.new("uuid", quote(str(uuidlib.uuid4()))),
        List.new("name", quote(f"{net} pour")),
        List.new("hatch", symbol("edge"), number(0.5)),
        connect,
        List.new("min_thickness", number(options["min_width"])),
        List.new(
            "fill",
            symbol("yes"),
            List.new("thermal_gap", number(THERMAL_GAP_MM)),
            List.new("thermal_bridge_width", number(THERMAL_SPOKE_MM)),
            List.new("island_removal_mode", "0"),
        ),
        List.new(
            "polygon",
            List.new("pts", *[List.new("xy", number(x / NM), number(y / NM)) for x, y in corners]),
        ),
    )


def run(path: str, args: dict) -> None:
    write.start()
    board = load_board(path)
    net_name = str(args["net"]).strip()
    layer_name = str(args["layer"]).strip()
    options = {
        "margin": float(args.get("margin") or DEFAULT_MARGIN_MM),
        "clearance": float(args.get("clearance") or DEFAULT_CLEARANCE_MM),
        "min_width": float(args.get("min-width") or DEFAULT_MIN_WIDTH_MM),
        "connect": str(args.get("connect") or "thermal").lower(),
    }
    if options["connect"] not in ("thermal", "solid"):
        envelope.fail("E_USAGE", "--connect 只能是 thermal 或 solid", {"got": options["connect"]})

    layer = layer_named(board, layer_name)
    if layer is None:
        envelope.fail(
            "E_NOT_FOUND",
            "板上没有这一层",
            {"layer": layer_name, "hint": "铜层名形如 F.Cu / B.Cu / In1.Cu"},
        )
    if not layer.name.endswith(".Cu"):
        envelope.fail("E_VALIDATION", "只能往铜层上敷铜", {"layer": layer_name})
    if net_name not in nets_of(board):
        envelope.fail(
            "E_NOT_FOUND",
            "板上没有这个网络",
            {
                "net": net_name,
                "hint": "网络名区分大小写；board audit 的 width_by_net 列出板上的网络",
            },
        )
    for zone in board.zones:
        if zone.net == net_name and zone.layers and zone.layers[0] == layer.name:
            envelope.fail(
                "E_CONFLICT",
                "这一层上这个网络已经有敷铜区了",
                {
                    "net": net_name,
                    "layer": layer_name,
                    "hint": "本命令只负责从无到有。要改参数请在 KiCad 里改，或先删掉原来的",
                },
            )

    rect = board_rect(board, options["margin"])
    width_mm = round((rect[2] - rect[0]) / NM, 2)
    height_mm = round((rect[3] - rect[1]) / NM, 2)
    preview = {
        "board": path,
        "net": net_name,
        "layer": layer_name,
        "area_mm": [width_mm, height_mm],
        "connect": options["connect"],
        "clearance_mm": options["clearance"],
        "will": f"在 {layer_name} 上给 {net_name} 敷一块 {width_mm}x{height_mm} mm 的铜并灌满",
    }
    envelope.check_confirm(args.get("confirm"), f"pour:{net_name}:{layer_name}", preview, path)

    tracks_before = len(board.tracks) + len(board.vias)
    node = zone_node(net_name, layer.name, rect, options)
    _insert_zone(board, node)
    board = Board(board.document, board.path)  # read again, the new zone with the rest

    # As pcbnew did: every zone on the board filled again, then saved.
    filler = zonefill.Filler(board, Settings.of(board.path.with_suffix(".kicad_pro")))
    ours = None
    for zone in board.zones:
        if zone.rule_area:
            continue
        fill = filler.fill(zone)
        zonefill.write(fill)
        if zone.node is node:
            ours = fill
    write.save(board)

    filled = polygons.total_area(ours.layers.get(layer.name, [])) if ours is not None else 0.0
    write.done(
        {
            "board": path,
            "net": net_name,
            "layer": layer_name,
            "area_mm": [width_mm, height_mm],
            "filled_mm2": round(filled / NM / NM, 1),
            "outlines": 1,
            "connect": options["connect"],
            "clearance_mm": options["clearance"],
            "tracks": tracks_before,
            "note": NOTE,
        }
    )


def _insert_zone(board: Board, node: List) -> None:
    """Put the zone where pcbnew puts a new one: before the zones the board
    has, or after its tracks and vias where it has none."""
    root = board.root
    items = root.items
    heads = [getattr(item, "head", None) for item in items]
    if "zone" in heads:
        root.insert(heads.index("zone"), node)
        return
    after = max((i for i, h in enumerate(heads)
                 if h in ("segment", "arc", "via", "footprint", "gr_line", "gr_arc", "gr_circle",
                          "gr_rect", "gr_poly", "gr_text")), default=None)  # fmt: skip
    if after is None:
        root.append(node)
    else:
        root.insert(after + 1, node)
