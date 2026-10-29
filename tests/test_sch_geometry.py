"""Pure-source geometry preparation and exact preservation checks."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat.circuit import Design  # noqa: E402
from kicad_cli.fileformat.schematic import Schematic  # noqa: E402
from kicad_cli.fileformat.sexpr import Document  # noqa: E402
from kicad_cli.sch_geometry import GeometryError, Transform, _mm, _ticks, run_geometry  # noqa: E402


def uid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "geometry-test/" + name))


def source(angle: int = 0, mirror: str | None = None) -> str:
    # Endpoints are expressed from explicit sheet orientation tables so the
    # fixture does not generate its wiring through the production transform.
    vectors = {0: (-2, 0), 90: (0, 2), 180: (2, 0), 270: (0, -2)}
    dx, dy = vectors[angle]
    if mirror == "x":
        dy = -dy
    elif mirror == "y":
        dx = -dx
    pin = (10 + dx, 10 + dy)
    far = (10 + 3 * dx, 10 + 3 * dy)
    branch = (far[0] + dy, far[1] - dx)
    nc = (10 - dx, 10 - dy)
    mirrored = f"(mirror {mirror})" if mirror else ""
    return f'''\ufeff(kicad_sch
 (version 20250114) (generator "geometry fixture")
 (uuid "{uid("root")}") (paper "A4")
 (lib_symbols
  (symbol "fixture:UNIT"
   (property "Reference" "U" (at 0 0 0))
   (symbol "UNIT_1_1"
    (pin passive line (at -2 0 0) (length 1) (name "LEFT") (number "1"))
    (pin passive line (at 2 0 180) (length 1) (name "RIGHT") (number "2")))
   (symbol "UNIT_2_1"
    (pin passive line (at -2 0 0) (length 1) (name "OTHER") (number "3")))))
 (symbol (lib_id "fixture:UNIT") (at\t10 10 {angle}) {mirrored} (unit 1)
  (in_bom yes) (on_board yes) (dnp no) (uuid "{uid("unit1")}")
  (property "Reference" "U1" (at 10.1234 9.9999 0) (effects (font (size 1.27 1.27))))
  (property "Value" "sentinel exact  spaces" (at 10 11 90) (hide yes))
  (pin "1" (uuid "{uid("pin1")}"))
  (instances (project "geometry" (path "/{uid("root")}" (reference "U1") (unit 1)))))
 (symbol (lib_id "fixture:UNIT") (at 50 50 90) (unit 2) (uuid "{uid("unit2")}")
  (property "Reference" "U1" (at 50 49 0))
  (property "Value" "untouched sibling unit" (at 50 51 0))
  (instances (project "geometry" (path "/{uid("root")}" (reference "U1") (unit 2)))))
 (wire (pts (xy {pin[0]} {pin[1]})   (xy {far[0]} {far[1]}))
  (stroke (width 0) (type default)) (uuid "{uid("wire")}"))
 (wire (pts (xy {far[0]} {far[1]}) (xy {branch[0]} {branch[1]}))
  (stroke (width 0) (type default)) (uuid "{uid("branch")}"))
 (junction (at {far[0]} {far[1]}) (diameter 0) (color 0 0 0 0) (uuid "{uid("junction")}"))
 (label "SIGNAL" (at {branch[0]} {branch[1]} 90)
  (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{uid("label")}"))
 (no_connect (at {nc[0]} {nc[1]}) (uuid "{uid("nc")}"))
)
'''.replace("\n", "\r\n")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "geometry.kicad_sch"
    root.write_bytes(source().encode("utf-8"))
    return root


def catalog(root: Path):
    from kicad_cli.sch_objects import Catalog

    return Catalog(Design(root))


def selection(root: Path, names: tuple[str, ...] | None = None) -> list[dict]:
    names = names or ("unit1", "wire", "branch", "junction", "label", "nc")
    wanted = {uid(name) for name in names}
    return [
        obj.record["selector"]
        for obj in catalog(root).objects
        if obj.node.find("uuid") is not None and obj.node.find("uuid").value(1) in wanted
    ]


def change(root: Path, **extra) -> dict:
    return {
        "op": "transform",
        "objects": selection(root),
        "translate_mm": [5, -2],
        "affected_instances": ["/" + uid("root")],
        **extra,
    }


def node_text(sch: Schematic, kind: str, uuid: str | None = None) -> str:
    node = next(
        item
        for item in sch.root.find_all(kind)
        if uuid is None or (item.find("uuid") is not None and item.find("uuid").value(1) == uuid)
    )
    return sch.document.source[node.start : node.end]


def candidate(root: Path, sources: dict[Path, str]) -> Schematic:
    return Schematic(Document.parse(sources[root.resolve()]), root)


def rejected(root: Path, changes: list, capsys, code: str = "E_VALIDATION") -> dict:
    before = {path: path.read_bytes() for path in root.parent.glob("*.kicad_sch")}
    with pytest.raises(SystemExit):
        run_geometry(root, changes)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False, result
    assert result["error"]["code"] == code, result
    assert {path: path.read_bytes() for path in before} == before
    return result


def test_decimal_quantum_is_exact_and_rejects_rounding() -> None:
    assert _ticks("-0.0001", "/x") == -1
    assert _ticks(0.1, "/x") == 1000
    assert _mm(_ticks("9999.9999", "/x")) == "9999.9999"
    assert _ticks("-10000", "/x") == -100_000_000
    assert _ticks("10000.0000", "/x") == 100_000_000
    assert _mm(0) == "0"
    for value in (
        "0.00001",
        "NaN",
        "Infinity",
        True,
        "10000.0001",
        "-10000.0001",
        "151849",  # KiCad10.0.6 would clamp a file coordinate here.
        "1000000",
        "1e1000000",
        "1e-1000000",
    ):
        with pytest.raises(GeometryError):
            _ticks(value, "/x")


def test_transform_uses_counterclockwise_screen_rotation_then_translation() -> None:
    transform = Transform.of(
        {"rotate_deg": 90, "pivot_mm": [10, 20], "translate_mm": [1, -2]}, "/change"
    )
    assert transform.point((_ticks(12, "/x"), _ticks(23, "/y"))) == (
        _ticks(14, "/x"),
        _ticks(16, "/y"),
    )


def test_connected_t_wire_label_junction_and_nc_translate_together(root: Path) -> None:
    before = root.read_bytes()
    original = Schematic.load(root)
    report, sources = run_geometry(root, [change(root)])
    assert report["ok"] is True, report
    assert all(value for value in report["verified"].values() if isinstance(value, bool))
    edited = candidate(root, sources)
    symbols = {symbol.uuid: symbol for symbol in edited.symbols}
    assert symbols[uid("unit1")].position == (15_000_000, 8_000_000)
    assert symbols[uid("unit1")].angle == 0
    assert symbols[uid("unit2")].position == (50_000_000, 50_000_000)
    assert edited.junctions == [(9_000_000, 8_000_000)]
    assert edited.no_connects == [(17_000_000, 8_000_000)]
    assert edited.labels[0].position == (9_000_000, 10_000_000)
    assert node_text(edited, "lib_symbols") == node_text(original, "lib_symbols")
    assert node_text(edited, "symbol", uid("unit2")) == node_text(original, "symbol", uid("unit2"))
    assert "(at\t15 8 0)" in sources[root.resolve()]
    assert '"sentinel exact  spaces"' in sources[root.resolve()]
    assert sources[root.resolve()].startswith("\ufeff")
    assert "\n" not in sources[root.resolve()].replace("\r\n", "")
    assert root.read_bytes() == before


@pytest.mark.parametrize("mirror", [None, "x", "y"])
@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotation_composes_existing_symbol_mirror_and_fields(
    root: Path, mirror: str | None, angle: int, rotation: int
) -> None:
    root.write_bytes(source(angle, mirror).encode("utf-8"))
    original = Schematic.load(root)
    sym_before = next(symbol for symbol in original.symbols if symbol.uuid == uid("unit1"))
    report, sources = run_geometry(
        root,
        [change(root, rotate_deg=rotation, pivot_mm=[10, 10], translate_mm=[0.0001, -0.0001])],
    )
    assert report["ok"] is True, report
    edited = candidate(root, sources)
    sym_after = next(symbol for symbol in edited.symbols if symbol.uuid == uid("unit1"))
    assert sym_after.mirror == mirror
    for pin in original.library["fixture:UNIT"].pins_of(1, 1):
        x, y = sym_before.transform(pin.position)
        x, y = x - 10_000_000, y - 10_000_000
        expected = {
            0: (10_000_100 + x, 9_999_900 + y),
            90: (10_000_100 + y, 9_999_900 - x),
            180: (10_000_100 - x, 9_999_900 - y),
            270: (10_000_100 - y, 9_999_900 + x),
        }[rotation]
        assert sym_after.transform(pin.position) == expected
    reference = sym_after.node.find_all("property")[0].find("at")
    if rotation == 90:
        assert reference.values() == ["10", "9.8765", "90"]
    assert node_text(edited, "symbol", uid("unit2")) == node_text(original, "symbol", uid("unit2"))


@pytest.mark.parametrize("mirror", [None, "x", "y"])
@pytest.mark.parametrize("old_field_angle", [0, 90, 180, 270])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_symbol_field_angles_are_upright_on_rotation_and_exact_on_translation(
    root: Path, mirror: str | None, old_field_angle: int, rotation: int
) -> None:
    text = source(90, mirror).replace("10.1234 9.9999 0", f"10.1234 9.9999 {old_field_angle}.000")
    root.write_bytes(text.encode("utf-8"))
    report, sources = run_geometry(root, [change(root, rotate_deg=rotation, pivot_mm=[10, 10])])
    assert report["ok"], report
    edited = candidate(root, sources)
    symbol = next(symbol for symbol in edited.symbols if symbol.uuid == uid("unit1"))
    field = symbol.node.find_all("property")[0]
    next_angle = (old_field_angle + rotation) % 180 if rotation else old_field_angle
    expected = str(next_angle) if next_angle != old_field_angle else f"{old_field_angle}.000"
    assert field.find("at").atom(3) == expected
    # A label's angle carries its orientation instead of the symbol-field policy.
    assert edited.labels[0].angle == (90 + rotation) % 360
    assert report["geometry"]["policy"]["symbol_fields_normalized_upright_on_rotation"]


def test_detaching_a_unit_from_its_wires_fails_the_combined_guard(root: Path) -> None:
    before = root.read_bytes()
    report, sources = run_geometry(root, [change(root, objects=selection(root, ("unit1",)))])
    assert report["ok"] is False
    assert report["verified"]["connections_preserved"] is False
    assert report["geometry"]["connections"]["differences"]
    assert sources
    assert root.read_bytes() == before


@pytest.mark.parametrize(
    "edit",
    [
        {"translate_mm": [0.00001, 0]},
        {"rotate_deg": 45, "pivot_mm": [10, 10]},
        {"rotate_deg": 90},
        {"objects": []},
        {"affected_instances": []},
        {"affected_instances": ["/" + uid("root"), "/" + uid("root")]},
        {"unexpected": True},
    ],
)
def test_invalid_transform_is_refused_before_any_file_write(root: Path, edit, capsys) -> None:
    rejected(root, [change(root, **edit)], capsys)


def test_a_batch_resolves_one_original_snapshot_and_forbids_duplicate_source_objects(
    root: Path, capsys
) -> None:
    rejected(root, [change(root), change(root)], capsys)
    rejected(root, [change(root), {"op": "set", "ref": "U1", "value": "new"}], capsys)


def test_disjoint_operations_use_original_selectors_and_one_combined_guard(root: Path) -> None:
    symbol = change(root, objects=selection(root, ("unit1",)))
    attached = change(root, objects=selection(root, ("wire", "branch", "junction", "label", "nc")))
    snapshot = symbol["objects"][0]["snapshot"]
    assert all(item["snapshot"] == snapshot for item in attached["objects"])
    report, sources = run_geometry(root, [symbol, attached])
    assert report["ok"] is True, report
    assert len(report["changes"]) == 2
    assert candidate(root, sources).symbols[0].position == (15_000_000, 8_000_000)


def test_one_invalid_later_operation_prevents_candidate_measurement(
    root: Path, capsys, monkeypatch
):
    import kicad_cli.sch_geometry_graph as guard

    def unexpected(*args, **kwargs):
        raise AssertionError("a rejected batch must not be measured as a partial candidate")

    monkeypatch.setattr(guard, "witness", unexpected)
    rejected(root, [change(root), {"op": "transform", "objects": []}], capsys)


def test_selected_off_grid_field_is_refused_without_rounding(root: Path, capsys) -> None:
    root.write_bytes(source().replace("10.1234 9.9999", "10.12345 9.9999").encode("utf-8"))
    rejected(root, [change(root)], capsys)


@pytest.mark.parametrize("field", ["snapshot", "root", "object_id"])
def test_stale_or_foreign_query_selectors_fail_closed(root: Path, field: str, capsys) -> None:
    given = change(root)
    given["objects"][0] = dict(given["objects"][0], **{field: "foreign"})
    rejected(root, [given], capsys, "E_CONFLICT")


def test_shared_source_requires_explicit_full_instance_union(tmp_path: Path, capsys) -> None:
    fixture = REPO / "tests" / "fixtures" / "sch_edit"
    for name in ("edit.kicad_sch", "edit_child.kicad_sch", "edit.kicad_pro"):
        shutil.copyfile(fixture / name, tmp_path / name)
    root = tmp_path / "edit.kicad_sch"
    obj = next(
        obj for obj in catalog(root).objects if obj.schematic.path.name == "edit_child.kicad_sch"
    )
    assert len(obj.instances) == 2
    result = rejected(
        root,
        [
            {
                "op": "transform",
                "objects": [obj.record["selector"]],
                "translate_mm": [1, 0],
                "affected_instances": [obj.instances[0].path],
            }
        ],
        capsys,
    )
    assert result["error"]["details"]["expected"] == sorted(item.path for item in obj.instances)


@pytest.mark.parametrize("limit_name,maximum", [("MAX_OPERATIONS", 1), ("MAX_SELECTED_OBJECTS", 5)])
def test_batch_resource_bounds_refuse_before_partial_preparation(
    root, capsys, monkeypatch, limit_name, maximum
):
    import kicad_cli.sch_geometry as geometry

    monkeypatch.setattr(geometry, limit_name, maximum)
    changes = [change(root), change(root)] if limit_name == "MAX_OPERATIONS" else [change(root)]
    rejected(root, changes, capsys)


@pytest.mark.parametrize("field", ["translate_mm", "pivot_mm"])
def test_absolute_coordinate_bound_is_enforced(root, capsys, field):
    rejected(root, [change(root, **{field: [10_000.0001, 0]})], capsys)


def test_transformed_output_must_remain_within_coordinate_bound(root, capsys):
    rejected(root, [change(root, translate_mm=[10_000, 0])], capsys)
