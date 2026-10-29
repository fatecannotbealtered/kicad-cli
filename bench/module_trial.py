"""Reproducible public-CLI acceptance method. Never writes CAD directly."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def put(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class Trial:
    def __init__(self, directory, binary=None, kicad_root=None):
        from kicad_cli.installation import resources

        self.installation = Path(
            kicad_root or os.environ.get("KICAD_CLI_ROOT", "D:/software/kicad/home")
        )
        self.root = Path(directory).resolve()
        self.root.mkdir(exist_ok=False)
        self.logs = self.root / "calls"
        self.logs.mkdir()
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.config = self.root / "runtime-config.json"
        put(
            self.config,
            {
                "format_version": 1,
                "kicad_root": str(self.installation),
                "profile_dir": str(self.profile),
            },
        )
        put(
            self.profile / "scope.json",
            {
                "scope_id": "owned-module-trial",
                "tool": "kicad-cli",
                "granted_by": "User-authorized isolated engineering module acceptance",
                "granted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "expires_at": None,
                "effects": ["observe", "change", "persist", "artifact"],
                "dangerous": False,
                "roots": [str(self.root)],
                "outputs": [str(self.root)],
                "limits": {"max_operations": 200},
            },
        )
        self.env = dict(
            os.environ,
            KICAD_CLI_RUNTIME_CONFIG=str(self.config),
            KICAD_CONFIG_HOME=str(self.profile / "kicad"),
            KICAD_DOCUMENTS_HOME=str(self.profile / "documents"),
            KICAD10_SYMBOL_DIR=str(resources(self.installation) / "symbols"),
            KICAD10_FOOTPRINT_DIR=str(resources(self.installation) / "footprints"),
            PYTHONIOENCODING="utf-8",
        )
        self.prefix = (
            list(map(str, binary))
            if isinstance(binary, (tuple, list))
            else [str(binary)]
            if binary
            else [sys.executable, "-B", str(REPO / "entry.py")]
        )
        self.index = 0
        self.cli("--check-environment")
        config = self.profile / "kicad/10.0"
        if not (config / "fp-lib-table").exists():
            shutil.copyfile(
                resources(self.installation) / "template/fp-lib-table",
                config / "fp-lib-table",
            )

    def raw(self, args):
        self.index += 1
        path = self.logs / f"{self.index:04}"
        path.mkdir()
        argv = [*map(str, args)]
        start = time.monotonic()
        proc = subprocess.run(argv, cwd=self.root, env=self.env, capture_output=True, timeout=600)
        (path / "stdout").write_bytes(proc.stdout)
        (path / "stderr").write_bytes(proc.stderr)
        put(
            path / "call.json",
            {
                "argv": argv,
                "cwd": str(self.root),
                "exit_code": proc.returncode,
                "seconds": time.monotonic() - start,
            },
        )
        return proc

    def cli(self, *args, require=True):
        proc = self.raw([*self.prefix, *args, "--compact"])
        result = json.loads(proc.stdout)
        if require and not result["ok"]:
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        return result.get("data") if result["ok"] else result

    def write(self, *args):
        plan = self.cli(*args, "--dry-run")
        if not plan["preview"]["can_apply"]:
            raise RuntimeError("Candidate review refuses: " + json.dumps(plan))
        receipt = self.cli(*args, "--confirm", plan["confirm_token"])
        if receipt["receipt"]["outcome"]["persistence"] != "saved":
            raise RuntimeError("Save incomplete: " + json.dumps(receipt))
        return plan, receipt


def library_trial(directory, binary=None, kicad_root=None):
    from kicad_cli.installation import official_cli

    t = Trial(directory, binary, kicad_root)
    project = t.root / "project"
    project.mkdir()
    spec_path = t.root / "coupon.json"
    put(spec_path, {"mode": "blank", "title": "SN74LVC1G14 project-local library mapping coupon"})
    t.write("sch", "create", "--spec", spec_path, "--out", project)
    root = project / "coupon.kicad_sch"
    (project / "LocalLogic.pretty").mkdir()
    example = REPO / "examples/library/sn74lvc1g14"
    shutil.copyfile(example / "source-facts.json", t.root / "source-facts.json")
    shutil.copyfile(example / "library.json", t.root / "library.json")
    t.write(
        "library",
        "build",
        "--project",
        project / "coupon.kicad_pro",
        "--spec",
        t.root / "library.json",
    )
    library = t.cli(
        "library", "inspect", "--project", project / "coupon.kicad_pro", "--library", "LocalLogic"
    )
    # Native consumers initialize only the owned profile before exact snapshots.
    native = official_cli(t.installation)
    t.raw([native, "sch", "erc", "--format", "json", "-o", t.root / "blank-erc.json", root])
    objects = t.cli("sch", "objects", "--schematic", root)
    page = objects["extensions"]["pages"][0]["instance_path"]
    queried = t.cli("sch", "symbols", "--schematic", root, "--symbol", "LocalLogic:SN74LVC1G14DBV")[
        "items"
    ][0]
    assert sorted(p["number"] for p in queried["pins"]) == ["1", "2", "3", "4", "5"]
    actions = [
        {
            "op": "place_symbol",
            "id": "U1",
            "page": page,
            "symbol": "LocalLogic:SN74LVC1G14DBV",
            "unit": 1,
            "ref": "U1",
            "at_mm": [76.2, 76.2],
        }
    ]
    expected = []
    for p in queried["pins"]:
        ordinal = p["pin_ordinal"]
        member = {"new": "U1", "instance_path": page, "pin_ordinal": ordinal}
        alias = "nc" + str(ordinal)
        actions.append(
            {"op": "create", "id": alias, "page": page, "kind": "no_connect", "at": {"pin": member}}
        )
        expected.append(
            {
                "members": [member, {"new": alias, "instance_path": page}],
                "explicit_names": [],
                "nc_members": [member],
            }
        )
    changes = t.root / "place.json"
    put(
        changes,
        {
            "changes": [
                {
                    "op": "circuit",
                    "snapshot": {
                        "root": objects["extensions"]["root"],
                        "snapshot": objects["snapshot"]["revision"],
                    },
                    "affected_instances": [page],
                    "actions": actions,
                    "expected": {"components": expected},
                }
            ]
        },
    )
    t.write("sch", "edit", "--schematic", root, "--changes", changes)
    t.cli("sch", "objects", "--schematic", root)
    p = t.raw(
        [
            native,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "-o",
            project / "current.net",
            root,
        ]
    )
    assert p.returncode == 0
    t.write(
        "board",
        "from-netlist",
        "--netlist",
        project / "current.net",
        "--out",
        project / "coupon.kicad_pcb",
        "--initialize-empty-project",
    )
    t.cli("project", "context", "--board", project / "coupon.kicad_pcb")
    t.cli("board", "routing", "inspect", "--board", project / "coupon.kicad_pcb")
    p = t.raw([native, "sch", "erc", "--format", "json", "-o", t.root / "saved-erc.json", root])
    assert p.returncode == 0
    put(
        t.root / "LIBRARY-RESULT.json",
        {
            "placement": "SCH and PCB saved/reopened via public CLI",
            "asset": queried,
            "library": library,
            "calls": t.index,
            "limits": "Pin mapping coupon with NC pins; no functioning inverter "
            "or hardware/manufacturing qualification",
        },
    )
    print(t.root)
    return t


if __name__ == "__main__":
    library_trial(sys.argv[1])
