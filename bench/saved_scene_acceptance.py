"""One bounded, read-only native acceptance of three already saved EXE scenes.

stage-inputs -> prepare -> freeze -> execute; each consumes an explicit digest.
No product mutation, receipt replay, retry, or old-cycle continuation is provided.
All raw records belong in a private, new output directory.
"""

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from bench.saved_scene_checks import (
    REPORT_NORMALIZATION_POLICY,
    children,
    compare_complete_sexp,
    compare_findings,
    compare_xml,
    one,
    sexp,
    sha,
)

SCENES = ("baseline", "A", "B", "C")
PIN = "9607a35fe0ea33fe822e1f2ad2704eae260d4018"
REQUIRED_FIELDS = {
    "schema_version",
    "output",
    "repository",
    "native_home",
    "host_python",
    "binary",
    "sdk",
    "spec",
    "dependency_pin_manifest",
    "source_binding",
    "source_files",
    "sources",
    "expected_binary_sha256",
    "expected_native_version",
    "expected_sdk_commit",
    "source_evidence",
    "timeout_seconds",
    "library_paths",
}
PAGE_NAMES = {"root": "edit.svg", "CHILD_A": "edit-CHILD_A.svg", "CHILD_B": "edit-CHILD_B.svg"}
PREPARATION_CONFIG_ADDITIONS = {
    "10.0/kicad.json",
    "10.0/kicad_common.json",
    "10.0/eeschema.json",
    "10.0/symbol_editor.json",
    "10.0/cvpcb.json",
    "10.0/pcbnew.json",
    "10.0/fpedit.json",
    "10.0/3d_viewer.json",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_bytes())


