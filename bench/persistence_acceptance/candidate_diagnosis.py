"""Finite real-candidate diagnosis, with private environment and dump evidence."""

import ctypes as C
import json
import os
import subprocess
import time
from pathlib import Path

from . import support

CASES = [
    {
        "id": name + "-" + mode,
        "command_id": name,
        "argv": argv,
        "mode": mode,
        "purpose": (
            "Compare the same historical failing request under ordinary and DEBUG_PROCESS startup"
        ),
    }
    for name, argv in (
        ("full-reference", ["reference", "--compact"]),
        ("selected-reference", ["reference", "--command", "board check", "--compact"]),
        ("operation-list", ["operation", "list", "--compact"]),
    )
    for mode in ("ordinary", "debug")
]


def standard_handles():
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes, kernel.GetStdHandle.restype = [C.c_uint32], C.c_void_p
    kernel.GetHandleInformation.argtypes = [C.c_void_p, C.POINTER(C.c_uint32)]
    kernel.GetFileType.argtypes, kernel.GetFileType.restype = [C.c_void_p], C.c_uint32
    result = {}
    for name, index in (("stdin", -10), ("stdout", -11), ("stderr", -12)):
        handle = kernel.GetStdHandle(index)
        flags = C.c_uint32()
        valid = bool(handle and kernel.GetHandleInformation(handle, C.byref(flags)))
        result[name] = {
            "handle": handle,
            "valid": valid,
            "flags": flags.value,
            "file_type": int(kernel.GetFileType(handle)) if valid else None,
        }
    return result


def ordinary(argv, cwd, env, output, timeout=120):
    from .process_tree import WindowsJob
    from .win32_control import inspect_job

    output.mkdir()
    job, process = WindowsJob(), None
    rows, result = (
        {},
        {
            "mode": "ordinary",
            "started_at": support.now(),
            "complete_process_history": False,
            "controlled_termination": False,
        },
    )
    try:
        with (output / "stdout").open("xb") as out, (output / "stderr").open("xb") as err:
            process = subprocess.Popen(
                list(map(str, argv)),
                cwd=cwd,
                env=env,
                stdout=out,
                stderr=err,
                creationflags=0x08000000 | 0x4,
            )
            job.assign_and_resume(process)
            result["root_pid"] = process.pid
            deadline = time.monotonic() + timeout
            while True:
                snap = inspect_job(job.handle)
                for item in snap["members"]:
                    key = (item["pid"], item["creation_filetime"])
                    if key not in rows:
                        rows[key] = dict(
                            item, observed_at=snap["at"], image_sha256=support.sha(item["image"])
                        )
                if process.poll() is not None and not snap["members"]:
                    result.update(exit=process.returncode, ended_tree=snap)
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Owned diagnostic invocation exceeded bound")
                time.sleep(0.01)
    except BaseException as exc:
        result["observer_failure"] = type(exc).__name__ + ": " + str(exc)
        result["members_before_termination"] = inspect_job(job.handle)
        result["controlled_termination"] = True
        job.terminate()
        if process:
            process.wait(timeout=10)
    finally:
        deadline = time.monotonic() + 10
        while job.active() and time.monotonic() < deadline:
            time.sleep(0.01)
        result["job_active_before_close"] = job.active()
        result["natural_tree_complete"] = not result["controlled_termination"] and not job.active()
        job.close()
        result.update(processes=list(rows.values()), finished_at=support.now())
        for name in ("stdout", "stderr"):
            result[name + "_sha256"] = support.sha(output / name)
        support.put(output / "TRACE.json", result)
    return result


