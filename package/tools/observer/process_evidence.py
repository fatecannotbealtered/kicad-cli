"""Durable per-call evidence for owned tests. No application success inferred from exit alone."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from owned_process import spawn, terminate

ENVIRONMENT_KEYS = {
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
    "PYTHONHOME",
    "PYTHONPATH",
    "KICAD_CONFIG_HOME",
    "KICAD_CLI_ROOT",
    "KICAD_CLI_PYTHON",
    "KICAD_CLI_OFFICIAL",
    "KICAD_CLI_DRIVER_SDK_PATH",
    "KICAD_CLI_IPC_CLIENT_PATH",
    "KICAD_CLI_STATE_DIR",
    "KICAD_CLI_SCOPE_FILE",
    "KICAD10_SYMBOL_DIR",
    "KICAD10_FOOTPRINT_DIR",
    "KICAD10_3DMODEL_DIR",
    "KIPRJMOD",
}


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def native_debug_enabled(command, native_root, *, enabled=False):
    """Opt in only for the two explicitly selected native installation images."""
    if not isinstance(enabled, bool):
        raise ValueError("Native diagnostic selection must be an explicit boolean")
    if not enabled:
        return False
    if os.name != "nt":
        raise ValueError("Native exception diagnostics require Windows")
    if not native_root or not Path(native_root).is_absolute():
        raise ValueError("Native diagnostics require an absolute declared installation root")
    if not command or not Path(command[0]).is_absolute():
        return False
    root = Path(native_root).resolve()
    selected = {(root / "bin" / name).resolve() for name in ("python.exe", "kicad-cli.exe")}
    return Path(command[0]).resolve() in selected


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    # Atomic replacement of our own diagnostic JSON; raw stdout/stderr are append-only streams.
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def files(paths):
    result = []
    for path in sorted({str(Path(p).resolve()) for p in paths}):
        p = Path(path)
        try:
            result.append(
                {"path": path, "present": p.is_file(), "sha256": sha(p) if p.is_file() else None}
            )
        except OSError as exc:
            result.append({"path": path, "error": type(exc).__name__ + ": " + str(exc)})
    return result


def input_files(args, env):
    selected, skip = set(), False
    for arg in map(str, args):
        if skip:
            skip = False
            continue
        if arg in {"--output", "--out", "--confirm"}:
            skip = True
            continue
        try:
            p = Path(arg)
            if p.is_file():
                selected.add(p)
                if p.suffix in {".kicad_pcb", ".kicad_sch", ".kicad_pro"}:
                    selected.update(
                        q
                        for q in p.parent.rglob("*")
                        if q.is_file()
                        and (
                            q.suffix
                            in {
                                ".kicad_pcb",
                                ".kicad_sch",
                                ".kicad_pro",
                                ".kicad_dru",
                                ".kicad_sym",
                                ".kicad_mod",
                            }
                            or q.name in {"fp-lib-table", "sym-lib-table"}
                        )
                    )
        except (OSError, ValueError):
            continue
    config = env.get("KICAD_CONFIG_HOME")
    if config and Path(config).is_dir():
        selected.update(p for p in Path(config).rglob("*") if p.is_file())
    return selected


def inspect_report(path, kind):
    path = Path(path)
    record = {"path": str(path)}
    try:
        if not path.is_file():
            return dict(record, status="missing")
        record.update(sha256=sha(path), bytes=path.stat().st_size)
        if kind in {"json", "erc", "drc"}:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError("Expected report object")
            if kind == "erc":
                if not isinstance(value.get("sheets"), list) or not all(
                    isinstance(s.get("violations"), list) for s in value["sheets"]
                ):
                    raise ValueError("ERC sheets/violations missing")
            if kind == "drc" and not all(
                isinstance(value.get(k), list) for k in ("violations", "unconnected_items")
            ):
                raise ValueError("DRC finding collections missing")
        elif kind == "xml":
            value = ET.parse(path).getroot()
            if (
                value.tag != "export"
                or value.find("components") is None
                or value.find("nets") is None
            ):
                raise ValueError("Expected KiCad netlist export")
        elif kind == "csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                if not next(csv.reader(stream), None):
                    raise ValueError("Missing CSV header")
        elif not path.stat().st_size:
            raise ValueError("Empty report")
        record["status"] = "valid"
    except (ValueError, ET.ParseError, UnicodeError, OSError, TypeError, AttributeError) as exc:
        record.update(status="invalid", error=type(exc).__name__ + ": " + str(exc))
    return record


def run_recorded(
    root,
    name,
    command,
    *,
    cwd,
    env,
    timeout,
    report=None,
    stdout_json=False,
    binding=None,
    inputs=None,
    role="acceptance-subprocess",
    debug=False,
    stdio="pipes",
):
    root = Path(root)
    if Path(name).name != name:
        raise ValueError("One local evidence name required")
    journal = root / (name + ".call.json")
    if journal.exists():
        raise FileExistsError("Do not overwrite a previous call: " + str(journal))
    command = [str(a) for a in command]
    input_paths = input_files(command[1:], env) if inputs is None else set(map(Path, inputs))
    out_path, err_path = root / (name + ".stdout"), root / (name + ".stderr")
    record = {
        "name": name,
        "state": "started",
        "started_at": utc(),
        "role": role,
        "program": files([command[0]]),
        "command": command,
        "cwd": str(Path(cwd).resolve()),
        "timeout_seconds": timeout,
        "environment": {k: v for k, v in env.items() if k in ENVIRONMENT_KEYS},
        "environment_policy": "Relevant allowlist; no token keys or full machine environment",
        "inputs_before": files(input_paths),
        "source_binding": binding,
        "exit": None,
        "process_ownership": "owned suspended process assigned to ephemeral job"
        if os.name == "nt"
        else "owned process group",
        "report_requirement": report,
        "stdio": stdio,
        "debugger": debug,
    }
    if report:
        record["report_before"] = files([report["path"]])
    write(journal, record)
    start = time.monotonic()
    process, job, threads, capture_errors = None, None, [], []
    value = None

    def copy_pipe(pipe, destination):
        try:
            while True:
                chunk = pipe.read1(65536)
                if not chunk:
                    break
                destination.write(chunk)
                destination.flush()
        except (OSError, ValueError) as exc:
            capture_errors.append(str(exc))
        finally:
            pipe.close()

    try:
        with out_path.open("xb") as out, err_path.open("xb") as err:
            if report and record["report_before"][0].get("present"):
                record["execution"] = "refused_preexisting_report"
                raise FileExistsError(
                    "Report path already exists; old evidence must not satisfy this call"
                )
            if stdio not in {"pipes", "files"}:
                raise ValueError("Unsupported stdio control")
            process, job = spawn(
                command,
                cwd=cwd,
                env=env,
                debug=debug,
                stdout=subprocess.PIPE if stdio == "pipes" else out,
                stderr=subprocess.PIPE if stdio == "pipes" else err,
            )
            record["pid"] = process.pid
            if job:
                record["root_identity"] = job.root_identity
            write(journal, record)
            for pipe, target in (
                ((process.stdout, out), (process.stderr, err)) if stdio == "pipes" else []
            ):
                thread = threading.Thread(target=copy_pipe, args=(pipe, target), daemon=True)
                threads.append(thread)
                thread.start()
            try:
                if debug:
                    from win_debugging import Debugger

                    scene = Debugger(process, root / (name + "-debug"))
                    diagnostic = scene.monitor(job, timeout)
                    record["debug_evidence"] = str(root / (name + "-debug/result.json"))
                    record["execution"] = diagnostic["execution"]
                else:
                    process.wait(timeout=timeout)
                    record["execution"] = (
                        "completed"
                        if process.returncode == 0
                        else (
                            "crashed"
                            if process.returncode < 0 or process.returncode >= 0xC0000000
                            else "nonzero_exit"
                        )
                    )
            except subprocess.TimeoutExpired:
                record["execution"] = "timeout"
                terminate(process, job)
                record["cleanup"] = "owned process tree terminated and root reaped"
            except KeyboardInterrupt:
                record["execution"] = "interrupted"
                terminate(process, job)
                record["cleanup"] = "owned process tree terminated and root reaped"
            finally:
                # Expected refusals need the same whole-tree witness as exit zero.
                if job:
                    if record.get("execution") in {"completed", "nonzero_exit"}:
                        deadline = time.monotonic() + 1
                        while job.active() and time.monotonic() < deadline:
                            time.sleep(0.01)
                        if job.active():
                            record["execution"] = "owned_descendants_remaining"
                            record["cleanup"] = (
                                "remaining owned descendants terminated; not a completed call"
                            )
                    record["job_finalization"] = job.close()
                    job = None
                for thread in threads:
                    thread.join(timeout=5)
            record["exit"] = process.returncode
        if any(t.is_alive() for t in threads) or capture_errors:
            record["capture_errors"] = capture_errors or ["Output stream did not finish"]
        if stdout_json:
            try:
                value = json.loads(out_path.read_bytes())
                if not isinstance(value, dict):
                    raise ValueError("Expected one JSON object")
                record["stdout_parse"] = "valid"
            except (ValueError, UnicodeError) as exc:
                record.update(stdout_parse="invalid", parse_error=str(exc))
        if report:
            record["report"] = inspect_report(report["path"], report["kind"])
        report_ok = not report or record["report"]["status"] == "valid"
        record["status"] = (
            "passed"
            if (
                record["execution"] == "completed"
                and report_ok
                and (
                    not stdout_json
                    or record.get("stdout_parse") == "valid"
                    and value.get("ok", True)
                )
                and not record.get("capture_errors")
            )
            else "failed"
        )
    except BaseException as exc:
        record.update(
            status="failed",
            execution=record.get("execution", "launch_or_capture_failure"),
            failure=type(exc).__name__ + ": " + str(exc),
        )
        if process and process.poll() is None:
            try:
                terminate(process, job)
                record["cleanup"] = "owned process tree terminated and root reaped"
            except (OSError, subprocess.SubprocessError) as cleanup:
                record["cleanup"] = "refused: " + str(cleanup)
        if process:
            record["exit"] = process.returncode
    finally:
        if job:
            record["job_finalization"] = job.close()
        record.update(
            state="finished",
            finished_at=utc(),
            duration_seconds=time.monotonic() - start,
            inputs_after=files(input_paths),
            program_after=files([command[0]]),
            stdout_sha256=files([out_path])[0].get("sha256"),
            stderr_sha256=files([err_path])[0].get("sha256"),
        )
        if report and "report" not in record:
            record["report"] = inspect_report(report["path"], report["kind"])
        record["engineering_effect"] = (
            "not_inferred; observe actual inputs/operation state after failure"
        )
        if record["program_after"] != record["program"]:
            record.update(status="failed", program_drift=True)
        write(journal, record)
    return record, value
