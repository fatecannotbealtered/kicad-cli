"""Derived library caches preserve native field inheritance, not just pin projections."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kicad_cli.fileformat.sexpr import Document, List
from kicad_cli.fileformat.symbols import _flatten


def flattened(source, name="Child"):
    library = Document.parse(source)
    nodes = {n.value(1): n for n in library.root.find_all("symbol")}
    before = library.dumps()
    result = _flatten(library, nodes[name], "Fixture:" + name, name, lambda _, key: nodes.get(key))
    assert library.dumps() == before
    return result, nodes


def properties(node):
    return {p.value(1): p.value(2) for p in node.find_all("property")}


def tree(node):
    return [tree(item) if isinstance(item, List) else item for item in node.items]


@pytest.mark.parametrize("name", ["Reference", "Value", "Footprint", "Datasheet", "Description"])
def test_empty_derived_mandatory_text_inherits_the_complete_parent_field(name):
    result, nodes = flattened(
        "(kicad_symbol_lib (version 20250114) "
        f'(symbol "Parent" (property "{name}" "parent text" (at 1 2 90) (hide yes))) '
        f'(symbol "Child" (extends "Parent") (property "{name}" "" (at 9 8 0))))'
    )
    actual = result.find_all("property")
    assert len(actual) == 1
    assert tree(actual[0]) == tree(nodes["Parent"].find("property"))


def test_mandatory_identity_is_case_insensitive_but_custom_identity_is_not():
    result, _ = flattened(
        "(kicad_symbol_lib (version 20250114) "
        '(symbol "Parent" (property "Footprint" "Parent:Package") '
        '(property "Custom" "parent") (property "custom" "distinct")) '
        '(symbol "Child" (extends "Parent") (property "fOoTpRiNt" "") '
        '(property "Custom" "")))'
    )
    assert properties(result) == {"Footprint": "Parent:Package", "custom": "distinct", "Custom": ""}


def test_nonempty_child_fields_override_while_native_metadata_empty_text_inherits():
    result, _ = flattened(
        "(kicad_symbol_lib (version 20250114) "
        '(symbol "Parent" (property "Footprint" "Old:Package") '
        '(property "ki_keywords" "parent keywords") (property "ki_fp_filters" "SOIC*")) '
        '(symbol "Child" (extends "Parent") (property "Footprint" "New:Package") '
        '(property "ki_keywords" "") (property "ki_fp_filters" "")))'
    )
    assert properties(result) == {
        "Footprint": "New:Package",
        "ki_keywords": "parent keywords",
        "ki_fp_filters": "SOIC*",
    }


def test_flat_symbol_keeps_empty_fields_flags_and_all_graphic_subtrees():
    source = (
        "(kicad_symbol_lib (version 20250114) "
        '(symbol "Child" (pin_names (offset 0.508)) (exclude_from_sim no) '
        '(in_bom yes) (on_board yes) (property "Footprint" "") '
        '(symbol "Child_0_1" (rectangle (start -1 1) (end 1 -1) '
        "(stroke (width 0.254) (type default)) (fill (type background)))) "
        '(symbol "Child_1_1" (pin passive line (at 0 2 270) (length 1) '
        '(name "P") (number "1"))) (embedded_fonts no)))'
    )
    result, nodes = flattened(source)
    assert properties(result)["Footprint"] == ""
    assert result.find("pin_names").value() is None
    assert tree(result.find("pin_names")) == tree(nodes["Child"].find("pin_names"))
    assert [tree(n) for n in result.find_all("symbol")] == [
        tree(n) for n in nodes["Child"].find_all("symbol")
    ]


def test_derived_units_only_change_the_symbol_name_prefix():
    result, nodes = flattened(
        "(kicad_symbol_lib (version 20250114) "
        '(symbol "Parent" (property "Reference" "U") '
        '(symbol "Parent_0_1" (circle (center 0 0) (radius 1))) '
        '(symbol "Parent_1_1" (pin passive line (at 0 2 270) (length 1) '
        '(name "P") (number "1")))) '
        '(symbol "Child" (extends "Parent") (property "Value" "Child")))'
    )
    for copied, original in zip(
        result.find_all("symbol"), nodes["Parent"].find_all("symbol"), strict=True
    ):
        assert copied.value(1) == original.value(1).replace("Parent", "Child", 1)

        def body(node):
            return [tree(v) if isinstance(v, List) else v for v in node.items[2:]]

        assert body(copied) == body(original)


@pytest.mark.parametrize("name", ["MAX3485", "MAX488E"])
def test_declared_installed_aliases_inherit_their_actual_nonempty_parent_footprint(name):
    # Optional native-resource evidence. Pure synthetic tests above remain
    # portable and do not redistribute the installation's library files.
    root = Path("D:/software/kicad/home/share/kicad/symbols/Interface_UART.kicad_sym")
    if not root.is_file():
        pytest.skip("Declared legal KiCad10.0.6 symbol installation is unavailable")
    library = Document.load(root)
    nodes = {n.value(1): n for n in library.root.find_all("symbol")}
    raw = nodes[name]
    parent = nodes[raw.find("extends").value(1)]
    assert properties(raw)["Footprint"] == ""
    assert properties(parent)["Footprint"]
    result = _flatten(library, raw, "Interface_UART:" + name, name, lambda _, key: nodes.get(key))
    assert properties(result)["Footprint"] == properties(parent)["Footprint"]


def test_public_placement_saved_reopen_matches_native_inherited_and_flat_symbols(tmp_path):
    cad = Path(os.environ.get("KICAD_CLI_FIDELITY_KICAD_ROOT", "D:/software/kicad/home"))
    official = cad / "bin" / "kicad-cli.exe"
    if not official.is_file():
        pytest.skip("Declared legal KiCad10.0.6 native installation is unavailable")
    repo = Path(__file__).resolve().parents[1]
    work, profile, evidence = (tmp_path / name for name in ("work", "profile", "evidence"))
    work.mkdir()
    evidence.mkdir()
    config = tmp_path / "runtime-config.json"
    config.write_text(
        json.dumps({"format_version": 1, "kicad_root": str(cad), "profile_dir": str(profile)}),
        encoding="utf-8",
    )
    # Native ERC's footprint check needs the matching legal table too. This
    # is isolated configuration preparation, not a generated/custom library.
    prefs = profile / "kicad" / "10.0"
    prefs.mkdir(parents=True)
    for name in ("sym-lib-table", "fp-lib-table"):
        shutil.copyfile(cad / "share" / "kicad" / "template" / name, prefs / name)
    scope = {
        "scope_id": "source-symbol-cache-fidelity",
        "tool": "kicad-cli",
        "granted_by": "isolated regression fixture",
        "granted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at": None,
        "effects": ["observe", "change", "persist"],
        "dangerous": False,
        "roots": [str(tmp_path.resolve())],
        "outputs": [str(tmp_path.resolve())],
        "limits": {"max_operations": 2},
    }
    (profile / "scope.json").write_text(json.dumps(scope), encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("KICAD") and key not in {"PYTHONPATH", "PYTHONHOME", "KIPRJMOD"}
    }
    env.update(KICAD_CLI_RUNTIME_CONFIG=str(config), PYTHONIOENCODING="utf-8")
    records = []
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]

    def public(label, *args):
        result = subprocess.run(
            [*prefix, *map(str, args), "--compact"],
            cwd=tmp_path if binary else repo,
            env=env,
            capture_output=True,
            timeout=90,
        )
        (evidence / (label + ".stdout")).write_bytes(result.stdout)
        (evidence / (label + ".stderr")).write_bytes(result.stderr)
        data = json.loads(result.stdout)
        assert result.returncode == 0 and data["ok"], data
        records.append(
            {"label": label, "code": result.returncode, "public_command": list(args[:2])}
        )
        return data["data"]

    def native(label, *args):
        isolated = dict(env)
        isolated.update(
            KICAD_CONFIG_HOME=str(profile / "kicad"),
            HOME=str(profile / "home"),
            USERPROFILE=str(profile / "home"),
            APPDATA=str(profile / "appdata"),
            LOCALAPPDATA=str(profile / "localappdata"),
            TEMP=str(profile / "tmp"),
            TMP=str(profile / "tmp"),
        )
        result = subprocess.run(
            [str(official), *map(str, args)],
            cwd=work,
            env=isolated,
            capture_output=True,
            timeout=60,
        )
        (evidence / (label + ".stdout")).write_bytes(result.stdout)
        (evidence / (label + ".stderr")).write_bytes(result.stderr)
        assert result.returncode == 0, (label, result.returncode)
        return result

    blank = tmp_path / "blank.json"
    blank.write_text('{"mode":"blank","title":"Fidelity regression"}', encoding="utf-8")
    args = ["sch", "create", "--spec", blank, "--out", work]
    preview = public("01-blank-preview", *args, "--dry-run")
    public("02-blank-confirm", *args, "--confirm", preview["confirm_token"])
    root = work / "blank.kicad_sch"
    # Initialize all actual consumers before the editable snapshot is queried.
    native(
        "03-native-prepare",
        "sch",
        "erc",
        "--format",
        "json",
        "--output",
        evidence / "blank-erc.json",
        root,
    )
    objects = public("04-empty-objects", "sch", "objects", "--schematic", root)
    page = objects["extensions"]["pages"][0]["instance_path"]
    actions, expected, intent = [], [], []
    for index, (symbol, ref) in enumerate(
        [("Interface_UART:MAX3485", "U1"), ("Interface_UART:MAX488E", "U2"), ("Device:R", "R1")]
    ):
        queried = public(
            f"05-symbol-{index}", "sch", "symbols", "--schematic", root, "--symbol", symbol
        )["items"][0]
        alias = f"part{index}"
        actions.append(
            {
                "op": "place_symbol",
                "id": alias,
                "page": page,
                "symbol": symbol,
                "unit": 1,
                "ref": ref,
                "at_mm": [[50.8, 114.3, 177.8][index], 63.5],
            }
        )
        for pin in queried["pins"]:
            ordinal = pin["pin_ordinal"]
            nc = f"nc{index}_{ordinal}"
            member = {"new": alias, "instance_path": page, "pin_ordinal": ordinal}
            actions.append(
                {
                    "op": "create",
                    "id": nc,
                    "page": page,
                    "kind": "no_connect",
                    "at": {"pin": {"new": alias, "instance_path": page, "pin_ordinal": ordinal}},
                }
            )
            expected.append(
                {
                    "members": [member, {"new": nc, "instance_path": page}],
                    "explicit_names": [],
                    "nc_members": [member],
                }
            )
        intent.append({"symbol": symbol, "ref": ref, "query": queried})
    (evidence / "intent-before-preview.json").write_text(
        json.dumps({"resources": intent, "components": expected}, indent=2), encoding="utf-8"
    )
    changes = tmp_path / "place.json"
    changes.write_text(
        json.dumps(
            {
                "changes": [
                    {
                        "op": "circuit",
                        "snapshot": {
                            "root": objects["extensions"]["root"],
                            "snapshot": objects["snapshot"]["revision"],
                        },
                        "affected_instances": [page],
                        "actions": actions,
                        "expected": {"components": expected},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    edit = ["sch", "edit", "--schematic", root, "--changes", changes]
    placed = public("06-place-preview", *edit, "--dry-run")
    public("07-place-confirm", *edit, "--confirm", placed["confirm_token"])
    saved_hash = hashlib.sha256(root.read_bytes()).hexdigest()
    reopened = public("08-reopen-objects", "sch", "objects", "--schematic", root)
    saved_symbols = [row for row in reopened["items"] if row["kind"] == "symbol"]
    assert len(saved_symbols) == 3
    identities = sorted((row["uuid"], row["geometry"]) for row in saved_symbols)
    for index, source in enumerate(intent):
        embedded = public(
            f"09-embedded-{index}",
            "sch",
            "symbols",
            "--schematic",
            root,
            "--symbol",
            source["symbol"],
        )["items"][0]
        for field in ("pins", "properties", "units", "body_styles", "power", "local_power"):
            assert embedded[field] == source["query"][field]
    native(
        "10-native-erc",
        "sch",
        "erc",
        "--format",
        "json",
        "--output",
        evidence / "saved-erc.json",
        root,
    )
    native(
        "11-native-netlist",
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadxml",
        "--output",
        evidence / "saved.xml",
        root,
    )
    erc = json.loads((evidence / "saved-erc.json").read_bytes())
    violations = [v for sheet in erc["sheets"] for v in sheet["violations"]]
    assert not any(v["type"] == "lib_symbol_mismatch" for v in violations), violations
    assert not violations, violations
    xml = (evidence / "saved.xml").read_text(encoding="utf-8")
    assert all(f'ref="{ref}"' in xml for ref in ("U1", "U2", "R1"))
    assert hashlib.sha256(root.read_bytes()).hexdigest() == saved_hash
    final = public("12-final-reopen", "sch", "objects", "--schematic", root)
    assert (
        sorted((r["uuid"], r["geometry"]) for r in final["items"] if r["kind"] == "symbol")
        == identities
    )
    (evidence / "RESULT.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "kind": (
                    "frozen public CLI + native10.0.6 regression"
                    if binary
                    else "source public CLI + native10.0.6 regression"
                ),
                "symbols": [entry["symbol"] for entry in intent],
                "saved_schematic_sha256": saved_hash,
                "native_erc_violations": violations,
                "ignored_checks": erc["ignored_checks"],
                "calls": records,
                "no_existing_design_or_state_used": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
