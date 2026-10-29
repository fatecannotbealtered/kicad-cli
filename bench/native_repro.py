"""Freeze the supplied native failure's declared inputs; never claim lost history recovered."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath

from process_evidence import ENVIRONMENT_KEYS, files, sha, utc, write

PROJECT_SUFFIXES = {
    ".kicad_sch",
    ".kicad_pcb",
    ".kicad_pro",
    ".kicad_dru",
    ".kicad_sym",
    ".kicad_mod",
}


def related_environment(current, binding, native_root):
    result = {k: v for k, v in current.items() if k in ENVIRONMENT_KEYS}
    result.update(
        KICAD_CLI_ROOT=str(native_root),
        KICAD_CLI_PYTHON=binding["native_python"]["path"],
        KICAD_CLI_OFFICIAL=binding["official_cli"]["path"],
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        KICAD10_SYMBOL_DIR=str(native_root / "share/kicad/symbols"),
        KICAD10_FOOTPRINT_DIR=str(native_root / "share/kicad/footprints"),
    )
    return result


def freeze(failed, output, official, historical_cwd, *, event_name=None):
    failed, output, official, historical_cwd = map(
        lambda p: Path(p).resolve(), (failed, output, official, historical_cwd)
    )
    if output.is_relative_to(failed) or output.is_relative_to(official.parent.parent):
        raise ValueError(
            "Reproduction output must be separate from history and native installation"
        )
    if not historical_cwd.is_dir():
        raise ValueError(
            "Historical working directory is unavailable; supply an explicit labelled control"
        )
    binding = json.loads((failed / "source-environment.json").read_bytes())
    if sha(official) != binding["official_cli"]["sha256"]:
        raise ValueError("Official native executable differs from the retained failure")
    events = json.loads((failed / "events.json").read_bytes())
    event = next(
        e
        for e in events
        if (
            e["name"] == event_name
            if event_name
            else e.get("exit") not in (None, 0) and e["argv"][:1] == ["sch"]
        )
    )
    if event["argv"][:1] != ["sch"]:
        raise ValueError("This native reproduction is bounded to schematic ERC/XML calls")
    source = next(Path(a).resolve() for a in event["argv"] if str(a).endswith(".kicad_sch"))
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    project.mkdir()
    copied = []
    for p in sorted(source.parent.rglob("*")):
        if p.is_file() and (
            p.suffix in PROJECT_SUFFIXES or p.name in {"fp-lib-table", "sym-lib-table"}
        ):
            dest = project / p.relative_to(source.parent)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
            copied.append(
                {"source": str(p), "copy": dest.relative_to(output).as_posix(), "sha256": sha(p)}
            )
    retained_config = failed / "configuration"
    gaps = [
        "Original ambient environment was not logged; explicit harness overrides only.",
        "Historical cwd is supplied/inferred; no original per-call cwd record.",
        "Input/config/report absolute paths must relocate to protect the historical scene.",
        "PID, timing, concurrency, OS caches and native memory cannot be preserved.",
        "Post-failure configuration is retained; exact call-time bytes are not wholly proven.",
        "Historical transitive module hashes are missing; current hashes cannot fill that gap.",
    ]
    if retained_config.is_dir():
        shutil.copytree(retained_config, output / "configuration")
    else:
        gaps.append("Historical configuration missing; no invented defaults for native baseline.")
    native_root = official.parent.parent
    env = related_environment(os.environ, binding, native_root)
    env["KICAD_CONFIG_HOME"] = str(output / "configuration")
    config_vars = {}
    for common in (output / "configuration").glob("*/kicad_common.json"):
        value = json.loads(common.read_bytes()).get("environment") or {}
        config_vars.update(value.get("vars") or {})
    variables = dict(config_vars, **env, KIPRJMOD=str(source.parent))
    footprints = re.findall(
        r'\(property\s+"Footprint"\s+"([^"]+)"', source.read_text(encoding="utf-8")
    )
    dependencies, unavailable = [], []
    for table in sorted(project.rglob("*lib-table")):
        content = table.read_text(encoding="utf-8")
        # This case uses flat KiCad native tables. Preserve original bytes.
        entries = re.findall(r'\(lib\s+\(name\s+"([^"]+)"\).*?\(uri\s+"([^"]+)"\)', content, re.S)
        if len(entries) != len(re.findall(r"\(lib\s", content)):
            unavailable.append(
                {"table": str(table), "reason": "Unsupported/incomplete native library table entry"}
            )
        for name, uri in entries:
            required = re.findall(r"\$\{([^}]+)\}", uri)
            missing = [key for key in required if key not in variables]
            if missing:
                unavailable.append({"table": str(table), "uri": uri, "missing_variables": missing})
                continue
            resolved = re.sub(r"\$\{([^}]+)\}", lambda m: str(variables[m[1]]), uri)
            dependency = Path(resolved)
            if not dependency.is_absolute():
                dependency = source.parent / dependency
            if table.name == "fp-lib-table":
                paths = [
                    dependency / (fp.split(":", 1)[1] + ".kicad_mod")
                    for fp in footprints
                    if fp.startswith(name + ":")
                ]
            else:
                paths = [dependency]
            for path in paths:
                if not path.is_file():
                    unavailable.append(
                        {"table": str(table), "path": str(path), "reason": "missing library file"}
                    )
                    continue
                # Copies are local reproduction data, not redistributed SDK binaries.
                dest = output / "dependency-files" / (sha(path) + path.suffix)
                dest.parent.mkdir(exist_ok=True)
                if not dest.exists():
                    shutil.copyfile(path, dest)
                dependencies.append(
                    {
                        "table": table.relative_to(output).as_posix(),
                        "name": name,
                        "source": str(path.resolve()),
                        "copy": dest.relative_to(output).as_posix(),
                        "sha256": sha(path),
                        "replay_policy": "Original URI retained; live digest must match",
                    }
                )
    for name in ("fp-lib-table", "sym-lib-table"):
        if not (project / name).is_file():
            unavailable.append(
                {
                    "path": name,
                    "reason": "Local table absent; global lookup is not an equivalent",
                }
            )
    capture = {
        "format": "kicad-native-repro.v1",
        "created_at": utc(),
        "failure_run": str(failed),
        "failure_event": event,
        "official_cli": {"path": str(official), "sha256": sha(official)},
        "historical_binding": binding,
        "historical_cwd": str(historical_cwd),
        "schematic_relative": source.relative_to(source.parent).as_posix(),
        "project": copied,
        "dependencies": dependencies,
        "unavailable": unavailable,
        "configuration_present": retained_config.is_dir(),
        "configuration_files": [
            {"relative_path": p.relative_to(output / "configuration").as_posix(), "sha256": sha(p)}
            for p in sorted((output / "configuration").rglob("*"))
            if p.is_file()
        ],
        "environment": env,
        "environment_provenance": (
            "Harness overrides plus current explicit OS allowlist; original ambient values unknown"
        ),
        "gaps": gaps,
        "historical_exact_reproduction": False,
        "declared_file_inputs_complete": not unavailable,
        "raw_stdout": files(
            [failed / (event["name"] + ".stdout"), failed / (event["name"] + ".stderr")]
        ),
        "scope": (
            "Owned local copies; no vendor changes, global settings, user attachment or upload"
        ),
    }
    if os.name == "nt":
        from win_debugging import file_version

        capture["official_cli"]["file_version"] = file_version(official)
    write(output / "manifest.json", capture)
    return capture


def verify_configuration(manifest, configuration):
    """Compare a relocated copy to the original manifest, never re-freeze it."""
    configuration = Path(configuration).resolve()
    expected = {}
    frozen_root = Path(manifest["environment"]["KICAD_CONFIG_HOME"])
    for row in manifest["configuration_files"]:
        relative = row.get("relative_path")
        if relative is None:
            # Earlier capsules recorded absolute paths. Derive the relative
            # name only from their frozen root, not the current machine.
            relative = Path(row["path"]).relative_to(frozen_root).as_posix()
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or ":" in relative or "\\" in relative:
            raise ValueError("Invalid frozen configuration path")
        if relative in expected:
            raise ValueError("Duplicate frozen configuration path")
        expected[relative] = row["sha256"]
    actual = {
        p.relative_to(configuration).as_posix(): sha(p)
        for p in sorted(configuration.rglob("*"))
        if p.is_file()
    }
    if configuration.is_dir() != manifest["configuration_present"] or actual != expected:
        raise ValueError("Frozen configuration drift: file set or content differs")
    return {
        "status": "passed",
        "files": expected,
        "frozen_root": str(frozen_root),
        "relocated_to": str(configuration),
        "path_relocation_only": True,
    }


def verify(manifest, root):
    verify_configuration(manifest, root / "configuration")
    for row in manifest["project"]:
        if sha(root / row["copy"]) != row["sha256"]:
            raise ValueError("Frozen reproduction input changed: " + row["copy"])
    for row in manifest["dependencies"]:
        if sha(row["source"]) != row["sha256"] or sha(root / row["copy"]) != row["sha256"]:
            raise ValueError("Native dependency drift: " + row["source"])
    if sha(manifest["official_cli"]["path"]) != manifest["official_cli"]["sha256"]:
        raise ValueError("Native executable drift")
