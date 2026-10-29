"""One bounded read-only qualification of three already saved move scenes.

stage-inputs -> prepare -> freeze -> execute. The controller never dispatches
the product, confirms a receipt, opens operation state, or retries a consumer.
Preparation is five separate native calls; the formal plan is twelve native
and six host render calls. Raw private records are retained at every stage.
"""

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from bench.move_safety_checks import (
    EXPECTED,
    JOINT_POLICY,
    check_board,
    check_drc,
    check_native,
    check_negative_preview,
    check_public_move_chain,
)
from bench.saved_scene_acceptance import (
    PIN,
    PREPARATION_CONFIG_ADDITIONS,
    deltas,
    dependency_identity,
    dependency_policy,
    environment_identity,
    now,
    put,
    read,
    record_call,
    tree,
)
from bench.saved_scene_acceptance import method_identity as shared_method_identity
from bench.saved_scene_checks import children, one, sexp, sha

SCENES = ("baseline", "negative", "positive")
PROJECTS = ("preparation-project", *SCENES)
EXPECTED_FILES = {
    "edit.kicad_pcb",
    "edit.kicad_sch",
    "edit_child.kicad_sch",
    "edit.kicad_pro",
    "fp-lib-table",
    "fixture.pretty/R_0603.kicad_mod",
    "fixture.pretty/R_0805.kicad_mod",
    "fixture.pretty/SO8.kicad_mod",
}
BASELINE_PCB_SHA256 = "59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f"
CONSUMER_COUNTS = {
    "preparation_native": 5,
    "formal_native": 12,
    "formal_host": 6,
    "full_images": 6,
}
EVIDENCE_ROLES = {
    "build",
    "package",
    "negative_preflight",
    "positive_preview",
    "positive_confirm",
    "positive_readback",
}
REQUIRED = {
    "schema_version",
    "output",
    "repository",
    "native_home",
    "host_python",
    "binary",
    "candidate_archive",
    "expected_binary_sha256",
    "expected_candidate_archive_sha256",
    "sdk",
    "spec",
    "dependency_pin_manifest",
    "source_binding",
    "source_evidence",
    "sources",
    "source_files",
    "intent_binding",
    "expected",
    "expected_native_version",
    "expected_sdk_commit",
    "timeout_seconds",
    "library_paths",
}


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Input must be a regular non-link file: " + str(path))
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError("Input has a linked parent: " + str(path))
    return path


def binding_record(value):
    if not isinstance(value, dict) or not {"path", "sha256"}.issubset(value):
        raise ValueError("An explicit path/SHA-256 binding is required")
    if not Path(value["path"]).is_absolute() or sha(regular(value["path"])) != value["sha256"]:
        raise ValueError("Bound source/receipt/intent bytes differ")


