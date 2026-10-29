"""Owned public-CLI invocation recorder; no CAD authoring primitives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NATIVE = Path("D:/software/kicad/home")


def put(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Case:
    @classmethod
    def attach(cls, directory, binary=None):
        self = cls.__new__(cls)
        self.root = Path(directory).resolve(strict=True)
        self.profile = self.root / "profile"
        self.logs = self.root / "calls"
        self.temp = self.root / "temp"
        self.config = self.root / "runtime-config.json"
        self.env = dict(
            os.environ,
            KICAD_CLI_RUNTIME_CONFIG=str(self.config),
            TEMP=str(self.temp),
            TMP=str(self.temp),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONIOENCODING="utf-8",
        )
        self.prefix = [str(binary)] if binary else [sys.executable, "-B", str(REPO / "entry.py")]
        self.index = max(
            (int(p.name.split("-")[0]) for p in self.logs.iterdir() if p.is_dir()), default=0
        )
        return self

    def __init__(self, directory, binary=None):
        self.root = Path(directory).resolve()
        self.root.mkdir(exist_ok=False)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.logs = self.root / "calls"
        self.logs.mkdir()
        self.temp = self.root / "temp"
        self.temp.mkdir()
        self.config = self.root / "runtime-config.json"
        put(
            self.config,
            {"format_version": 1, "kicad_root": str(NATIVE), "profile_dir": str(self.profile)},
        )
        self.env = dict(
            os.environ,
            KICAD_CLI_RUNTIME_CONFIG=str(self.config),
            TEMP=str(self.temp),
            TMP=str(self.temp),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONIOENCODING="utf-8",
        )
        self.prefix = [str(binary)] if binary else [sys.executable, "-B", str(REPO / "entry.py")]
        self.index = 0
        self.cli("--check-environment")
        scope = self.cli("modules")["observation"]["operation_scope"]["example"]
        scope.update(
            scope_id="functional-owned",
            granted_by="explicit user development task",
            roots=[str(self.root)],
            outputs=[str(self.root)],
            limits={"max_operations": 300},
        )
        put(self.profile / "scope.json", scope)
        shutil.copyfile(
            NATIVE / "share/kicad/template/fp-lib-table", self.profile / "kicad/10.0/fp-lib-table"
        )

    def raw(self, argv, label="call", native=False):
        while True:
            self.index += 1
            output = self.logs / f"{self.index:04}-{label}"
            try:
                output.mkdir()
                break
            except FileExistsError:
                continue
        env = dict(self.env)
        if native:
            env["KICAD_CONFIG_HOME"] = str(self.profile / "kicad")
            env["KICAD_DOCUMENTS_HOME"] = str(self.profile / "documents")
        command = list(map(str, argv))
        process = subprocess.run(command, cwd=self.root, env=env, capture_output=True, timeout=900)
        (output / "stdout").write_bytes(process.stdout)
        (output / "stderr").write_bytes(process.stderr)
        put(
            output / "call.json",
            {
                "argv": command,
                "exit_code": process.returncode,
                "at_utc": datetime.now(timezone.utc).isoformat(),
                "stdout_sha256": hashlib.sha256(process.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(process.stderr).hexdigest(),
            },
        )
        return process

    def cli(self, *args, require=True):
        process = self.raw([*self.prefix, *args, "--compact"], label="public")
        result = json.loads(process.stdout)
        if require and not result["ok"]:
            error = result["error"]
            raise RuntimeError(
                f"{error['code']}: {error['message']} (raw evidence call {self.index})"
            )
        return result["data"] if result["ok"] else result

    def write(self, *args):
        preview = self.cli(*args, "--dry-run")
        assert preview["preview"]["can_apply"], preview
        result = self.cli(*args, "--confirm", preview["confirm_token"])
        assert result["receipt"]["outcome"]["execution"] == "completed", result
        return preview, result

    def native(self, *args):
        process = self.raw([NATIVE / "bin/kicad-cli.exe", *args], label="native", native=True)
        assert process.returncode == 0, process.stderr.decode(errors="replace")
        return process
