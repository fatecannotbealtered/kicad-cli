"""One external fault attempt per predeclared case against the final EXE."""

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

from .support import (
    ASSETS,
    BINARY,
    CASES,
    ROOT,
    SETTINGS,
    Scene,
    identity,
    now,
    put,
    read,
    record_sql,
    sha,
    tree,
)
from .win32_control import OwnedProcess

sys.path.insert(0, str(ASSETS["readers"]))
from sexpr_reader import child, children, parse


def signed(path, state):
    if not path.exists():
        return None
    value = read(path)
    payload = dict(value)
    signature = payload.pop("hmac", "")
    key = bytes.fromhex((state / "confirm.secret").read_text(encoding="ascii"))
    valid = hmac.compare_digest(
        signature,
        hmac.new(
            key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256
        ).hexdigest(),
    )
    return {"path": str(path), "sha256": sha(path), "hmac_valid": valid, "value": value}


def status_files(rows):
    return {r["path"]: sha(r["path"]) for r in rows}


def compare_intent(rows):
    checks = []
    for row in rows:
        left = parse(Path(row["path"]).read_text(encoding="utf-8"))
        right = parse(Path(row["candidate"]).read_text(encoding="utf-8"))
        expected = {"C1": ("100nF", "120nF"), "R10": ("2k2", "3k3")}
        actual = []
        for before, after in zip(children(left, "symbol"), children(right, "symbol"), strict=True):
            ref = next((p[2] for p in children(before, "property") if p[1] == "Reference"), None)
            if ref in expected:
                old, new = expected[ref]
                a = next(p for p in children(before, "property") if p[1] == "Value")
                b = next(p for p in children(after, "property") if p[1] == "Value")
                assert (a[2], b[2]) == (old, new), (ref, a[2], b[2])
                actual.append(
                    {"ref": ref, "uuid": child(before, "uuid")[1], "before": old, "after": new}
                )
                b[2] = a[2]
        assert left == right, "Independent syntax tree changed outside declared Value text"
        checks.append(
            {"path": row["path"], "only_declared_values_changed": True, "changes": actual}
        )
    assert {c["ref"] for row in checks for c in row["changes"]} == {"C1", "R10"}
    return checks


