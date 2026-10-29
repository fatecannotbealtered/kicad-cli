"""Harness for explicitly scoped owned experiments, not a product workflow API."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from process_evidence import run_recorded

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


class Run:
    def __init__(self, options):
        working_dir = getattr(options, "working_dir", None)
        selected_driver = getattr(options, "driver_sdk", None)
        self.standalone = working_dir is not None
        self.cwd = working_dir.resolve() if self.standalone else REPO
        if self.standalone:
            if not options.binary or not selected_driver:
                raise ValueError("Outside-repository acceptance requires --binary and --driver-sdk")
            if not self.cwd.is_dir() or self.cwd.is_relative_to(REPO):
                raise ValueError("--working-dir must be an existing directory outside the checkout")
            if any(name == "kicad_cli" or name.startswith("kicad_cli.") for name in sys.modules):
                raise ValueError(
                    "Standalone acceptance must not import product code in its harness"
                )
        driver_sdk = (selected_driver or REPO / "kicad_cli/_driver_sdk").resolve()
        if (
            not (driver_sdk / "__init__.py").is_file()
            or not (driver_sdk.parent / "driver_spec").is_dir()
        ):
            raise ValueError("Explicit driver SDK needs its adjacent pinned driver_spec")
        for label, path in (
            ("native installation", options.kicad_root),
            ("configuration seed", options.config_seed),
        ):
            if not path or not path.is_dir():
                raise ValueError(f"Missing explicit {label}: {path}")
        self.sdk = options.native_python or options.kicad_root / "bin/python.exe"
        self.official = options.official_cli or options.kicad_root / "bin/kicad-cli.exe"
        for label, path in (
            ("native Python", self.sdk),
            ("official CLI", self.official),
            ("candidate", options.binary),
        ):
            if path is not None and not path.is_file():
                raise ValueError(f"Missing {label}: {path}")
        self.root = options.output.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.events = []
        self.scope = self.root / "scope.json"
        write(
            self.scope,
            {
                "scope_id": self.root.name,
                "tool": "kicad-cli",
                "granted_by": "User authorizes isolated engineering change acceptance",
                "granted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "expires_at": None,
                "effects": ["observe", "change", "persist", "artifact"],
                "dangerous": False,
                "roots": [str(self.root)],
                "outputs": [str(self.root)],
                "notes": "Owned copies only; no user editor, permission or dependency changes",
            },
        )
        configuration = self.root / "configuration"
        shutil.copytree(options.config_seed, configuration / options.config_seed.name)
        parent_env = dict(os.environ)
        if self.standalone:
            parent_env = {k: v for k, v in parent_env.items() if not k.startswith("KICAD")}
            for key in ("PYTHONPATH", "PYTHONHOME"):
                parent_env.pop(key, None)
        self.env = dict(
            parent_env,
            KICAD_CLI_ROOT=str(options.kicad_root.resolve()),
            KICAD_CLI_PYTHON=str(self.sdk.resolve()),
            KICAD_CLI_OFFICIAL=str(self.official.resolve()),
            KICAD_CLI_SCOPE_FILE=str(self.scope),
            KICAD_CLI_STATE_DIR=str(self.root / "state"),
            KICAD_CLI_DRIVER_SDK_PATH=str(driver_sdk),
            KICAD_CONFIG_HOME=str(configuration),
            PYTHONIOENCODING="utf-8",
            PYTHONDONTWRITEBYTECODE="1",
        )
        for key in ("KICAD_CLI_TRACE", "KICAD_CLI_FAULT", "KICAD_CLI_TEST_FAULT"):
            self.env.pop(key, None)
        self.cli = (
            [str(options.binary.resolve())]
            if options.binary
            else [sys.executable, "-B", "-m", "kicad_cli.main"]
        )
        self.binding = {
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "binary": {"path": str(options.binary.resolve()), "sha256": sha(options.binary)}
            if options.binary
            else None,
            "native_python": {"path": str(self.sdk.resolve()), "sha256": sha(self.sdk)},
            "official_cli": {"path": str(self.official.resolve()), "sha256": sha(self.official)},
            "configuration_seed": str(options.config_seed.resolve()),
            "working_directory": str(self.cwd),
            "standalone": self.standalone,
            "external_driver_sdk": str(driver_sdk),
            "external_driver_spec": str(driver_sdk.parent / "driver_spec"),
            "external_driver_pin_sha256": sha(driver_sdk.parent / "driver_spec/PIN.json"),
            "configuration_before": snapshot(configuration),
            "methods": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")},
        }
        write(self.root / "source-environment.json", self.binding)
        sys.path.insert(0, str(REPO))
        from scripts.fcc_evidence import digest_manifest, source_manifest

        self.source_files = source_manifest(REPO)
        self.binding["source_manifest_sha256"] = digest_manifest(self.source_files)
        write(self.root / "source-files.json", self.source_files)
        write(self.root / "source-environment.json", self.binding)
        identity = self.native("native-dependencies", "native_sdk_reader.py")
        major = identity["application_version"].split(".")[0]
        for suffix, folder in (("FOOTPRINT_DIR", "footprints"), ("SYMBOL_DIR", "symbols")):
            directory = options.kicad_root / "share/kicad" / folder
            if directory.is_dir():
                self.env[f"KICAD{major}_{suffix}"] = str(directory.resolve())

    def call(
        self,
        name,
        args,
        *,
        program=None,
        json_output=True,
        expect=0,
        timeout=1900,
        report=None,
        role=None,
    ):
        command = program or self.cli
        record, value = run_recorded(
            self.root,
            name,
            command + [str(a) for a in args],
            cwd=self.cwd,
            env=self.env,
            timeout=timeout,
            stdout_json=json_output,
            report=report,
            binding=self.binding,
            role=role or ("product-cli" if program is None else "independent-reader"),
        )
        event = dict(record, program=command, argv=[str(a) for a in args])
        self.events.append(event)
        write(self.root / "events.json", self.events)
        if record["status"] != "passed":
            from scripts.fcc_evidence import source_manifest

            failure_binding = dict(
                self.binding,
                source_unchanged=source_manifest(REPO) == self.source_files,
                configuration_after=snapshot(self.root / "configuration"),
                failure_call=name,
                terminal_call_sha256=sha(self.root / (name + ".call.json")),
            )
            write(self.root / "source-after-failure.json", failure_binding)
        if (expect == 0 and record["status"] != "passed") or (
            expect is not None and record["exit"] != expect
        ):
            raise RuntimeError(
                f"{name}: {record['execution']}, exit {record['exit']}; inspect {name}.call.json"
            )
        return value

    def mutate(self, name, args):
        preview = self.call(name + "-preview", args + ["--dry-run", "--compact"])
        if not preview["data"]["preview"]["can_apply"]:
            raise RuntimeError(f"{name}: refused preview; inspect {name}-preview.stdout")
        result = self.call(
            name + "-receipt",
            args
            + [
                "--confirm",
                preview["data"]["confirm_token"],
                "--idempotency-key",
                name,
                "--compact",
            ],
        )
        receipt = result["data"]["receipt"]
        self.call(
            name + "-show", ["operation", "show", "--id", receipt["operation_id"], "--compact"]
        )
        return receipt

    def native(self, name, reader, path=None):
        args = ["-I", "-B", str(Path(__file__).with_name(reader))]
        if path is not None:
            args.append(str(path))
        return self.call(name, args, program=[str(self.sdk)])

    def official_call(self, name, args, *, expect=0):
        report = None
        if "--output" in args:
            kind = (
                "erc"
                if args[:2] == ["sch", "erc"]
                else "drc"
                if args[:2] == ["pcb", "drc"]
                else ("xml" if "kicadxml" in args else "csv")
            )
            report = {"path": str(args[args.index("--output") + 1]), "kind": kind}
        return self.call(
            name,
            args,
            program=[str(self.official)],
            json_output=False,
            expect=expect,
            report=report,
            role="official-native-cli",
        )

    def finish_binding(self):
        from scripts.fcc_evidence import source_manifest

        self.binding["source_unchanged"] = source_manifest(REPO) == self.source_files
        assert self.binding["source_unchanged"], "Source changed during this run"
        binary = self.binding["binary"]
        if binary:
            assert sha(binary["path"]) == binary["sha256"]
        if self.standalone:
            imported = [n for n in sys.modules if n == "kicad_cli" or n.startswith("kicad_cli.")]
            assert not imported, "Standalone harness imported product code"
            self.binding["harness_product_modules"] = imported
        self.binding["configuration_after"] = snapshot(self.root / "configuration")
        write(self.root / "source-environment.json", self.binding)


def arguments(parser):
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--kicad-root", type=Path, required=True)
    parser.add_argument("--native-python", type=Path)
    parser.add_argument("--official-cli", type=Path)
    parser.add_argument("--config-seed", type=Path, required=True)
    parser.add_argument(
        "--working-dir", type=Path, help="Existing cwd outside checkout; requires EXE"
    )
    parser.add_argument(
        "--driver-sdk", type=Path, help="Existing external pinned _driver_sdk directory"
    )
