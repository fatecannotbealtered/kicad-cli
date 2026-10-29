"""Gerber files read back into copper: what a Gerber image covers, as rings.

A Gerber file is a picture: shapes flashed at points, lines drawn with a
pen, regions filled. This reads the part of the format pcbnew's plotter
writes -- mm, absolute coordinates, circle, rectangle and obround
apertures and aperture macros, linear and circular moves, regions, dark and
clear polarity -- and gives back what it covers, a set of rings in whole
nanometres (`polygons.py`). It is how a plot is checked: two Gerbers of a
layer cover the same copper, or they do not.

The Gerber layer format is published by Ucamco ("The Gerber Layer Format
Specification"); nothing here comes from a reader of anyone else's.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import geometry, polygons
from .polygons import Ring

MAX_ERROR = 1_000  # nm: how far a circle's chords may stray inside it


@dataclass
class Aperture:
    kind: str  # C, R, O, P or a macro's name
    params: list[float]


@dataclass
class Image:
    """What a file draws, in order: (dark, rings) for each thing drawn."""

    layers: list[tuple[bool, list[Ring]]] = field(default_factory=list)

    def covered(self) -> list[Ring]:
        """What is dark when everything is drawn: clear shapes cut what was
        drawn before them."""
        out: list[Ring] = []
        dark_run: list[Ring] = []
        for dark, rings in self.layers:
            # Each operation has already been normalized by the reader.
            # Reversing a region's inner contours would fill its holes.
            if dark:
                dark_run += rings
                continue
            if dark_run:
                out = polygons.union(out + dark_run)
                dark_run = []
            out = polygons.difference(out, rings)
        if dark_run:
            out = polygons.union(out + dark_run)
        return out


def _circle(cx: float, cy: float, d: float) -> Ring:
    r = d / 2
    n = max(8, geometry.segments_for(r, 360, MAX_ERROR))
    turn = 2 * math.pi / n
    return [
        (round(cx + r * math.cos(turn * k)), round(cy + r * math.sin(turn * k))) for k in range(n)
    ]


def _stadium(a, b, d: float) -> Ring:
    """A line drawn with a round pen of diameter d."""
    (ax, ay), (bx, by) = a, b
    if (ax, ay) == (bx, by):
        return _circle(ax, ay, d)
    r = d / 2
    angle = math.atan2(by - ay, bx - ax)
    n = max(4, geometry.segments_for(r, 180, MAX_ERROR))
    out = []
    for (cx, cy), start in (((bx, by), angle - math.pi / 2), ((ax, ay), angle + math.pi / 2)):
        for k in range(n + 1):
            t = start + math.pi * k / n
            out.append((round(cx + r * math.cos(t)), round(cy + r * math.sin(t))))
    return out


def _rect(cx, cy, w, h, angle_deg=0.0) -> Ring:
    local = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    return _turn(local, cx, cy, angle_deg)


def _turn(points, cx, cy, angle_deg) -> Ring:
    """Points turned counter-clockwise by the angle about the origin, as a
    Gerber file's y-up frame turns them, then moved."""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return [(round(cx + x * c - y * s), round(cy + x * s + y * c)) for x, y in points]


def _arc_points(start, end, centre, clockwise: bool) -> list[tuple[float, float]]:
    (sx, sy), (ex, ey), (cx, cy) = start, end, centre
    r = math.hypot(sx - cx, sy - cy)
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    sweep = a1 - a0
    if clockwise:
        if sweep >= 0:
            sweep -= 2 * math.pi
    else:
        if sweep <= 0:
            sweep += 2 * math.pi
    n = max(1, geometry.segments_for(r, abs(math.degrees(sweep)), MAX_ERROR))
    return [(cx + r * math.cos(a0 + sweep * k / n), cy + r * math.sin(a0 + sweep * k / n))
            for k in range(1, n + 1)]  # fmt: skip


