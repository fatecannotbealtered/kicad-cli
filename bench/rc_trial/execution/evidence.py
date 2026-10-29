"""Independent evidence recorder; never imports or edits engineering product data."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_ROOT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("RC4_WORK_ROOT", str(BASE_ROOT))).resolve()
if not ROOT.is_relative_to(BASE_ROOT):
    raise RuntimeError("Recorder work root must remain within the authorized execution directory")
PACKET = BASE_ROOT.parent / "agent-packet"
DEPENDENCIES = PACKET / "DEPENDENCIES.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def console_summary(raw: str) -> str:
    try:
        value = json.loads(raw)
        data = value.get("data", {})
        if "preview" in data:
            p = data["preview"]
            trial = p.get("extensions", {}).get("trial", {}).get("result")
            if isinstance(trial, dict):
                trial = {k: v for k, v in trial.items() if k not in ("verify", "effective_classes")}
            value = {
                "ok": value.get("ok"),
                "preview": {
                    key: p.get(key)
                    for key in (
                        "operation_id",
                        "capability_id",
                        "can_apply",
                        "expires_at",
                        "outputs",
                        "unknowns",
                    )
                },
                "change_count": len(p.get("changes", [])),
                "change_sample": p.get("changes", [])[:4],
                "trial": trial,
                "extension_keys": list(p.get("extensions", {})),
            }
        elif "receipt" in data:
            r = data["receipt"]
            outcome = r.get("outcome", {})
            value = {
                "ok": value.get("ok"),
                "operation_id": r.get("operation_id"),
                "failed_stage": r.get("failed_stage"),
                "outcome": {k: v for k, v in outcome.items() if k != "verification"},
                "verification": {
                    k: v for k, v in outcome.get("verification", {}).items() if k != "extensions"
                },
                "extension_keys": list(r.get("extensions", {})),
                "task": r.get("extensions", {}).get("task"),
            }
        elif "operation_status" in data:
            op = data["operation_status"]
            value = {
                "ok": value.get("ok"),
                "operation_status_keys": list(op),
                "operation_status": {
                    k: v
                    for k, v in op.items()
                    if k
                    not in ("receipt", "preview", "extensions", "plan", "last_receipt", "result")
                },
            }
        return json.dumps(value, ensure_ascii=False)
    except (ValueError, AttributeError):
        return raw


def snapshot() -> dict:
    result = {}
    for folder_name in ("project", "artifacts", "inputs", "plans", "consumers", "configuration"):
        folder = ROOT / folder_name
        if folder.exists():
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    result[str(path.relative_to(ROOT))] = digest(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-root")
    parser.add_argument(
        "mode",
        choices=("read", "list", "hash", "candidate", "official", "consumer", "native-consumer"),
    )
    parser.add_argument("label")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    arguments = options.args
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    settings = json.loads(DEPENDENCIES.read_text(encoding="utf-8-sig"))
    if ROOT != BASE_ROOT:
        settings["environment"]["KICAD_CONFIG_HOME"] = str(ROOT / "configuration")
        settings["environment"]["KICAD_CLI_STATE_DIR"] = str(ROOT / "state")
    selection_file = ROOT / "plans/candidate-selection.json"
    selection = (
        json.loads(selection_file.read_text(encoding="utf-8")) if selection_file.exists() else None
    )
    if selection:
        settings["candidate"] = {"path": selection["candidate_path"], "sha256": selection["sha256"]}
    folder = ROOT / "evidence" / options.label
    folder.mkdir(parents=True, exist_ok=False)
    candidate = Path(settings["candidate"]["path"])
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith("KICAD") or key.startswith("PYTHON"):
            environment.pop(key, None)
    for key in settings.get("remove_environment", []):
        environment.pop(key, None)
    environment.update(settings["environment"])
    if options.config_root:
        environment["KICAD_CONFIG_HOME"] = str(Path(options.config_root).resolve())
    environment["TEMP"] = str(ROOT / "temporary")
    environment["TMP"] = str(ROOT / "temporary")
    Path(environment["TEMP"]).mkdir(exist_ok=True)
    for name in ("KICAD_CONFIG_HOME", "KICAD_CLI_STATE_DIR"):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    command = None
    programs = {
        "candidate": str(candidate),
        "official": settings["environment"]["KICAD_CLI_OFFICIAL"],
        "consumer": settings["readonly_consumer_python"],
        "native-consumer": settings["environment"]["KICAD_CLI_PYTHON"],
    }
    if options.mode in programs:
        command = [programs[options.mode], *arguments]
    start = {
        "label": options.label,
        "started_at": now(),
        "terminal": "running",
        "mode": options.mode,
        "argv": command or [options.mode, *arguments],
        "recorder_argv": sys.argv,
        "cwd": str(ROOT),
        "recorder_sha256": digest(Path(__file__)),
        "candidate_sha256": digest(candidate),
        "expected_candidate_sha256": settings["candidate"]["sha256"],
        "candidate_selection": selection,
        "dependencies_sha256": digest(DEPENDENCIES),
        "scope_sha256": digest(Path(environment["KICAD_CLI_SCOPE_FILE"])),
        "environment_overrides": {key: environment[key] for key in settings["environment"]},
        "removed_environment": settings.get("remove_environment", []),
        "temporary_directory": environment["TEMP"],
        "inputs": {arg: digest(Path(arg)) for arg in arguments if Path(arg).is_file()},
        "before": snapshot(),
    }
    (folder / "start.json").write_text(
        json.dumps(start, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    stdout_path, stderr_path = folder / "stdout.txt", folder / "stderr.txt"
    exit_code = 0
    begin = time.monotonic()
    try:
        if start["candidate_sha256"].lower() != start["expected_candidate_sha256"].lower():
            raise RuntimeError("Candidate hash does not match supplied DEPENDENCIES.json")
        if command:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr
                )
                start["pid"] = process.pid
                (folder / "start.json").write_text(
                    json.dumps(start, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                exit_code = process.wait()
        else:
            records = []
            for arg in arguments:
                path = Path(arg)
                if options.mode == "read":
                    records.append(
                        {
                            "path": str(path),
                            "sha256": digest(path),
                            "text": path.read_text(encoding="utf-8-sig"),
                        }
                    )
                elif options.mode == "list":
                    records.append(
                        {
                            "path": str(path),
                            "entries": [
                                {"name": p.name, "directory": p.is_dir()}
                                for p in sorted(path.iterdir())
                            ],
                        }
                    )
                else:
                    records.append({"path": str(path), "sha256": digest(path)})
            stdout_path.write_text(
                json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            stderr_path.write_text("", encoding="utf-8")
    except Exception as error:
        exit_code = 127
        stderr_path.write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
        if not stdout_path.exists():
            stdout_path.write_text("", encoding="utf-8")
    finish = {
        **start,
        "finished_at": now(),
        "duration_seconds": time.monotonic() - begin,
        "terminal": "completed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "stdout_sha256": digest(stdout_path),
        "stderr_sha256": digest(stderr_path),
        "after": snapshot(),
    }
    (folder / "finish.json").write_text(
        json.dumps(finish, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {"label": options.label, "exit_code": exit_code, "evidence": str(folder)},
            ensure_ascii=False,
        )
    )
    raw_output = stdout_path.read_text(encoding="utf-8", errors="replace")
    print(console_summary(raw_output) if options.mode == "candidate" else raw_output)
    if stderr_path.stat().st_size:
        print("STDERR:\n" + stderr_path.read_text(encoding="utf-8", errors="replace"))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
