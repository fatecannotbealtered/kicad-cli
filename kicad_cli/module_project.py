"""Shared, captured project context and native finding identities for new modules."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from . import kicad_env
from .payload import drc_runner


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("Non-finite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def strict_context(board):
    board = Path(board).resolve()
    project = board.with_suffix(".kicad_pro")
    if not project.is_file():
        raise ValueError("Matching .kicad_pro is required; refusing implicit default rules")
    data = json.loads(project.read_bytes())
    if not isinstance(data, dict) or not isinstance(data.get("board"), dict):
        raise ValueError("Matching project must contain board settings")
    return {
        suffix: sha(p.read_bytes()) if (p := board.with_suffix(suffix)).is_file() else "absent"
        for suffix in (".kicad_pcb", ".kicad_pro", ".kicad_dru")
    }


def check_baseline(actual, expected):
    if actual != expected:
        from .sync_inputs import Drift

        raise Drift(
            "Declared board/project/custom-rule baseline differs",
            changed_files=[k for k in actual if actual[k] != expected.get(k)],
        )


def capture_board_context(board, result=None):
    from .engineering_inputs import Inputs
    from .fileformat import lib_tables
    from .fileformat.board import Board

    result = result or Inputs()
    board = Path(board).resolve()
    strict_context(board)
    result.documents(board=board)
    installation = kicad_env.find_kicad_root()
    config = lib_tables.default_config_dir()
    for directory in filter(None, (board.parent, config)):
        result.add(Path(directory) / "kicad_common.json")
    variables = lib_tables._variables(
        board.parent, Path(installation) if installation else None, config
    )
    libraries, disabled = {}, set()

    def reader(path):
        path = result.add(path)
        return result.files[path]

    for directory in filter(None, (config, board.parent)):
        libraries.update(
            lib_tables._read(
                Path(directory) / "fp-lib-table", variables, set(), disabled, reader=reader
            )
        )
    relocated = [str(board.parent)]
    saved = Board.load(board)
    for footprint in saved.footprints:
        nickname, separator, name = footprint.library_id.partition(":")
        library = libraries.get(nickname)
        if not separator or not library or library.kind != "KiCad" or nickname in disabled:
            raise ValueError(
                "Footprint has no enabled, captured library binding: " + footprint.library_id
            )
        if any(v in name for v in ("/", "\\", ":")) or name in {".", ".."}:
            raise ValueError("Unsafe footprint library item name")
        result.add(library.path / (name + ".kicad_mod"), required=True)
        if "KIPRJMOD" in library.uri or not Path(library.uri).is_absolute():
            relocated.append(str(library.path.resolve()))
    result.stage_root = Path(os.path.commonpath(relocated))
    result.validate()
    return result


def native_check(board):
    context = strict_context(board)
    value = drc_runner.run(str(board), kicad_env.find_official_cli(), timeout=240)
    report = value.report
    if report["kicad_version"] != "10.0.6":
        raise ValueError("Native qualification currently requires KiCad10.0.6")
    check_baseline(strict_context(board), context)
    return {
        "report": report,
        "context": context,
        "executable": value.executable,
        "executable_sha256": sha(Path(value.executable).read_bytes()),
        "scope": "native PCB DRC, saved fill; schematic parity not requested",
        "project_exclusions": json.loads(Path(board).with_suffix(".kicad_pro").read_bytes())
        .get("board", {})
        .get("design_settings", {})
        .get("drc_exclusions", []),
    }


def finding_key(group, row):
    return json.dumps(
        [
            group,
            row["type"],
            row["severity"],
            row.get("excluded", False),
            sorted((i["uuid"], i["pos"]["x"], i["pos"]["y"]) for i in row["items"]),
        ],
        sort_keys=True,
    )


def compare_native(before, after):
    def index(value):
        rows = [
            (finding_key(g, r), r) for g in drc_runner.REPORT_GROUPS for r in value["report"][g]
        ]
        return Counter(k for k, _ in rows), dict(rows)

    old, old_rows = index(before)
    new, new_rows = index(after)

    def counts(value):
        return dict(
            Counter(r["severity"] for g in drc_runner.REPORT_GROUPS for r in value["report"][g])
        )

    introduced = [new_rows[k] for k, count in sorted((new - old).items()) for _ in range(count)]
    resolved = [old_rows[k] for k, count in sorted((old - new).items()) for _ in range(count)]
    blocked = [r for r in introduced if r["severity"] == "error" and not r.get("excluded")]
    # New unconnected findings are blocked even if a project weakens their severity.
    old_unconnected = {
        finding_key("unconnected_items", r) for r in before["report"]["unconnected_items"]
    }
    new_unconnected = {
        finding_key("unconnected_items", r) for r in after["report"]["unconnected_items"]
    }
    return {
        "status": "failed" if blocked or new_unconnected - old_unconnected else "passed",
        "before_counts": counts(before),
        "after_counts": counts(after),
        "introduced": introduced,
        "resolved": resolved,
        "retained_count": sum((old & new).values()),
        "ignored_checks": after["report"].get("ignored_checks", []),
        "project_exclusions": after["project_exclusions"],
        "scope": after["scope"],
        "manufacturing_approval": False,
    }


def capture(command, params):
    from .engineering_inputs import Inputs
    from .library_build import output_paths, validate_spec

    result = Inputs()
    spec_path = result.add(params["spec"], required=True)
    spec = load_json(result.files[spec_path])
    if command == "library build":
        validate_spec(spec)
        primary = result.add(params["project"], required=True)
        if primary.suffix != ".kicad_pro":
            raise ValueError("--project must identify an existing .kicad_pro")
        for path in output_paths(primary.parent, spec):
            if not path.parent.is_dir():
                raise ValueError(
                    "Create the owned library output directory first: " + str(path.parent)
                )
            result.add(path)
        for source in spec["sources"]:
            path = result.add(spec_path.parent / source["file"], required=True)
            if sha(result.files[path]) != source["sha256"]:
                raise ValueError("Source evidence hash differs: " + str(path))
    else:
        from jsonschema import Draft202012Validator

        from .module_contract import PATCH_SPEC

        problems = list(Draft202012Validator(PATCH_SPEC).iter_errors(spec))
        if problems:
            raise ValueError(problems[0].json_path + ": " + problems[0].message)
        primary = Path(params["board"]).resolve()
        capture_board_context(primary, result)
        check_baseline(strict_context(primary), spec["baseline"])
    return result, primary
