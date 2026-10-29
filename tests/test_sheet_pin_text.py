"""Default sheet-pin placement plus optional public CLI/native SVG regression.

Native execution is explicit: set KICAD_CLI_PIN_TEXT_NATIVE_ROOT to a legal
KiCad10.0.6 installation. It creates only a new disposable project, never an
existing engineer's design. KICAD_CLI_TEST_BINARY may select a frozen candidate.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_cli.fileformat.sexpr import Document, render
from kicad_cli.sch_hierarchy_build import Hierarchy

REPO = Path(__file__).resolve().parents[1]
EXPECTED = {"left": "left", "right": "right", "top": "right", "bottom": "left"}


def owner():
    return SimpleNamespace(_scalar=lambda value, _path: value, uid=lambda i, tag: f"{i}:{tag}")


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_new_pin_text_side_preserves_anchor_identity_shape_and_size(edge):
    hierarchy = Hierarchy(owner())
    pin = hierarchy._pin(
        {"name": "SIGNAL", "shape": "input", "edge": edge, "offset_mm": 10},
        (40_000_000, 30_000_000),
        (40_000_000, 30_000_000),
        7,
        2,
        "/pin",
    )
    expected = {
        "left": ["40", "40", "180"],
        "right": ["80", "40", "0"],
        "top": ["50", "30", "90"],
        "bottom": ["50", "60", "270"],
    }
    assert pin.find("at").values() == expected[edge]
    assert pin.value(1) == "SIGNAL" and pin.atom(2) == "input"
    assert pin.find("uuid").value(1) == "7:sheet-pin/2"
    assert pin.find("effects").find("font").find("size").values() == ["1.27", "1.27"]
    assert pin.find("effects").find("justify").values() == [EXPECTED[edge]]


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_frame_rederives_only_horizontal_justification(edge, tmp_path):
    # A synthetic legacy input with deliberately non-default typography.
    # This is a unit compatibility fixture, not the public/native proof below.
    document = Document.parse(
        '(sheet (at 40 30) (size 40 30) (uuid "sheet")'
        ' (pin "SIGNAL" input (at 40 40 180)'
        " (effects (font (size 1.524 1.524) bold) (justify left bottom mirror))"
        ' (uuid "pin-id")))'
    )
    node = document.root
    pin = node.find("pin")
    font_before = render(pin.find("effects").find("font"))
    obj = SimpleNamespace(
        kind="sheet",
        object_id="selected",
        schematic=SimpleNamespace(path=tmp_path / "root.kicad_sch"),
        source_relative="root.kicad_sch",
        record={"uuid": "sheet"},
    )
    c = owner()
    c.catalog = SimpleNamespace(resolve=lambda _target: obj)
    c.documents = {obj.schematic.path.resolve(): SimpleNamespace(path=obj.schematic.path)}
    c.work_nodes = {obj.object_id: node}
    c.used_targets, c.touched, c.changed_files = set(), [], set()
    c.action_targets, c.report = {}, []
    hierarchy = Hierarchy(c)
    hierarchy.refresh = lambda: None
    hierarchy.frame(
        {
            "op": "update_sheet",
            "target": {},
            "at_mm": [40, 30],
            "size_mm": [40, 30],
            "pin_positions": [{"edge": edge, "offset_mm": 10}],
        },
        0,
    )
    assert pin.value(1) == "SIGNAL" and pin.atom(2) == "input"
    assert pin.find("uuid").value(1) == "pin-id"
    assert render(pin.find("effects").find("font")) == font_before
    assert pin.find("effects").find("justify").values() == [EXPECTED[edge], "bottom", "mirror"]


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _offset(value, delta):
    return float(Decimal(str(value)) + Decimal(delta))


def _symbols(data):
    return {
        row["uuid"]: (
            row["geometry"],
            [
                {key: value for key, value in field.items() if key != "source_span"}
                for field in row["fields"]
            ],
        )
        for row in data["items"]
        if row["kind"] == "symbol"
    }


def _raw_symbols(data, directory):
    result = {}
    for row in data["items"]:
        if row["kind"] != "symbol":
            continue
        raw = (directory / Path(row["source_file"]).name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["source_sha256"]
        start, end = row["source_span"]
        result[row["uuid"]] = raw.decode("utf-8")[start:end]
    return result


def _svg_pin_boxes(path, names):
    """Bounds of actual native stroke-font paths, rather than stored justify tokens."""
    tree = ET.parse(path)
    parents = {child: parent for parent in tree.iter() for child in parent}
    result = {}
    ns = {"s": "http://www.w3.org/2000/svg"}
    for group in tree.findall('.//s:g[@class="stroked-text"]', ns):
        description = group.find("s:desc", ns)
        if description is None or description.text not in names:
            continue
        current = group
        while current in parents:
            assert "transform" not in current.attrib, "Native SVG transform needs explicit handling"
            current = parents[current]
        points = [
            (float(x), float(y))
            for stroke in group.findall(".//s:path", ns)
            for x, y in re.findall(r"[ML]\s*([-+\d.]+)[,\s]+([-+\d.]+)", stroke.attrib["d"])
        ]
        assert points, description.text
        assert description.text not in result, "Pin names must be unique in the root page"
        result[description.text] = [
            min(x for x, _ in points),
            min(y for _, y in points),
            max(x for x, _ in points),
            max(y for _, y in points),
        ]
    assert set(result) == set(names)
    return result


@pytest.mark.skipif(
    not os.environ.get("KICAD_CLI_PIN_TEXT_NATIVE_ROOT"),
    reason="Explicit legal KiCad10.0.6 root is required for native sheet-pin text proof",
)
def test_public_four_edges_native_text_save_reopen_and_edge_change(tmp_path):
    installation = Path(os.environ["KICAD_CLI_PIN_TEXT_NATIVE_ROOT"])
    work, profile, logs = (tmp_path / name for name in ("work", "profile", "logs"))
    for folder in (work, profile, logs):
        folder.mkdir()
    tools = tmp_path / "tools"
    shutil.copytree(REPO / "package/tools", tools)
    spec = importlib.util.spec_from_file_location("pin_text_job_method", tools / "run_observed.py")
    observed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observed)
    config = tmp_path / "runtime-config.json"
    _json(
        config, {"format_version": 1, "kicad_root": str(installation), "profile_dir": str(profile)}
    )
    _json(
        profile / "scope.json",
        {
            "scope_id": "sheet-pin-native-owned",
            "tool": "kicad-cli",
            "granted_by": "owned style regression",
            "granted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "expires_at": None,
            "effects": ["observe", "change", "persist"],
            "dangerous": False,
            "roots": [str(tmp_path)],
            "outputs": [str(tmp_path)],
            "limits": {"max_operations": 3},
        },
    )
    extra = {"KICAD_CLI_RUNTIME_CONFIG": str(config)}
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    calls = []

    def run(label, argv, *, product=False):
        output = logs / label
        observed.run(
            argv, output, profile, REPO if product and not binary else tmp_path, extra=extra
        )
        calls.append(label)
        return json.loads((output / "stdout").read_bytes()) if product else output

    def cli(label, *args):
        value = run(label, [*prefix, *map(str, args), "--compact"], product=True)
        assert value["ok"], value
        return value["data"]

    def query(label):
        data = cli(label, "sch", "objects", "--schematic", root)
        assert not data["has_more"]
        return data

    def edit(label, request):
        changes = tmp_path / (label + ".json")
        _json(changes, {"changes": [request]})
        args = ["sch", "edit", "--schematic", root, "--changes", changes]
        data = cli(label + "-preview", *args, "--dry-run")
        candidate = tmp_path / (label + "-candidate")
        candidate.mkdir()
        for row in data["preview"]["extensions"]["files"]:
            shutil.copyfile(row["candidate"], candidate / Path(row["path"]).name)
        # A candidate review requires the unchanged companion project too.
        companions = [*work.glob("*.kicad_pro"), *work.glob("*.kicad_sch")]
        for companion in companions:
            if not (candidate / companion.name).exists():
                shutil.copyfile(companion, candidate / companion.name)
        return args, data, candidate

    expected = {
        "NET_L": [["R1", "1"], ["R3", "1"]],
        "NET_R": [["R1", "2"], ["R3", "2"]],
        "NET_T": [["R2", "1"], ["R4", "1"]],
        "NET_B": [["R2", "2"], ["R4", "2"]],
    }
    _json(
        tmp_path / "INTENT.json",
        {
            "native_pin_groups": expected,
            "frame_mm": [100, 50, 60, 60],
            "pin_font_mm": 1.27,
            "external_stub_mm": 10.16,
        },
    )
    blank = tmp_path / "ports.json"
    _json(blank, {"mode": "blank", "title": "Four-edge sheet pin style regression"})
    root = work / "ports.kicad_sch"
    create = ["sch", "create", "--spec", blank, "--out", work]
    initial = cli("00-blank-preview", *create, "--dry-run")
    cli("01-blank-save", *create, "--confirm", initial["confirm_token"])
    official = installation / "bin/kicad-cli.exe"
    version = run("01a-native-version", [official, "--version"])
    assert (version / "stdout").read_text(encoding="utf-8").strip() == "10.0.6"
    # Initialize the exact native/font consumers on a separate blank copy,
    # before taking the circuit's input/configuration snapshot.
    warm = tmp_path / "warmup"
    warm.mkdir()
    for source in (root, root.with_suffix(".kicad_pro")):
        shutil.copyfile(source, warm / source.name)
    run(
        "01b-warm-xml",
        [
            official,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            warm / "nets.xml",
            warm / root.name,
        ],
    )
    run("01c-warm-svg", [official, "sch", "export", "svg", "--output", warm, warm / root.name])
    node, sharp = (
        os.environ.get("KICAD_CLI_PIN_TEXT_NODE"),
        os.environ.get("KICAD_CLI_PIN_TEXT_SHARP"),
    )
    if node and sharp:
        run(
            "01d-warm-png",
            [node, tools / "render_svg.cjs", warm / "ports.svg", warm / "full-page.png", sharp],
        )
    before = query("02-empty-query")
    page = before["extensions"]["pages"][0]["instance_path"]
    resource = cli("03-resource", "sch", "symbols", "--schematic", root, "--symbol", "Device:R")
    pins = {pin["number"]: pin["pin_ordinal"] for pin in resource["items"][0]["pins"]}
    assert set(pins) == {"1", "2"}
    child = {"new": "box", "parent": page}
    edges = ["left", "right", "top", "bottom"]
    names = ["PIN_LEFT_LONG", "PIN_RIGHT_LONG", "PIN_TOP_LONG", "PIN_BOTTOM_LONG"]
    nets = ["NET_L", "NET_R", "NET_T", "NET_B"]
    endpoints = [[89.84, 80], [170.16, 80], [130, 39.84], [130, 120.16]]
    actions = [
        {
            "op": "create_sheet",
            "id": "box",
            "page": page,
            "file": "child.kicad_sch",
            "name": "Box",
            "at_mm": [100, 50],
            "size_mm": [60, 60],
            "pins": [
                {"name": n, "shape": "passive", "edge": e, "offset_mm": 30}
                for n, e in zip(names, edges, strict=True)
            ],
        }
    ]
    for ident, ref, target, xy in [
        ("pr1", "R1", page, [40.64, 50.8]),
        ("pr2", "R2", page, [40.64, 91.44]),
        ("cr1", "R3", child, [80, 50.8]),
        ("cr2", "R4", child, [80, 91.44]),
    ]:
        actions.append(
            {
                "op": "place_symbol",
                "id": ident,
                "page": target,
                "symbol": "Device:R",
                "unit": 1,
                "ref": ref,
                "value": "10k",
                "at_mm": xy,
                "fields": {
                    "Reference": {
                        "at_mm": [_offset(xy[0], "6.35"), _offset(xy[1], "-1.27")],
                        "angle_deg": 0,
                    },
                    "Value": {
                        "at_mm": [_offset(xy[0], "6.35"), _offset(xy[1], "1.27")],
                        "angle_deg": 0,
                    },
                },
            }
        )
    components = []
    for i, (name, net, end) in enumerate(zip(names, nets, endpoints, strict=True)):
        pr, cr, number = (
            ("pr1", "cr1", "1")
            if i == 0
            else ("pr1", "cr1", "2")
            if i == 1
            else ("pr2", "cr2", "1")
            if i == 2
            else ("pr2", "cr2", "2")
        )
        ordinal = pins[number]
        actions.extend(
            [
                {
                    "op": "create",
                    "id": f"parent{i}",
                    "page": page,
                    "kind": "label",
                    "text": net,
                    "at": {"pin": {"new": pr, "instance_path": page, "pin_ordinal": ordinal}},
                },
                {
                    "op": "create",
                    "id": f"port{i}",
                    "page": child,
                    "kind": "hierarchical_label",
                    "text": name,
                    "shape": "passive",
                    "at": {"pin": {"new": cr, "instance_path": child, "pin_ordinal": ordinal}},
                },
                {
                    "op": "create",
                    "id": f"wire{i}",
                    "page": page,
                    "kind": "wire",
                    "points": [
                        {
                            "sheet_pin": {
                                "new": "box",
                                "instance_path": page,
                                "sheet_pin_ordinal": i,
                            }
                        },
                        end,
                    ],
                },
                {
                    "op": "create",
                    "id": f"end{i}",
                    "page": page,
                    "kind": "label",
                    "text": net,
                    "at": {"object": {"new": f"wire{i}"}, "instance_path": page, "endpoint": 1},
                },
            ]
        )
        components.append(
            {
                "members": [
                    {"new": pr, "instance_path": page, "pin_ordinal": ordinal},
                    {"new": cr, "instance_path": child, "pin_ordinal": ordinal},
                    {"new": "box", "instance_path": page, "sheet_pin_ordinal": i},
                    *[
                        {"new": f"{kind}{i}", "instance_path": target}
                        for kind, target in [
                            ("parent", page),
                            ("port", child),
                            ("wire", page),
                            ("end", page),
                        ]
                    ],
                ],
                "explicit_names": [
                    {"kind": "label", "name": "/" + net},
                    {"kind": "sheet_pin", "name": "/Box/" + name},
                    {"kind": "hierarchical_label", "name": "/Box/" + name},
                ],
                "nc_members": [],
            }
        )
    request = {
        "op": "circuit",
        "snapshot": {
            "root": before["extensions"]["root"],
            "snapshot": before["snapshot"]["revision"],
        },
        "affected_instances": [page, child],
        "actions": actions,
        "expected": {"components": components},
    }
    args, prepared, candidate = edit("04-build", request)
    verification = []

    def native(label, directory, edge_order):
        output = tmp_path / (label + "-native")
        output.mkdir()
        run(
            label + "-xml",
            [
                official,
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                output / "nets.xml",
                directory / root.name,
            ],
        )
        run(
            label + "-svg",
            [official, "sch", "export", "svg", "--output", output, directory / root.name],
        )
        document = ET.parse(output / "nets.xml")
        groups = {
            frozenset((n.attrib["ref"], n.attrib["pin"]) for n in net.findall("node"))
            for net in document.findall("./nets/net")
        }
        assert groups == {frozenset(map(tuple, group)) for group in expected.values()}
        boxes = _svg_pin_boxes(output / "ports.svg", names)
        for name, edge in zip(names, edge_order, strict=True):
            left, top, right, bottom = boxes[name]
            assert 100 < left < right < 160 and 50 < top < bottom < 110, (name, edge, boxes[name])
        # All four external wire segments remain in the exterior half-plane.
        verification.append(
            {
                "label": label,
                "native_groups": [sorted(group) for group in groups],
                "glyph_boxes_mm": boxes,
                "pin_edges": dict(zip(names, edge_order, strict=True)),
            }
        )
        node, sharp = (
            os.environ.get("KICAD_CLI_PIN_TEXT_NODE"),
            os.environ.get("KICAD_CLI_PIN_TEXT_SHARP"),
        )
        if node and sharp:
            run(
                label + "-png",
                [
                    node,
                    tools / "render_svg.cjs",
                    output / "ports.svg",
                    output / "full-page.png",
                    sharp,
                ],
            )

    native("05-candidate", candidate, edges)
    cli("06-build-save", *args, "--confirm", prepared["confirm_token"])
    first = query("07-saved-query")
    first_sheet = next(row for row in first["items"] if row["kind"] == "sheet")
    native("08-saved", work, edges)
    child_before = (work / "child.kicad_sch").read_bytes()
    symbols_before = _symbols(first)
    raw_symbols_before = _raw_symbols(first, work)
    rotated = ["top", "bottom", "right", "left"]
    # New offsets avoid a transient collinear overlap with another old stub
    # during the explicitly ordered edits; no overlap guard is relaxed.
    new_ends = [[135.56, 39.84], [135.56, 120.16], [170.16, 85.56], [89.84, 85.56]]
    current_wires = [
        next(
            row
            for row in first["items"]
            if row["kind"] == "wire" and row["geometry"]["points_mm"][-1] == end
        )
        for end in endpoints
    ]
    end_labels = [
        next(
            row
            for row in first["items"]
            if row["kind"] == "label"
            and row["text"] == net
            and row["geometry"]["coordinate_mm"] == end
        )
        for net, end in zip(nets, endpoints, strict=True)
    ]
    moves = [
        {
            "op": "update_sheet",
            "target": first_sheet["selector"],
            "at_mm": [100, 50],
            "size_mm": [60, 60],
            "pin_positions": [{"edge": edge, "offset_mm": 35.56} for edge in rotated],
        }
    ]
    for i, (wire, label, end) in enumerate(zip(current_wires, end_labels, new_ends, strict=True)):
        moves.extend(
            [
                {
                    "op": "update",
                    "target": wire["selector"],
                    "points": [
                        {
                            "sheet_pin": {
                                "selector": first_sheet["selector"],
                                "instance_path": page,
                                "sheet_pin_ordinal": i,
                            }
                        },
                        end,
                    ],
                },
                {
                    "op": "update",
                    "target": label["selector"],
                    "at": {"object": wire["selector"], "instance_path": page, "endpoint": 1},
                },
            ]
        )
    updated = {
        "op": "circuit",
        "snapshot": {
            "root": first["extensions"]["root"],
            "snapshot": first["snapshot"]["revision"],
        },
        "affected_instances": [page],
        "actions": moves,
        "expected": {"components": []},
    }
    args2, preview2, candidate2 = edit("09-move", updated)
    native("10-candidate-moved", candidate2, rotated)
    cli("11-move-save", *args2, "--confirm", preview2["confirm_token"])
    final = query("12-reopened-query")
    final_sheet = next(row for row in final["items"] if row["kind"] == "sheet")
    assert final_sheet["uuid"] == first_sheet["uuid"]
    old_pins, new_pins = (
        sheet["instances"][0]["sheet_pins"] for sheet in (first_sheet, final_sheet)
    )
    assert [
        {k: pin[k] for k in ("uuid", "name", "direction", "sheet_pin_ordinal")} for pin in old_pins
    ] == [
        {k: pin[k] for k in ("uuid", "name", "direction", "sheet_pin_ordinal")} for pin in new_pins
    ]
    assert (work / "child.kicad_sch").read_bytes() == child_before
    assert _symbols(final) == symbols_before
    assert _raw_symbols(final, work) == raw_symbols_before
    native("13-saved-moved", work, rotated)
    _json(
        tmp_path / "NATIVE-RESULT.json",
        {
            "status": "passed",
            "source_runtime_only": not bool(binary),
            "native_verification": verification,
            "public_calls": calls,
            "electrical_intent_authored_before_preview": True,
            "pin_and_symbol_identity_preserved": True,
            "full_page_pngs_require_actual_view": True,
            "product_source_sha256": hashlib.sha256(
                (REPO / "kicad_cli/sch_hierarchy_build.py").read_bytes()
            ).hexdigest(),
        },
    )
