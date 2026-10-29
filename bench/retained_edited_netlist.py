"""Bind one native netlist read of an already saved source-test edit.

Default is plan-only. Use fresh output after the original serial source cycle.
No original edit, preview, token or confirmation is replayed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import traceback
from pathlib import Path

from bench.retained_move_readback import files, put, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--configuration-seed", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    source, retained, out = args.source.resolve(), args.run.resolve(), args.out.resolve()
    if out.is_relative_to(source) or out.is_relative_to(retained):
        raise ValueError("Choose new output outside source and original evidence")
    terminal = retained / "FINISH.json"
    finish = json.loads(terminal.read_bytes()) if terminal.exists() else None
    if args.execute and finish is None:
        raise ValueError("Wait for the original serial source terminal")
    if finish is not None and (
        finish["exit"] not in (0, 1)
        or not finish["job_finalization"]["natural_tree_complete"]
        or (retained / "NATIVE-STOP.json").exists()
    ):
        raise ValueError("Original cycle has no admitted natural terminal")
    anchor = json.loads(args.anchor.read_bytes())
    matches = [
        p
        for p in (retained / "tmp").glob("test_kicad_reads_the_edited_de*")
        if p.name[-1].isdigit()
    ]
    if len(matches) != 1:
        raise ValueError("Original saved edit case is ambiguous")
    original = matches[0]
    inputs = {p.name: sha(p) for p in original.glob("*.kicad_*")}
    if inputs != anchor["current_saved_project"]:
        raise ValueError("Retained saved project drifted from the failure review")
    old_record = json.loads(
        (original / "native-evidence/edited-netlist-0000/process.call.json").read_bytes()
    )
    native_root = args.native_root.resolve()
    official = native_root / "bin/kicad-cli.exe"
    if old_record["exit"] != 0 or sha(official) != old_record["program"][0]["sha256"]:
        raise ValueError("Original export or native executable identity differs")
    out.mkdir(parents=True, exist_ok=False)
    methods = [
        "tests/test_sch_edit.py",
        "tests/native_process.py",
        "bench/retained_edited_netlist.py",
    ]
    plan = {
        "kind": "Read-only native export from copies of the preserved successful source edit",
        "original_case_id": anchor["case"],
        "inputs": inputs,
        "anchor_sha256": sha(args.anchor),
        "provisional_until_original_terminal": finish is None,
        "original_finish_sha256": sha(terminal) if finish is not None else None,
        "native_cli_sha256": sha(official),
        "method_files_sha256": {name: sha(source / name) for name in methods},
        "new_native_exports": 1,
        "original_confirmation_replays": 0,
        "protection": (
            "Original files and copied schematics stay unchanged; "
            "copied project metadata changes are recorded separately."
        ),
    }
    put(out / "PLAN.json", plan)
    if not args.execute:
        print(json.dumps({"status": "plan_only", "native_exports": 1}))
        return 0
    project = out / "project"
    project.mkdir()
    for name in inputs:
        shutil.copyfile(original / name, project / name)
    configuration = out / "configuration"
    shutil.copytree(args.configuration_seed, configuration)
    common = configuration / "10.0/kicad_common.json"
    if common.exists():
        value = json.loads(common.read_bytes())
        value.setdefault("system", {})["working_dir"] = str(project)
        common.write_text(json.dumps(value, indent=2), encoding="utf-8")
    for key in tuple(os.environ):
        if key.startswith(("KICAD", "PYTHON")) or key == "KIPRJMOD":
            del os.environ[key]
    os.environ.update(
        KICAD_CLI_ROOT=str(native_root),
        KICAD_CLI_PYTHON=str(native_root / "bin/python.exe"),
        KICAD_CLI_OFFICIAL=str(official),
        KICAD_CONFIG_HOME=str(configuration),
        KICAD_CLI_NATIVE_TEST_ROOT=str(out),
        KICAD_CLI_NATIVE_TEST_EVIDENCE=str(out / "native-evidence"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.path[:0] = [str(source / "tests"), str(source)]
    import test_sch_edit as checks

    def forbidden(*_args, **_kwargs):
        raise RuntimeError("Saved netlist verification cannot invoke an edit or confirmation")

    checks.run = checks.plan = checks.edit = forbidden
    try:
        native_nets, output = checks._kicad_nets(project / "edit.kicad_sch")
        assert native_nets == checks.nets(project / "edit.kicad_sch")
        text = output.read_text(encoding="utf-8")
        assert re.search(r'\(ref "R20"\)\s+\(value "10k"\)', text)
        assert '(name "MPN") "RC0603"' in text
        assert inputs == {p.name: sha(p) for p in original.glob("*.kicad_*")}
        after = files(project)
        assert all(
            after[name] == digest for name, digest in inputs.items() if name.endswith(".kicad_sch")
        )
        record = json.loads(output.with_name("process.call.json").read_bytes())
        assert (
            record["report"]["sha256"] == sha(output)
            and record["job_finalization"]["natural_tree_complete"]
        )
        result = {
            "status": "passed",
            "native_netlist_sha256": sha(output),
            "native_record_sha256": sha(output.with_name("process.call.json")),
            "native_exit": record["exit"],
            "whole_job_natural": True,
            "original_files_unchanged": True,
            "copied_schematics_unchanged": True,
            "copied_project_metadata_changes": {
                name: {"before": inputs.get(name), "after": digest}
                for name, digest in after.items()
                if inputs.get(name) != digest
            },
            "nets_equal": True,
            "value_and_mpn_equal": True,
            "new_native_exports": 1,
            "original_confirmation_replays": 0,
        }
        assert plan["method_files_sha256"] == {name: sha(source / name) for name in methods}
        put(out / "RESULT.json", result)
    except BaseException:
        put(out / "PRIVATE-FAILURE.json", {"traceback": traceback.format_exc()})
        raise
    print(json.dumps({"status": "passed", "new_native_exports": 1, "confirmation_replays": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
