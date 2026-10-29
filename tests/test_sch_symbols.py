"""Closed, bounded symbol resource discovery; all inputs are owned source fixtures."""

from __future__ import annotations

import hashlib

import pytest

from kicad_cli import kicad_env
from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_symbols import Resources, capture_resources, query
from kicad_cli.sync_inputs import Drift, InputError

LIB = """(kicad_symbol_lib (version 20250114) (generator "fixture")
 (symbol "BASE" (property "Reference" "R" (at 0 0 0))
  (property "Value" "BASE" (at 0 0 0))
  (symbol "BASE_1_1"
   (pin passive line (at -2 0 0) (length 1) (name "DUP") (number "7"))
   (pin passive line (at 2 0 180) (length 1) (name "DUP") (number "7"))))
 (symbol "CHILD" (extends "BASE") (property "Value" "CHILD" (at 0 0 0))))"""


@pytest.fixture
def resources(tmp_path, monkeypatch):
    monkeypatch.setattr(kicad_env, "find_kicad_root", lambda: None)
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "owned.kicad_sch"
    root.write_bytes(b'(kicad_sch (version 20250114) (uuid "root") (lib_symbols))\n')
    library = tmp_path / "symbols.kicad_sym"
    library.write_text(LIB, encoding="utf-8")
    included = tmp_path / "included-table"
    included.write_text(
        '(sym_lib_table (lib (name "Local") (type "KiCad") '
        '(uri "${KIPRJMOD}/symbols.kicad_sym") (options "") (descr "")))',
        encoding="utf-8",
    )
    (tmp_path / "sym-lib-table").write_text(
        '(sym_lib_table (lib (name "all") (type "Table") '
        '(uri "${KIPRJMOD}/included-table") (options "") (descr "")))',
        encoding="utf-8",
    )
    return root, included, library


def test_queries_have_explicit_discovery_levels_and_pagination(resources):
    root, _, _ = resources
    libraries = query(root)
    assert libraries["mode"] == "library_identities"
    assert libraries["items"][0]["library_id"] == "Local"
    assert libraries["items"][0]["pins"] == []
    assert all("symbols.kicad_sym" not in row["path"] for row in libraries["inputs"])
    symbols = query(root, library="Local", limit=1)
    assert symbols["mode"] == "symbol_identities" and symbols["has_more"]
    assert symbols["next_offset"] == 1 and symbols["items"][0]["symbol_id"] == "Local:BASE"
    second = query(root, library="Local", offset=1, limit=1)
    assert second["items"][0]["symbol_id"] == "Local:CHILD" and not second["has_more"]


def test_complete_descriptor_preserves_repeated_pin_number_and_parent_geometry(resources):
    root, _, library = resources
    data = query(root, symbol="Local:CHILD")
    descriptor = data["items"][0]
    assert descriptor["detail_level"] == "pin_geometry"
    assert descriptor["units"] == [1] and descriptor["body_styles"] == [1]
    assert [p["number"] for p in descriptor["pins"]] == ["7", "7"]
    assert [p["pin_ordinal"] for p in descriptor["pins"]] == [0, 1]
    assert [p["position_nm"] for p in descriptor["pins"]] == [[-2_000_000, 0], [2_000_000, 0]]
    assert descriptor["properties"]["Value"] == "CHILD"
    assert descriptor["source_file"] == str(library)


