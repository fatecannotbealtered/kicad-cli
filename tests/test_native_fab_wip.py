"""Minimal offline consumers for unconnected manufacturing WIP libraries.

These do not certify a fabrication package or connect the libraries to fab CLI.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET

import pytest

from kicad_cli.fileformat import gerber, vectorplot
from kicad_cli.fileformat.artwork import Fill, Flash, Stroke
from kicad_cli.fileformat.board import Board
from kicad_cli.native import drill

NM = 1_000_000


def inside(rings, point):
    x, y = point
    crossings = 0
    for ring in rings:
        for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1], strict=True):
            if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
                crossings += 1
    return bool(crossings % 2)


def board(tmp_path):
    path = tmp_path / "fixture.kicad_pcb"
    path.write_text(
        '(kicad_pcb (version 20250131) (generator "offline-test") '
        "(general (thickness 1.6)) "
        '(layers (0 "F.Cu" signal) (31 "B.Cu" signal)) '
        '(setup) (net 0 "") (net 1 "GND"))',
        encoding="utf-8",
    )
    return Board.load(path)


def test_gerber_roundtrip_keeps_flash_and_stroke_locations():
    shapes = [
        Flash("rect", (NM, 2 * NM), (NM, NM)),
        Stroke([(3 * NM, NM), (5 * NM, NM)], 200_000),
    ]
    text = gerber.write(shapes)
    image = gerber.read(text).covered()
    # Gerber's physical coordinates are y-up, unlike the board's y-down.
    assert inside(image, (NM, -2 * NM))
    assert inside(image, (4 * NM, -NM))
    assert not inside(image, (NM, 2 * NM))
    assert not inside(image, (4 * NM, -2 * NM))
    assert text == gerber.write(shapes)


def test_gerber_regions_keep_their_holes():
    outer = [(0, 0), (4 * NM, 0), (4 * NM, 4 * NM), (0, 4 * NM)]
    hole = [(NM, NM), (NM, 3 * NM), (3 * NM, 3 * NM), (3 * NM, NM)]
    image = gerber.read(gerber.write([Fill([outer, hole])])).covered()
    assert inside(image, (500_000, -500_000))
    assert not inside(image, (2 * NM, -2 * NM))


def test_gerber_unknown_macro_primitive_is_not_silently_accepted():
    text = "%FSLAX46Y46*%%MOMM*%%AMBad*99,1,2*%%ADD10Bad*%D10*X0Y0D03*M02*"
    with pytest.raises(ValueError, match="primitive 99 is not read"):
        gerber.read(text)


def test_svg_is_an_independently_readable_xml_document(tmp_path):
    text = vectorplot.svg(
        board(tmp_path), [Flash("circle", (NM, 2 * NM), (NM, NM))], 'A&B <fixture> "title"'
    )
    parsed = ET.fromstring(text)
    assert parsed.tag == "{http://www.w3.org/2000/svg}svg"
    assert text == vectorplot.svg(
        board(tmp_path), [Flash("circle", (NM, 2 * NM), (NM, NM))], 'A&B <fixture> "title"'
    )


def test_pdf_cross_reference_points_to_each_object(tmp_path):
    payload = vectorplot.pdf(
        board(tmp_path), [Flash("rect", (NM, NM), (NM, NM))], r"fixture (board) \ report"
    )
    assert payload.startswith(b"%PDF-")
    start = int(re.search(rb"startxref\n(\d+)\n", payload).group(1))
    assert payload[start:].startswith(b"xref\n")
    lines = payload[start:].splitlines()
    count = int(lines[1].split()[1])
    for number, entry in enumerate(lines[3 : 3 + count - 1], 1):
        offset = int(entry.split()[0])
        assert payload[offset:].startswith(f"{number} 0 obj\n".encode())


def test_excellon_consumed_tool_and_coordinate_data_agrees(tmp_path):
    source = board(tmp_path)
    found = [
        drill.Hole(NM, 2 * NM, 500_000, True, False),
        drill.Hole(3 * NM, 4 * NM, 300_000, True, True),
        drill.Hole(5 * NM, 6 * NM, 800_000, False, False, ((5 * NM, 6 * NM), (6 * NM, 6 * NM))),
    ]
    chosen = drill.tools(found)
    when = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)
    text = drill.excellon(source, chosen, "MixedPlating,1,2", False, (0, 0), when)
    tools = [
        match.groups()
        for line in text.splitlines()
        if (match := re.fullmatch(r"T(\d+)C([0-9.]+)", line))
    ]
    assert [(number, float(size)) for number, size in tools] == [("1", 0.3), ("2", 0.5), ("3", 0.8)]
    assert "X1.0Y-2.0" in text and "X3.0Y-4.0" in text
    assert "G00X5.0Y-6.0" in text and "G01X6.0Y-6.0" in text
    assert text == drill.excellon(source, chosen, "MixedPlating,1,2", False, (0, 0), when)
