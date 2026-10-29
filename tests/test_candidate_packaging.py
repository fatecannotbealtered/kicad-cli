"""Package provenance relocation must preserve archived inputs and other bytes."""

import hashlib
import json
import shutil

import pytest

from scripts.build_internal_candidate import relocate_saved_netlist_sources


def prepare(tmp_path, source):
    archive = tmp_path / "archive"
    archive.mkdir()
    literal = json.dumps(source).encode()
    raw = b"(export\r\n (design\r\n  (source " + literal + b"))\r\n (nets (net (code 1))))\r\n"
    (archive / "c.net").write_bytes(raw)
    (archive / "c.kicad_sch").write_bytes(b"frozen schematic")
    package = tmp_path / "package"
    copied = package / "saved-examples" / "case"
    shutil.copytree(archive, copied)
    origin = {
        "cases": {
            "case": {
                "files": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in archive.iterdir()
                }
            }
        }
    }
    return archive, package, copied, origin, raw, literal


def test_projection_changes_only_provenance_and_keeps_original(tmp_path):
    archive, package, copied, origin, raw, literal = prepare(
        tmp_path, r"D:\private\old\c.kicad_sch"
    )
    result = relocate_saved_netlist_sources(package, origin)
    assert (archive / "c.net").read_bytes() == raw
    assert (copied / "c.net").read_bytes() == raw.replace(literal, b'"c.kicad_sch"', 1)
    assert (copied / "c.kicad_sch").read_bytes() == b"frozen schematic"
    assert len(result) == 1 and not result[0]["historical_acceptance_inherited"]
    manifest = json.loads((package / "saved-examples/ORIGINS.json").read_bytes())
    assert manifest["cases"]["case"]["files"]["c.net"] == result[0]["package_file_sha256"]
    assert "private" not in json.dumps(manifest)


@pytest.mark.parametrize("source", [r"D:\private\wrong.kicad_sch", "../c.kicad_sch"])
def test_unrelated_or_traversing_source_is_refused(tmp_path, source):
    _, package, copied, origin, raw, _ = prepare(tmp_path, source)
    with pytest.raises(ValueError):
        relocate_saved_netlist_sources(package, origin)
    assert (copied / "c.net").read_bytes() == raw


def test_relative_source_keeps_all_original_bytes(tmp_path):
    _, package, copied, origin, raw, _ = prepare(tmp_path, "c.kicad_sch")
    assert relocate_saved_netlist_sources(package, origin) == []
    assert (copied / "c.net").read_bytes() == raw


def test_missing_source_metadata_cannot_be_silently_accepted(tmp_path):
    _, package, copied, origin, raw, _ = prepare(tmp_path, r"D:\private\c.kicad_sch")
    broken = raw.replace(b"(source ", b"(other ")
    (copied / "c.net").write_bytes(broken)
    origin["cases"]["case"]["files"]["c.net"] = hashlib.sha256(broken).hexdigest()
    with pytest.raises(ValueError, match="unambiguous"):
        relocate_saved_netlist_sources(package, origin)
    assert (copied / "c.net").read_bytes() == broken


def test_manifest_traversal_cannot_select_an_external_target(tmp_path):
    _, package, copied, origin, raw, _ = prepare(tmp_path, r"D:\private\c.kicad_sch")
    origin["cases"]["../external"] = origin["cases"].pop("case")
    with pytest.raises(ValueError, match="Unsafe"):
        relocate_saved_netlist_sources(package, origin)
    assert (copied / "c.net").read_bytes() == raw