def file_reference(value, *, current=True):
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("A source-binding file reference has unknown/missing fields")
    if (
        not isinstance(value["path"], str)
        or not Path(value["path"]).is_absolute()
        or not isinstance(value["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
    ):
        raise ValueError("A source-binding file reference has an invalid path/digest")
    if current:
        binding_record(value)
    return {"path": str(Path(value["path"]).resolve()), "sha256": value["sha256"]}


def manifest_reference(value, *, current=True):
    if not isinstance(value, dict) or set(value) != EXPECTED_FILES:
        raise ValueError("A source-binding manifest must contain exactly eight engineering files")
    return {name: file_reference(item, current=current) for name, item in value.items()}


def source_manifests(config):
    return {
        scene: {
            name: {
                "path": str(Path(config["sources"][scene][name]).resolve()),
                "sha256": config["source_files"][scene][name],
            }
            for name in EXPECTED_FILES
        }
        for scene in SCENES
    }


def source_archive_identity(record):
    reference = file_reference(record["source_archive"])
    checkout = Path(record["source_checkout"])
    if not checkout.is_absolute() or not checkout.is_dir():
        raise ValueError("The build checkout locator must identify its retained source tree")
    with zipfile.ZipFile(reference["path"]) as packed:
        names = packed.namelist()
        if len(names) != len(set(names)) or "source-manifest.json" not in names:
            raise ValueError("The actual build source archive has duplicate/missing entries")
        manifest = json.loads(packed.read("source-manifest.json"))
        files = {}
        for name in names:
            if name == "source-manifest.json":
                continue
            relative = Path(name)
            if (
                relative.is_absolute()
                or "\\" in name
                or ".." in relative.parts
                or relative.suffix != ".py"
                or {"_driver_sdk", "driver_spec"}.intersection(relative.parts)
            ):
                raise ValueError("The actual build source archive contains undeclared source paths")
            files[name] = hashlib.sha256(packed.read(name)).hexdigest()
            if sha(regular(checkout / "kicad_cli" / name)) != files[name]:
                raise ValueError(
                    "Retained build input source differs from its actual source archive"
                )
    actual = {
        p.relative_to(checkout / "kicad_cli").as_posix(): sha(regular(p))
        for p in (checkout / "kicad_cli").rglob("*.py")
        if not {"_driver_sdk", "driver_spec"}.intersection(
            p.relative_to(checkout / "kicad_cli").parts
        )
    }
    if (
        not files
        or type(manifest.get("format")) is not int
        or manifest != {"format": 1, "version": "2.0.0", "files": files}
        or files != actual
    ):
        raise ValueError("The actual build archive version/hash/source-file closure differs")
    return reference


def retained_checkout_commit(checkout):
    checkout = Path(checkout).resolve()
    pointer = checkout / ".git"
    if pointer.is_file():
        value = regular(pointer).read_text(encoding="utf-8").strip()
        if not value.startswith("gitdir: "):
            raise ValueError("Retained checkout has an invalid gitdir locator")
        directory = (checkout / value.removeprefix("gitdir: ")).resolve()
    elif pointer.is_dir() and not pointer.is_symlink():
        directory = pointer
    else:
        raise ValueError("Retained checkout Git identity is unavailable")
    head = regular(directory / "HEAD").read_text(encoding="ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise ValueError("Acceptance requires the retained detached clean build/package checkout")
    return head


def python_script_argv(argv):
    if not isinstance(argv, list) or not all(isinstance(atom, str) for atom in argv):
        raise ValueError("A recorded Python invocation must be an argv array")
    result = list(argv)
    if len(result) > 1 and result[1] == "-B":
        result.pop(1)
    if len(result) < 2 or result[1].startswith("-"):
        raise ValueError("Only one optional -B interpreter flag is accepted")
    return result


def recorded_process_identity(reference, program, command, cwd, script):
    """Consume existing owned-Job build/package capture; never start a process."""
    path = Path(file_reference(reference)["path"])
    record = read(path)
    expected_program = {"path": program["path"], "present": True, "sha256": program["sha256"]}

    def captured(rows):
        if not isinstance(rows, list):
            raise ValueError("Captured process file identities are missing")
        result = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "present", "sha256"}:
                raise ValueError("Captured process file identity schema differs")
            if row["present"] is not True:
                raise ValueError("Build/package evidence has an absent declared input")
            ref = file_reference({"path": row["path"], "sha256": row["sha256"]})
            result.append(dict(ref, present=True))
        return result

    programs = captured(record.get("program"))
    after_programs = captured(record.get("program_after"))
    inputs = captured(record.get("inputs_before"))
    after_inputs = captured(record.get("inputs_after"))
    final = record.get("job_finalization", {})
    root = record.get("root_identity", {})
    if (
        record.get("state") != "finished"
        or record.get("status") != "passed"
        or record.get("execution") != "completed"
        or type(record.get("exit")) is not int
        or record["exit"] != 0
        or record.get("command") != command
        or Path(record.get("cwd", "")).resolve() != Path(cwd).resolve()
        or programs != [expected_program]
        or after_programs != programs
        or inputs != after_inputs
        or dict(script, present=True) not in inputs
        or root.get("pid") != record.get("pid")
        or not root.get("creation_filetime")
        or root.get("assigned_before_resume") is not True
        or root.get("owned_job_member") is not True
        or Path(root.get("image", "")).resolve() != Path(program["path"])
        or final.get("natural_tree_complete") is not True
        or final.get("controlled_termination") is not False
        or final.get("control_events") != []
        or any(
            final.get(name, {}).get("all_members_attributed") is not True
            or final.get(name, {}).get("members") != []
            for name in ("before_close", "after_cleanup")
        )
    ):
        raise ValueError("Actual build/package command, inputs, program or natural Job differs")
    name = record.get("name")
    if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
        raise ValueError("Actual build/package capture has an unsafe stdio name")
    streams = {}
    for stream in ("stdout", "stderr"):
        target = regular(path.parent / (name + "." + stream))
        if sha(target) != record.get(stream + "_sha256"):
            raise ValueError("Actual build/package stdio differs from its retained process capture")
        streams[stream] = {"path": str(target.resolve()), "sha256": sha(target)}
    return {
        "record": file_reference(reference),
        "streams": streams,
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
    }


def build_record_identity(reference, build_commit, exe_sha):
    record = read(file_reference(reference)["path"])
    if (
        set(record)
        != {
            "schema_version",
            "kind",
            "source_commit",
            "source_checkout",
            "command",
            "cwd",
            "python",
            "build_script",
            "source_archive",
            "build_log",
            "binary",
            "exit_code",
            "process_evidence",
        }
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["kind"] != "move-build-v1"
        or record["source_commit"] != build_commit
        or type(record["exit_code"]) is not int
        or record["exit_code"] != 0
    ):
        raise ValueError("The actual retained build v1 record has a different identity/outcome")
    binary = file_reference(record["binary"])
    python = file_reference(record["python"])
    script = file_reference(record["build_script"])
    log = file_reference(record["build_log"])
    archive = source_archive_identity(record)
    cwd = Path(record["cwd"]).resolve()
    checkout = Path(record["source_checkout"]).resolve()
    argv = python_script_argv(record["command"])
    if (
        binary["sha256"] != exe_sha
        or Path(binary["path"]).name != "kicad-cli.exe"
        or Path(archive["path"]).name != "checker-sources.zip"
        or cwd != checkout
        or Path(script["path"]) != checkout / "build.py"
        or not isinstance(argv, list)
        or len(argv) != 6
        or not all(isinstance(arg, str) for arg in argv)
        or Path(argv[0]).resolve() != Path(python["path"])
        or (cwd / argv[1]).resolve() != Path(script["path"])
        or argv[2] != "--output"
        or (cwd / argv[3]).resolve() != Path(binary["path"]).parent
        or argv[4] != "--work-dir"
        or (cwd / argv[5]).resolve() != Path(archive["path"]).parent
    ):
        raise ValueError("Build command/input archive/output EXE association differs")
    if retained_checkout_commit(checkout) != build_commit:
        raise ValueError("Actual retained checkout HEAD differs from the build commit")
    process = recorded_process_identity(
        record["process_evidence"], python, record["command"], str(cwd), script
    )
    if log not in process["streams"].values():
        raise ValueError("The build log is detached from the actual captured stdout/stderr")
    return {
        "source_commit": build_commit,
        "binary": binary,
        "source_archive": archive,
        "process_evidence": process,
    }


def package_record_identity(reference, config, build, candidate):
    record = read(file_reference(reference)["path"])
    if (
        set(record)
        != {
            "schema_version",
            "kind",
            "source_checkout",
            "python",
            "script",
            "command",
            "cwd",
            "process_evidence",
            "stdout",
        }
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["kind"] != "move-package-v1"
    ):
        raise ValueError("Actual package source evidence must use machine record v1")
    python, script = file_reference(record["python"]), file_reference(record["script"])
    checkout, cwd = Path(record["source_checkout"]).resolve(), Path(record["cwd"]).resolve()
    argv = python_script_argv(record["command"])
    if (
        cwd != checkout
        or retained_checkout_commit(checkout) != candidate["package_source_commit"]
        or Path(script["path"]) != checkout / "scripts/build_internal_candidate.py"
        or Path(argv[0]).resolve() != Path(python["path"])
        or (cwd / argv[1]).resolve() != Path(script["path"])
        or len(argv[2:]) % 2
    ):
        raise ValueError("Actual package command/source checkout identity differs")
    options = {}
    for at in range(2, len(argv), 2):
        key, value = argv[at : at + 2]
        if key in options or key not in {"--source", "--exe", "--output", "--build-source-commit"}:
            raise ValueError("Unexpected/duplicate actual package option")
        options[key] = value
    if not {"--source", "--exe", "--output"}.issubset(options):
        raise ValueError("Actual package call is missing a required input/output")
    if (
        (cwd / options["--source"]).resolve() != checkout
        or (cwd / options["--exe"]).resolve() != Path(build["binary"]["path"])
        or (
            "--build-source-commit" in options
            and options["--build-source-commit"] != build["source_commit"]
        )
        or (
            candidate["package_source_commit"] != build["source_commit"]
            and "--build-source-commit" not in options
        )
    ):
        raise ValueError("Actual package call selects another build EXE/source identity")
    process = recorded_process_identity(
        record["process_evidence"], python, record["command"], str(cwd), script
    )
    if file_reference(record["stdout"]) != process["streams"]["stdout"]:
        raise ValueError("Package JSON is detached from its actual captured stdout")
    package = read(record["stdout"]["path"])
    if (cwd / options["--output"]).resolve() != Path(package["package"]).resolve():
        raise ValueError("Actual package output directory differs from its captured JSON")
    if datetime.fromisoformat(build["process_evidence"]["finished_at"]) > datetime.fromisoformat(
        process["started_at"]
    ):
        raise ValueError("The actual package process predates the selected build")
    return package, process


def public_call_identity(reference, config):
    from bench.native_observation import inspect_trace, root_process

    record = read(file_reference(reference)["path"])
    if (
        set(record) - {"actor"}
        != {
            "schema_version",
            "kind",
            "argv",
            "cwd",
            "trace",
            "stdout",
            "before_files",
            "after_files",
        }
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["kind"] != "move-public-call-v1"
        or not isinstance(record["argv"], list)
        or not record["argv"]
        or not all(isinstance(arg, str) for arg in record["argv"])
        or not Path(record["cwd"]).is_absolute()
    ):
        raise ValueError("An actual public-call v1 record is incomplete")
    if "actor" in record and record["actor"] not in {"root", "independent"}:
        raise ValueError("A public-call actor must be root or independent")
    trace_file = Path(file_reference(record["trace"])["path"])
    stdout = Path(file_reference(record["stdout"])["path"])
    if trace_file.name != "TRACE.json" or stdout != trace_file.parent / "stdout":
        raise ValueError("A public envelope is detached from its actual raw process observation")
    classification = inspect_trace(trace_file.parent, allowed_exit_codes=(0,))
    classification_policy(classification)
    trace = read(trace_file)
    root = root_process(trace)
    if (
        not Path(record["argv"][0]).is_absolute()
        or Path(root["image"]).resolve() != Path(record["argv"][0]).resolve()
        or sha(regular(record["argv"][0])) != config["expected_binary_sha256"]
        or root["image_sha256"] != config["expected_binary_sha256"]
        or root["metadata"]["CommandLine"] != subprocess.list2cmdline(record["argv"])
        or ("cwd" in trace and Path(trace["cwd"]).resolve() != Path(record["cwd"]).resolve())
    ):
        raise ValueError("The actual public command line/image is from another EXE or invocation")
    record["before_files"] = manifest_reference(record["before_files"], current=False)
    record["after_files"] = manifest_reference(record["after_files"], current=False)
    return record, read(stdout), trace


def public_arguments(record, *, readback=False):
    words, options, at = [], {}, 1
    valued = {
        "--board",
        "--moves",
        "--confirm",
        "--format",
        "--schematic",
        "--idempotency-key",
        "--request-id",
    }
    flags = {"--dry-run", "--json", "--quiet", "--compact"}
    argv = record["argv"]
    while at < len(argv):
        atom = argv[at]
        if atom.startswith("--"):
            key, separator, value = atom.partition("=")
            if key in options or key not in valued | flags:
                raise ValueError("Unexpected/duplicate option in the finite public call")
            if key in flags:
                if separator:
                    raise ValueError("A public boolean flag has an attached value")
                options[key] = True
            else:
                if not separator:
                    at += 1
                    if at >= len(argv):
                        raise ValueError("Missing public-call option value")
                    value = argv[at]
                options[key] = value
        else:
            words.append(atom)
        at += 1
    command = ["project", "parity"] if readback else ["board", "footprint", "move"]
    if words != command or ("--format" in options and options["--format"] != "json"):
        raise ValueError("The public invocation is not the declared finite saved-file command")
    if "--board" not in options or not Path(options["--board"]).is_absolute():
        raise ValueError("Public saved-file calls must identify an absolute board target")
    request_keys = {"--idempotency-key", "--request-id"}.intersection(options)
    if len(request_keys) > 1 or (readback and request_keys):
        raise ValueError("Only one write request-id/idempotency-key alias is accepted")
    if request_keys:
        value = options[next(iter(request_keys))]
        if not 1 <= len(value) <= 512 or any(
            ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value
        ):
            raise ValueError("A write request key must contain 1..512 printable characters")
    return options


def captured_file_rows(rows):
    result = {}
    for row in rows:
        if set(row) != {"path", "resolved", "present", "sha256", "identity"}:
            raise ValueError("Captured preflight dependency schema differs")
        path = Path(row["path"])
        if (
            not path.is_absolute()
            or not Path(row["resolved"]).is_absolute()
            or path.resolve() != Path(row["resolved"]).resolve()
            or str(path.resolve()) in result
            or type(row["present"]) is not bool
        ):
            raise ValueError("Captured preflight dependencies have ambiguous file identities")
        actual = path.is_file()
        if (
            actual is not row["present"]
            or row["sha256"] != (sha(regular(path)) if actual else None)
            or (path.exists() and not actual)
        ):
            raise ValueError("Actual preflight dependency bytes/presence differ from capture")
        result[str(path.resolve())] = row["sha256"]
    return result


def captured_tree_identity(value, expected_path):
    root = Path(expected_path).resolve()
    if (
        set(value) != {"path", "present", "directories", "files"}
        or Path(value["path"]).resolve() != root
        or type(value["present"]) is not bool
        or value["present"] is not root.is_dir()
    ):
        raise ValueError("Actual preflight configuration/resource tree identity differs")
    hashes = tree(root)
    if len(value["files"]) != len(set(value["files"])) or set(value["files"]) != set(hashes):
        raise ValueError("Actual preflight configuration/resource file closure differs")
    expected_directories = {}
    if root.is_dir():
        for folder in [root, *(p for p in root.rglob("*") if p.is_dir())]:
            expected_directories[folder.relative_to(root).as_posix()] = sorted(
                p.name for p in folder.iterdir()
            )
    directories = {}
    for row in value["directories"]:
        if set(row) != {"relative", "identity", "members"} or row["relative"] in directories:
            raise ValueError("Captured directory membership schema differs")
        directories[row["relative"]] = row["members"]
    if directories != expected_directories:
        raise ValueError("Actual preflight directory membership differs from capture")
    return {str((root / name).resolve()): digest for name, digest in hashes.items()}


def preflight_context_identity(envelope, config, project_files):
    binding = envelope["data"]["preview"]["extensions"]["preflight"]["input_binding"]
    selection = binding["selection"]
    native = Path(config["native_home"]).resolve()
    official = native / "bin/kicad-cli.exe"
    if (
        Path(selection["installation"]).resolve() != native
        or Path(selection["official_executable"]).resolve() != official
        or selection["expected_version_series"] != "10.0"
    ):
        raise ValueError("Public preflight used another official KiCad installation/tool")
    project = Path(project_files["edit.kicad_pcb"]["path"]).parent
    seed = Path(selection["config_root"])
    if not seed.is_absolute() or seed.resolve().is_relative_to(project):
        raise ValueError("Public preflight configuration seed is not a separate absolute tree")
    seed = seed.resolve()
    # Project PCB is intentionally different now for the successful public save;
    # its captured baseline is verified by the independent public-chain helper.
    external_rows = [
        row for row in binding["files"] if not Path(row["path"]).resolve().is_relative_to(project)
    ]
    if any(not Path(row["path"]).resolve().is_relative_to(seed) for row in external_rows):
        raise ValueError("Public preflight consulted an undeclared external project dependency")
    config_files = captured_file_rows(external_rows)
    captured_config = captured_tree_identity(binding["configuration_tree"], seed)
    expected_config_paths = set(captured_config) | {
        str((seed / "10.0" / name).resolve()) for name in ("kicad_common.json", "fp-lib-table")
    }
    if {
        name: digest for name, digest in config_files.items() if digest is not None
    } != captured_config or set(config_files) != expected_config_paths:
        raise ValueError("Public preflight configuration capture is incomplete")
    tools = binding["tools"]
    actual_tools = captured_file_rows(tools["files"])
    dlls = sorted(p.name for p in (native / "bin").iterdir() if p.suffix.lower() == ".dll")
    expected_tools = {str(official.resolve()): sha(regular(official))}
    expected_tools.update(
        {
            str((native / "bin" / name).resolve()): sha(regular(native / "bin" / name))
            for name in dlls
        }
    )
    if tools["bin_DLL_members"] != dlls or len(tools["resource_trees"]) != 3:
        raise ValueError("Public preflight native tool/resource inventory differs")
    resource_paths = {
        native / "share/kicad" / name for name in ("resources", "schemas", "internat")
    }
    if {Path(value["path"]).resolve() for value in tools["resource_trees"]} != resource_paths:
        raise ValueError("Public preflight selected another native resource tree")
    for value in tools["resource_trees"]:
        expected_tools.update(captured_tree_identity(value, value["path"]))
    if actual_tools != expected_tools:
        raise ValueError(
            "Actual public preflight tool/resource bytes differ from frozen dependencies"
        )
    return {
        "configuration_seed": str(seed),
        "configuration_files": config_files,
        "official_executable": {
            "path": str(official.resolve()),
            "sha256": expected_tools[str(official.resolve())],
        },
        "tool_files": actual_tools,
    }


def _validate_source_binding(config):
    document = read(file_reference(config["source_binding"])["path"])
    if (
        set(document)
        != {
            "schema_version",
            "kind",
            "build_commit",
            "binary",
            "candidate_archive",
            "scenes",
            "negative_candidate_export",
            "evidence",
        }
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["kind"] != "move-safety-source-binding"
        or not isinstance(document["build_commit"], str)
        or re.fullmatch(r"[0-9a-f]{40}", document["build_commit"]) is None
    ):
        raise ValueError("Source binding must be the explicit finite machine schema v1")
    binary = file_reference(document["binary"])
    archive = file_reference(document["candidate_archive"])
    if binary != {
        "path": str(Path(config["binary"]).resolve()),
        "sha256": config["expected_binary_sha256"],
    }:
        raise ValueError("Source binding selects another executable")
    if archive != {
        "path": str(Path(config["candidate_archive"]).resolve()),
        "sha256": config["expected_candidate_archive_sha256"],
    }:
        raise ValueError("Source binding selects another candidate ZIP")
    scenes = {name: manifest_reference(files) for name, files in document["scenes"].items()}
    if scenes != source_manifests(config):
        raise ValueError("Source binding selects another saved engineering scene")
    evidence = {
        row["role"]: {"path": row["path"], "sha256": row["sha256"]}
        for row in config["source_evidence"]
    }
    if document["evidence"] != evidence:
        raise ValueError("The six semantic source roles are detached from the bound raw records")
    build = build_record_identity(evidence["build"], document["build_commit"], binary["sha256"])
    candidate = package_identity(config)
    package, package_process = package_record_identity(
        evidence["package"], config, build, candidate
    )
    with zipfile.ZipFile(archive["path"]) as packed:
        package_files = json.loads(packed.read("FILES.sha256.json"))["files"]
    if (
        set(package)
        != {"package", "archive", "archive_sha256", "candidate", "files", "local_links_checked"}
        or Path(package["archive"]).resolve() != Path(archive["path"])
        or package["archive_sha256"] != archive["sha256"]
        or package["candidate"] != candidate
        or candidate["source_commit"] != document["build_commit"]
        or package["local_links_checked"] is not True
        or type(package["files"]) is not int
        or package["files"] != len(package_files)
    ):
        raise ValueError("Actual package output/ZIP/CANDIDATE/build association differs")
    calls, envelopes, traces = {}, {}, {}
    for role in EVIDENCE_ROLES - {"build", "package"}:
        calls[role], envelopes[role], traces[role] = public_call_identity(evidence[role], config)
    originals = config["source_files"]["baseline"]
    negative = calls["negative_preflight"]
    negative_files = manifest_reference(negative["after_files"])
    if (
        negative["before_files"] != negative_files
        or {name: item["sha256"] for name, item in negative_files.items()} != originals
    ):
        raise ValueError(
            "The blocked negative preview changed or selected another baseline project"
        )
    exported = document["negative_candidate_export"]
    if (
        set(exported) != {"origin", "saved"}
        or file_reference(exported["saved"]) != scenes["negative"]["edit.kicad_pcb"]
    ):
        raise ValueError("The negative candidate export is not the actual native input")
    negative_check = check_negative_preview(
        envelopes["negative_preflight"],
        negative_files,
        dict(exported["saved"], origin=exported["origin"]),
    )
    positive = scenes["positive"]
    before = {name: dict(item, sha256=originals[name]) for name, item in positive.items()}
    preview, confirm, readback = (
        calls["positive_" + name] for name in ("preview", "confirm", "readback")
    )
    if (
        preview["before_files"] != before
        or preview["after_files"] != before
        or confirm["before_files"] != before
        or confirm["after_files"] != positive
        or readback["before_files"] != positive
        or readback["after_files"] != positive
    ):
        raise ValueError(
            "The actual preview/confirm/readback before-after eight-file chain differs"
        )
    positive_check = check_public_move_chain(
        envelopes["positive_preview"],
        envelopes["positive_confirm"],
        envelopes["positive_readback"],
        positive,
        originals,
    )
    contexts = {
        "negative": preflight_context_identity(
            envelopes["negative_preflight"], config, negative_files
        ),
        "positive": preflight_context_identity(envelopes["positive_preview"], config, positive),
    }
    for role, trace in traces.items():
        predecessor = build["process_evidence"] if role == "negative_preflight" else package_process
        if datetime.fromisoformat(predecessor["finished_at"]) > datetime.fromisoformat(
            trace["started_at"]
        ):
            raise ValueError("A retained public use predates its selected build/package producer")
    for role, record in calls.items():
        options = public_arguments(record, readback=role == "positive_readback")
        target = (
            positive["edit.kicad_pcb"]["path"]
            if role.startswith("positive_")
            else negative_files["edit.kicad_pcb"]["path"]
        )
        if Path(options["--board"]).resolve() != Path(target):
            raise ValueError("An actual public call selects another project target")
        if role == "positive_readback":
            if {"--moves", "--dry-run", "--confirm"}.intersection(options):
                raise ValueError("Readback evidence contains a modifying invocation")
            if "--schematic" in options and Path(options["--schematic"]).resolve() != Path(
                positive["edit.kicad_sch"]["path"]
            ):
                raise ValueError("Readback selected another schematic")
        else:
            wanted = "R1:98.5,100" if role == "negative_preflight" else "R4:99.5,110"
            if options.get("--moves") != wanted or "--schematic" in options:
                raise ValueError(
                    "An actual preview/confirm requested another footprint translation"
                )
            if role.endswith("preview") or role == "negative_preflight":
                if options.get("--dry-run") is not True or "--confirm" in options:
                    raise ValueError("Preview evidence does not describe one dry-run")
            elif (
                "--dry-run" in options
                or options.get("--confirm")
                != envelopes["positive_preview"]["data"]["confirm_token"]
            ):
                raise ValueError("The actual confirm argv is detached from its preview token")
            if role == "positive_confirm":
                key = options.get("--idempotency-key", options.get("--request-id"))
                receipt = envelopes["positive_confirm"]["data"]["receipt"]
                if receipt.get("request_id") != key or receipt.get("replayed", False) is not False:
                    raise ValueError(
                        "The actual confirm request key/replay state differs from its receipt"
                    )
    for first, second in (
        ("positive_preview", "positive_confirm"),
        ("positive_confirm", "positive_readback"),
    ):
        if datetime.fromisoformat(traces[first]["finished_at"]) > datetime.fromisoformat(
            traces[second]["started_at"]
        ):
            raise ValueError("The retained public receipt chain is out of chronological order")
    return {
        "schema_version": 1,
        "status": "passed",
        "build": build,
        "candidate_source_commit": candidate["source_commit"],
        "negative": negative_check,
        "positive": positive_check,
        "public_preflight_contexts": contexts,
        "call_sources": {
            role: record.get("actor", "unspecified") for role, record in calls.items()
        },
        "package_process": package_process,
        "confirm_token_sha256": hashlib.sha256(
            envelopes["positive_preview"]["data"]["confirm_token"].encode()
        ).hexdigest(),
        "scope": (
            "Machine-linked retained build/package/public records; "
            "not signed or running-bytecode attestation; "
            "no token signature validation or state access"
        ),
    }


def validate_source_binding(config):
    try:
        return _validate_source_binding(config)
    except (KeyError, TypeError, IndexError, AttributeError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Incomplete machine source-binding evidence: " + str(exc)) from exc


def load_config(path):
    value = read(regular(path))
    if set(value) != REQUIRED or value["schema_version"] != 1:
        raise ValueError("Explicit safety acceptance config has unknown/missing fields")
    if value["expected"] != EXPECTED:
        raise ValueError("The three predeclared fixed expectations differ")
    if value["expected_sdk_commit"] != PIN or value["expected_native_version"] != "10.0.6":
        raise ValueError("The existing SDK/spec and native version remain fixed")
    if type(value["timeout_seconds"]) not in (int, float) or not (
        0 < value["timeout_seconds"] <= 300
    ):
        raise ValueError("Every consumer needs a bounded timeout within 1..300 seconds")
    for key in (
        "output",
        "repository",
        "native_home",
        "host_python",
        "binary",
        "candidate_archive",
        "sdk",
        "spec",
        "dependency_pin_manifest",
    ):
        if not Path(value[key]).is_absolute():
            raise ValueError("Config path must be absolute: " + key)
    if set(value["sources"]) != set(SCENES) or set(value["source_files"]) != set(SCENES):
        raise ValueError("Exactly baseline/negative/positive saved inputs are required")
    for scene in SCENES:
        if (
            set(value["sources"][scene]) != EXPECTED_FILES
            or set(value["source_files"][scene]) != EXPECTED_FILES
        ):
            raise ValueError("Each saved scene must bind exactly the original eight inputs")
        if any(not Path(p).is_absolute() for p in value["sources"][scene].values()):
            raise ValueError("Every source file locator must be absolute")
    if value["source_files"]["baseline"]["edit.kicad_pcb"] != BASELINE_PCB_SHA256:
        raise ValueError("The frozen original PCB SHA-256 differs")
    for scene in ("negative", "positive"):
        for name in EXPECTED_FILES - {"edit.kicad_pcb"}:
            if value["source_files"][scene][name] != value["source_files"]["baseline"][name]:
                raise ValueError("A protected non-board source differs: " + scene + "/" + name)
    for digest in (
        value["expected_binary_sha256"],
        value["expected_candidate_archive_sha256"],
        *(d for files in value["source_files"].values() for d in files.values()),
    ):
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Every declared SHA-256 must be a lowercase hexadecimal digest")
    if not isinstance(value["source_evidence"], list) or not value["source_evidence"]:
        raise ValueError("The new build/package/independent-use evidence must be bound")
    roles = [item.get("role") for item in value["source_evidence"] if isinstance(item, dict)]
    if (
        any(not isinstance(role, str) or not role for role in roles)
        or len(roles) != len(set(roles))
        or not EVIDENCE_ROLES.issubset(roles)
    ):
        raise ValueError("Each new build/package/negative/positive evidence role is required")
    for item in (value["source_binding"], value["intent_binding"], *value["source_evidence"]):
        binding_record(item)
    intent = read(value["intent_binding"]["path"])
    operation = intent.get("fixed_operation", {})
    if (
        operation.get("reference") != "R4"
        or operation.get("footprint_uuid") != EXPECTED["positive"]["uuid"]
        or operation.get("from_mm") != [100, 110]
        or operation.get("to_mm") != [99.5, 110]
        or {m["relative_path"]: m["sha256"] for m in intent["source"]["input_manifest"]}
        != value["source_files"]["baseline"]
    ):
        raise ValueError("The original positive engineering intent or eight-input manifest differs")
    validate_source_binding(value)
    value["_config_binding"] = {"path": str(Path(path).resolve()), "sha256": sha(path)}
    return value


def source_identity(config):
    result = {}
    for scene in SCENES:
        result[scene] = {}
        for name, path in sorted(config["sources"][scene].items()):
            digest = sha(regular(path))
            if digest != config["source_files"][scene][name]:
                raise ValueError("Saved source input drift: " + scene + "/" + name)
            result[scene][name] = {"path": str(Path(path).resolve()), "sha256": digest}
    return result


def method_identity(config):
    result = shared_method_identity(config)
    repo = Path(config["repository"])
    for name in (
        "move_safety_acceptance.py",
        "move_safety_checks.py",
        "move_engineering_checks.py",
    ):
        path = regular(repo / "bench" / name)
        result[str(path.resolve())] = sha(path)
    return result


def identity(config):
    binding_record(config["_config_binding"])
    binding_record(config["intent_binding"])
    archive = regular(config["candidate_archive"])
    if sha(archive) != config["expected_candidate_archive_sha256"]:
        raise ValueError("The exact new candidate ZIP changed")
    return {
        "config": config,
        "methods": method_identity(config),
        "dependencies": dependency_identity(config),
        "environment": environment_identity(config),
        "original_source_files": source_identity(config),
        "candidate_archive": {"path": str(archive.resolve()), "sha256": sha(archive)},
        "original_positive_intent": config["intent_binding"],
        "source_association": validate_source_binding(config),
    }


def package_identity(config):
    with zipfile.ZipFile(regular(config["candidate_archive"])) as packed:
        names = packed.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Candidate ZIP contains duplicate entries")
        candidate = json.loads(packed.read("CANDIDATE.json"))
        exe_sha = hashlib.sha256(packed.read("bin/kicad-cli.exe")).hexdigest()
        manifest = json.loads(packed.read("FILES.sha256.json"))
        actual = {
            name: hashlib.sha256(packed.read(name)).hexdigest()
            for name in names
            if name != "FILES.sha256.json"
        }
        if (
            set(manifest) != {"source_commit", "files"}
            or manifest["source_commit"] != candidate.get("package_source_commit")
            or manifest["files"] != actual
            or any(
                Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name
                for name in names
            )
        ):
            raise ValueError("Actual candidate ZIP members differ from its complete file manifest")
    if (
        exe_sha != config["expected_binary_sha256"]
        or candidate.get("sha256") != exe_sha
        or candidate.get("external_driver_pin") != PIN
        or candidate.get("native_case_version") != config["expected_native_version"]
        or candidate.get("release_readiness") != "unpublishable"
        or candidate.get("executable") != "bin/kicad-cli.exe"
    ):
        raise ValueError("New EXE/ZIP/candidate/pin identities do not agree")
    return candidate


def config_values(root):
    result = {}
    for name in tree(root / "configuration"):
        if name not in PREPARATION_CONFIG_ADDITIONS:
            raise ValueError("Undeclared configuration file: " + name)
        value = read(root / "configuration" / name)
        if not isinstance(value, dict):
            raise ValueError("Configuration must be a complete regular JSON object")
        result[name] = value
    return result


def config_identity(root):
    result = {}
    allowed = {str((root / name).resolve()) for name in PROJECTS}
    for name, digest in tree(root / "configuration").items():
        if name == "10.0/kicad_common.json":
            value = read(root / "configuration" / name)
            working = value.get("system", {}).get("working_dir")
            if not isinstance(working, str) or str(Path(working).resolve()) not in allowed:
                raise ValueError("working_dir escapes the four predeclared projects")
            value["system"]["working_dir"] = "<predeclared-consumer-working-directory>"
            result[name] = {"canonical_json": value}
        else:
            result[name] = {"sha256": digest}
    return result


def validate_config_transition(root, before):
    after = config_values(root)
    if set(before) - set(after):
        raise ValueError("Initialization removed an existing preference")
    for name, old in before.items():
        new = copy.deepcopy(after[name])
        if old == new:
            continue
        if name != "10.0/kicad_common.json":
            raise ValueError("A preference changed outside working_dir metadata")
        previous = old.get("system", {}).get("working_dir")
        if not isinstance(previous, str):
            raise ValueError("Existing common working_dir metadata is missing")
        config_identity(root)
        new["system"]["working_dir"] = previous
        if new != old:
            raise ValueError("Common preferences changed outside working_dir metadata")
    config_identity(root)


def project_trees(root):
    return {name: tree(root / name) for name in PROJECTS}


def stop_tree(path):
    try:
        return {"files": tree(path)}
    except (OSError, ValueError) as exc:
        return {"unavailable": type(exc).__name__ + ": " + str(exc)}


def expected_engineering(config, metadata=None):
    result = {}
    for project in PROJECTS:
        origin = "baseline" if project == "preparation-project" else project
        result[project] = dict(config["source_files"][origin])
        if metadata and metadata["present"]:
            result[project]["edit.kicad_prl"] = metadata["sha256"]
    return result


def source_structure(config):
    base = config["sources"]["baseline"]["edit.kicad_pcb"]
    return {
        scene: check_board(base, config["sources"][scene]["edit.kicad_pcb"], scene)
        for scene in SCENES
    }


def reference_scope(project):
    """Keep this fixture-local consumer independent of global library collections."""
    board = sexp((project / "edit.kicad_pcb").read_text(encoding="utf-8"))
    for fp in children(board, "footprint"):
        if not json.loads(fp[1]).startswith("fixture:") or children(fp, "model"):
            raise ValueError("A board library/model reference escaped the frozen local fixture")
    for name in ("edit.kicad_sch", "edit_child.kicad_sch"):
        schematic = sexp((project / name).read_text(encoding="utf-8"))
        for symbol in children(schematic, "symbol"):
            if not json.loads(one(symbol, "lib_id")[1]).startswith("fixture:"):
                raise ValueError("A schematic symbol escaped its embedded fixture library")
    table = sexp((project / "fp-lib-table").read_text(encoding="utf-8"))
    libraries = children(table, "lib")
    if (
        len(libraries) != 1
        or one(libraries[0], "name")[1] != '"fixture"'
        or one(libraries[0], "type")[1] != '"KiCad"'
        or one(libraries[0], "uri")[1] != '"${KIPRJMOD}/fixture.pretty"'
    ):
        raise ValueError("The local footprint table resolution differs")


def native_calls(config, scene):
    root = Path(config["output"])
    preparing = scene == "prepare"
    project = root / ("preparation-project" if preparing else scene)
    output = root / ("preparation-output" if preparing else scene + "-output")
    native = Path(config["native_home"]) / "bin"
    board = project / "edit.kicad_pcb"
    rows = [
        (
            "sdk",
            [
                native / "python.exe",
                "-B",
                "-S",
                Path(config["repository"]) / "bench/saved_scene_native_read.py",
                board,
            ],
            ["owned-call/stdout"],
        ),
        (
            "drc",
            [
                native / "kicad-cli.exe",
                "pcb",
                "drc",
                "--severity-all",
                "--format",
                "json",
                "--output",
                output / "drc.json",
                board,
            ],
            [str(output / "drc.json")],
        ),
    ]
    for side, layers in (("front", "F.Cu,F.SilkS,Edge.Cuts"), ("back", "B.Cu,B.SilkS,Edge.Cuts")):
        rows.append(
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
                [str(output / (side + ".svg"))],
            )
        )
    return [
        {
            "id": scene + "-" + name,
            "kind": name,
            "scene": scene,
            "argv": list(map(str, argv)),
            "cwd": str(project),
            "timeout_seconds": config["timeout_seconds"],
            "allowed_exit_codes": [0],
            "expected_outputs": outputs,
        }
        for name, argv, outputs in rows
    ]


def preparation_calls(config):
    root = Path(config["output"])
    return [
        {
            "id": "prepare-version",
            "kind": "version",
            "scene": "prepare",
            "argv": [str(Path(config["native_home"]) / "bin/kicad-cli.exe"), "--version"],
            "cwd": str(root / "preparation-project"),
            "timeout_seconds": config["timeout_seconds"],
            "allowed_exit_codes": [0],
            "expected_outputs": ["owned-call/stdout"],
        },
        *native_calls(config, "prepare"),
    ]


def render_calls(config, scene):
    root = Path(config["output"])
    output = root / (scene + "-output")
    return [
        {
            "id": scene + "-" + side + "-render",
            "kind": "render",
            "scene": scene,
            "argv": [
                config["host_python"],
                "-B",
                "-S",
                str(Path(config["repository"]) / "bench/saved_scene_render.py"),
                str(output / (side + ".svg")),
                str(output / (side + ".png")),
                "8",
            ],
            "cwd": str(root / scene),
            "timeout_seconds": config["timeout_seconds"],
            "allowed_exit_codes": [0],
            "expected_outputs": [str(output / (side + ".png"))],
            "page_identity": "PCB-" + side,
        }
        for side in ("front", "back")
    ]


def complete_output_names():
    return ["back.png", "back.svg", "drc.json", "front.png", "front.svg"]


def reject_stopped(root):
    for stage in ("stage-inputs", "prepare", "freeze", "execute"):
        if (root / (stage.upper() + "-STOP.json")).exists():
            raise ValueError("A stopped cycle is retained; no retry/resume/refreeze is supported")


def stage_inputs(config):
    root = Path(config["output"])
    if root.exists():
        raise ValueError("A new safety consumer cycle output directory is required")
    binding = identity(config)
    structures = source_structure(config)
    candidate = package_identity(config)
    root.mkdir(parents=True)
    (root / "configuration").mkdir()
    for scene in PROJECTS:
        origin = "baseline" if scene == "preparation-project" else scene
        folder = root / scene
        folder.mkdir()
        for name, source in config["sources"][origin].items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(regular(source), target)
        if tree(folder) != config["source_files"][origin]:
            raise ValueError("Staging did not preserve exact eight-file source bytes")
        reference_scope(folder)
    (root / "preparation-output").mkdir()
    value = {
        "schema_version": 1,
        "at": now(),
        "binding": binding,
        "candidate": candidate,
        "engineering": project_trees(root),
        "independent_saved_structure": structures,
        "expected": EXPECTED,
        "joint_representation_policy": JOINT_POLICY,
        "preparation_calls": preparation_calls(config),
        "native_consumers": {s: native_calls(config, s) for s in SCENES},
        "host_renderers": {s: render_calls(config, s) for s in SCENES},
        "counts": CONSUMER_COUNTS,
        "metadata_policy": {
            "preparation_new_config_paths": sorted(PREPARATION_CONFIG_ADDITIONS),
            "formal_mutable_pointer": "10.0/kicad_common.json#/system/working_dir",
            "allowed_working_directories": [str((root / p).resolve()) for p in PROJECTS],
            "project_metadata": (
                "Only optional prepared edit.kicad_prl; presence and exact SHA "
                "frozen before formal calls"
            ),
            "engineering_rules_libraries_ignores": "strict; no changes or formal additions",
        },
        "dependency_policy": dependency_policy(config),
        "schematic_parity": {"status": "not_executed", "native_parity_pass_claim": False},
        "product_writer_or_confirm_calls": 0,
        "native_calls_started": 0,
    }
    put(root / "INPUT-FROZEN.json", value)
    return {"input_sha256": sha(root / "INPUT-FROZEN.json"), "native_calls_started": 0}


def native_runtime_identity(value):
    return {name: value[name] for name in ("startup", "reader_sha256", "pcbnew_wrapper_sha256")}


def svg_identity(path):
    document = ET.parse(regular(path))
    root = document.getroot()
    viewport = root.get("viewBox", "").split()
    if root.tag.split("}")[-1] != "svg" or len(viewport) != 4:
        raise ValueError("Native full SVG viewport is missing")
    dimensions = list(map(float, viewport))
    if dimensions[2] <= 0 or dimensions[3] <= 0:
        raise ValueError("Native SVG viewport is empty")
    return {"sha256": sha(path), "viewBox": viewport, "coverage": "full native SVG; no cropping"}


def verify_outputs(row, directory):
    for output in row["expected_outputs"]:
        regular(directory / "stdout" if output == "owned-call/stdout" else output)


def classification_policy(value):
    if (
        value.get("status") not in {"completed", "completed_with_forwarded_first_chance"}
        or value.get("may_validate_functional_output") is not True
        or value.get("observation_complete") is not True
        or value.get("stop_required") is not False
        or value.get("functional_output_verified") is not False
        or value.get("issues")
        or value.get("second_chance_notifications")
        or value.get("fail_fast_status_notifications")
    ):
        raise ValueError("Complete instance-bound DEBUG and natural whole-Job evidence is required")


def call_with_deltas(config, row, directory):
    root = Path(config["output"])
    for output in row["expected_outputs"]:
        if output != "owned-call/stdout" and (Path(output).exists() or Path(output).is_symlink()):
            raise ValueError("A fresh declared consumer output already exists")
    before, projects = tree(root / "configuration"), project_trees(root)
    values = config_values(root)
    try:
        result = record_call(config, row, directory)
    finally:
        put(
            directory.parent / (row["id"] + ".DELTAS.json"),
            {
                "at": now(),
                "configuration": deltas(before, tree(root / "configuration")),
                "projects": {p: deltas(projects[p], tree(root / p)) for p in PROJECTS},
                "configuration_values_before": values,
                "configuration_values_after": config_values(root),
            },
        )
    classification_policy(result)
    validate_config_transition(root, values)
    verify_outputs(row, directory)
    return result


def prepare(config, digest):
    root = Path(config["output"])
    reject_stopped(root)
    if sha(root / "INPUT-FROZEN.json") != digest:
        raise ValueError("Preparation must consume the original input SHA-256")
    initial = read(root / "INPUT-FROZEN.json")
    if (
        identity(config) != initial["binding"]
        or project_trees(root) != initial["engineering"]
        or initial["engineering"] != expected_engineering(config)
        or initial["preparation_calls"] != preparation_calls(config)
        or initial["native_consumers"] != {s: native_calls(config, s) for s in SCENES}
        or initial["host_renderers"] != {s: render_calls(config, s) for s in SCENES}
        or initial["counts"] != CONSUMER_COUNTS
        or initial["expected"] != EXPECTED
        or initial["joint_representation_policy"] != JOINT_POLICY
        or tree(root / "configuration")
        or tree(root / "preparation-output")
    ):
        raise ValueError("Pre-preparation method/dependency/source/configuration drift")
    put(root / "PREPARE-START.json", {"at": now(), "input_sha256": digest})
    evidence = root / "preparation-calls"
    evidence.mkdir()
    native_identity = None
    sealed, sealed_trees, outputs = {}, {}, set()

    def verify_retained():
        for path, expected_sha in sealed.items():
            if sha(regular(path)) != expected_sha:
                raise ValueError("Previously consumed preparation output/evidence changed")
        for path, expected_tree in sealed_trees.items():
            if tree(path) != expected_tree:
                raise ValueError("Previously completed preparation call file set changed")

    for row in initial["preparation_calls"]:
        verify_retained()
        if identity(config) != initial["binding"]:
            raise ValueError("Preparation method/dependency/source/environment changed")
        call_with_deltas(config, row, evidence / row["id"])
        verify_retained()
        current = project_trees(root)
        for project in PROJECTS:
            original = initial["engineering"][project]
            if any(current[project].get(k) != v for k, v in original.items()) or set(
                current[project]
            ) - set(original) - ({"edit.kicad_prl"} if project == "preparation-project" else set()):
                raise ValueError("Preparation changed engineering or undeclared metadata")
        if row["kind"] == "version":
            if (evidence / row["id"] / "stdout").read_text(encoding="utf-8").strip() != "10.0.6":
                raise ValueError("Prepared official native version differs")
        elif row["kind"] == "sdk":
            observed = read(evidence / row["id"] / "stdout")
            path = root / "preparation-project/edit.kicad_pcb"
            check_native(observed, observed, path, path, "baseline")
            native_identity = native_runtime_identity(observed)
        elif row["kind"] == "drc":
            report = read(root / "preparation-output/drc.json")
            path = root / "preparation-project/edit.kicad_pcb"
            check_drc(report, report, path, path, "baseline")
        else:
            svg_identity(root / "preparation-output" / (row["kind"].removesuffix("-svg") + ".svg"))
        outputs.update(
            Path(name).name for name in row["expected_outputs"] if name != "owned-call/stdout"
        )
        if set(tree(root / "preparation-output")) != outputs:
            raise ValueError("Preparation output file set differs from completed declared calls")
        sealed_trees[str((evidence / row["id"]).resolve())] = tree(evidence / row["id"])
        for folder in (evidence, root / "preparation-output"):
            for name, expected_sha in tree(folder).items():
                sealed[str((folder / name).resolve())] = expected_sha
    verify_retained()
    metadata = root / "preparation-project/edit.kicad_prl"
    if metadata.is_symlink() or (metadata.exists() and not metadata.is_file()):
        raise ValueError("Prepared metadata must be absent or a regular non-link file")
    if metadata.is_file():
        for scene in SCENES:
            shutil.copyfile(metadata, root / scene / metadata.name)
    if native_identity is None:
        raise ValueError("The prepared native SDK startup identity is missing")
    value = {
        "schema_version": 1,
        "at": now(),
        "input_sha256": digest,
        "binding": identity(config),
        "configuration": tree(root / "configuration"),
        "configuration_canonical": config_identity(root),
        "engineering": project_trees(root),
        "preparation_outputs": tree(root / "preparation-output"),
        "preparation_call_evidence": tree(evidence),
        "native_runtime_identity": native_identity,
        "prepared_project_metadata": {
            "present": metadata.is_file(),
            "sha256": sha(metadata) if metadata.is_file() else None,
            "formal_policy": "exact presence and bytes; no further project metadata additions",
        },
        "preparation_native_calls": 5,
        "formal_native_calls": 0,
        "formal_host_calls": 0,
    }
    put(root / "PREPARATION.json", value)
    return {"preparation_sha256": sha(root / "PREPARATION.json"), "preparation_native_calls": 5}


def freeze(config, digest):
    root = Path(config["output"])
    reject_stopped(root)
    put(root / "FREEZE-START.json", {"at": now(), "preparation_sha256": digest})
    if sha(root / "PREPARATION.json") != digest:
        raise ValueError("Freeze must consume the original preparation SHA-256")
    initial, prepared = read(root / "INPUT-FROZEN.json"), read(root / "PREPARATION.json")
    if (
        sha(root / "INPUT-FROZEN.json") != prepared["input_sha256"]
        or identity(config) != initial["binding"]
        or prepared["binding"] != initial["binding"]
        or prepared["configuration"] != tree(root / "configuration")
        or prepared["engineering"] != project_trees(root)
        or prepared["engineering"]
        != expected_engineering(config, prepared["prepared_project_metadata"])
        or prepared["preparation_outputs"] != tree(root / "preparation-output")
        or prepared["preparation_call_evidence"] != tree(root / "preparation-calls")
    ):
        raise ValueError("Prepared method/engineering/configuration/evidence drift before freeze")
    for scene in SCENES:
        if (root / (scene + "-output")).exists() or (root / (scene + "-calls")).exists():
            raise ValueError("Formal consumer output must not predate the original plan")
    plan = {
        "schema_version": 1,
        "at": now(),
        "binding": initial["binding"],
        "input_sha256": prepared["input_sha256"],
        "preparation_sha256": digest,
        "engineering": prepared["engineering"],
        "configuration": prepared["configuration_canonical"],
        "native_runtime_identity": prepared["native_runtime_identity"],
        "expected": EXPECTED,
        "joint_representation_policy": JOINT_POLICY,
        "independent_saved_structure": source_structure(config),
        "native_consumers": {s: native_calls(config, s) for s in SCENES},
        "host_renderers": {s: render_calls(config, s) for s in SCENES},
        "complete_output_names": complete_output_names(),
        "counts": initial["counts"],
        "metadata_policy": initial["metadata_policy"],
        "schematic_parity": initial["schematic_parity"],
        "truth": (
            "original declared R1/R4 rigid translations; independent saved-file checks; "
            "no output-derived refreeze"
        ),
        "manual_views_completed": 0,
    }
    put(root / "ORIGINAL-PLAN.json", plan)
    return {
        "plan": str(root / "ORIGINAL-PLAN.json"),
        "plan_sha256": sha(root / "ORIGINAL-PLAN.json"),
        "formal_native_calls": 0,
    }


def verify_plan(config, path, digest):
    root = Path(config["output"])
    reject_stopped(root)
    if Path(path).resolve() != (root / "ORIGINAL-PLAN.json").resolve() or sha(path) != digest:
        raise ValueError("The explicit original formal plan path/SHA-256 differs")
    approved = read(path)
    if (
        approved["binding"] != identity(config)
        or approved["engineering"] != project_trees(root)
        or approved["configuration"] != config_identity(root)
        or approved["expected"] != EXPECTED
        or approved["joint_representation_policy"] != JOINT_POLICY
        or approved["counts"] != CONSUMER_COUNTS
        or approved["native_consumers"] != {s: native_calls(config, s) for s in SCENES}
        or approved["host_renderers"] != {s: render_calls(config, s) for s in SCENES}
        or approved["complete_output_names"] != complete_output_names()
        or sha(root / "INPUT-FROZEN.json") != approved["input_sha256"]
        or sha(root / "PREPARATION.json") != approved["preparation_sha256"]
    ):
        raise ValueError("Original plan/method/dependency/source/configuration/command drift")
    prepared = read(root / "PREPARATION.json")
    if (
        prepared["preparation_outputs"] != tree(root / "preparation-output")
        or prepared["preparation_call_evidence"] != tree(root / "preparation-calls")
        or approved["native_runtime_identity"] != prepared["native_runtime_identity"]
        or approved["engineering"]
        != expected_engineering(config, prepared["prepared_project_metadata"])
    ):
        raise ValueError("Retained initialization evidence or runtime identity changed")
    return approved


def render_identity(row, directory):
    result = read(directory / "stdout")
    source, target = map(Path, row["argv"][4:6])
    if (
        result.get("source_sha256") != sha(source)
        or result.get("target_sha256") != sha(target)
        or result.get("coverage") != "full SVG viewport; no cropping"
        or result.get("startup", {}).get("no_site") is not True
        or result.get("startup", {}).get("dont_write_bytecode") is not True
        or result.get("startup", {}).get("pth_user_site_sitecustomize_executed") is not False
        or not result.get("actual_module_sources")
        or len(result.get("pixel_size", [])) != 2
        or any(type(n) is not int or n <= 0 for n in result["pixel_size"])
    ):
        raise ValueError("Complete host image/startup/module-source binding differs")
    return {
        "id": row["id"],
        "scene": row["scene"],
        "page_identity": row["page_identity"],
        "source": str(source),
        "target": str(target),
        "source_sha256": sha(source),
        "target_sha256": sha(target),
        "pixel_size": result["pixel_size"],
        "coverage": result["coverage"],
    }


def execute(config, plan, digest):
    root = Path(config["output"])
    reject_stopped(root)
    put(root / "EXECUTION-START.json", {"at": now(), "original_plan_sha256": digest})
    approved = verify_plan(config, plan, digest)
    sealed, sealed_trees, results, classifications = {}, {}, {}, []

    def verify():
        verify_plan(config, plan, digest)
        for path, expected_sha in sealed.items():
            if sha(regular(path)) != expected_sha:
                raise ValueError("Previously consumed raw output/evidence changed: " + path)
        for path, expected_tree in sealed_trees.items():
            if tree(path) != expected_tree:
                raise ValueError("Previously completed consumer output/evidence file set changed")

    def seal(folder):
        for name, expected_sha in tree(folder).items():
            path = str((folder / name).resolve())
            if path in sealed and sealed[path] != expected_sha:
                raise ValueError("Previously sealed consumer output/evidence changed")
            sealed[path] = expected_sha

    for scene in SCENES:
        verify()
        output, evidence = root / (scene + "-output"), root / (scene + "-calls")
        output.mkdir()
        evidence.mkdir()
        put(root / (scene.upper() + "-START.json"), {"at": now(), "plan_sha256": digest})
        checks, images = {}, []
        for row in approved["native_consumers"][scene]:
            verify()
            classification = call_with_deltas(config, row, evidence / row["id"])
            verify()
            classifications.append({"id": row["id"], "classification": classification})
            if row["kind"] == "sdk":
                observed = read(evidence / row["id"] / "stdout")
                if native_runtime_identity(observed) != approved["native_runtime_identity"]:
                    raise ValueError("Formal native reader startup/provider identity changed")
                before = (
                    observed
                    if scene == "baseline"
                    else read(root / "baseline-calls/baseline-sdk/stdout")
                )
                checks["native"] = check_native(
                    before,
                    observed,
                    root / "baseline/edit.kicad_pcb",
                    root / scene / "edit.kicad_pcb",
                    scene,
                )
            elif row["kind"] == "drc":
                report = read(output / "drc.json")
                before = report if scene == "baseline" else read(root / "baseline-output/drc.json")
                checks["drc"] = check_drc(
                    before,
                    report,
                    root / "baseline/edit.kicad_pcb",
                    root / scene / "edit.kicad_pcb",
                    scene,
                )
            else:
                checks[row["kind"]] = svg_identity(
                    output / (row["kind"].removesuffix("-svg") + ".svg")
                )
            seal(output)
            seal(evidence)
            sealed_trees[str((evidence / row["id"]).resolve())] = tree(evidence / row["id"])
        for row in approved["host_renderers"][scene]:
            verify()
            classification = call_with_deltas(config, row, evidence / row["id"])
            verify()
            classifications.append({"id": row["id"], "classification": classification})
            images.append(render_identity(row, evidence / row["id"]))
            seal(output)
            seal(evidence)
            sealed_trees[str((evidence / row["id"]).resolve())] = tree(evidence / row["id"])
        if sorted(tree(output)) != approved["complete_output_names"]:
            raise ValueError("Formal scene output set differs from the original plan")
        checks["complete_board"] = check_board(
            root / "baseline/edit.kicad_pcb", root / scene / "edit.kicad_pcb", scene
        )
        value = {
            "schema_version": 1,
            "at": now(),
            "scene": scene,
            "status": "consumer_checks_completed_manual_pending",
            "original_plan_path": str(Path(plan).resolve()),
            "original_plan_sha256": digest,
            "checks": checks,
            "images": images,
            "manual_views_completed": 0,
            "native_consumers": 4,
            "host_renderers": 2,
            "complete_output_tree": tree(output),
            "call_evidence": tree(evidence),
            "engineering": tree(root / scene),
            "configuration": tree(root / "configuration"),
            "schematic_parity": {"status": "not_executed", "native_parity_pass_claim": False},
        }
        result = root / (scene.upper() + "-RESULT.json")
        put(result, value)
        results[scene] = {"path": str(result), "sha256": sha(result), "images": images}
        sealed[str(result.resolve())] = sha(result)
        sealed_trees[str(output.resolve())] = tree(output)
        sealed_trees[str(evidence.resolve())] = tree(evidence)
    verify()
    result = {
        "schema_version": 1,
        "at": now(),
        "status": "consumer_checks_completed_manual_pending",
        "original_plan_path": str(Path(plan).resolve()),
        "original_plan_sha256": digest,
        "scenes": results,
        "classifications": classifications,
        "preparation_native_calls": 5,
        "formal_native_calls": 12,
        "formal_host_calls": 6,
        "full_images": 6,
        "manual_views_completed": 0,
        "manual_review_policy": (
            "append separate root/independent-agent full-image view records; "
            "never revise manual_pending artifacts"
        ),
        "product_writer_or_confirm_calls": 0,
        "old_ERC_or_visual_calls_repeated": 0,
        "schematic_parity": {"status": "not_executed", "native_parity_pass_claim": False},
    }
    put(root / "RESULT.json", result)
    return {
        "result_sha256": sha(root / "RESULT.json"),
        "formal_native_calls": 12,
        "formal_host_calls": 6,
    }


def main():
    if not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        raise SystemExit("move_safety_acceptance requires Python -B -S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("stage-inputs", "prepare", "freeze", "execute"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input-sha256")
    parser.add_argument("--preparation-sha256")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    root = None
    try:
        # Locate an already-owned cycle before validating mutable config/intent
        # bindings, so a drift refusal is also retained and cannot be resumed.
        if args.plan and args.plan.is_absolute() and args.plan.name == "ORIGINAL-PLAN.json":
            candidate = args.plan.parent
            initial = candidate / "INPUT-FROZEN.json"
            if initial.is_file() and (
                read(initial)
                .get("binding", {})
                .get("config", {})
                .get("_config_binding", {})
                .get("path")
                == str(args.config.resolve())
            ):
                root = candidate
        raw_config = read(regular(args.config))
        raw_output = raw_config.get("output") if isinstance(raw_config, dict) else None
        if isinstance(raw_output, str) and Path(raw_output).is_absolute():
            candidate = Path(raw_output)
            initial = candidate / "INPUT-FROZEN.json"
            if initial.is_file() and (
                read(initial)
                .get("binding", {})
                .get("config", {})
                .get("_config_binding", {})
                .get("path")
                == str(args.config.resolve())
            ):
                root = candidate
        config = load_config(args.config)
        root = Path(config["output"])
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
                parser.error("execute requires the original --plan and --plan-sha256")
            result = execute(config, args.plan, args.plan_sha256)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        stop = root / (args.stage.upper() + "-STOP.json") if root else None
        if root is not None and root.is_dir() and not stop.exists():
            put(
                stop,
                {
                    "at": now(),
                    "stage": args.stage,
                    "failure": type(exc).__name__ + ": " + str(exc),
                    "action": "preserve this cycle; no retry/resume/refreeze/receipt replay",
                    "engineering_at_stop": {name: stop_tree(root / name) for name in PROJECTS},
                    "configuration_at_stop": stop_tree(root / "configuration"),
                    "planned_call_counts": CONSUMER_COUNTS,
                    "retained_call_evidence": {
                        name: stop_tree(root / name)
                        for name in ("preparation-calls", *(scene + "-calls" for scene in SCENES))
                    },
                },
            )
        print(json.dumps({"ok": False, "stopped": True, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
