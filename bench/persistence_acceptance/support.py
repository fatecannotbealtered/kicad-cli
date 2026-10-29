"""Parameterized local acceptance context. No product imports or implicit execution."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
ASSETS = (
    {
        "fixtures": REPO / "bench/saved_examples",
        "readers": REPO / "bench/rc_trial/execution/consumers",
        "support": REPO / "bench",
        "intent": REPO / "bench/advanced_edit/requirements/intent-S4.json",
        "public_surface": REPO / "docs/PUBLIC_COMMANDS.json",
    }
    if (REPO / "bench/saved_examples").is_dir()
    else {
        "fixtures": PACKAGE.parent / "saved-examples",
        "readers": PACKAGE.parent / "rc/execution/consumers",
        "support": PACKAGE / "support",
        "intent": PACKAGE.parent / "advanced-edit/requirements/intent-S4.json",
        "public_surface": PACKAGE.parent / "docs/PUBLIC_COMMANDS.json",
    }
)
ROOT = None
BINARY = None
SETTINGS = {}
CASES = json.loads(PACKAGE.joinpath("matrix.json").read_bytes())


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def put(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def tree(path):
    return {
        p.relative_to(path).as_posix(): sha(p) for p in sorted(Path(path).rglob("*")) if p.is_file()
    }


def configure(config, output, *, execute=False, control=False, authorized_by=None):
    global ROOT, BINARY, SETTINGS
    data = read(config)
    required = (
        "test_root",
        "exe",
        "exe_sha256",
        "build_source_commit",
        "sdk",
        "native_root",
        "configuration",
    )
    if any(key not in data for key in required):
        raise ValueError(
            "Explicit test root, EXE hash/source, SDK, native root "
            "and isolated configuration seed required"
        )
    for key in ("test_root", "exe", "sdk", "native_root", "configuration"):
        path = Path(data[key])
        if not path.is_absolute():
            raise ValueError("Absolute local dependency and test-root paths required: " + key)
        data[key] = str(path.resolve())
    root = Path(data["test_root"])
    output = Path(output).resolve()
    if output == root or not output.is_relative_to(root) or output.exists():
        raise ValueError("Output must be a new child directory of the declared test root")
    for name in ("fixtures", "readers", "support"):
        asset = ASSETS[name].resolve()
        if output.is_relative_to(asset) or asset.is_relative_to(output):
            raise ValueError("Test output overlaps immutable fixture/runtime assets")
    binary = Path(data["exe"])
    if sha(binary) != data["exe_sha256"]:
        raise ValueError("EXE hash mismatch; no process started")
    if execute and not authorized_by:
        raise ValueError("Explicit test-copy authorization attribution required")
    ROOT, BINARY = output, binary
    SETTINGS = dict(
        data,
        execute=execute,
        allow_owned_termination=control,
        authorized_by=authorized_by,
        config_sha256=sha(config),
    )
    return {
        "action": "execute" if execute else "plan_only",
        "output": str(ROOT),
        "exe_sha256": data["exe_sha256"],
        "build_source_commit": data["build_source_commit"],
        "owned_termination_enabled": bool(execute and control),
    }


def identity(pid):
    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        f'Get-CimInstance Win32_Process -Filter "ProcessId = {int(pid)}" | '
        "Select-Object ProcessId,ParentProcessId,CreationDate,Name,ExecutablePath | "
        "ConvertTo-Json -Compress",
    ]
    result = subprocess.run(command, capture_output=True, timeout=20)
    return {
        "query_exit": result.returncode,
        "process": json.loads(result.stdout) if result.stdout.strip() else None,
    }


def record_sql(state, operation):
    connection = sqlite3.connect(
        (state / "operations.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT operation_id, command, status, extra, receipt, resolution, "
            "started_at, updated_at, finished_at, lease_until "
            "FROM operations WHERE operation_id=?",
            (operation,),
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM consumed_tokens WHERE operation_id=?", (operation,)
        ).fetchone()[0]
    finally:
        connection.close()
    value = dict(row) if row else None
    if value:
        value["consumed_tokens_count"] = count
        for key in ("extra", "receipt", "resolution"):
            value[key] = json.loads(value[key]) if value[key] else None
    return value


class Scene:
    def __init__(self, output, case):
        if not SETTINGS.get("execute") or not SETTINGS.get("authorized_by"):
            raise PermissionError("Execution disabled; explicitly authorize owned copies first")
        self.root = Path(output).resolve()
        if self.root != ROOT:
            raise ValueError("Output differs from the reviewed execution root")
        self.origin = read(ASSETS["fixtures"] / "ORIGINS.json")["cases"][case]
        self.original = ASSETS["fixtures"] / case
        if tree(self.original) != self.origin["files"]:
            raise ValueError("Synthetic input differs from its recorded origin")
        self.root.mkdir(parents=True, exist_ok=False)
        self.project = self.root / "project"
        shutil.copytree(self.original, self.project)
        shutil.copytree(SETTINGS["configuration"], self.root / "configuration")
        config_file = self.root / "configuration/10.0/kicad_common.json"
        if config_file.exists():
            config = read(config_file)
            config.setdefault("system", {})["working_dir"] = str(self.root)
            config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.config_hashes = tree(self.root / "configuration")
        native = Path(SETTINGS["native_root"])
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("KICAD", "PYTHON")) and k != "KIPRJMOD"
        }
        self.env.update(
            PYTHONIOENCODING="utf-8",
            PYTHONDONTWRITEBYTECODE="1",
            KICAD_CLI_DRIVER_SDK_PATH=SETTINGS["sdk"],
            KICAD_CLI_ROOT=str(native),
            KICAD_CLI_PYTHON=SETTINGS.get("native_python", str(native / "bin/python.exe")),
            KICAD_CLI_OFFICIAL=SETTINGS.get("official_cli", str(native / "bin/kicad-cli.exe")),
            KICAD10_SYMBOL_DIR=str(native / "share/kicad/symbols"),
            KICAD10_FOOTPRINT_DIR=str(native / "share/kicad/footprints"),
            KICAD_CONFIG_HOME=str(self.root / "configuration"),
            KICAD_CLI_STATE_DIR=str(self.root / "state"),
            KICAD_CLI_SCOPE_FILE=str(self.root / "scope.json"),
        )
        put(
            self.root / "scope.json",
            {
                "scope_id": "owned-persistence-" + self.root.name,
                "tool": "kicad-cli",
                "granted_by": SETTINGS["authorized_by"],
                "granted_at": now(),
                "expires_at": None,
                "effects": ["observe", "change", "persist", "artifact"],
                "dangerous": False,
                "roots": [str(self.root)],
                "outputs": [str(self.root)],
            },
        )
        self.binding = {"source_commit": SETTINGS["build_source_commit"], "exe_sha256": sha(BINARY)}
        self.calls = self.root / "calls"
        self.calls.mkdir()
        put(
            self.root / "SETUP.json",
            {
                "at": now(),
                "binding": self.binding,
                "origin": self.origin,
                "files": tree(self.project),
                "configuration": self.config_hashes,
                "environment": {
                    k: v for k, v in self.env.items() if k.startswith(("KICAD", "PYTHON"))
                },
                "authorization": SETTINGS["authorized_by"],
                "config_sha256": SETTINGS["config_sha256"],
            },
        )

    def raw_call(self, name, argv, *, report=None, json_output=True, timeout=180):
        sys.path.insert(0, str(ASSETS["support"]))
        from process_evidence import native_debug_enabled, run_recorded

        command = list(map(str, argv))
        record, value = run_recorded(
            self.calls,
            name,
            command,
            cwd=self.root,
            env=self.env,
            timeout=timeout,
            report=report,
            stdout_json=json_output,
            binding=self.binding,
            role="opt-in-persistence-acceptance",
            debug=native_debug_enabled(
                command,
                SETTINGS.get("native_root"),
                enabled=SETTINGS.get("native_debug", False),
            ),
        )
        if record["execution"] in {"crashed", "timeout", "owned_descendants_remaining"}:
            put(self.root / ("STOP-" + name + ".json"), record)
            raise RuntimeError("Stop this acceptance cycle: " + record["execution"])
        if (
            tree(self.original) != self.origin["files"]
            or tree(self.root / "configuration") != self.config_hashes
        ):
            raise RuntimeError("Original input or isolated configuration drift")
        return record["exit"], value

    def call(self, name, args):
        return self.raw_call(name, [BINARY, *args, "--compact"])

    def freeze(self, name):
        destination = self.root / name
        destination.mkdir(exist_ok=False)
        shutil.copytree(self.project, destination / "project")
        for folder in ("document-transactions", "document-archive-preparations", "recovery"):
            if (self.root / "state" / folder).exists():
                shutil.copytree(self.root / "state" / folder, destination / folder)
        put(destination / "FILES.json", tree(destination))
        return destination
