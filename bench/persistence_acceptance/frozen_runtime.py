"""Opt-in public execution of the frozen Windows platform/dependency fallback."""

import argparse
import ctypes
import json

from . import support as S
from .process_tree import observe


def run(config, output, execute=False):
    plan = S.configure(
        config,
        output,
        execute=execute,
        authorized_by="User-authorized isolated runtime probes" if execute else None,
    )
    if not execute:
        return plan
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(S.BINARY))
    names = list(archive.toc)
    for name in archive.toc:
        if name.endswith(".pyz"):
            names.extend(archive.open_embedded_archive(name).toc)
    assert not any("_wmi" in name.lower() for name in names)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.IsWow64Process2.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    process_machine, native_machine = ctypes.c_ushort(), ctypes.c_ushort()
    if not kernel.IsWow64Process2(
        kernel.GetCurrentProcess(), ctypes.byref(process_machine), ctypes.byref(native_machine)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    expected_arch = {0x8664: "amd64", 0xAA64: "arm64"}[native_machine.value]
    root = S.ROOT
    root.mkdir(parents=True, exist_ok=False)
    cases = [
        {"id": mode + "-" + command, "mode": mode, "command": command}
        for mode in ("root-discovery", "explicit", "bad-python", "bad-root")
        for command in ("context", "doctor")
    ]
    cases += [
        {"id": mode + "-context", "mode": mode, "command": "context"}
        for mode in ("arch-missing", "arch-conflict")
    ]
    S.put(
        root / "FROZEN.json",
        {
            "cases": cases,
            "exe_sha256": S.sha(S.BINARY),
            "build_source_commit": S.SETTINGS["build_source_commit"],
            "script_sha256": S.sha(__file__),
            "archive_wmi_entries": [],
            "kernel_native_machine": native_machine.value,
            "kernel_process_machine": process_machine.value,
            "expected_platform": "windows",
            "expected_arch": expected_arch,
            "expected": "All envelopes exit 0; context identity and doctor native/official checks "
            "match explicit configuration. Bad selection never silently falls back.",
            "engineering_writes": False,
        },
    )
    rows = []
    for case in cases:
        S.ROOT = root / case["id"]
        scene = S.Scene(S.ROOT, "U4")
        if case["mode"] == "arch-missing":
            scene.env.pop("PROCESSOR_ARCHITECTURE", None)
            scene.env.pop("PROCESSOR_ARCHITEW6432", None)
        elif case["mode"] == "arch-conflict":
            scene.env.update(PROCESSOR_ARCHITECTURE="ARM64", PROCESSOR_ARCHITEW6432="x86")
        if case["mode"] == "root-discovery":
            for key in ("KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL"):
                scene.env.pop(key)
        elif case["mode"] == "bad-python":
            scene.env["KICAD_CLI_PYTHON"] = str(scene.root / "missing-python.exe")
        elif case["mode"] == "bad-root":
            scene.env["KICAD_CLI_ROOT"] = str(scene.root / "missing-installation")
            for key in ("KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL"):
                scene.env.pop(key)
        before = S.tree(scene.project)
        trace = observe(
            [S.BINARY, case["command"], "--compact"],
            scene.root,
            scene.env,
            scene.root / "observed",
            timeout=180,
        )
        value = S.read(scene.root / "observed/stdout")
        assert trace["exit"] == 0 and value["ok"]
        assert trace["complete_tree"] and trace["job_active_before_close"] == 0
        assert before == S.tree(scene.project)
        native_ok = case["mode"] not in {"bad-python", "bad-root"}
        official_ok = case["mode"] != "bad-root"
        if case["command"] == "context":
            env = value["data"]["environment"]
            assert env["platform"] == "windows" and env["arch"] == expected_arch
            components = {c["name"]: c for c in env["components"]}
            assert components["KiCad external pcbnew interpreter"]["identity_status"] == (
                "observed" if native_ok else "unavailable"
            )
            result = {
                "platform": env["platform"],
                "arch": env["arch"],
                "native_identity": components["KiCad external pcbnew interpreter"][
                    "identity_status"
                ],
            }
        else:
            checks = {c["check"]: c for c in value["data"]["checks"]}
            assert checks["native_backend"]["status"] == ("pass" if native_ok else "fail")
            assert checks["official_cli"]["status"] == ("pass" if official_ok else "fail")
            result = {
                "diagnostic_completed": True,
                "checks": {k: v["status"] for k, v in checks.items()},
            }
        rows.append(
            {
                "id": case["id"],
                "status": "passed",
                "observed": result,
                "trace_sha256": S.sha(scene.root / "observed/TRACE.json"),
                "complete_tree": True,
                "empty_job": True,
                "protected_unchanged": True,
            }
        )
        S.put(scene.root / "RESULT.json", rows[-1])
        print(json.dumps(rows[-1]), flush=True)
    result = {
        "status": "passed",
        "cases": rows,
        "binary_sha256": S.sha(S.BINARY),
        "scope": "Runtime fallback of this frozen artifact; old C8/C409 not closed.",
    }
    S.put(root / "RESULT.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.out, args.execute)))
