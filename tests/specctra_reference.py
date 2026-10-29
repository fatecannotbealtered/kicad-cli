"""pcbnew's Specctra DSN and session import as the reference for this tool's.

A DSN is compared by what Freerouting reads from it: the layers, the board's
outline, its planes, keep-outs, vias and rules; each part's place and the
pins, padstacks and keep-outs of its image; the nets with their pins and the
classes; the wiring. Not compared: the lines of an image's outline, which
Freerouting draws and never routes against; the order pcbnew lists its
classes in, which is a hash table's; the names of custom pads' padstacks,
which carry a hash of pcbnew's; and corners a few nanometres apart, which
pcbnew sometimes lists twice. Numbers are compared to within a micrometre --
six significant digits leave no finer on a coordinate past 100 mm.

Test scaffolding, deleted with the payloads.
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

from payload_reference import kicad_python

from kicad_cli.fileformat import specctra
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.drc.settings import Settings

TOL = 1.01  # um
# Corners nearer than this are one corner: pcbnew lists both ends of a side
# of no length, and six digits can print two such ends 10 nm apart.
NEAR = 0.0151  # um
HERE = Path(__file__).resolve().parent


def pcbnew(*args: str, timeout: int = 900) -> int:
    proc = subprocess.run(
        [kicad_python(), str(HERE / "pcbnew_specctra.py"), *args],
        capture_output=True,
        timeout=timeout,
    )
    return proc.returncode


# -- reading a DSN -----------------------------------------------------------------


def _tokens(text: str):
    text = re.sub(r"\(parser.*?\n  \)\n", "", text, flags=re.S)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = text.index('"', i + 1)
            yield ("Q", text[i + 1 : j])
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield text[i:j]
            i = j


def _parse(text: str) -> list:
    stack: list[list] = [[]]
    for t in _tokens(text):
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t[1] if isinstance(t, tuple) else t)
    return stack[0][0]


def _find(node, head):
    return [x for x in node if isinstance(x, list) and x and x[0] == head]


def _one(node, head):
    found = _find(node, head) if node is not None else []
    return found[0] if found else None


def _points(seq):
    vals = [float(v) for v in seq]
    return list(zip(vals[0::2], vals[1::2], strict=False))


class Ring(list):
    """A closed ring of corners: the same ring whichever corner it starts at."""


def _ring(pts):
    """A ring, corners a few nanometres apart taken as one."""
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    pts = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > NEAR]
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) <= NEAR:
        pts = pts[:-1]
    return Ring(pts)


def _flat(seq):
    for item in seq:
        if isinstance(item, (list, tuple)):
            yield from _flat(item)
        else:
            yield item


def _shape(shape):
    kind, layer = shape[0], shape[1]
    if kind == "polygon":
        return (kind, layer, shape[2], _ring(_points(shape[3:])))
    if kind == "path":
        return (kind, layer, shape[2], _points(shape[3:]))
    return (kind, layer, [float(v) for v in shape[2:]])


def model(text: str) -> dict:
    root = _parse(text)
    m: dict = {}
    st = _one(root, "structure")
    m["layers"] = [(x[1], _one(x, "type")[1]) for x in _find(st, "layer")]
    m["boundary"] = [_ring(_points(p[3:])) for b in _find(st, "boundary") for p in _find(b, "path")]
    m["planes"] = sorted(
        ((p[1], p[2][1], _ring(_points(p[2][3:]))) for p in _find(st, "plane")),
        key=lambda x: (x[0], x[1], sorted((round(px), round(py)) for px, py in x[2])),
    )
    m["keepouts"] = sorted(
        (
            (
                k[2][0],
                k[2][1],
                _ring(_points(k[2][3:])) if k[2][0] == "polygon" else [float(v) for v in k[2][2:]],
            )
            for k in _find(st, "keepout")
        ),
        key=lambda x: (x[0], x[1], str(sorted(round(v) for v in _flat(x[2])))),
    )
    vias = _one(st, "via")
    m["via list"] = (vias[1], sorted(vias[1:])) if vias else None
    m["rule"] = _one(st, "rule")
    lib = _one(root, "library")
    stacks = {p[1]: [_shape(s[1]) for s in _find(p, "shape")] for p in _find(lib, "padstack")}
    images = {}
    for image in _find(lib, "image"):
        pins = []
        for pin in _find(image, "pin"):
            rest = pin[2:]
            turn = 0.0
            if isinstance(rest[0], list):
                turn = float(rest[0][1])
                rest = rest[1:]
            pins.append((stacks.get(pin[1]), turn, rest[0], float(rest[1]), float(rest[2])))
        keep = [(k[2][0], k[2][1], [float(v) for v in k[2][2:]]) for k in _find(image, "keepout")]
        images[image[1]] = (pins, keep)
    m["parts"] = {}
    for component in _find(_one(root, "placement"), "component"):
        for place in _find(component, "place"):
            pn = _one(place, "PN")
            pins, keep = images.get(component[1], ([], []))
            m["parts"][place[1]] = {
                "image": component[1].split("::")[0],
                "at": (float(place[2]), float(place[3]), place[4], float(place[5])),
                "PN": pn[1] if pn else None,
                "pins": pins,
                "keepouts": keep,
            }
    net = _one(root, "network")
    m["nets"] = [
        (n[1], [p for pins in _find(n, "pins") for p in pins[1:]]) for n in _find(net, "net")
    ]
    m["classes"] = {}
    for c in _find(net, "class"):
        names = [x for x in c[2:] if not isinstance(x, list)]
        circuit = _one(c, "circuit")
        use = _one(circuit, "use_via") if circuit else None
        m["classes"][c[1]] = (sorted(names), stacks.get(use[1]) if use else None, _one(c, "rule"))
    wiring = _one(root, "wiring")
    m["wires"] = [
        (w[1][1], float(w[1][2]), _points(w[1][3:]), _one(w, "net")[1], _one(w, "type")[1])
        for w in _find(wiring, "wire")
    ]
    m["vias"] = [
        (stacks.get(v[1]), float(v[2]), float(v[3]), _one(v, "net")[1], _one(v, "type")[1])
        for v in _find(wiring, "via")
    ]
    return m


def close(a, b, tol: float = TOL) -> bool:
    if isinstance(a, Ring) and isinstance(b, Ring):
        if len(a) != len(b):
            return False
        return any(
            all(close(x, b[(k + j) % len(b)], tol) for j, x in enumerate(a))
            for k in range(len(b) or 1)
        )
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b, strict=True))
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


def differences(theirs: str, ours: str, show: int = 4) -> list[str]:
    """What Freerouting would read differently in our DSN than in pcbnew's."""
    a, b = model(theirs), model(ours)
    out = []
    for key in (
        "layers",
        "boundary",
        "planes",
        "keepouts",
        "via list",
        "rule",
        "nets",
        "wires",
        "vias",
    ):
        if not close(a[key], b[key]):
            out.append(f"{key}: pcbnew {str(a[key])[:200]} / ours {str(b[key])[:200]}")
    for ref in sorted(a["parts"].keys() | b["parts"].keys()):
        pa, pb = a["parts"].get(ref), b["parts"].get(ref)
        if pa is None or pb is None:
            out.append(f"part {ref}: only in {'ours' if pa is None else 'pcbnew'}")
            continue
        for field in ("image", "at", "PN", "pins", "keepouts"):
            if not close(pa[field], pb[field]):
                out.append(
                    f"part {ref} {field}: pcbnew {str(pa[field])[:200]} "
                    f"/ ours {str(pb[field])[:200]}"
                )
    for name in a["classes"].keys() | b["classes"].keys():
        if not close(a["classes"].get(name), b["classes"].get(name)):
            out.append(
                f"class {name}: pcbnew {a['classes'].get(name)} / ours {b['classes'].get(name)}"
            )
    return out[:show] + ([f"... {len(out) - show} more"] if len(out) > show else [])