class _Macro:
    def __init__(self, body: list[str]) -> None:
        self.body = [b for b in body if b and not b.startswith("0 ") and b != "0"]

    def rings(self, params: list[float], cx: float, cy: float, unit: float) -> list[Ring]:
        variables = {f"${i + 1}": v for i, v in enumerate(params)}
        out: list[Ring] = []
        for statement in self.body:
            if statement.startswith("$") and "=" in statement:
                name, expr = statement.split("=", 1)
                variables[name] = _eval(expr, variables)
                continue
            words = [_eval(w, variables) for w in statement.split(",")]
            code = int(words[0])
            if code == 1:  # circle: exposure, diameter, x, y[, rotation]
                _exp, d, x, y = words[1:5]
                rot = words[5] if len(words) > 5 else 0.0
                ((px, py),) = _turn([(x * unit, y * unit)], 0, 0, rot)
                out.append(_circle(cx + px, cy + py, d * unit))
            elif code == 4:  # outline: exposure, n, x0, y0, ... xn, yn, rotation
                n = int(words[2])
                pts = [(words[3 + 2 * i] * unit, words[4 + 2 * i] * unit) for i in range(n + 1)]
                rot = words[3 + 2 * (n + 1)]
                out.append(_turn(pts[:-1], cx, cy, rot))
            elif code == 20:  # vector line: exposure, width, x1, y1, x2, y2, rotation
                _exp, w, x1, y1, x2, y2, rot = words[1:8]
                length = math.hypot(x2 - x1, y2 - y1) * unit
                angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
                mx, my = (x1 + x2) / 2 * unit, (y1 + y2) / 2 * unit
                local = _rect(mx, my, length, w * unit, angle)
                out.append(_turn(local, cx, cy, rot))
            elif code == 21:  # centre line: exposure, width, height, x, y, rotation
                _exp, w, h, x, y, rot = words[1:7]
                out.append(_turn(_rect(x * unit, y * unit, w * unit, h * unit), cx, cy, rot))
            elif code == 5:  # polygon: exposure, vertices, x, y, diameter, rotation
                _exp, n, x, y, d, rot = words[1:7]
                pts = [(x * unit + d * unit / 2 * math.cos(2 * math.pi * k / int(n)),
                        y * unit + d * unit / 2 * math.sin(2 * math.pi * k / int(n)))
                       for k in range(int(n))]  # fmt: skip
                out.append(_turn(pts, cx, cy, rot))
            else:
                raise ValueError(f"aperture macro primitive {code} is not read")
        return out