def test_every_included_table_and_selected_resource_is_bound(resources):
    root, included, library = resources
    capture = capture_resources(root, ["Local:BASE"])
    assert included in capture.files and library in capture.files
    assert root.parent / "sym-lib-table" in capture.files
    included.write_text(included.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(Drift, match="identity changed"):
        capture.validate()


def test_closed_capture_uses_original_paths_and_cannot_read_an_undeclared_file(resources):
    root, included, library = resources
    capture = capture_resources(root, ["Local:BASE"])
    closed = Resources(root, captured_files=dict(capture.files))
    before = closed.resolve("Local:BASE")[1]
    library.write_text("THIS DISK CONTENT MUST NOT BE READ", encoding="utf-8")
    assert closed.resolve("Local:BASE")[1] == before
    closed.validate()
    missing = dict(capture.files)
    missing.pop(included)
    with pytest.raises(InputError, match="undeclared captured input"):
        Resources(root, captured_files=missing)


def test_added_library_file_and_missing_file_have_distinct_rechecks(resources):
    root, _, library = resources
    capture = capture_resources(root, ["Local:BASE"])
    library.unlink()
    with pytest.raises(Drift):
        capture.validate()


@pytest.mark.parametrize(
    "symbol", ["missing-colon", "Local:", ":BASE", "Local:../BASE", "Local:X:Y"]
)
def test_invalid_library_identity_never_becomes_a_path(resources, symbol):
    with pytest.raises(InputError):
        query(resources[0], symbol=symbol)


def test_missing_symbol_and_filter_disagreement_are_diagnostic(resources):
    with pytest.raises(InputError, match="no such symbol"):
        query(resources[0], symbol="Local:ABSENT")
    with pytest.raises(InputError, match="disagree"):
        query(resources[0], library="Other", symbol="Local:BASE")


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": []}])
def test_public_bounds_are_not_coerced(resources, kwargs):
    with pytest.raises(InputError):
        query(resources[0], **kwargs)


@pytest.mark.parametrize(
    "inheritance",
    [
        '(symbol "CHILD" (extends "MISSING"))',
        '(symbol "CHILD" (extends "MID")) (symbol "MID" (extends "BASE"))',
        '(symbol "CHILD" (extends "CHILD"))',
        '(symbol "CHILD" (extends "MID")) (symbol "MID" (extends "CHILD"))',
    ],
)
def test_unproved_inheritance_is_discoverable_but_not_exact(resources, inheritance):
    root, _, library = resources
    library.write_text(
        '(kicad_symbol_lib (version 20250114) (symbol "BASE") ' + inheritance + ")",
        encoding="utf-8",
    )
    identities = query(root, library="Local")
    assert any(row["symbol_id"] == "Local:CHILD" for row in identities["items"])
    with pytest.raises(InputError):
        query(root, symbol="Local:CHILD")


CACHED = """(lib_symbols
 (symbol "Local:BASE" (property "Value" "cached" (at 0 0 0))
  (symbol "BASE_0_1" (pin passive line (at 0 0 0) (length 1) (name "COMMON") (number "8")))
  (symbol "BASE_1_1" (pin passive line (at 7.62 2.54 0) (length 1) (name "CACHE") (number "9")))
  (symbol "BASE_2_1" (pin passive line (at -7.62 -2.54 0) (length 1) (name "SECOND") (number "9"))))
 (symbol "OnlyHere:MEM" (symbol "MEM_1_1"
  (pin passive line (at 1.27 0 0) (length 1) (name "ONLY") (number "5")))))"""


def cached_root(root, extra=""):
    root.write_text(
        '(kicad_sch (version 20250114) (uuid "root") ' + CACHED + extra + ")",
        encoding="utf-8",
    )


def test_exact_symbol_query_matches_placement_embedded_priority(resources):
    root, _, _ = resources
    cached_root(root)
    actual = query(root, symbol="Local:BASE")
    item = actual["items"][0]
    assert item["origin"] == "embedded" and item["source_file"] == str(root)
    assert item["source_sha256"] == hashlib.sha256(root.read_bytes()).hexdigest()
    assert item["units"] == [1, 2] and item["body_styles"] == [1]
    assert [p["pin_ordinal"] for p in item["pins"]] == [0, 1, 2]
    assert [p["number"] for p in item["pins"]] == ["8", "9", "9"]
    assert item["pins"][1]["position_nm"] == [7_620_000, 2_540_000]
    assert actual["resolution"]["target_instance_path"] == "/root"
    capture = capture_resources(root, [("Local:BASE", "/root")])
    resolved, written = capture.resolve("Local:BASE", embedded=Design(root).instances[0].schematic)
    assert written == item and resolved.symbol.pins[1].position == (7_620_000, 2_540_000)


def test_cache_only_symbols_are_discoverable_at_both_identity_levels(resources):
    root, _, _ = resources
    cached_root(root)
    libraries = query(root)["items"]
    cached_library = next(i for i in libraries if i["library_id"] == "OnlyHere")
    assert cached_library["origin"] == "embedded" and cached_library["library_type"] == "Embedded"
    symbols = query(root, library="OnlyHere")["items"]
    assert [i["symbol_id"] for i in symbols] == ["OnlyHere:MEM"]
    assert symbols[0]["origin"] == "embedded"
    assert query(root, symbol="OnlyHere:MEM")["items"][0]["pins"][0]["number"] == "5"
    merged = query(root, library="Local")["items"]
    assert [(i["symbol_id"], i["origin"]) for i in merged] == [
        ("Local:BASE", "embedded"),
        ("Local:CHILD", "library"),
    ]


def test_exact_instance_page_selects_its_source_cache_with_shared_occurrences(resources):
    root, _, _ = resources

    def sheet(uid, name):
        return (
            f'(sheet (at 10 10) (size 10 10) (uuid "{uid}") '
            f'(property "Sheetname" "{name}") (property "Sheetfile" "child.kicad_sch"))'
        )

    root.write_text(
        '(kicad_sch (version 20250114) (uuid "root") (lib_symbols) '
        + sheet("a", "A")
        + sheet("b", "B")
        + ")",
        encoding="utf-8",
    )
    child = root.parent / "child.kicad_sch"
    child.write_text(
        '(kicad_sch (version 20250114) (uuid "child") ' + CACHED + ")", encoding="utf-8"
    )
    root_descriptor = query(root, symbol="Local:BASE")["items"][0]
    assert root_descriptor["origin"] == "library"
    assert root_descriptor["pins"][0]["position_nm"] == [-2_000_000, 0]
    a = query(root, symbol="Local:BASE", page="/root/a")
    b = query(root, symbol="Local:BASE", page="/root/b")
    assert a["items"] == b["items"] and a["items"][0]["origin"] == "embedded"
    assert a["resolution"]["target_source_file"] == str(child)
    assert b["resolution"]["target_instance_path"] == "/root/b"
    assert "OnlyHere" in [i["library_id"] for i in query(root, page="/root/b")["items"]]


@pytest.mark.parametrize("page", ["/wrong", "child", "2", [], 2])
def test_unknown_or_non_instance_page_refuses_before_library_resolution(resources, page):
    with pytest.raises(InputError, match="full instance_path"):
        query(resources[0], symbol="Local:BASE", page=page)