def ours(board: Path, dsn_path: str, version: str = "10.0") -> str:
    return specctra.write(
        Board.load(board), Settings.of(board.with_suffix(".kicad_pro")), dsn_path, version
    )


# -- a session to import -------------------------------------------------------------


def session_of(board_path: Path) -> str:
    """A Specctra session holding a board's own tracks and vias, written the
    way Freerouting writes one (micrometres, ten units to one)."""
    board = Board.load(board_path)
    dsn = specctra.Dsn(board, Settings.of(board_path.with_suffix(".kicad_pro")))
    dsn.text(str(board_path.with_suffix(".dsn")), "10.0")
    q = specctra.quote

    def c(v_nm: int) -> str:
        return str(round(v_nm / 100))

    lines = [
        f"(session {q(board_path.stem + '.ses')}",
        f"  (base_design {q(board_path.stem + '.dsn')})",
        "  (placement",
        "    (resolution um 10)",
    ]
    for image, fps in dsn.components:
        lines.append(f"    (component {q(image.name)}")
        for fp in fps:
            side = "back" if fp.layer == "B.Cu" else "front"
            turn = (fp.angle + 180) % 360 if side == "back" else fp.angle % 360
            x, y = fp.position
            lines.append(
                f"      (place {q(dsn.refs[id(fp)])} {c(x)} {c(-y)} {side} {specctra.g(turn)})"
            )
        lines.append("    )")
    lines += ["  )", "  (was_is", "  )", "  (routes", "    (resolution um 10)", "    (library_out"]
    for name in dsn.via_names():
        lines.append(f"      (padstack {q(name)}")
        diameter = float(name.split("_")[1].split(":")[0])
        for layer in dsn.copper:
            lines += [
                "        (shape",
                f"          (circle {dsn.layer(layer)} {round(diameter * 10)} 0 0)",
                "        )",
            ]
        lines += ["        (attach off)", "      )"]
    lines += ["    )", "    (network_out"]
    nets: dict[str, tuple[list, list]] = {}
    for t in board.tracks:
        nets.setdefault(t.net, ([], []))[0].append(t)
    for v in board.vias:
        nets.setdefault(v.net, ([], []))[1].append(v)
    for net, (tracks, vias) in nets.items():
        if not net:
            continue
        lines.append(f"      (net {q(net)}")
        for t in tracks:
            lines += [
                "        (wire",
                f"          (path {dsn.layer(t.layer)} {round(t.width / 100)}",
                f"            {c(t.start[0])} {c(-t.start[1])}",
                f"            {c(t.end[0])} {c(-t.end[1])}",
                "          )",
                "        )",
            ]
        for v in vias:
            name = dsn.via_name(v.diameter, v.drill)
            lines += [
                "        (via",
                f"          {q(name)} {c(v.position[0])} {c(-v.position[1])}",
                "        )",
            ]
        lines.append("      )")
    lines += ["    )", "  )", ")"]
    return "\n".join(lines) + "\n"


def copper(path: Path):
    """A board's tracks and vias as multisets, order aside: pcbnew saves
    new tracks in the order of their random uuids."""
    from collections import Counter  # noqa: PLC0415

    b = Board.load(path)
    return (
        Counter((t.kind, t.start, t.end, t.width, t.layer, t.net) for t in b.tracks),
        Counter((v.position, v.diameter, v.drill, tuple(v.layers), v.net) for v in b.vias),
    )
