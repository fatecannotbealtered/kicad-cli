"""One saved connector-value edit and attributable cleanup recovery on a new U4 copy."""

import copy
import ctypes as C
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import support


def verify_cleaning(journal, checkpoint, operation, intended):
    """Phase is in the independently signed checkpoint, not the document journal."""
    assert journal["hmac_valid"] and checkpoint["hmac_valid"]
    record = checkpoint["value"]
    assert journal["value"]["operation_id"] == record["operation_id"] == operation
    assert record["phase"] == "cleaning" and record["mode"] == "commit"
    assert record["journal"] == {k: v for k, v in journal["value"].items() if k != "hmac"}
    assert record["observed"] == intended


def run():
    from .environment_matrix import owned_path
    from .faults import signed
    from .process_tree import observe
    from .public_contract import infrastructure, validate_document

    sys.path.insert(0, str(support.ASSETS["readers"]))
    from sexpr_reader import children, parse

    scene = support.Scene(support.ROOT, "U4")
    root, project = scene.root, scene.project
    primary = project / "edit.kicad_sch"
    # Frozen before preview: whitespace widens only the backup acquisition window.
    with primary.open("ab") as stream:
        stream.write(b"\n" * (2 * 1024 * 1024))
    original = support.tree(project)
    support.put(root / "changes.json", {"changes": [{"op": "set", "ref": "J1", "value": "PWR_IN"}]})
    support.put(
        root / "FROZEN.json",
        {
            "binding": scene.binding,
            "files": original,
            "intent": "One J1 Value IN_PWR -> PWR_IN; all other syntax and project files preserved",
            "preparation": "2 MiB trailing whitespace on the owned root before preview",
            "attempts": 1,
            "confirmations": 1,
            "control": "Hold only this operation's owned backup without FILE_SHARE_DELETE",
            "expected": (
                "saved bytes, cleanup refusal, applied readback, resolve, no confirm replay"
            ),
            "miss": "not_covered; do not repeat confirmation or create another attempt",
            "script_sha256": support.sha(__file__),
        },
    )
    spec = Path(support.SETTINGS["sdk"]).parent / "driver_spec/schemas"
    schemas = {support.read(p)["$id"]: support.read(p) for p in spec.glob("*.schema.json")}
    calls = []

    def call(name, argv, expected):
        folder = scene.calls / name
        try:
            trace = observe([support.BINARY, *argv, "--compact"], root, scene.env, folder)
        finally:
            if (folder / "TRACE.json").exists():
                calls.append({"id": name, "trace_sha256": support.sha(folder / "TRACE.json")})
        assert trace["exit"] == expected and trace["job_active_before_close"] == 0
        assert all(
            infrastructure(p, support.BINARY, os.environ["SystemRoot"]) for p in trace["processes"]
        )
        doc = support.read(folder / "stdout")
        validate_document(doc, {"$ref": "urn:tool-driver-cli-spec:response:2.0"}, schemas)
        calls[-1].update(exit=expected, error=None if doc["ok"] else doc["error"]["code"])
        return doc

    result = {"status": "failed", "calls": calls, "binding": scene.binding}
    held = None
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        C.c_wchar_p,
        C.c_uint32,
        C.c_uint32,
        C.c_void_p,
        C.c_uint32,
        C.c_uint32,
        C.c_void_p,
    ]
    kernel.CreateFileW.restype = C.c_void_p
    kernel.CloseHandle.argtypes = [C.c_void_p]
    kernel.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
    kernel.OpenProcess.restype = C.c_void_p
    kernel.GetProcessTimes.argtypes = [C.c_void_p] * 5
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
    try:
        argv = ["sch", "edit", "--schematic", str(primary), "--changes", str(root / "changes.json")]
        preview = call("preview", [*argv, "--dry-run"], 0)["data"]
        plan, token = preview["preview"], preview["confirm_token"]
        assert plan["can_apply"] and support.tree(project) == original
        rows = plan["extensions"]["files"]
        assert len(rows) == 1 and Path(rows[0]["path"]) == primary
        operation = plan["operation_id"]
        result["operation_id"] = operation
        before = parse(primary.read_text(encoding="utf-8"))
        candidate = parse(Path(rows[0]["candidate"]).read_text(encoding="utf-8"))
        wanted = copy.deepcopy(before)
        changed = 0
        for symbol in children(wanted, "symbol"):
            props = {p[1]: p for p in children(symbol, "property")}
            if props["Reference"][2] == "J1":
                assert props["Value"][2] == "IN_PWR"
                props["Value"][2] = "PWR_IN"
                changed += 1
        assert changed == 1 and candidate == wanted
        backup = owned_path(Path(str(primary) + "." + operation + ".backup"), root)
        journal = Path(str(primary) + ".kicad-cli.journal")
        # Only one candidate writer. The other thread observes its full Debug tree.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call, "confirm", [*argv, "--confirm", token], 6)
            deadline = time.monotonic() + 120
            while not future.done() and time.monotonic() < deadline:
                if not backup.exists():
                    time.sleep(0.001)
                    continue
                record = support.record_sql(root / "state", operation)
                processes = [
                    support.read(p) for p in (scene.calls / "confirm").glob("process-*.json")
                ]
                live = []
                for process in processes:
                    if Path(process["image"]).resolve() != support.BINARY:
                        continue
                    handle = kernel.OpenProcess(0x101000, False, process["pid"])
                    if not handle:
                        continue
                    try:
                        times = [C.c_ulonglong() for _ in range(4)]
                        if (
                            kernel.GetProcessTimes(handle, *[C.byref(t) for t in times])
                            and kernel.WaitForSingleObject(handle, 0) == 258
                        ):
                            assert (
                                times[0].value == process["creation_filetime"] and process["owned"]
                            )
                            live.append(
                                {"pid": process["pid"], "birth": times[0].value, "alive": True}
                            )
                    finally:
                        kernel.CloseHandle(handle)
                assert live
                lease = {
                    "at": support.now(),
                    "lease_until_epoch": record["lease_until"],
                    "processes": live,
                    "before_control": True,
                }
                held = kernel.CreateFileW(str(backup), 0x80000000, 3, None, 3, 0x80, None)
                if held == C.c_void_p(-1).value:
                    held = None
                if held:
                    support.put(root / "LEASE-BEFORE-CONTROL.json", lease)
                    support.put(
                        root / "HANDLE-HELD.json",
                        {
                            "at": support.now(),
                            "backup": str(backup),
                            "share": "read|write, delete refused",
                            "operation_id": operation,
                        },
                    )
                break  # one acquisition opportunity; no repeated stage chasing
            try:
                receipt = future.result(timeout=140)
                assert held, "Owned cleanup boundary not hit; not covered"
                assert receipt["error"]["code"] == "E_OUTCOME_UNKNOWN"
                actual = receipt["error"]["details"]["receipt"]["outcome"]
                assert actual["change"] == "applied"
                witness = signed(journal, root / "state")
                checkpoint = signed(
                    root
                    / "state/document-transactions"
                    / (hashlib.sha256(operation.encode()).hexdigest() + ".json"),
                    root / "state",
                )
                verify_cleaning(witness, checkpoint, operation, {str(primary): rows[0]["intended"]})
                assert (
                    support.sha(primary) == rows[0]["intended"]
                    and support.sha(backup) == rows[0]["baseline"]
                )
                support.put(
                    root / "CLEANUP-WITNESS.local.json",
                    {"journal": witness, "checkpoint": checkpoint},
                )
            finally:
                if held:
                    assert kernel.CloseHandle(held)
                    held = None
                    support.put(
                        root / "HANDLE-RELEASED.json", {"at": support.now(), "released": True}
                    )
        final = {**original, primary.name: rows[0]["intended"]}
        shown = call("show", ["operation", "show", "--id", operation], 0)["data"][
            "operation_status"
        ]
        readback = shown["extensions"]["recovery_readback"]
        assert readback["outcome"] == "applied" and readback["cleanup"]["status"] == "attributable"
        resolve = [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            readback["evidence_file"],
        ]
        blocked = call("preview-blocked", [*argv, "--dry-run"], 6)
        assert blocked["error"]["code"] == "E_CONFLICT"
        resolution = call("resolve", resolve, 0)["data"]["operation_status"]
        assert resolution["resolution"]["outcome"] == "applied"
        assert call("resolve-repeat", resolve, 2)["error"]["code"] == "E_VALIDATION"
        follow = call("preview-after-resolve", [*argv, "--dry-run"], 0)["data"]["preview"]
        assert follow["can_apply"]
        assert support.tree(project) == final
        assert parse(primary.read_text(encoding="utf-8")) == wanted
        assert support.tree(scene.original) == scene.origin["files"]
        result.update(
            status="saved_recovery_passed_native_visual_pending",
            partial=False,
            changed_saved=True,
            cleanup_recovery="applied",
            original_confirm_replayed=False,
            protected_files=final,
            independent_structure="only J1 Value changed",
            engineering_confirmations=1,
            native_visual="not yet executed",
        )
    except BaseException as exc:
        result["failure"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if held:
            kernel.CloseHandle(held)
        support.put(root / "RESULT.json", result)
    print(json.dumps({k: result[k] for k in ("status", "failure", "operation_id") if k in result}))
    return result