def _eval(text: str, variables: dict[str, float]) -> float:
    expr = text.strip().replace("x", "*").replace("X", "*")
    for name in sorted(variables, key=len, reverse=True):
        expr = expr.replace(name, repr(variables[name]))
    if not re.fullmatch(r"[0-9eE.+\-*/() ]*", expr):
        raise ValueError(f"not an arithmetic expression: {text!r}")
    return float(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - digits and operators only


def read(text: str) -> Image:
    """The image a Gerber file draws."""
    image = Image()
    apertures: dict[int, Aperture] = {}
    macros: dict[str, _Macro] = {}
    unit = 1_000_000.0  # nm per file unit: mm
    decimal = 6  # digits after the point, as the format statement sets them
    x = y = 0.0
    current: Aperture | None = None
    mode = "G01"
    dark = True
    region: list[list[tuple[float, float]]] | None = None
    drawn: list[Ring] = []

    def flush():
        nonlocal drawn
        if drawn:
            image.layers.append((dark, drawn))
            drawn = []

    def number(word: str) -> float:
        if "." in word:
            return float(word) * unit
        sign = -1 if word.startswith("-") else 1
        digits = word.lstrip("+-")
        return sign * int(digits) / 10**decimal * unit

    blocks = re.findall(r"%[^%]*%|[^%*]+\*", text.replace("\r", "").replace("\n", ""))
    for block in blocks:
        if block.startswith("%"):
            body = block.strip("%")
            for part in [p for p in body.split("*") if p]:
                if part.startswith("FS"):
                    m = re.match(r"FSLAX\d(\d)Y\d\d", part)
                    decimal = int(m.group(1))
                elif part.startswith("MO"):
                    unit = 1_000_000.0 if part == "MOMM" else 25_400_000.0
                elif part.startswith("LP"):
                    flush()
                    dark = part == "LPD"
                elif part.startswith("AD"):
                    m = re.match(r"ADD(\d+)([A-Za-z_][A-Za-z0-9_.]*),?(.*)", part)
                    params = [float(v) for v in m.group(3).split("X") if v] if m.group(3) else []
                    apertures[int(m.group(1))] = Aperture(m.group(2), params)
            if body.startswith("AM"):
                parts = [p for p in body.split("*")]
                macros[parts[0][2:]] = _Macro([p.strip() for p in parts[1:]])
            continue
        word = block[:-1]
        if word.startswith("G04") or not word:
            continue
        for g in re.findall(r"G0?([0-9]+)", word.split("X")[0].split("Y")[0].split("D")[0]):
            code = int(g)
            if code in (1, 2, 3):
                mode = f"G0{code}"
            elif code == 36:
                region = [[]]
            elif code == 37:
                if region is not None:
                    flush()
                    loops = [
                        [(round(px), round(py)) for px, py in loop]
                        for loop in region
                        if len(loop) >= 3
                    ]
                    image.layers.append((dark, polygons.union(loops, polygons.EVENODD)))
                region = None
        m = re.fullmatch(r"(?:G0?[0-9]+)?(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?"
                         r"(?:D0?(\d+))?", word)  # fmt: skip
        if m is None:
            continue
        nx = number(m.group(1)) if m.group(1) is not None else x
        ny = number(m.group(2)) if m.group(2) is not None else y
        d = int(m.group(5)) if m.group(5) is not None else None
        if d is not None and d >= 10:
            current = apertures[d]
            continue
        if d == 1 or (d is None and (m.group(1) or m.group(2))):
            if mode == "G01":
                path = [(nx, ny)]
            else:
                i = number(m.group(3)) if m.group(3) else 0.0
                j = number(m.group(4)) if m.group(4) else 0.0
                path = _arc_points((x, y), (nx, ny), (x + i, y + j), mode == "G02")
            if region is not None:
                if not region[-1]:
                    region[-1].append((x, y))
                region[-1] += path
            elif current is not None and current.kind == "C":
                pen = current.params[0] * unit
                prev = (x, y)
                for p in path:
                    drawn += polygons.union([_stadium(prev, p, pen)])
                    prev = p
        elif d == 2:
            if region is not None and region[-1]:
                region.append([])
        elif d == 3 and current is not None:
            flush()
            image.layers.append((dark, polygons.union(_flash(current, nx, ny, unit, macros))))
        x, y = nx, ny
    flush()
    return image


def _flash(ap: Aperture, x: float, y: float, unit: float, macros) -> list[Ring]:
    p = [v * unit for v in ap.params]
    if ap.kind == "C":
        return [_circle(x, y, p[0])]
    if ap.kind == "R":
        return [_rect(x, y, p[0], p[1])]
    if ap.kind == "O":
        w, h = p[0], p[1]
        if w >= h:
            return [_stadium((x - (w - h) / 2, y), (x + (w - h) / 2, y), h)]
        return [_stadium((x, y - (h - w) / 2), (x, y + (h - w) / 2), w)]
    if ap.kind == "P":
        d, n = p[0], int(ap.params[1])
        rot = ap.params[2] if len(ap.params) > 2 else 0.0
        pts = [(d / 2 * math.cos(2 * math.pi * k / n), d / 2 * math.sin(2 * math.pi * k / n))
               for k in range(n)]  # fmt: skip
        return [_turn(pts, x, y, rot)]
    macro = macros.get(ap.kind)
    if macro is None:
        raise ValueError(f"aperture {ap.kind} is not defined")
    return macro.rings(ap.params, x, y, unit)


# -- writing -------------------------------------------------------------------------


def _xy(p) -> str:
    """A board point in a Gerber file: nanometres are its 4.6 millimetres,
    and its y runs up."""
    return f"X{round(p[0])}Y{round(-p[1])}"


def file_function(board, layer: str) -> tuple[str, str | None]:
    """A layer's X2 file function and polarity, as KiCad names them."""
    copper = board.copper_layers
    if layer in copper:
        n = copper.index(layer) + 1
        where = "Top" if layer == "F.Cu" else ("Bot" if layer == "B.Cu" else "Inr")
        return f"Copper,L{n},{where}", "Positive"
    side = "Top" if layer.startswith("F.") else "Bot"
    named = {
        "Mask": ("Soldermask", "Negative"), "Paste": ("Paste", "Positive"),
        "SilkS": ("Legend", "Positive"), "Silkscreen": ("Legend", "Positive"),
        "Adhes": ("Glue", "Positive"), "Adhesive": ("Glue", "Positive"),
        "Fab": ("AssemblyDrawing", None),
    }  # fmt: skip
    kind = layer.split(".", 1)[-1]
    if layer[:2] in ("F.", "B.") and kind in named:
        name, polarity = named[kind]
        return f"{name},{side}", polarity
    fixed = {
        "Edge.Cuts": "Profile,NP", "Dwgs.User": "OtherDrawing,Comment",
        "Cmts.User": "Other,Comment", "Eco1.User": "Other,ECO1", "Eco2.User": "Other,ECO2",
    }  # fmt: skip
    return fixed.get(layer, "Other,User"), None


def project_id(board) -> str:
    """The ProjectId attribute: the board's name, a GUID made of its file
    name's bytes as KiCad makes it (a 4 and a 9 put in where a GUID's
    version and variant go), and the title block's revision."""
    digits = board.path.name.encode("utf-8").hex()
    digits = (digits[:12] + "4" + digits[12:15] + "9" + digits[15:] + "0" * 32)[:32]
    guid = f"{digits[:8]}-{digits[8:12]}-{digits[12:16]}-{digits[16:20]}-{digits[20:32]}"
    block = board.root.find("title_block")
    rev = block.find("rev") if block is not None else None
    revision = rev.value(1) if rev is not None and rev.value(1) else "rev?"
    return f"{board.path.stem},{guid},{revision}"


def _escape(text: str) -> str:
    """A field of an attribute, its comma, star, percent and backslash
    written as \\uXXXX."""
    return "".join(f"\\u{ord(c):04X}" if c in ",*%\\" else c for c in text)


# The aperture functions whose objects carry the net they belong to.
NETTED = ("ComponentPad", "SMDPad", "ViaPad", "Conductor", "ConnectorPad", "HeatsinkPad",
          "TestPad", "CastellatedPad", "BGAPad", "WasherPad")  # fmt: skip


class Writer:
    """A Gerber X2 file of one layer's artwork (`artwork.py`)."""

    def __init__(self) -> None:
        self.apertures: dict[tuple, int] = {}
        self.body: list[str] = []
        self.current: int | None = None
        self.objects: dict[str, str] = {}  # TO attributes in force
        self.group: str | None = None  # the component they belong to

    def aperture(self, key: tuple) -> int:
        if key not in self.apertures:
            self.apertures[key] = 10 + len(self.apertures)
        return self.apertures[key]

    def select(self, code: int) -> None:
        if code != self.current:
            self.body.append(f"D{code}*")
            self.current = code

    def attributes(self, wanted: dict[str, str], group: str | None = None) -> None:
        """The object attributes a shape carries: all dropped where it
        carries none, or belongs to another component."""
        if not wanted:
            if self.objects:
                self.body.append("%TD*%")
                self.objects = {}
            self.group = group
            return
        if self.objects and (group != self.group or set(self.objects) - set(wanted)):
            self.body.append("%TD*%")
            self.objects = {}
        self.group = group
        for key in ("P", "N", "C"):
            if key in wanted and self.objects.get(key) != wanted[key]:
                self.body.append(f"%TO.{key},{wanted[key]}*%")
                self.objects[key] = wanted[key]

    def flash(self, flash, function: str | None) -> None:
        shape, (w, h) = flash.shape, flash.size
        turned = flash.angle % 360
        if shape == "circle":
            self.select(self.aperture(("C", w, 0, function)))
            self.body.append(f"{_xy(flash.at)}D03*")
            return
        if shape in ("rect", "oval") and turned % 90 == 0:
            if turned % 180:
                w, h = h, w
            self.select(self.aperture(("R" if shape == "rect" else "O", w, h, function)))
            self.body.append(f"{_xy(flash.at)}D03*")
            return
        self.region(_flash_rings(flash), function)

    def stroke(self, stroke, function: str | None) -> None:
        self.select(self.aperture(("C", stroke.width, 0, function)))
        pts = stroke.points
        if stroke.circle:
            (cx, cy), (ex, ey) = pts
            i, j = round(cx - ex), round(-(cy - ey))
            self.body += ["G75*", f"{_xy((ex, ey))}D02*", f"G02{_xy((ex, ey))}I{i}J{j}D01*", "G01*"]
            return
        if stroke.arc:
            start, mid, end = pts
            found = geometry._circle_through(start, mid, end)  # noqa: SLF001
            if found is None:
                self.body += [f"{_xy(start)}D02*", f"{_xy(end)}D01*"]
                return
            cx, cy, _r, _a1, sweep = found
            # A positive sweep with y down turns clockwise once y runs up,
            # as it does in the file.
            code = "G02" if sweep > 0 else "G03"
            i, j = round(cx - start[0]), round(-(cy - start[1]))
            self.body += ["G75*", f"{_xy(start)}D02*", f"{code}{_xy(end)}I{i}J{j}D01*", "G01*"]
            return
        self.body.append(f"{_xy(pts[0])}D02*")
        for p in pts[1:]:
            self.body.append(f"{_xy(p)}D01*")
        if stroke.closed and pts[-1] != pts[0]:
            self.body.append(f"{_xy(pts[0])}D01*")

    def region(self, rings, function: str | None = None) -> None:
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            return
        if function:
            self.body.append(f"%TA.AperFunction,{function}*%")
        # Contours belonging to one fill form one region. Emitting a second
        # dark region for an inner contour fills the intended hole.
        self.body.append("G36*")
        for ring in rings:
            self.body.append(f"{_xy(ring[0])}D02*")
            self.body += [f"{_xy(p)}D01*" for p in ring[1:]]
            self.body.append(f"{_xy(ring[0])}D01*")
        self.body.append("G37*")
        if function:
            self.body.append("%TD.AperFunction*%")

    def text(self, head: list[str]) -> str:
        lines = list(head) + ["G04 APERTURE LIST*"]
        for key, code in self.apertures.items():
            kind, a, b, function = key
            if function:
                lines.append(f"%TA.AperFunction,{function}*%")
            if kind == "C":
                lines.append(f"%ADD{code}C,{a / 1e6:.6f}*%")
            else:
                lines.append(f"%ADD{code}{kind},{a / 1e6:.6f}X{b / 1e6:.6f}*%")
            if function:
                lines.append("%TD*%")
        lines.append("G04 APERTURE END LIST*")
        if self.objects:
            self.body.append("%TD*%")
        return "\n".join(lines + self.body + ["M02*"]) + "\n"


def _flash_rings(flash) -> list[Ring]:
    """A flash that no standard aperture draws, as the polygon it covers."""
    w, h = flash.size
    if flash.shape == "polygon":
        return flash.rings or []
    if flash.shape == "roundrect":
        local = geometry.rounded_rectangle(w, h, flash.radius, MAX_ERROR)
    elif flash.shape == "trapezoid":
        local = geometry.trapezoid(w, h, *flash.delta)
    elif flash.shape == "oval":
        local = geometry.oval(w, h, MAX_ERROR)
    else:
        local = geometry.rectangle(w, h)
    return [geometry.place(local, *flash.at, flash.angle)]


def write(shapes, board=None, layer: str = "", when=None, version: str = "") -> str:
    """A layer's artwork as a Gerber X2 file."""
    from .artwork import Fill, Flash, Stroke  # noqa: PLC0415

    writer = Writer()
    copper = layer.endswith(".Cu")
    for shape in shapes:
        attrs = shape.attrs or {}
        function = attrs.get("function")
        wanted = {}
        if copper:
            if attrs.get("ref") and attrs.get("pin") is not None:
                pin = f"{_escape(attrs['ref'])},{_escape(attrs['pin'])}"
                if attrs.get("pinfunction"):
                    pin += f",{_escape(attrs['pinfunction'])}"
                wanted["P"] = pin
            if attrs.get("net") is not None and (function or "").split(",")[0] in NETTED:
                wanted["N"] = _escape(attrs["net"])
        elif attrs.get("ref"):
            wanted["C"] = _escape(attrs["ref"])
        writer.attributes(wanted, attrs.get("ref"))
        if isinstance(shape, Flash):
            writer.flash(shape, function)
        elif isinstance(shape, Stroke):
            writer.stroke(shape, function)
        elif isinstance(shape, Fill):
            writer.region(shape.rings, function)
    head = []
    if board is not None:
        function, polarity = file_function(board, layer)
        head += [f"%TF.GenerationSoftware,fateforge,kicad-cli,{version}*%"]
        if when is not None:
            head.append(f"%TF.CreationDate,{when.isoformat(timespec='seconds')}*%")
        head += [
            f"%TF.ProjectId,{project_id(board)}*%",
            "%TF.SameCoordinates,Original*%",
            f"%TF.FileFunction,{function}*%",
        ]
        if polarity:
            head.append(f"%TF.FilePolarity,{polarity}*%")
    head += [
        "%FSLAX46Y46*%",
        "G04 Gerber Fmt 4.6, Leading zero omitted, Abs format (unit mm)*",
        "%MOMM*%",
        "%LPD*%",
        "G01*",
    ]
    return writer.text(head)
