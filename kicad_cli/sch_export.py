"""Current single-source SCH artifacts using captured inputs and the native exporter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import engineering_backend, kicad_env
from .fileformat.sexpr import Document, quote
from .module_project import sha

FORMATS = {"netlist": ".net", "svg": ".svg", "pdf": ".pdf"}


def capture(params):
    from .engineering_inputs import Inputs, _parent_regular

    result = Inputs()
    primary = Path(params["schematic"]).resolve()
    saved = result.documents(schematic=primary)
    if len(saved.design.instances) != 1:
        raise ValueError(
            "sch export currently supports one source/root page; hierarchy export is unsupported"
        )
    directory = Path(params["out"]).resolve()
    _parent_regular(directory)
    if not directory.is_dir():
        raise ValueError("Create an owned output directory before SCH export")
    fmt = params["file-format"]
    output = directory / (primary.stem + FORMATS[fmt])
    result.add(output)
    if result.files[output] is not None:
        raise FileExistsError("SCH export requires an absent output file: " + str(output))
    # Snapshot consulted project tables, environment variables and selected libraries.
    from .fileformat import lib_tables

    root = kicad_env.find_kicad_root()
    config = lib_tables.default_config_dir()
    for base in filter(None, (primary.parent, config)):
        for name in ("sym-lib-table", "fp-lib-table", "kicad_common.json"):
            result.add(Path(base) / name)
    if config is not None and Path(config).parent.is_dir():
        for file in Path(config).parent.rglob("*"):
            if file.is_file():
                result.add(file, required=True)
    tables = lib_tables.Tables.of(primary.parent, Path(root) if root else None)
    for instance in saved.design.instances:
        for part in instance.schematic.symbols:
            nickname = part.lib_id.partition(":")[0]
            if nickname in tables.symbols:
                result.add(tables.symbols[nickname].path, required=True)
    result.validate()
    return result, primary


def prepare(request):
    mapping = {Path(k): Path(v) for k, v in request["mapping"].items()}
    original = Path(request["original_primary"])
    source = Path(request["primary"])
    params = request["params"]
    fmt = params["file-format"]
    output = Path(params["out"]).resolve() / (original.stem + FORMATS[fmt])
    candidate = mapping[output]
    candidate.parent.mkdir(parents=True, exist_ok=True)
    native = engineering_backend.observe_schematic()
    command = [native["files"][0]["path"], "sch", "export", fmt]
    if fmt == "netlist":
        command += ["--format", "kicadsexpr"]
    command += ["-o", str(candidate.parent if fmt == "svg" else candidate), str(source)]
    before = {p: sha(p.read_bytes()) for p in mapping.values() if p.is_file()}
    from .payload.drc_runner import config_root

    config_seed = config_root().resolve()
    native_config = source.parent / ".native-sch-config"
    native_config.mkdir()
    seed_manifest = {}
    for original_path, captured_path in mapping.items():
        if original_path.is_relative_to(config_seed) and captured_path.is_file():
            target = native_config / original_path.relative_to(config_seed)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(captured_path.read_bytes())
            seed_manifest[target.relative_to(native_config).as_posix()] = sha(target.read_bytes())
    child = subprocess.run(
        command,
        capture_output=True,
        timeout=240,
        cwd=source.parent,
        env=dict(
            os.environ,
            KICAD_CONFIG_HOME=str(native_config),
            KICAD_DOCUMENTS_HOME=str(native_config / "documents"),
        ),
    )
    if child.returncode or not candidate.is_file():
        raise ValueError(
            "Native SCH export failed: " + child.stderr.decode("utf-8", "replace")[-1200:]
        )
    if any(sha(p.read_bytes()) != value for p, value in before.items()):
        raise ValueError("Native exporter modified captured source inputs")
    normalized = False
    if fmt == "netlist":
        doc = Document.load(candidate)
        design = doc.root.find("design")
        if design is not None and design.find("source") is not None:
            design.find("source").set(1, quote(str(original)))
            candidate.write_text(doc.dumps(), encoding="utf-8")
            normalized = True
    from .engineering_readback import observe

    observed = observe([{"path": str(candidate)}])
    return {
        "ok": True,
        "primary_candidate": str(source),
        "files": [
            {
                "path": str(output),
                "candidate": str(candidate),
                "intended": sha(candidate.read_bytes()),
            }
        ],
        "result": {
            "format": fmt,
            "source_schematic": str(original),
            "native": native,
            "native_command": command,
            "native_returncode": child.returncode,
            "derived_source_locator_normalized": normalized,
            "artifact": observed[0],
            "source_inputs_preserved": True,
            "native_config_seed": seed_manifest,
            "native_config_policy": "Native writes only its operation-owned snapshot; "
            "original profile files remain drift-bound",
            "checked": "Artifact structure and bound bytes; no ERC/DRC or visual signoff",
        },
        "finding_comparison": None,
        "official_native": True,
    }
