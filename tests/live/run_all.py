#!/usr/bin/env python3
"""Run every behavior matrix of kicad-cli on the real KiCad, the way the live records were made.

    python tests/live/run_all.py --targets <folder> [command ...]
    python tests/live/run_all.py --targets <folder> "net update#no_scope,normal"

``<folder>`` is a work folder listed under Authorized Targets in docs/E2E.md. The script makes the
practice copies there from tests/live/targets and restores them before each run. A command name
followed by ``#rows`` runs only those rows. KiCad is found through KICAD_CLI_KICAD_DIR, as
kicad-cli finds it; run on a machine with enough free memory, since some rows open KiCad's editors.

Each command's plain rows run in one call; the rows that need a prepared situation run alone:
``ambiguous`` on the copy with a duplicated reference, ``conflict`` with the file open in KiCad's
own editor, ``no_scope`` on the document as it was, ``read_back_mismatch`` with the result changed
during the pause by tests/live/disturb.py. Some commands first make what their rows act on: the
zone that ``zone delete`` deletes, the track a new via joins.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[2]
PRISTINE = Path(__file__).resolve().parent / "targets"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXE = ".exe" if os.name == "nt" else ""
EDITOR = {".kicad_pcb": "pcbnew", ".kicad_sch": "eeschema"}

SCH, PCB, PRO = "schmitt/schmitt.kicad_sch", "schmitt/schmitt.kicad_pcb", "schmitt/schmitt.kicad_pro"
LP = "layout-practice/layout-practice.kicad_pcb"
ZONE = json.dumps([{"x_mm": 10, "y_mm": 10}, {"x_mm": 45, "y_mm": 10}, {"x_mm": 45, "y_mm": 37}, {"x_mm": 10, "y_mm": 37}])
GND_STUB = json.dumps([{"x_mm": 23.85, "y_mm": 13.2}, {"x_mm": 21.6, "y_mm": 13.2}])
WRITE = {"lock": True, "tamper": "restore"}
DUP_SCH = {"dup": "schmitt-dup/schmitt.kicad_sch"}
# command: (document, options). dir: --set dir= a new folder; dir_in: a new folder inside that folder;
# dup: the copy with a duplicated reference for the ambiguous row; lock: a conflict row with the file
# open in KiCad; tamper: how read_back_mismatch changes the result; prep: a command run first.
JOBS: dict[str, tuple[str, dict[str, Any]]] = {
    "project get": (PRO, {}), "library list": (PRO, {}), "library get": (PRO, {}),
    "doc get": (SCH, {}), "doc list": ("schmitt", {"dir_in": "schmitt"}),
    "schematic check": (SCH, {}), "symbol list": (SCH, {}), "net list": (SCH, {}),
    "symbol get": (SCH, DUP_SCH),
    "schematic export": (SCH, {"dir": True, "tamper": "delete"}),
    "schematic render": (SCH, {"dir": True, "tamper": "delete"}),
    "schematic create": (PRO, {"dir": True, "tamper": "delete"}),
    "symbol create": ("schmitt-sch/schmitt.kicad_sch", WRITE),
    "symbol update": ("schmitt-sch/schmitt.kicad_sch", {**WRITE, **DUP_SCH}),
    "symbol move": ("schmitt-sch/schmitt.kicad_sch", {**WRITE, **DUP_SCH}),
    "symbol delete": ("schmitt-sch/schmitt.kicad_sch", {**WRITE, **DUP_SCH}),
    "pin update": ("schmitt-sch/schmitt.kicad_sch", {**WRITE, **DUP_SCH}),
    "net update": ("schmitt-sch/schmitt.kicad_sch", WRITE),
    "board create": (SCH, {"dir": True, "tamper": "delete"}),
    "board check": (PCB, {}), "footprint list": (PCB, {}), "track list": (PCB, {}),
    "board export": (PCB, {"dir": True, "tamper": "delete"}),
    "board render": (PCB, {"dir": True, "tamper": "delete"}),
    "footprint create": ("schmitt/LocalLogic.pretty", {"tamper": "delete"}),
    "netlist import": ("schmitt-eco/schmitt.kicad_pcb", WRITE),
    "footprint move": ("schmitt-dup/schmitt.kicad_pcb", WRITE),
    "track create": ("schmitt-dup/schmitt.kicad_pcb", WRITE),
    "track delete": (PCB, WRITE),
    "board get": (LP, {}),
    "board update": (LP, WRITE),
    "footprint update": ("schmitt-dup/schmitt.kicad_pcb", WRITE),
    "via create": (LP, {**WRITE, "prep": ["track", "create", "--net", "GND", "--layer", "F.Cu", "--width", "0.5mm",
                                          "--points", GND_STUB]}),
    "zone list": (LP, {}),
    "zone create": (LP, WRITE),
    "zone delete": (LP, {**WRITE, "prep": ["zone", "create", "--net", "GND", "--layer", "B.Cu", "--points", ZONE]}),
}


class Live:
    def __init__(self, targets: Path) -> None:
        self.targets = targets
        self.runs = targets / "runs"
        state = targets / ".state"
        state.mkdir(parents=True, exist_ok=True)
        scope = targets / "scope.json"  # for the preparing commands; the matrix runner writes its own
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        scope.write_text(json.dumps({"scope_id": "kicad-cli-live-runs", "effects": ["change", "artifact"],
                                     "dangerous": True, "targets": [targets.as_uri() + "/"], "expires_at": expires},
                                    indent=2), encoding="utf-8")
        self.env = dict(os.environ, KICAD_CLI_SCOPE_FILE=str(scope), KICAD_CLI_STATE_DIR=str(state))

    def reset(self, name: str) -> None:
        target = self.targets / name
        for _ in range(240):  # a program that just closed may still hold a file for a moment
            shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                break
            time.sleep(0.5)
        shutil.copytree(PRISTINE / name, target)

    def fresh_dir(self, name: str) -> Path:
        path = self.runs / f"{name.replace(' ', '-')}-{time.time_ns()}"
        path.mkdir(parents=True)
        return path

    def tool(self, *args: str) -> dict[str, Any]:
        p = subprocess.run([sys.executable, "-m", "kicad_cli", *args], cwd=REPO, env=self.env, capture_output=True,
                           text=True, encoding="utf-8", creationflags=NO_WINDOW)
        try:
            return json.loads(p.stdout)
        except ValueError:
            return {"ok": False, "raw": p.stdout + p.stderr}

    def confirm(self, *args: str) -> None:
        out = self.tool(*args)
        details = (out.get("error") or {}).get("details") or {}
        if details.get("confirm_token"):
            extra = ["--dangerous"] if (details.get("preview") or {}).get("tier") == "dangerous" else []
            out = self.tool(*args, "--confirm", details["confirm_token"], *extra)
        if not out.get("ok"):
            raise RuntimeError(f"preparing failed: {json.dumps(out)[:400]}")

    def matrix(self, command: str, sets: dict[str, str], rows: list[str], tamper: str | None = None) -> bool:
        args = [sys.executable, "scripts/spec.py", "matrix", "--command", command, "--row", ",".join(rows)]
        for name, value in sets.items():
            args += ["--set", f"{name}={value}"]
        if tamper:
            args += ["--tamper", tamper, "--pause", "60"]
        p = subprocess.run(args, cwd=REPO, env=self.env, capture_output=True, text=True, encoding="utf-8",
                           creationflags=NO_WINDOW)
        out = (p.stdout + p.stderr).strip()
        print(f"  [{','.join(rows)}] " + " | ".join(line for line in out.splitlines() if ": pass" in line
                                                   or ": fail" in line or "rror" in line[:40]), flush=True)
        return "fail" not in out and p.returncode == 0

    def run(self, name: str) -> bool:
        command, _, only = name.partition("#")
        doc_rel, opts = JOBS[command]
        target = doc_rel.split("/")[0]
        matrix = json.loads((REPO / "tests" / "matrix" / f"{command.replace(' ', '-')}.json").read_text(encoding="utf-8"))
        rows = [r["row"] for r in matrix["rows"]
                if r["applies"] and not r.get("unrunnable") and (not only or r["row"] in only.split(","))]
        special = {"no_scope", "read_back_mismatch"} | ({"conflict"} if opts.get("lock") else set()) | \
                  ({"ambiguous"} if opts.get("dup") else set())
        plain = [r for r in rows if r not in special]
        doc = self.targets / doc_rel
        ok = True

        def sets() -> dict[str, str]:
            if opts.get("dir_in"):
                folder = self.targets / opts["dir_in"] / f"run-{time.time_ns()}"
                folder.mkdir()
            else:
                folder = self.fresh_dir(command)
            return {"doc": str(doc), "dir": str(folder)}

        def prep() -> None:
            self.reset(target)
            if opts.get("prep"):
                self.confirm(*opts["prep"], "--doc", str(doc))

        print(command, flush=True)
        if plain:
            prep()
            ok &= self.matrix(command, sets(), plain)
        if "ambiguous" in rows and opts.get("dup"):
            self.reset(opts["dup"].split("/")[0])
            ok &= self.matrix(command, {"doc": str(self.targets / opts["dup"]), "dir": str(self.fresh_dir(command))},
                              ["ambiguous"])
        if "conflict" in rows and opts.get("lock"):
            prep()
            with opened(doc):
                ok &= self.matrix(command, sets(), ["conflict"])
        if "no_scope" in rows:  # alone, on the document as it was: the runner gives it no scope file
            prep()
            ok &= self.matrix(command, sets(), ["no_scope"])
        if "read_back_mismatch" in rows:
            prep()
            if opts.get("tamper") == "restore":
                copy = self.fresh_dir(command + "-copy") / doc.name
                shutil.copyfile(doc, copy)  # the file as it was before the write
                tamper = "{python} tests/live/disturb.py restore " + str(copy)
            else:
                tamper = "{python} tests/live/disturb.py delete"
            ok &= self.matrix(command, sets(), ["read_back_mismatch"], tamper=tamper)
        self.reset(target)
        return ok


@contextlib.contextmanager
def opened(path: Path, wait: float = 90.0) -> Iterator[Path]:
    """The file open in KiCad's own editor, until KiCad's lock file is beside it; then the editor is
    closed and the lock files it left are removed."""
    kicad = os.environ.get("KICAD_CLI_KICAD_DIR")
    if not kicad:
        raise SystemExit("Set KICAD_CLI_KICAD_DIR to KiCad's installation folder: conflict rows open its editors.")
    lock = path.with_name(f"~{path.name}.lck")
    before = set(path.parent.glob("~*.lck"))
    proc = subprocess.Popen([str(Path(kicad) / "bin" / (EDITOR[path.suffix] + EXE)), str(path)], creationflags=NO_WINDOW)
    try:
        end = time.time() + wait
        while not lock.exists():
            if time.time() > end or proc.poll() is not None:
                raise RuntimeError(f"KiCad did not lock {path.name}")
            time.sleep(0.5)
        yield lock
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            proc.kill()
        for _ in range(40):
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        for left in set(path.parent.glob("~*.lck")) - before:
            left.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--targets", required=True, type=Path, help="a work folder listed in docs/E2E.md")
    parser.add_argument("commands", nargs="*", help="commands to run, each optionally with #rows; default: all")
    args = parser.parse_args()
    live = Live(args.targets.resolve())
    names = args.commands or list(JOBS)
    failed = [name for name in names if not live.run(name)]
    shutil.rmtree(live.runs, ignore_errors=True)
    print("FAILED:", failed if failed else "none")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