def run_case(case_id):
    case = CASES[case_id]
    scene = Scene(ROOT, "U4")
    output, project, state = scene.root, scene.project, scene.root / "state"
    result = {
        "id": case_id,
        "binding": scene.binding,
        "case": case,
        "started_at": now(),
        "attempt": 1,
        "class": "real_EXE_external_fault",
        "hit": False,
        "native_crashes": 0,
    }
    control = None
    try:
        for name in ("edit.kicad_sch", "edit_child.kicad_sch"):
            p = project / name
            with p.open("ab") as f:
                f.write(
                    b"\n"
                    * (
                        (48 if name == "edit_child.kicad_sch" else 8) * 1024 * 1024
                        if case_id == "F5-partial-save"
                        else 8 * 1024 * 1024
                    )
                )
        put(
            output / "changes.json",
            {
                "changes": [
                    {"op": "set", "ref": "C1", "value": "120nF"},
                    {"op": "set", "ref": "R10", "value": "3k3"},
                ]
            },
        )
        original_primary = tree(project)
        put(
            output / "FROZEN.json",
            {
                "at": now(),
                "binding": scene.binding,
                "matrix_sha256": sha(Path(__file__).with_name("matrix.json")),
                "controller_sha256": sha(Path(__file__).with_name("win32_control.py")),
                "harness_sha256": sha(Path(__file__)),
                "files": original_primary,
                "preparation": (
                    "8 MiB root / 48 MiB child trailing whitespace for F5; "
                    "8 MiB each for historical F0-F4 methods. "
                    "Before preview; no product/journal injection"
                ),
                "changes": read(output / "changes.json"),
                "observational_stop_seconds": 150,
            },
        )
        for command in ("reference", "context", "doctor"):
            code, value = scene.call(command, [command])
            assert code == 0 and value["ok"], value
        code, value = scene.call("edit-reference", ["reference", "--command", "sch edit"])
        assert code == 0 and value["ok"]
        args = [
            "sch",
            "edit",
            "--schematic",
            str(project / "edit.kicad_sch"),
            "--changes",
            str(output / "changes.json"),
        ]
        code, preview = scene.call("preview", [*args, "--dry-run"])
        assert code == 0 and preview["data"]["preview"]["can_apply"], preview
        assert tree(project) == original_primary
        plan, token = preview["data"]["preview"], preview["data"]["confirm_token"]
        rows = plan["extensions"]["files"]
        assert len(rows) == 2 and all(r["baseline"] != r["intended"] for r in rows)
        assert {Path(r["path"]) for r in rows} == {
            project / "edit.kicad_sch",
            project / "edit_child.kicad_sch",
        }
        assert all(
            Path(r["candidate"]).resolve().is_relative_to(state.resolve() / "engineering-trials")
            for r in rows
        )
        put(output / "INDEPENDENT-PREVIEW.json", compare_intent(rows))
        operation = plan["operation_id"]
        result["operation_id"] = operation
        journal = Path(plan["extensions"]["primary"] + ".kicad-cli.journal")
        checkpoint = (
            state
            / "document-transactions"
            / (hashlib.sha256(operation.encode()).hexdigest() + ".json")
        )
        pending = [Path(r["path"] + "." + operation + ".pending") for r in rows]
        original_inodes = {r["path"]: Path(r["path"]).stat().st_ino for r in rows}
        cmd = [str(BINARY), *args, "--confirm", token, "--compact"]
        put(
            output / "CONFIRM-START.json",
            {
                "at": now(),
                "argv": cmd,
                "operation": operation,
                "binding": scene.binding,
                "stdout_mode": "undrained pipe" if case_id.startswith("F2") else "file",
            },
        )
        control = OwnedProcess(
            cmd,
            output,
            scene.env,
            output / "confirm.stdout",
            output / "confirm.stderr",
            pipe=case_id.startswith("F2"),
            allow_control=SETTINGS["allow_owned_termination"],
        )
        put(output / "CONTROL-START.json", {"at": now(), "events": control.events})
        deadline = time.monotonic() + 150
        trigger = None
        writer_pid = None
        writer_birth = None
        lease_witness = None
        while control.running() and time.monotonic() < deadline:
            try:
                if journal.exists() and writer_birth is None:
                    doc = read(journal)
                    if doc.get("operation_id") == operation:
                        signed_journal = signed(journal, state)
                        if signed_journal["hmac_valid"]:
                            writer_pid = doc["pid"]
                            writer_birth = control.identify(writer_pid)["creation_filetime"]
                            lease_row = record_sql(state, operation)
                            lease_witness = {
                                "sampled_at": now(),
                                "sampled_epoch": time.time(),
                                "raw_lease_until": lease_row["lease_until"],
                                "status": lease_row["status"],
                                "operation_id": operation,
                                "live_writer": control.identify(writer_pid),
                                "before_control": True,
                            }
                            put(output / "LEASE-BEFORE-CONTROL.json", lease_witness)
                if (
                    case_id == "F5-partial-save"
                    and writer_birth is not None
                    and Path(rows[0]["path"]).stat().st_ino != original_inodes[rows[0]["path"]]
                ):
                    trigger = {"kind": "first_formal_target_replaced", "target_index": 0}
                elif case_id.startswith(("F0", "F4")) and journal.exists():
                    doc = read(journal)
                    if doc.get("operation_id") == operation:
                        trigger = {"kind": "journal", "sha256": sha(journal)}
                        writer_pid = doc["pid"]
                elif case_id.startswith("F1") and (
                    any(p.exists() for p in pending)
                    or any(Path(p).stat().st_ino != inode for p, inode in original_inodes.items())
                ):
                    doc = read(journal)
                    assert doc["operation_id"] == operation
                    trigger = {
                        "kind": "bound_pending_or_replaced_file",
                        "paths": [str(p) for p in pending if p.exists()],
                        "replaced": [
                            p
                            for p, inode in original_inodes.items()
                            if Path(p).stat().st_ino != inode
                        ],
                    }
                    writer_pid = doc["pid"]
                elif case_id.startswith("F3") and checkpoint.exists():
                    doc = read(checkpoint)
                    if doc.get("operation_id") == operation and doc.get("phase") == "cleaning":
                        trigger = {
                            "kind": "cleaning_checkpoint",
                            "cursor": doc["cursor"],
                            "pending": doc["pending"],
                        }
                        writer_pid = doc["journal"]["pid"]
                elif case_id.startswith("F2") and control.pipe_available():
                    doc = record_sql(state, operation)
                    writer_pid = (doc.get("extra") or {}).get("writer_pid")
                    trigger = {"kind": "stdout_backpressure", "available": control.pipe_available()}
                if trigger:
                    break
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.001)
        if trigger and writer_pid:
            try:
                control.freeze(writer_pid, BINARY, expected_birth=writer_birth)
            except (OSError, RuntimeError) as exc:
                result["freeze_error"] = str(exc)
        frozen = control.debug_event is not None
        if frozen:
            current = status_files(rows)
            j, cp = signed(journal, state), signed(checkpoint, state)
            store = record_sql(state, operation)
            pends = {
                str(p): {"sha256": sha(p), "bytes": p.stat().st_size} for p in pending if p.exists()
            }
            for row, path in zip(rows, pending, strict=True):
                if str(path) in pends:
                    payload = path.read_bytes()
                    pends[str(path)]["is_intended_prefix"] = (
                        Path(row["candidate"]).read_bytes().startswith(payload)
                    )
                    pends[str(path)]["complete_intended_bytes"] = sha(path) == row["intended"]
            witness = {
                "at": now(),
                "trigger": trigger,
                "writer": identity(writer_pid),
                "owned_parent": identity(control.pid),
                "events": control.events,
                "current": current,
                "pending_files": pends,
                "journal": j,
                "checkpoint": cp,
                "store_readonly": store,
                "lease_before_control": lease_witness,
            }
            put(output / "FROZEN-WITNESS.json", witness)
            scene.freeze("frozen-witness-files")
            baseline = {r["path"]: r["baseline"] for r in rows}
            intended = {r["path"]: r["intended"] for r in rows}
            attributed = bool(
                j and j["hmac_valid"] and j["value"]["operation_id"] == operation
            ) or bool(cp and cp["hmac_valid"] and cp["value"]["operation_id"] == operation)
            if case_id.startswith(("F0", "F4")):
                hit = current == baseline
            elif case_id == "F5-partial-save":
                hit = (
                    current != baseline
                    and current != intended
                    and any(current[r["path"]] == r["intended"] for r in rows)
                    and any(current[r["path"]] == r["baseline"] for r in rows)
                    and all(current[r["path"]] in (r["baseline"], r["intended"]) for r in rows)
                )
            elif case_id.startswith("F1"):
                hit = any(p["bytes"] > 0 and p["is_intended_prefix"] for p in pends.values()) or (
                    current != baseline
                    and current != intended
                    and all(current[r["path"]] in (r["baseline"], r["intended"]) for r in rows)
                )
            elif case_id.startswith("F2"):
                hit = current == intended and bool(
                    cp
                    and cp["hmac_valid"]
                    and cp["value"]["phase"] in {"cleaning", "cleaned"}
                    or store["status"] == "succeeded"
                )
            else:
                hit = bool(cp and cp["hmac_valid"] and cp["value"]["phase"] == "cleaning")
            hit = hit and attributed
            result.update(
                hit=bool(hit),
                actual_frozen_files=current,
                actual_checkpoint_phase=cp["value"]["phase"] if cp else None,
            )
        if control.running() or frozen:
            stopped = control.stop()
        else:
            stopped = {
                "parent_pid": control.pid,
                "parent_exit": control.exit_code(),
                "external_controlled": False,
            }
            if stopped["parent_exit"] >= 0xC0000000:
                result["native_crashes"] += 1
                raise RuntimeError("Spontaneous native exception: stop this cycle")
        put(
            output / "CONTROL-FINISH.json",
            {
                "at": now(),
                "trigger": trigger,
                "frozen": frozen,
                "stop": stopped,
                "events": control.events,
            },
        )
        if control.pipe_fd is not None:
            captured = bytearray()
            while control.pipe_available():
                captured.extend(os.read(control.pipe_fd, control.pipe_available()))
            (output / "confirm.stdout").write_bytes(captured)
            try:
                json.loads(captured)
                result["complete_receipt_delivered"] = True
                result["hit"] = False
            except (ValueError, UnicodeError):
                result["complete_receipt_delivered"] = False
            result["captured_pipe_bytes_after_stop"] = len(captured)
        finalization = control.close()
        put(output / "JOB-FINALIZATION.json", finalization)
        result["controlled_termination"] = finalization["controlled_termination"]
        result["natural_tree_complete"] = finalization["natural_tree_complete"]
        control = None
        scene.freeze("after-stop")
        if case_id.startswith("F4"):
            assert frozen, (
                "No owned stop witness; external drift is not authorized on a live target"
            )
            target = Path(rows[0]["path"])
            before = sha(target)
            with target.open("ab") as f:
                f.write(b"\n")
                f.flush()
                os.fsync(f.fileno())
            put(
                output / "EXTERNAL-DRIFT.json",
                {
                    "at": now(),
                    "target": str(target),
                    "before": before,
                    "after": sha(target),
                    "operation": "Append one predeclared newline only after owned writer stop",
                },
            )
            scene.freeze("after-external-drift")
        store = record_sql(state, operation)
        put(output / "STORE-AFTER-STOP.json", store)
        assert store["status"] != "planned" and store["consumed_tokens_count"] > 0, (
            "No consumed-token evidence: do not repeat confirmation"
        )
        lease = store.get("lease_until")
        delay = max(0, (lease or 0) - time.time() + 0.2)
        assert delay < 35, "Unexpected lease; do not alter store or clock"
        if delay:
            put(
                output / "LEASE-WAIT.json",
                {
                    "started_at": now(),
                    "seconds": delay,
                    "lease_until": lease,
                    "clock_unchanged": True,
                },
            )
            time.sleep(delay)
        project_bytes = {n: sha(project / n) for n in original_primary}
        code, shown = scene.call("fresh-show", ["operation", "show", "--id", operation])
        assert code == 0 and shown["ok"], shown
        status = shown["data"]["operation_status"]
        observed = status.get("extensions", {}).get("recovery_readback")
        result["fresh_status"] = status["status"]
        result["observed_outcome"] = (
            observed.get("outcome") if observed else "terminal-" + status["status"]
        )
        result["recovery_readback"] = observed
        if case_id.startswith("F5") and status["status"] in {"partial", "unknown"}:
            code, blocked = scene.call("unresolved-preview", [*args, "--dry-run"])
            assert code == 6 and blocked["error"]["code"] == "E_CONFLICT", blocked
            assert {n: sha(project / n) for n in original_primary} == project_bytes
            result["unresolved_write_blocked"] = True
        if (
            observed
            and observed.get("outcome") in {"applied", "not_applied", "partial"}
            and observed.get("cleanup", {}).get("status") == "attributable"
            and (not case_id.startswith("F5") or observed.get("outcome") == "partial")
        ):
            resolve = [
                "operation",
                "resolve",
                "--id",
                operation,
                "--outcome",
                observed["outcome"],
                "--evidence",
                observed["evidence_file"],
            ]
            code, value = scene.call("resolve", resolve)
            assert code == 0 and value["ok"], value
            archives = list((state / "recovery" / operation).glob("baseline-*"))
            assert {sha(p) for p in archives} == {r["baseline"] for r in rows}
            result["archives"] = {str(p): sha(p) for p in archives}
            code, value = scene.call("duplicate-resolve", resolve)
            assert code == 2 and value["error"]["code"] == "E_VALIDATION", value
            result["resolved"] = observed["outcome"]
            code, settled = scene.call("resolved-show", ["operation", "show", "--id", operation])
            assert code == 0 and settled["ok"], settled
            result["resolved_public_status"] = settled["data"]["operation_status"]["status"]
        elif not case_id.startswith("F5"):
            evidence = observed.get("evidence_file") if observed else str(checkpoint)
            code, value = scene.call(
                "inappropriate-applied-resolve",
                [
                    "operation",
                    "resolve",
                    "--id",
                    operation,
                    "--outcome",
                    "applied",
                    "--evidence",
                    evidence or str(output / "FROZEN.json"),
                ],
            )
            assert code == 2 and value["error"]["code"] == "E_VALIDATION", value
            result["resolved"] = False
            if case_id.startswith("F4"):
                assert observed and observed["outcome"] == "unknown", observed
        if not case_id.startswith("F5"):
            code, value = scene.call("duplicate-confirm", [*args, "--confirm", token])
            assert code == 6 and value["error"]["code"] == "E_CONFLICT", value
        else:
            result["original_confirmation_never_replayed"] = True
        code, value = scene.call("later-preview", [*args, "--dry-run"])
        if observed and observed.get("outcome") == "unknown":
            assert code == 6 and value["error"]["code"] == "E_CONFLICT", value
            result["unknown_blocked_no_replay"] = True
        else:
            assert code == 0 and value["ok"], value
        assert {n: sha(project / n) for n in original_primary} == project_bytes
        assert tree(scene.original) == scene.origin["files"]
        scene.freeze("final-files")
        result.update(
            status="passed" if result["hit"] else "uncovered",
            checks_passed=True,
            primary_files_unchanged_by_recovery=True,
            original_unchanged=True,
        )
        if case_id.startswith("F5"):
            from .f5 import read_facts, verdict

            result["f5_facts"] = read_facts(output, stage_hit=result["hit"], operation=operation)
            result["f5_acceptance"] = verdict(result["f5_facts"])
            result["status"] = result["f5_acceptance"]["status"]
            result["checks_passed"] = result["f5_acceptance"]["partial_recovery_passed"]
    except BaseException as exc:
        result.update(status="failed", failure=type(exc).__name__ + ": " + str(exc))
        if control is not None:
            try:
                if control.debug_pid is not None:
                    control.stop()
                control.close()
                result["controller_finally_closed"] = True
                put(output / "CONTROL-EXCEPTION.json", control.events)
            except BaseException as cleanup:
                result["controller_cleanup_error"] = str(cleanup)
        result["diagnostic_action"] = "Preserve this failure and inspect; no repeat of this case"
    result["finished_at"] = now()
    put(output / "RESULT.json", result)
    print(
        json.dumps(
            {
                "id": case_id,
                "status": result["status"],
                "hit": result["hit"],
                "failure": result.get("failure"),
            }
        ),
        flush=True,
    )
    return result
