"""Bind the actual external native interpreter and extension used for trials."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import kicad_env
from .move_transaction import digest, regular


def observe():
    python = kicad_env.find_python()
    if python is None:
        raise ValueError("External KiCad interpreter unavailable")
    code = (
        "import json, pcbnew, _pcbnew; "
        "print(json.dumps({'version':pcbnew.GetBuildVersion(), "
        "'module':_pcbnew.__file__, 'wrapper':pcbnew.__file__}))"
    )
    child = subprocess.run([python, "-B", "-c", code], capture_output=True, timeout=30)
    if child.returncode:
        raise ValueError("External native version/module probe failed")
    value = json.loads(child.stdout)
    value["files"] = [
        {"path": str(p := Path(name).resolve(strict=True)), "sha256": digest(p)}
        for name in (python, value["module"], value["wrapper"])
    ]
    return value


def validate(value):
    if value is None:
        return
    selected = (
        kicad_env.find_official_cli()
        if value.get("backend_kind") == "schematic-cli"
        else kicad_env.find_python()
    )
    if str(Path(selected).resolve()) != value["files"][0]["path"]:
        raise ValueError("Selected native interpreter changed")
    for row in value["files"]:
        regular(Path(row["path"]))
        if digest(Path(row["path"])) != row["sha256"]:
            raise ValueError("External native component changed after preview")


def observe_schematic():
    official = kicad_env.find_official_cli()
    from .payload.drc_runner import config_root

    version = None
    if official:
        with tempfile.TemporaryDirectory(
            prefix="sch-probe-", dir=os.environ.get("TEMP")
        ) as directory:
            config = Path(directory) / "config"
            seed = config_root()
            if seed.is_dir():
                shutil.copytree(seed, config)
            else:
                config.mkdir()
            child = subprocess.run(
                [official, "--version"],
                capture_output=True,
                timeout=30,
                env=dict(
                    os.environ,
                    KICAD_CONFIG_HOME=str(config),
                    KICAD_DOCUMENTS_HOME=str(Path(directory) / "documents"),
                ),
            )
            if child.returncode == 0:
                version = child.stdout.decode("utf-8", "replace").strip()
    if not official or version != "10.0.6":
        raise ValueError("SCH export currently requires the selected legal KiCad10.0.6 CLI")
    return {
        "backend_kind": "schematic-cli",
        "version": "10.0.6",
        "files": [{"path": str(Path(official).resolve()), "sha256": digest(Path(official))}],
    }