def run():
    from .process_tree import observe

    base = support.ROOT
    base.mkdir(parents=True, exist_ok=False)
    matrix = {
        "format": "candidate-diagnostic-matrix.v1",
        "cases": CASES,
        "binding": {
            "exe_sha256": support.sha(support.BINARY),
            "build_source_commit": support.SETTINGS["build_source_commit"],
            "script_sha256": support.sha(__file__),
            "scripts": {
                path.name: support.sha(path)
                for path in (
                    support.PACKAGE / "process_tree.py",
                    support.PACKAGE / "win32_control.py",
                    support.PACKAGE / "support.py",
                    support.ASSETS["support"] / "owned_process.py",
                    support.ASSETS["support"] / "win_debugging.py",
                )
            },
            "sdk_files": {
                path.relative_to(Path(support.SETTINGS["sdk"]).parent).as_posix(): support.sha(path)
                for path in sorted(Path(support.SETTINGS["sdk"]).parent.rglob("*"))
                if path.is_file() and path.suffix in {".py", ".json"}
            },
            "native_executables": {
                name: support.sha(Path(support.SETTINGS["native_root"]) / "bin" / name)
                for name in ("python.exe", "kicad-cli.exe")
            },
        },
        "factor": "ordinary controlled startup versus DEBUG_PROCESS observation",
        "common": (
            "same EXE, args, external SDK/native installations, inherited stdin, "
            "file stdout/stderr; fresh equivalent copy per cell"
        ),
        "normalization": (
            "new cwd/state/config paths and scope IDs/time are recorded substitutions, "
            "not silently equal bytes"
        ),
        "limits": (
            "Ordinary process sampling is not complete creation history; "
            "WER/exception traces differ in visibility. No acceptance retry."
        ),
    }
    support.put(base / "FROZEN-MATRIX.json", matrix)
    results = []
    selected = support.SETTINGS.get("diagnostic_case_ids", [c["id"] for c in CASES])
    if len(selected) != len(set(selected)) or set(selected) - {c["id"] for c in CASES}:
        raise ValueError("Unknown or duplicated diagnostic IDs")
    for case in CASES:
        if case["id"] not in selected:
            continue
        support.ROOT = base / case["id"]
        scene = support.Scene(support.ROOT, "RC-ECO")
        folder = scene.root / "observed"
        before = support.tree(scene.project)
        # This full environment is intentionally local-only. It may contain credentials.
        support.put(scene.root / "PRIVATE-ENVIRONMENT.json", scene.env)
        support.put(
            scene.root / "LAUNCH.json",
            {
                "at": support.now(),
                "case": case,
                "argv": [str(support.BINARY), *case["argv"]],
                "cwd": str(scene.root),
                "parent_pid": os.getpid(),
                "parent_standard_handles": standard_handles(),
                "child_stdin": "inherited",
                "child_stdout_stderr": "owned files",
                "descendant_handle_details": (
                    "available in exception dump handle/memory streams if an exception occurs"
                ),
                "environment_sha256": support.sha(scene.root / "PRIVATE-ENVIRONMENT.json"),
                "protected_before": before,
                "binding": scene.binding,
            },
        )
        row = {"id": case["id"], "mode": case["mode"]}
        try:
            if case["mode"] == "debug":
                trace = observe([support.BINARY, *case["argv"]], scene.root, scene.env, folder)
            else:
                trace = ordinary([support.BINARY, *case["argv"]], scene.root, scene.env, folder)
            row["exit"] = trace["exit"]
            row["status"] = "completed" if trace["exit"] == 0 else "failed_cycle"
        except BaseException as exc:
            row.update(status="failed_cycle", failure=type(exc).__name__ + ": " + str(exc))
        if (folder / "TRACE.json").exists():
            trace = support.read(folder / "TRACE.json")
            row.update(
                trace_sha256=support.sha(folder / "TRACE.json"),
                exit=trace.get("exit"),
                exceptions=trace.get("exceptions", []),
            )
        row["protected_unchanged"] = support.tree(scene.project) == before
        support.put(scene.root / "OUTCOME.json", row)
        results.append(row)
        print(
            json.dumps({k: v for k, v in row.items() if k in {"id", "status", "exit", "failure"}}),
            flush=True,
        )
    result = {
        "status": "diagnostic_matrix_recorded",
        "cases": results,
        "engineering_confirmations": 0,
        "root_cause": "requires_evidence_review",
    }
    support.put(base / "RESULT.json", result)
    return result
