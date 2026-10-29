"""Actual Windows EXE cleanup refusals at each owned sidecar deletion.

Uses temporary sharing handles, not ACL changes or product fault switches.
Only the exact owned executable may be terminated on a test timeout.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--working-dir", type=Path)
    parser.add_argument("--driver-sdk", type=Path)
    parser.add_argument(
        "--baseline", type=Path, help="Frozen three-part input; run one cleanup interruption"
    )
    args = parser.parse_args()
    root, binary = args.output.resolve(), args.binary.resolve()
    cwd = args.working_dir.resolve() if args.working_dir else REPO
    if args.working_dir and (not cwd.is_dir() or cwd.is_relative_to(REPO) or not args.driver_sdk):
        parser.error("Outside cwd must exist and requires an explicit external --driver-sdk")
    driver_sdk = (args.driver_sdk or REPO / "kicad_cli/_driver_sdk").resolve()
    baseline = args.baseline.resolve() if args.baseline else REPO / "tests/fixtures/sch_edit"
    original = {
        p.name: sha(p)
        for p in baseline.iterdir()
        if p.suffix in {".kicad_sch", ".kicad_pcb", ".kicad_pro"}
    }
    if args.baseline:
        intended = json.loads((REPO / "bench/engineering_change_intent.json").read_bytes())
        assert original == intended["baseline_files"], "Frozen three-part input differs"
    root.mkdir(parents=True, exist_ok=False)
    assert os.name == "nt", "Windows evidence only; no platform inference"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

    def hold(path):
        handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
        return None if handle in {None, ctypes.c_void_p(-1).value} else handle

    def terminate_owned(pid):
        handle = kernel.OpenProcess(0x1001, False, pid)
        assert handle
        try:
            image = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(image))
            assert kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size))
            assert Path(image.value).resolve() == binary
            assert kernel.TerminateProcess(handle, 94)
        finally:
            kernel.CloseHandle(handle)

    def scene(mode, index):
        folder = root / f"{mode}-{index}"
        project = folder / "project"
        shutil.copytree(
            baseline,
            project,
            ignore=shutil.ignore_patterns("*.py", "__pycache__"),
        )
        # Valid whitespace enlarges the window for acquiring an owned backup
        # sharing handle. It changes no circuit or user engineering input.
        for path in project.glob("*.kicad_sch"):
            path.write_bytes(path.read_bytes() + b"\n" * (2 * 1024 * 1024))
        changes = folder / "changes.json"
        dump(
            changes,
            {
                "changes": [{"op": "set", "ref": "C1", "value": "470nF"}]
                if args.baseline
                else [
                    {"op": "set", "ref": "R1", "value": "12k"},
                    {"op": "set", "ref": "R10", "value": "33k"},
                ]
            },
        )
        scope = folder / "scope.json"
        dump(
            scope,
            {
                "scope_id": folder.name,
                "tool": "kicad-cli",
                "granted_by": "owned test",
                "granted_at": "2026-10-08T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "change", "persist"],
                "dangerous": False,
                "roots": [str(folder)],
                "outputs": [],
                "notes": "Owned disposable EXE tests",
            },
        )
        env = dict(
            {
                k: v
                for k, v in os.environ.items()
                if not k.startswith("KICAD") and k not in {"PYTHONPATH", "PYTHONHOME"}
            },
            PYTHONIOENCODING="utf-8",
            PYTHONDONTWRITEBYTECODE="1",
            KICAD_CLI_SCOPE_FILE=str(scope),
            KICAD_CLI_STATE_DIR=str(folder / "state"),
            KICAD_CLI_DRIVER_SDK_PATH=str(driver_sdk),
            KICAD_CONFIG_HOME=str(folder / "configuration"),
        )
        env.pop("KICAD_CLI_TRACE", None)
        argv = [
            "sch",
            "edit",
            "--schematic",
            str(project / ("decoupling.kicad_sch" if args.baseline else "edit.kicad_sch")),
            "--changes",
            str(changes),
            "--compact",
        ]

        def call(name, words):
            proc = subprocess.run(
                [str(binary), *words], cwd=cwd, env=env, capture_output=True, timeout=120
            )
            value = json.loads(proc.stdout)
            token = value.get("data", {}).pop("confirm_token", None)
            dump(
                folder / (name + ".json"),
                {
                    "returncode": proc.returncode,
                    "parsed": value,
                    "stderr": proc.stderr.decode("utf-8", "replace"),
                },
            )
            return proc.returncode, value, token

        code, preview, token = call("preview", argv + ["--dry-run"])
        assert code == 0 and preview["ok"], preview
        plan = preview["data"]["preview"]
        rows = plan["extensions"]["files"]
        assert len(rows) == (1 if args.baseline else 2)
        operation = plan["operation_id"]
        primary = project / ("decoupling.kicad_sch" if args.baseline else "edit.kicad_sch")
        actions = (
            [Path(r["path"] + "." + operation + ".backup") for r in rows]
            + [Path(r["path"] + ".kicad-cli.lock") for r in rows]
            + [Path(str(primary) + ".kicad-cli.journal")]
        )
        selected = actions[index if mode == "commit" else 0]
        with (
            (folder / "confirm.stdout").open("wb") as out,
            (folder / "confirm.stderr").open("wb") as err,
        ):
            proc = subprocess.Popen(
                [str(binary), *argv, "--confirm", token], cwd=cwd, env=env, stdout=out, stderr=err
            )
            handle = None
            deadline = time.monotonic() + 120
            try:
                while proc.poll() is None and time.monotonic() < deadline:
                    handle = hold(selected)
                    if handle:
                        break
                    time.sleep(0.0005)
                assert handle, "Owned sharing boundary was not reached"
                code = proc.wait(timeout=120)
            except BaseException:
                if proc.poll() is None:
                    journal = actions[-1]
                    if journal.is_file():
                        writer = json.loads(journal.read_bytes())
                        assert writer["operation_id"] == operation
                        terminate_owned(writer["pid"])
                    terminate_owned(proc.pid)
                    proc.wait(timeout=30)
                raise
            finally:
                if handle:
                    kernel.CloseHandle(handle)
        immediate = json.loads((folder / "confirm.stdout").read_bytes())
        assert code == 6 and immediate["error"]["code"] == "E_OUTCOME_UNKNOWN", immediate
        assert immediate["error"]["details"]["receipt"]["outcome"]["change"] == "applied"
        saved = {r["path"]: sha(r["path"]) for r in rows}
        assert saved == {r["path"]: r["intended"] for r in rows}

        def evidence(label):
            code, shown, _ = call(label, ["operation", "show", "--id", operation, "--compact"])
            assert code == 0 and shown["ok"], shown
            observed = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
            assert (
                observed["outcome"] == "applied" and observed["cleanup"]["status"] == "attributable"
            ), observed
            return observed

        observed = evidence("show")

        def resolve_words(record):
            return [
                "operation",
                "resolve",
                "--id",
                operation,
                "--outcome",
                "applied",
                "--evidence",
                record["evidence_file"],
                "--compact",
            ]

        if mode == "resolve":
            handle = hold(actions[index])
            assert handle
            try:
                code, refused, _ = call("resolve-refused", resolve_words(observed))
                assert code != 0 and refused["ok"] is False
            finally:
                kernel.CloseHandle(handle)
            assert {p: sha(p) for p in saved} == saved
            observed = evidence("show-after-resolve-refusal")
        code, resolved, _ = call("resolve", resolve_words(observed))
        assert code == 0 and resolved["ok"], resolved
        assert {p: sha(p) for p in saved} == saved
        archives = list((folder / "state/recovery" / operation).glob("baseline-*"))
        archived = {str(p): sha(p) for p in archives}
        assert set(archived.values()) == {r["baseline"] for r in rows}
        assert all(not p.exists() for p in actions)
        code, repeated, _ = call("resolve-repeated", resolve_words(observed))
        assert code == 2 and repeated["error"]["code"] == "E_VALIDATION"
        assert {p: sha(p) for p in saved} == saved and {p: sha(p) for p in archived} == archived
        code, following, _ = call("followup-preview", argv + ["--dry-run"])
        assert code == 0 and following["ok"]
        result = {
            "mode": mode,
            "index": index,
            "status": "passed",
            "expected": "applied saved files; resumable attributable cleanup",
            "observed_saved_sha256": saved,
            "baseline_archives_sha256": archived,
            "repeat_safe_refusal": True,
            "followup_preview": True,
            "permission_changes": False,
            "historical_WinError5_cause_fixed": False,
            "working_directory": str(cwd),
            "driver_sdk": str(driver_sdk),
            "original_input_unchanged": original
            == {p.name: sha(p) for p in baseline.iterdir() if p.name in original},
            "interruption": "Temporary sharing refusal after save; no process-crash claim",
        }
        dump(folder / "result.json", result)
        print(folder.name, "passed", flush=True)
        return result

    results = (
        [scene("commit", 0)]
        if args.baseline
        else [scene(mode, i) for mode in ("commit", "resolve") for i in range(5)]
    )
    dump(
        root / "result.json",
        {
            "status": "passed",
            "cases": results,
            "binary_sha256": sha(binary),
            "method_sha256": sha(__file__),
            "bounds": (
                "Actual Windows sharing refusal at selected owned cleanup deletions; "
                "source process-crash matrix is separate"
            ),
        },
    )


if __name__ == "__main__":
    main()