def put(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def tree(path, *, excluded_root_directories=()):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Symlink input is not accepted: " + str(path))
    result = {}
    for directory, subdirs, filenames in os.walk(path):
        directory = Path(directory)
        if directory == path:
            subdirs[:] = [name for name in subdirs if name not in excluded_root_directories]
        for name in sorted([*subdirs, *filenames]):
            item = directory / name
            if item.is_symlink():
                raise ValueError("Symlink input is not accepted: " + str(item))
            if item.is_file():
                result[item.relative_to(path).as_posix()] = sha(item)
    return result


def load_config(path):
    value = read(path)
    if set(value) != REQUIRED_FIELDS or value["schema_version"] != 1:
        raise ValueError("Explicit config schema differs; unknown/missing keys refused")
    if set(value["sources"]) != set(SCENES) or set(value["source_files"]) != set(SCENES):
        raise ValueError("Exactly baseline/A/B/C saved inputs are required")
    if value["expected_sdk_commit"] != PIN or value["expected_native_version"] != "10.0.6":
        raise ValueError("The existing native/SDK version is fixed")
    if (
        value["expected_binary_sha256"]
        != "21c46f431918e3b48263f7e49e7177f042eb1b300dba6cddfc062dc0d87a1225"
    ):
        raise ValueError("This method consumes the exact existing 21c EXE saved results")
    for key in (
        "output",
        "repository",
        "native_home",
        "host_python",
        "binary",
        "sdk",
        "spec",
        "dependency_pin_manifest",
    ):
        if not Path(value[key]).is_absolute():
            raise ValueError("Config paths must be absolute: " + key)
    if value["timeout_seconds"] <= 0 or value["timeout_seconds"] > 300:
        raise ValueError("Bounded timeout must be within 1..300 seconds")
    return value


def environment(config):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("KICAD", "PYTHON", "CANDIDATE_")) and key != "KIPRJMOD"
    }
    env.update(
        KICAD_CONFIG_HOME=str(Path(config["output"]) / "configuration"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        PYTHONNOUSERSITE="1",
    )
    return env


def environment_identity(config):
    return {
        key: hashlib.sha256(value.encode("utf-8")).hexdigest()
        for key, value in sorted(environment(config).items())
    }


def method_identity(config):
    repo = Path(config["repository"])
    paths = [
        repo / "bench" / name
        for name in (
            "saved_scene_acceptance.py",
            "saved_scene_checks.py",
            "saved_scene_native_read.py",
            "saved_scene_render.py",
            "native_observation.py",
            "owned_process.py",
            "win_debugging.py",
        )
    ]
    paths += list((repo / "bench/persistence_acceptance").glob("*.py"))
    paths += [repo / "bench/persistence_acceptance/matrix.json"]
    # Python -B suppresses writes but can read existing bytecode. Bind that
    # cache input and package initializers too; new files change this mapping.
    folders = {path.parent for path in paths}
    for folder in folders:
        paths += list((folder / "__pycache__").glob("*.pyc"))
    for folder in (repo / "bench", repo / "bench/persistence_acceptance"):
        if (folder / "__init__.py").exists():
            paths.append(folder / "__init__.py")
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Method input must be a regular non-link file: " + str(path))
    return {str(path.resolve()): sha(path) for path in sorted(set(paths))}


def dependency_identity(config):
    result = {}
    native = Path(config["native_home"])
    paths = [Path(config[key]) for key in ("sdk", "spec")]
    paths += [native / "bin"]
    optional = [
        native / "share/kicad" / name for name in ("template", "resources", "schemas", "internat")
    ]
    paths += [Path(path) for path in config["library_paths"]]
    host = Path(config["host_python"])
    paths += [host, *host.parent.glob("*.dll"), *host.parent.glob("python*.zip")]
    paths += [host.parent / "DLLs"]
    paths += [host.parent / "Lib/site-packages" / name for name in ("fitz", "pymupdf")]
    optional += [
        host.parent / "python._pth",
        host.parent / (host.stem + "._pth"),
        host.parent / f"python{sys.version_info.major}{sys.version_info.minor}._pth",
        host.parent / "pyvenv.cfg",
        host.parent.parent / "pyvenv.cfg",
        host.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip",
    ]
    paths += list(host.parent.glob("*._pth"))
    for path in sorted(set(optional)):
        result[str(path.resolve())] = {
            "exists": path.exists(),
            "files": tree(path) if path.is_dir() else sha(path) if path.is_file() else None,
        }
    result[str((host.parent / "Lib").resolve())] = {
        "excluded_root_directories": ["site-packages"],
        "files": tree(host.parent / "Lib", excluded_root_directories=("site-packages",)),
    }
    for path in sorted(set(paths)):
        if not path.exists():
            raise ValueError("Declared dependency is absent: " + str(path))
        result[str(path.resolve())] = tree(path) if path.is_dir() else sha(path)
    pin = read(config["dependency_pin_manifest"])
    if pin["commit"] != PIN:
        raise ValueError("Pinned dependency manifest commit differs")
    for name, record in pin["files"].items():
        if name.startswith("_driver_sdk/"):
            path = Path(config["sdk"]) / name.removeprefix("_driver_sdk/")
        elif name.startswith("driver_spec/"):
            path = Path(config["spec"]) / name.removeprefix("driver_spec/")
        else:
            continue
        if sha(path) != record["sha256"]:
            raise ValueError("Pinned dependency bytes differ: " + name)
    result["pin_manifest"] = sha(config["dependency_pin_manifest"])
    if sha(config["binary"]) != config["expected_binary_sha256"]:
        raise ValueError("Original tested EXE identity differs")
    result["tested_exe"] = sha(config["binary"])
    for item in [config["source_binding"], *config["source_evidence"]]:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Original source/receipt binding differs: " + item["path"])
        result[item["path"]] = item["sha256"]
    return result


def dependency_policy(config):
    return {
        "native_complete": [
            "bin",
            "share/kicad/template",
            "share/kicad/resources",
            "share/kicad/schemas",
            "share/kicad/internat",
        ],
        "native_optional_absence_bound": [
            "share/kicad/template",
            "share/kicad/resources",
            "share/kicad/schemas",
            "share/kicad/internat",
        ],
        "native_excluded_not_consulted": [
            "3D models",
            "demos",
            "unreferenced global symbol/footprint collections",
        ],
        "input_reference_requirements": (
            "all SCH lib_id library names exactly fixture, all symbols embedded; "
            "PCB contains no model or face node; eight local engineering files "
            "and library_paths strict"
        ),
        "controller_python": "-B -S required; no site/user-site/.pth/sitecustomize startup",
        "host_complete": [
            "Python executable and adjacent DLL/zip/_pth/pyvenv identities",
            "Lib except site-packages",
            "DLLs",
            "Lib/site-packages/fitz",
            "Lib/site-packages/pymupdf",
        ],
        "renderer_python": (
            "-B -S; stdlib and DLL search only before explicit site-packages append; "
            "actual imported module paths/hashes recorded and undeclared code refused"
        ),
        "renderer_dynamic_module": {
            "swig_runtime_data5": (
                "exact SWIG registration keys; bound pymupdf._mupdf/_extra .pyd provider hashes"
            ),
            "typing.io/typing.re": "exact object aliases in bound standard-library typing.py",
        },
        "native_reader_python": (
            "-B -S; only fixed native bin Lib/DLL/zip and explicit site-packages path; "
            "no sitecustomize/addsitedir or Documents third-party startup; "
            "actual module sources and search paths recorded"
        ),
        "declared_library_paths": config["library_paths"],
        "SDK_spec": "complete exact trees and fixed pin manifest",
    }


def input_reference_check(root):
    result = {}
    for scene in SCENES:
        libraries = set()
        for filename in ("edit.kicad_sch", "edit_child.kicad_sch"):
            schematic = sexp((root / scene / filename).read_text(encoding="utf-8"))
            embedded = one(schematic, "lib_symbols")
            embedded_ids = {json.loads(node[1]) for node in children(embedded, "symbol")}
            for symbol in children(schematic, "symbol"):
                identity = json.loads(one(symbol, "lib_id")[1])
                libraries.add(identity.split(":", 1)[0])
                if identity not in embedded_ids or identity.split(":", 1)[0] != "fixture":
                    raise ValueError("Input needs an undeclared external schematic library")
        board = sexp((root / scene / "edit.kicad_pcb").read_text(encoding="utf-8"))

        def external(node):
            return (
                isinstance(node, list)
                and bool(node)
                and (
                    node[0] in {"model", "face"}
                    or any(external(child) for child in node if isinstance(child, list))
                )
            )

        if external(board):
            raise ValueError("PCB model/font reference exceeds this fixed dependency plan")
        result[scene] = {
            "schematic_library_names": sorted(libraries),
            "all_symbol_ids_embedded": True,
            "PCB_model_or_face_nodes": 0,
        }
    return result


def config_values(root):
    values = {}
    for relative in tree(root / "configuration"):
        if relative not in PREPARATION_CONFIG_ADDITIONS:
            raise ValueError("Undeclared configuration file: " + relative)
        value = read(root / "configuration" / relative)
        if not isinstance(value, dict):
            raise ValueError("Default preference must be a regular JSON object: " + relative)
        values[relative] = value
    return values


def validate_config_transition(root, before_values):
    after = config_values(root)
    if set(before_values) - set(after):
        raise ValueError("Initialization removed a preference")
    for relative, old in before_values.items():
        new = copy.deepcopy(after[relative])
        if old == new:
            continue
        if relative != "10.0/kicad_common.json":
            raise ValueError("Initialization modified an existing non-metadata preference")
        old_working = old.get("system", {}).get("working_dir")
        working = new.get("system", {}).get("working_dir")
        if not isinstance(old_working, str) or not isinstance(working, str):
            raise ValueError("working_dir metadata is not a string")
        allowed = {str((root / name).resolve()) for name in ("preparation-project", *SCENES)}
        if str(Path(working).resolve()) not in allowed:
            raise ValueError("Initialized working_dir escapes predeclared projects")
        new["system"]["working_dir"] = old_working
        if old != new:
            raise ValueError("Initialization changed common preference outside working_dir")
    return after


def config_identity(root):
    result = {}
    allowed = {str((root / name).resolve()) for name in ("preparation-project", *SCENES)}
    for name, digest in tree(root / "configuration").items():
        if name == "10.0/kicad_common.json":
            value = read(root / "configuration" / name)
            working = value.get("system", {}).get("working_dir")
            if not isinstance(working, str) or str(Path(working).resolve()) not in allowed:
                raise ValueError("working_dir is outside the predeclared project set")
            value["system"]["working_dir"] = "<predeclared-project-working-directory>"
            result[name] = {"canonical_json": value}
        else:
            result[name] = {"sha256": digest}
    return result


def binding(config):
    root = Path(config["output"])
    return {
        "config": config,
        "engineering": {name: tree(root / name) for name in SCENES},
        "configuration": config_identity(root),
        "methods": method_identity(config),
        "dependencies": dependency_identity(config),
        "environment": environment_identity(config),
    }


def commands(config, scene, *, preparation=False):
    root = Path(config["output"])
    project = root / ("preparation-project" if preparation else scene)
    output = root / ("preparation-output" if preparation else scene + "-output")
    native = Path(config["native_home"]) / "bin"
    sch, board = project / "edit.kicad_sch", project / "edit.kicad_pcb"
    rows = [
        (
            "xml",
            [
                native / "kicad-cli.exe",
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                output / "netlist.xml",
                sch,
            ],
        )
    ]
    if preparation or scene in {"baseline", "A"}:
        rows += [
            (
                "erc",
                [
                    native / "kicad-cli.exe",
                    "sch",
                    "erc",
                    "--severity-all",
                    "--format",
                    "json",
                    "--output",
                    output / "erc.json",
                    sch,
                ],
            ),
            (
                "drc",
                [
                    native / "kicad-cli.exe",
                    "pcb",
                    "drc",
                    "--schematic-parity",
                    "--severity-all",
                    "--format",
                    "json",
                    "--output",
                    output / "drc.json",
                    board,
                ],
            ),
            (
                "sdk",
                [
                    native / "python.exe",
                    "-B",
                    "-S",
                    Path(config["repository"]) / "bench/saved_scene_native_read.py",
                    board,
                ],
            ),
        ]
    if preparation or scene != "baseline":
        rows += [
            (
                "schematic-svg",
                [
                    native / "kicad-cli.exe",
                    "sch",
                    "export",
                    "svg",
                    "--output",
                    output / "schematic-svg",
                    sch,
                ],
            )
        ]
    if preparation or scene == "A":
        for side, layers in (
            ("front", "F.Cu,F.SilkS,Edge.Cuts"),
            ("back", "B.Cu,B.SilkS,Edge.Cuts"),
        ):
            rows += [
                (
                    side + "-svg",
                    [
                        native / "kicad-cli.exe",
                        "pcb",
                        "export",
                        "svg",
                        "--layers",
                        layers,
                        "--mode-single",
                        "--fit-page-to-board",
                        "--exclude-drawing-sheet",
                        "--output",
                        output / (side + ".svg"),
                        board,
                    ],
                )
            ]
    prefix = (
        "prepare"
        if preparation
        else {"baseline": "baseline", "A": "final", "B": "alias", "C": "child"}[scene]
    )
    rows = [
        {
            "id": prefix + "-" + name,
            "kind": name,
            "scene": scene,
            "argv": list(map(str, argv)),
            "cwd": str(project),
            "timeout_seconds": config["timeout_seconds"],
            "allowed_exit_codes": [0],
        }
        for name, argv in rows
    ]
    for row in rows:
        kind = row["kind"]
        if kind == "sdk":
            row["expected_outputs"] = ["owned-call/stdout"]
        elif kind == "schematic-svg":
            row["expected_outputs"] = [
                str(output / "schematic-svg" / filename) for filename in PAGE_NAMES.values()
            ]
        else:
            filename = {
                "xml": "netlist.xml",
                "erc": "erc.json",
                "drc": "drc.json",
                "front-svg": "front.svg",
                "back-svg": "back.svg",
            }[kind]
            row["expected_outputs"] = [str(output / filename)]
    return rows


def formal_plan(config):
    rows = [row for scene in SCENES for row in commands(config, scene)]
    if len(rows) != 15:
        raise ValueError("Formal consumer set must have exactly 15 IDs")
    return rows


def image_plan(config):
    root = Path(config["output"])
    rows = [
        {
            "id": scene + "-" + identity,
            "scene": scene,
            "page_identity": identity,
            "source": str(root / (scene + "-output/schematic-svg") / filename),
            "scale": "3",
        }
        for scene in ("A", "B", "C")
        for identity, filename in PAGE_NAMES.items()
    ]
    rows += [
        {
            "id": "A-" + side,
            "scene": "A",
            "page_identity": "PCB-" + side,
            "source": str(root / "A-output" / (side + ".svg")),
            "scale": "8",
        }
        for side in ("front", "back")
    ]
    return rows


def expected_output_names(config):
    root = Path(config["output"])
    result = {scene: set() for scene in SCENES}
    for row in formal_plan(config):
        for name in row["expected_outputs"]:
            if name != "owned-call/stdout":
                result[row["scene"]].add(
                    Path(name).relative_to(root / (row["scene"] + "-output")).as_posix()
                )
    for row in image_plan(config):
        png = Path(row["source"]).with_suffix(".png")
        result[row["scene"]].add(png.relative_to(root / (row["scene"] + "-output")).as_posix())
    return {scene: sorted(names) for scene, names in result.items()}


def record_call(config, row, directory):
    from bench.native_observation import inspect_trace
    from bench.persistence_acceptance.process_tree import observe

    put(directory.parent / (directory.name + ".INVOCATION.json"), {"at": now(), **row})
    observe(row["argv"], row["cwd"], environment(config), directory, timeout=row["timeout_seconds"])
    classification = inspect_trace(directory, allowed_exit_codes=tuple(row["allowed_exit_codes"]))
    put(directory.parent / (directory.name + ".CLASSIFICATION.json"), classification)
    if classification.get("may_validate_functional_output") is not True:
        raise RuntimeError("Native observation requires stopping this cycle: " + row["id"])
    return classification


def structure_checks(root):
    result = {}
    base = root / "baseline"
    allowed_files = {
        "A": {"edit.kicad_sch", "edit.kicad_pcb"},
        "B": {"edit.kicad_sch"},
        "C": {"edit_child.kicad_sch"},
    }
    for scene in ("A", "B", "C"):
        old, new = tree(base), tree(root / scene)
        if set(old) != set(new) or any(
            old[name] != new[name] for name in old if name not in allowed_files[scene]
        ):
            raise ValueError(
                "Protected engineering/library/project/preferences file differs: " + scene
            )
    for scene, filename, kind in (
        ("A", "edit.kicad_sch", "schematic"),
        ("A", "edit.kicad_pcb", "board"),
        ("B", "edit.kicad_sch", "schematic"),
        ("C", "edit_child.kicad_sch", "child"),
    ):
        result[scene + ":" + filename] = compare_complete_sexp(
            base / filename, root / scene / filename, scene, kind
        )
    return result


def stage_inputs(config):
    root = Path(config["output"])
    root.mkdir(parents=True, exist_ok=False)
    source_binding = read(config["source_binding"]["path"])
    if sha(config["source_binding"]["path"]) != config["source_binding"]["sha256"]:
        raise ValueError("Original read-only saved scene binding differs")
    for scene, source_scene in (
        ("baseline", "baseline"),
        ("A", "final"),
        ("B", "alias"),
        ("C", "child"),
    ):
        if (
            source_binding["source_manifests"][source_scene] != config["source_files"][scene]
            or Path(source_binding["sources"][source_scene]).resolve()
            != Path(config["sources"][scene]).resolve()
        ):
            raise ValueError("Declared engineering source does not match original EXE audit")
    inventories = {}
    for scene in SCENES:
        source = Path(config["sources"][scene])
        inventories[scene] = tree(source)
        required = config["source_files"][scene]
        if len(required) != 8:
            raise ValueError("Exactly eight authenticated engineering files per saved scene")
        for relative, digest in required.items():
            rel = Path(relative)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("Source relative path escapes scene")
            if inventories[scene].get(relative) != digest:
                raise ValueError("Saved engineering source differs: " + scene + "/" + relative)
            target = root / scene / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        omitted = set(inventories[scene]) - set(required)
        if any(not name.endswith(".py") for name in omitted):
            raise ValueError("Undeclared source input must not be silently omitted")
    shutil.copytree(root / "baseline", root / "preparation-project")
    (root / "configuration").mkdir()
    (root / "preparation-calls").mkdir()
    (root / "preparation-output").mkdir()
    for scene in SCENES:
        for name in ("edit.kicad_sch", "edit_child.kicad_sch"):
            ast = sexp((root / scene / name).read_text(encoding="utf-8"))
            for symbol in children(ast, "symbol"):
                for lib in children(symbol, "lib_id"):
                    library = json.loads(lib[1]).split(":", 1)[0]
                    if library != "fixture" and not any(
                        Path(path).stem == library for path in config["library_paths"]
                    ):
                        raise ValueError(
                            "External schematic lib_id lacks declared library dependency"
                        )
    structure = structure_checks(root)
    references = input_reference_check(root)
    native = Path(config["native_home"]) / "bin/kicad-cli.exe"
    version = {
        "id": "prepare-version",
        "kind": "version",
        "argv": [str(native), "--version"],
        "cwd": str(root / "preparation-project"),
        "timeout_seconds": config["timeout_seconds"],
        "allowed_exit_codes": [0],
    }
    snapshot = {
        "schema_version": 1,
        "at": now(),
        "config": config,
        "source_inventories_including_explicitly_not_copied_generators": inventories,
        "engineering": {scene: tree(root / scene) for scene in SCENES},
        "preparation_engineering": tree(root / "preparation-project"),
        "structure": structure,
        "methods": method_identity(config),
        "dependencies": dependency_identity(config),
        "environment": environment_identity(config),
        "preparation_calls": [version, *commands(config, "baseline", preparation=True)],
        "formal_calls": formal_plan(config),
        "images": image_plan(config),
        "report_normalization": REPORT_NORMALIZATION_POLICY,
        "dependency_policy": dependency_policy(config),
        "input_reference_checks": references,
        "native_calls_started": 0,
        "normal_metadata_policy": {
            "only_mutable_config_json_pointer": "10.0/kicad_common.json#/system/working_dir",
            "allowed_values": [
                str((root / scene).resolve()) for scene in ("preparation-project", *SCENES)
            ],
            "everything_else_in_configuration": "exact bytes and complete file set",
            "preparation_exact_allowed_default_config_paths": sorted(PREPARATION_CONFIG_ADDITIONS),
            "preparation_existing_preference_mutations": "only common.system.working_dir",
            "project_metadata": (
                "prepared edit.kicad_prl copied identically before formal freeze; strict thereafter"
            ),
        },
    }
    put(root / "INPUT-FROZEN.json", snapshot)
    return {
        "input": str(root / "INPUT-FROZEN.json"),
        "sha256": sha(root / "INPUT-FROZEN.json"),
        "native_calls_started": 0,
    }


def verify_initial(config, approved):
    root = Path(config["output"])
    if (
        approved["config"] != config
        or approved["methods"] != method_identity(config)
        or approved["dependencies"] != dependency_identity(config)
        or approved["environment"] != environment_identity(config)
    ):
        raise ValueError("Pre-native method/config/dependency/environment drift")
    if {scene: tree(root / scene) for scene in SCENES} != approved["engineering"]:
        raise ValueError("Original formal engineering copies drifted")
    if tree(root / "preparation-project") != approved["preparation_engineering"]:
        raise ValueError(
            "Original preparation engineering copy drifted before native initialization"
        )


def deltas(before, after):
    return [
        {
            "relative_path": key,
            "before": before.get(key),
            "after": after.get(key),
            "kind": "added" if key not in before else "removed" if key not in after else "modified",
        }
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    ]


def prepare(config, digest):
    root = Path(config["output"])
    if sha(root / "INPUT-FROZEN.json") != digest:
        raise ValueError("Original preparation input digest differs")
    approved = read(root / "INPUT-FROZEN.json")
    verify_initial(config, approved)
    put(root / "PREPARE-START.json", {"at": now(), "input_sha256": digest})
    original = tree(root / "preparation-project")
    all_deltas = []
    for row in approved["preparation_calls"]:
        config_before, project_before = (
            tree(root / "configuration"),
            tree(root / "preparation-project"),
        )
        values_before = config_values(root)
        try:
            record_call(config, row, root / "preparation-calls" / row["id"])
        finally:
            config_after, project_after = (
                tree(root / "configuration"),
                tree(root / "preparation-project"),
            )
            delta = {
                "id": row["id"],
                "configuration": deltas(config_before, config_after),
                "project": deltas(project_before, project_after),
                "configuration_values_before": values_before,
                "configuration_values_after": {
                    name: read(root / "configuration" / name)
                    for name in config_after
                    if name.endswith(".json")
                },
            }
            put(root / "preparation-calls" / (row["id"] + ".DELTAS.json"), delta)
            all_deltas.append(delta)
        validate_config_transition(root, values_before)
        if any(project_after.get(name) != value for name, value in original.items()) or set(
            project_after
        ) - set(original) - {"edit.kicad_prl"}:
            raise RuntimeError("Initialization changed engineering or undeclared project files")
        if (
            row["kind"] == "version"
            and (root / "preparation-calls" / row["id"] / "stdout")
            .read_text(encoding="utf-8")
            .strip()
            != "10.0.6"
        ):
            raise ValueError("Actual official native version differs")
    if not (root / "preparation-project/edit.kicad_prl").exists():
        raise ValueError("Preparation did not initialize expected project metadata")
    additions = []
    for scene in SCENES:
        target = root / scene / "edit.kicad_prl"
        shutil.copyfile(root / "preparation-project/edit.kicad_prl", target)
        additions.append(
            {
                "scene": scene,
                "file": "edit.kicad_prl",
                "sha256": sha(target),
                "origin": "preparation-project/edit.kicad_prl",
            }
        )
    prepared = {
        "schema_version": 1,
        "at": now(),
        "input_sha256": digest,
        "configuration_all_files": tree(root / "configuration"),
        "configuration_canonical": config_identity(root),
        "all_preparation_deltas": all_deltas,
        "explicit_formal_metadata_additions": additions,
        "engineering": {scene: tree(root / scene) for scene in SCENES},
        "preparation_call_evidence": tree(root / "preparation-calls"),
        "preparation_output": tree(root / "preparation-output"),
        "formal_consumers_executed": 0,
        "images_manually_viewed": 0,
    }
    put(root / "PREPARATION.json", prepared)
    return {
        "preparation": str(root / "PREPARATION.json"),
        "sha256": sha(root / "PREPARATION.json"),
        "formal_consumers_executed": 0,
    }


def freeze(config, digest):
    root = Path(config["output"])
    if sha(root / "PREPARATION.json") != digest:
        raise ValueError("Preparation digest differs")
    prepared = read(root / "PREPARATION.json")
    approved = read(root / "INPUT-FROZEN.json")
    if sha(root / "INPUT-FROZEN.json") != prepared["input_sha256"]:
        raise ValueError("Original input freeze changed")
    for name, actual in (
        ("methods", method_identity(config)),
        ("dependencies", dependency_identity(config)),
        ("environment", environment_identity(config)),
        ("config", config),
    ):
        if approved[name] != actual:
            raise ValueError("Preparation-to-freeze identity drift: " + name)
    if prepared["engineering"] != {scene: tree(root / scene) for scene in SCENES} or prepared[
        "configuration_all_files"
    ] != tree(root / "configuration"):
        raise ValueError("Prepared engineering/configuration drift before formal freeze")
    if prepared["preparation_call_evidence"] != tree(root / "preparation-calls") or prepared[
        "preparation_output"
    ] != tree(root / "preparation-output"):
        raise ValueError("Preparation evidence drift")
    frozen = {
        "schema_version": 1,
        "at": now(),
        "binding": binding(config),
        "preparation_sha256": digest,
        "original_input_sha256": sha(root / "INPUT-FROZEN.json"),
        "normal_metadata_policy": approved["normal_metadata_policy"],
        "native_consumers": formal_plan(config),
        "images": image_plan(config),
        "expected_complete_scene_output_names": expected_output_names(config),
        "structure": structure_checks(root),
        "report_normalization": approved["report_normalization"],
        "dependency_policy": approved["dependency_policy"],
        "input_reference_checks": input_reference_check(root),
        "manual_view": "pending; actual full images must be opened after generation",
    }
    put(root / "FROZEN.json", frozen)
    return {
        "plan": str(root / "FROZEN.json"),
        "sha256": sha(root / "FROZEN.json"),
        "formal_consumers_executed": 0,
    }


def compare_native(root):
    old = read(root / "formal-calls/baseline-sdk/stdout")
    new = read(root / "formal-calls/final-sdk/stdout")
    if (
        not old["native_version"].startswith("10.0.6")
        or old["native_version"] != new["native_version"]
    ):
        raise ValueError("Actual pcbnew readback version differs")
    expected = copy.deepcopy(old)
    refs = {row["ref"]: row for row in expected["footprints"]}
    if refs["R1"]["value"] != "10k" or refs["R3"]["dnp"] or "MPN" in refs["R3"]["fields"]:
        raise ValueError("Actual pcbnew saved baseline precondition differs")
    refs["R1"]["value"] = "22k"
    if "Value" in refs["R1"]["fields"]:
        refs["R1"]["fields"]["Value"] = "22k"
    refs["R3"]["dnp"], refs["R3"]["fields"]["MPN"] = True, "RC0805"
    for row in expected["footprints"]:
        for pad in row["pads"]:
            if pad["net"] == "/SIG":
                pad["net"] = "/DATA"
    for row in expected["tracks"]:
        if row["net"] == "/SIG":
            row["net"] = "/DATA"
    for key in ("input_sha256",):
        expected[key] = new[key]
    if expected != new or not old["input_unchanged"] or not new["input_unchanged"]:
        raise ValueError("Independent pcbnew exact metadata/pads/copper observation differs")
    if old["input_sha256"] != sha(root / "baseline/edit.kicad_pcb") or new["input_sha256"] != sha(
        root / "A/edit.kicad_pcb"
    ):
        raise ValueError("Native board readback input binding differs")
    return {
        "status": "passed",
        "baseline_input": old["input_sha256"],
        "A_input": new["input_sha256"],
        "all_observed_fields_pads_copper": "equal outside exact intent",
        "unobserved_native_properties": "independent complete S-expression protection",
    }


def execute(config, plan, digest):
    root = Path(config["output"])
    if Path(plan).resolve() != (root / "FROZEN.json").resolve() or sha(plan) != digest:
        raise ValueError("Explicit reviewed formal plan bytes differ")
    approved = read(plan)
    if (
        approved["native_consumers"] != formal_plan(config)
        or approved["images"] != image_plan(config)
        or approved["report_normalization"] != REPORT_NORMALIZATION_POLICY
        or approved["dependency_policy"] != dependency_policy(config)
        or approved["input_reference_checks"] != input_reference_check(root)
        or approved["expected_complete_scene_output_names"] != expected_output_names(config)
        or binding(config) != approved["binding"]
    ):
        raise ValueError("Formal frozen execution content drift before any process")
    put(root / "EXECUTION-START.json", {"at": now(), "original_plan_sha256": digest})
    calls = root / "formal-calls"
    calls.mkdir()
    for scene in SCENES:
        (root / (scene + "-output")).mkdir()
    for row in approved["native_consumers"]:
        if binding(config) != approved["binding"]:
            raise ValueError("Formal pre-call input/config/dependency drift")
        before = tree(root / "configuration")
        values_before = config_values(root)
        try:
            record_call(config, row, calls / row["id"])
        finally:
            put(
                calls / (row["id"] + ".CONFIG-DELTAS.json"),
                {
                    "id": row["id"],
                    "deltas": deltas(before, tree(root / "configuration")),
                    "configuration_values_before": values_before,
                    "configuration_values_after": {
                        name: read(root / "configuration" / name)
                        for name in tree(root / "configuration")
                        if name.endswith(".json")
                    },
                },
            )
        for path in row["expected_outputs"]:
            expected_output = (
                calls / row["id"] / "stdout" if path == "owned-call/stdout" else Path(path)
            )
            if not expected_output.is_file():
                raise ValueError("Declared consumer output missing: " + str(expected_output))
        if binding(config) != approved["binding"]:
            raise RuntimeError("Formal post-call input/config/dependency drift; cycle stopped")
    validation_failures = []

    def check(name, operation):
        try:
            return operation()
        except (ValueError, KeyError, TypeError) as exc:
            failure = {
                "status": "failed",
                "check": name,
                "failure": type(exc).__name__ + ": " + str(exc),
            }
            validation_failures.append(failure)
            put(root / ("VALIDATION-" + name + ".json"), failure)
            return failure

    # A failed functional assertion never receives pass credit. It does not
    # suppress the other independent checks or already-exported full drawings.
    # Process/observation/protection failures above still stop immediately.
    structure = check("structure", lambda: structure_checks(root))
    xml = {
        scene: check(
            "xml-" + scene,
            lambda scene=scene: compare_xml(
                root / "baseline-output/netlist.xml", root / (scene + "-output/netlist.xml"), scene
            ),
        )
        for scene in ("A", "B", "C")
    }
    findings = {
        name: check(
            name,
            lambda name=name: compare_findings(
                root / "baseline-output" / (name + ".json"),
                root / "A-output" / (name + ".json"),
                drc=name == "drc",
            ),
        )
        for name in ("erc", "drc")
    }
    native = check("pcbnew", lambda: compare_native(root))
    for scene in ("A", "B", "C"):
        if {p.name for p in (root / (scene + "-output/schematic-svg")).glob("*.svg")} != set(
            PAGE_NAMES.values()
        ):
            raise ValueError("Root and both exact child page identities are not complete")
    images = []
    for image in approved["images"]:
        source = Path(image["source"])
        target = source.with_suffix(".png")
        row = {
            "id": "raster-" + image["id"],
            "argv": [
                config["host_python"],
                "-B",
                "-S",
                str(Path(config["repository"]) / "bench/saved_scene_render.py"),
                str(source),
                str(target),
                image["scale"],
            ],
            "cwd": str(root),
            "timeout_seconds": config["timeout_seconds"],
            "allowed_exit_codes": [0],
        }
        if binding(config) != approved["binding"]:
            raise ValueError("Pre-render frozen input drift")
        record_call(config, row, calls / row["id"])
        if (
            not target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            or binding(config) != approved["binding"]
        ):
            raise ValueError("Full-viewport raster invalid or input drifted")
        images.append(
            {
                **image,
                "source_sha256": sha(source),
                "path": str(target),
                "sha256": sha(target),
                "manual_view": "pending",
                "coverage": "complete native SVG viewport; no crop",
            }
        )
    output_trees = {scene: tree(root / (scene + "-output")) for scene in SCENES}
    if {scene: sorted(files) for scene, files in output_trees.items()} != approved[
        "expected_complete_scene_output_names"
    ]:
        raise ValueError("Formal output tree differs from exact frozen file set")
    result = {
        "schema_version": 1,
        "at": now(),
        "status": "failed" if validation_failures else "functional_checks_passed_manual_pending",
        "validation_failures": validation_failures,
        "plan_sha256": digest,
        "formal_native_consumers": 15,
        "formal_consumer_ids": [row["id"] for row in approved["native_consumers"]],
        "structure": structure,
        "official_xml": xml,
        "pcbnew_readback": native,
        "complete_native_findings": findings,
        "images": images,
        "manual_views_completed": 0,
        "B_C_PCB": "frozen bytes unchanged; no sync/parity credit",
        "preparation_consumers_counted_as_formal": 0,
        "historical_lock_cleanup_and_next_preview": (
            "original separately bound receipt/EXE test evidence only; no new product operation"
        ),
        "call_evidence": tree(calls),
        "complete_scene_output_trees": output_trees,
    }
    put(root / "NATIVE-RESULT.json", result)
    return {
        "completed": True,
        "functional_checks_passed": not validation_failures,
        "report": str(root / "NATIVE-RESULT.json"),
        "manual_images_pending": 11,
    }


def main():
    if not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        raise SystemExit("saved_scene_acceptance requires Python -B -S before any native process")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("stage-inputs", "prepare", "freeze", "execute"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input-sha256")
    parser.add_argument("--preparation-sha256")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    config = load_config(args.config)
    root = Path(config["output"])
    try:
        if args.stage == "stage-inputs":
            result = stage_inputs(config)
        elif args.stage == "prepare":
            if not args.input_sha256:
                parser.error("prepare requires --input-sha256")
            result = prepare(config, args.input_sha256)
        elif args.stage == "freeze":
            if not args.preparation_sha256:
                parser.error("freeze requires --preparation-sha256")
            result = freeze(config, args.preparation_sha256)
        else:
            if not args.plan or not args.plan_sha256:
                parser.error("execute requires --plan and --plan-sha256")
            result = execute(config, args.plan, args.plan_sha256)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        if root.is_dir() and not (root / (args.stage.upper() + "-STOP.json")).exists():
            put(
                root / (args.stage.upper() + "-STOP.json"),
                {
                    "at": now(),
                    "stage": args.stage,
                    "failure": type(exc).__name__ + ": " + str(exc),
                    "action": "preserve entire cycle; no retry or ambiguous-write replay",
                    "configuration_at_stop": tree(root / "configuration"),
                    "engineering_at_stop": {scene: tree(root / scene) for scene in SCENES},
                },
            )
        raise


if __name__ == "__main__":
    main()
