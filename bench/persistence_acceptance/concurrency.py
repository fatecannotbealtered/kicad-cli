"""One bounded concurrency witness on disposable saved copies; never replay a fault."""

import datetime
import hashlib
import shutil
import sys
import time
from pathlib import Path

from .support import ASSETS, BINARY, ROOT, SETTINGS, Scene, now, put, read, record_sql, sha, tree

MATRIX = {
    "format": "bounded-concurrency.v1",
    "operation": "sch edit: C1 Value on two independent saved RC copies",
    "store": "shared state; fixed SDK locks normalized target identity, not all targets",
    "attempts": 1,
    "cases": [
        {"id": "running-query", "expected": "running while the observed lease is live"},
        {"id": "same-target", "expected": "E_CONFLICT/6 lock_held; B stays planned, token unused"},
        {
            "id": "disjoint-target",
            "expected": "C saves while A is held; A formal baseline unchanged",
        },
        {
            "id": "expired-lease-query",
            "expected": "unknown after observed lease expiry; no resolution",
        },
        {
            "id": "cleaning-query",
            "expected": "signed cleaning/all intended; public lease semantics",
        },
        {"id": "saved-query", "expected": "succeeded, completed/applied/saved receipt"},
        {"id": "repeat-confirm", "expected": "E_CONFLICT/6 token_used; files unchanged"},
        {"id": "scope-expired", "expected": "E_FORBIDDEN/4; no engineering change"},
        {"id": "parameter-drift", "expected": "E_CONFLICT/6 token_mismatch; no engineering change"},
        {"id": "file-drift", "expected": "E_CONFLICT/6 state_drift; retain external bytes"},
    ],
    "phase_evidence": (
        "signed operation journal, owned job/PID birth, per-file SHA, SQLite read-only lease"
    ),
    "safety": (
        "no old F4/F5 access; no token replay after uncertain outcome; "
        "one attempt; no product hooks"
    ),
}


def error(code, doc, expected, *, conflict=None):
    assert code == {"E_CONFLICT": 6, "E_FORBIDDEN": 4}[expected], doc
    assert not doc["ok"] and doc["error"]["code"] == expected, doc
    details = doc["error"]["details"]
    assert details["dispatch_state"] == "not_started" and details["next_action"]
    if conflict:
        assert details["conflict"] == conflict, doc


def only_value(before, after, expected):
    sys.path.insert(0, str(ASSETS["readers"]))
    from sexpr_reader import children, parse

    left, right = parse(before.decode("utf-8")), parse(after.decode("utf-8"))
    changes = []
    for a, b in zip(children(left, "symbol"), children(right, "symbol"), strict=True):
        props = {p[1]: p for p in children(a, "property")}
        if props.get("Reference", [None, None, None])[2] == "C1":
            q = next(p for p in children(b, "property") if p[1] == "Value")
            assert q[2] == expected
            changes.append({"ref": "C1", "before": props["Value"][2], "after": q[2]})
            q[2] = props["Value"][2]
    assert changes and left == right, "Independent syntax tree changed outside C1 Value"
    return changes


def saved_outcome(observed):
    assert observed["status"] == "succeeded", observed
    outcome = observed["receipt"]["outcome"]
    assert outcome["execution"] == "completed"
    assert outcome["change"] == "applied" and outcome["persistence"] == "saved"
    assert outcome["verification"]["status"] == "passed"
    return outcome


def cleaning_status(observed, lease, operation, target, revision):
    stamp = datetime.datetime.fromisoformat(
        observed["observed_at"].replace("Z", "+00:00")
    ).timestamp()
    expected = "running" if stamp < lease else "unknown"
    assert observed["status"] == expected
    assert observed["operation_id"] == operation and observed["target"] == target
    if expected == "unknown":
        extension = observed["extensions"]
        assert extension["writer_process"]["alive_or_unknown"] is True
        readback = extension["recovery_readback"]
        assert readback["completion"] == "completed" and readback["outcome"] == "applied"
        assert readback["operation_id"] == operation and readback["target"] == target
        assert readback["snapshot"]["revision"] == revision
        assert readback["verification"]["status"] == "passed"
        assert readback["cleanup"]["status"] == "attributable"
    return expected


def run():
    from .faults import signed
    from .win32_control import OwnedProcess

    scene = Scene(ROOT, "RC-ECO")
    state = ROOT / "state"
    second = ROOT / "project-c"
    shutil.copytree(scene.project, second)
    primary = scene.project / "rc4.kicad_sch"
    # Whitespace is frozen before preview, only to make the external pre-save window observable.
    with primary.open("ab") as stream:
        stream.write(b"\n" * (8 * 1024 * 1024))
    original = {"A": tree(scene.project), "C": tree(second)}
    raw_before = {"A": primary.read_bytes(), "C": (second / primary.name).read_bytes()}
    result = {"status": "failed", "binding": scene.binding, "started_at": now(), "cases": {}}
    put(
        ROOT / "FROZEN-CONCURRENCY.json",
        {
            "matrix": MATRIX,
            "files": original,
            "preparation": "8 MiB trailing newline on A before all previews",
            "script_sha256": sha(__file__),
        },
    )
    control = None
    plans, args, tokens = {}, {}, {}

    def call(name, argv):
        code, doc = scene.call(name, argv)
        return code, doc

    def query(name, operation):
        code, doc = call(name, ["operation", "show", "--id", operation])
        assert code == 0 and doc["ok"], doc
        return doc["data"]["operation_status"]

    def held_query(name, operation):
        before = record_sql(state, operation)["lease_until"]
        observed = query(name, operation)
        after = record_sql(state, operation)["lease_until"]
        put(
            ROOT / ("LEASE-" + name + ".json"),
            {
                "at": now(),
                "operation_id": operation,
                "lease_before_epoch": before,
                "lease_after_epoch": after,
                "query_observed_at": observed["observed_at"],
                "query_status": observed["status"],
                "query_stdout_sha256": sha(scene.calls / (name + ".stdout")),
                "writer_birth": control.debug_birth,
            },
        )
        assert before == after and control.debug_event is not None
        stamp = datetime.datetime.fromisoformat(
            observed["observed_at"].replace("Z", "+00:00")
        ).timestamp()
        assert observed["status"] == ("running" if stamp < before else "unknown")
        return observed

    def freeze_at(path, operation, phase, deadline):
        while control.running() and time.monotonic() < deadline:
            try:
                observed = signed(path, state)
                if observed and observed["hmac_valid"]:
                    value = observed["value"]
                    if value.get("operation_id") == operation and (
                        phase is None or value.get("phase") == phase
                    ):
                        pid = value["pid"] if phase is None else value["journal"]["pid"]
                        birth = control.identify(pid)["creation_filetime"]
                        put(
                            ROOT / ("LEASE-BEFORE-" + (phase or "apply") + ".json"),
                            {
                                "sampled_at": now(),
                                "operation_id": operation,
                                "lease_until_epoch": record_sql(state, operation)["lease_until"],
                                "writer": control.identify(pid),
                                "before_controlled_pause": True,
                            },
                        )
                        control.freeze(pid, BINARY, expected_birth=birth)
                        verified = signed(path, state)
                        if (
                            verified
                            and verified["hmac_valid"]
                            and (phase is None or verified["value"].get("phase") == phase)
                        ):
                            return birth, verified
                        control.resume(expected_birth=birth)
                        return None, None  # one bounded attempt, never chase a second hit
            except (OSError, ValueError, KeyError):
                if control.debug_pid is not None:
                    raise
            time.sleep(0.001)
        return None, None

    try:
        for name, target, value in (
            ("A", primary, "130nF"),
            ("B", primary, "140nF"),
            ("C", second / primary.name, "150nF"),
        ):
            changes = ROOT / ("changes-" + name + ".json")
            put(changes, {"changes": [{"op": "set", "ref": "C1", "value": value}]})
            args[name] = ["sch", "edit", "--schematic", str(target), "--changes", str(changes)]
            code, doc = call("preview-" + name, [*args[name], "--dry-run"])
            assert code == 0 and doc["data"]["preview"]["can_apply"], doc
            plans[name], tokens[name] = doc["data"]["preview"], doc["data"]["confirm_token"]
        assert tree(scene.project) == original["A"] and tree(second) == original["C"]
        operations = {k: v["operation_id"] for k, v in plans.items()}
        assert len(set(operations.values())) == 3
        assert plans["A"]["target"] == plans["B"]["target"]
        assert plans["C"]["target"] != plans["A"]["target"]
        a_files = {r["path"] for r in plans["A"]["extensions"]["files"]}
        c_files = {r["path"] for r in plans["C"]["extensions"]["files"]}
        assert a_files and c_files and a_files.isdisjoint(c_files)
        result["operation_ids"] = operations
        put(
            ROOT / "OPERATIONS.json",
            {
                "at": now(),
                "operations": operations,
                "public_arguments": args,
                "expected_final_values": {"A": "130nF", "B": "must not write", "C": "150nF"},
                "change_inputs": {
                    name: sha(ROOT / ("changes-" + name + ".json")) for name in plans
                },
                "targets": {k: v["target"] for k, v in plans.items()},
                "scope": read(ROOT / "scope.json"),
            },
        )
        control = OwnedProcess(
            [BINARY, *args["A"], "--confirm", tokens["A"], "--compact"],
            ROOT,
            scene.env,
            ROOT / "A.stdout",
            ROOT / "A.stderr",
            allow_control=SETTINGS["allow_owned_termination"],
        )
        journal = Path(str(primary) + ".kicad-cli.journal")
        birth, witness = freeze_at(journal, operations["A"], None, time.monotonic() + 120)
        if birth is None:
            raise RuntimeError("Pre-save stage not hit; no competing confirmations started")
        put(
            ROOT / "PRE-SAVE-WITNESS.json",
            {
                "at": now(),
                "signed": witness,
                "events": control.events,
                "files": tree(scene.project),
            },
        )
        assert all(sha(r["path"]) == r["baseline"] for r in plans["A"]["extensions"]["files"])
        result["cases"]["running-query"] = held_query("show-running-A", operations["A"])
        assert result["cases"]["running-query"]["status"] == "running"
        code, doc = call("confirm-B-once", [*args["B"], "--confirm", tokens["B"]])
        error(code, doc, "E_CONFLICT", conflict="lock_held")
        stored_b = record_sql(state, operations["B"])
        assert stored_b["status"] == "planned" and stored_b["consumed_tokens_count"] == 0
        result["cases"]["same-target"] = {"passed": True, "B_token_consumed": False}
        code, doc = call("confirm-C-once", [*args["C"], "--confirm", tokens["C"]])
        assert code == 0 and doc["ok"], doc
        saved_c = query("show-saved-C", operations["C"])
        saved_outcome(saved_c)
        assert saved_c["operation_id"] == operations["C"]
        assert saved_c["receipt"]["target"] == plans["C"]["target"]
        assert all(sha(r["path"]) == r["baseline"] for r in plans["A"]["extensions"]["files"])
        result["cases"]["disjoint-target"] = {
            "passed": True,
            "A_still_suspended": control.debug_pid is not None,
            "C_changes": only_value(raw_before["C"], (second / primary.name).read_bytes(), "150nF"),
        }
        lease = record_sql(state, operations["A"])["lease_until"]
        deadline = time.monotonic() + 45
        while time.time() <= lease + 0.1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert time.time() > lease and control.debug_pid is not None
        result["cases"]["expired-lease-query"] = held_query("show-expired-lease-A", operations["A"])
        assert result["cases"]["expired-lease-query"]["status"] == "unknown"
        # The process is still owned and alive: no resolution is attempted.
        control.resume(expected_birth=birth)
        checkpoint = (
            state
            / "document-transactions"
            / (hashlib.sha256(operations["A"].encode()).hexdigest() + ".json")
        )
        cleaning_birth, cleaning = freeze_at(
            checkpoint, operations["A"], "cleaning", time.monotonic() + 120
        )
        if cleaning_birth:
            put(
                ROOT / "CLEANING-WITNESS.json",
                {"at": now(), "signed": cleaning, "files": tree(scene.project)},
            )
            assert all(sha(r["path"]) == r["intended"] for r in plans["A"]["extensions"]["files"])
            observed = held_query("show-cleaning-A", operations["A"])
            lease_at_query = record_sql(state, operations["A"])["lease_until"]
            cleaning_status(
                observed, lease_at_query, operations["A"], plans["A"]["target"], sha(primary)
            )
            result["cases"]["cleaning-query"] = {"passed": True, "observation": observed}
            control.resume(expected_birth=cleaning_birth)
        else:
            result["cases"]["cleaning-query"] = {
                "status": "not_covered",
                "reason": "signed cleaning phase not held",
            }
        deadline = time.monotonic() + 120
        while control.running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not control.running(), "Owned A exceeded bound"
        if not control.wait_tree_empty(5):
            raise RuntimeError("Root exited but owned descendants remain; not natural completion")
        result["natural_exit_tree"] = control.tree_snapshot()
        result["A_exit"] = control.exit_code()
        if control.exit_code() >= 0xC0000000:
            raise RuntimeError("Spontaneous process exception; stop this cycle")
        assert control.exit_code() == 0 and read(ROOT / "A.stdout")["ok"]
        observed = query("show-saved-A", operations["A"])
        assert observed["status"] == "succeeded", observed
        saved_outcome(observed)
        result["cases"]["saved-query"] = {
            "passed": True,
            "observation": observed,
            "changes": only_value(raw_before["A"], primary.read_bytes(), "130nF"),
        }
        after = tree(scene.project)
        code, doc = call("repeat-A-refused", [*args["A"], "--confirm", tokens["A"]])
        error(code, doc, "E_CONFLICT", conflict="token_used")
        assert tree(scene.project) == after
        result["cases"]["repeat-confirm"] = {"passed": True}
        for label, baseline, directory in (
            ("A", original["A"], scene.project),
            ("C", original["C"], second),
        ):
            actual = tree(directory)
            assert set(actual) == set(baseline)
            assert all(actual[p] == digest for p, digest in baseline.items() if p != primary.name)
            result[label + "_protected_unchanged"] = True
        # Separate unconfirmed plans test drift/expiry. None is replayed after rejection.
        for kind in ("scope-expired", "parameter-drift", "file-drift"):
            code, doc = call("preview-" + kind, [*args["B"], "--dry-run"])
            assert code == 0 and doc["data"]["preview"]["can_apply"], doc
            token = doc["data"]["confirm_token"]
            command = list(args["B"])
            if kind == "scope-expired":
                scope = read(ROOT / "scope.json")
                scope["expires_at"] = (
                    (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1))
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                expired = ROOT / "expired-scope.json"
                put(expired, scope)
                scene.env["KICAD_CLI_SCOPE_FILE"] = str(expired)
            elif kind == "parameter-drift":
                command[-1] = str(ROOT / "changes-C.json")
            else:
                with primary.open("ab") as stream:
                    stream.write(b"\n")  # external drift stays, never overwritten
            frozen = tree(scene.project)
            code, rejected = call("confirm-" + kind + "-once", [*command, "--confirm", token])
            error(
                code,
                rejected,
                "E_FORBIDDEN" if kind == "scope-expired" else "E_CONFLICT",
                conflict={"parameter-drift": "token_mismatch", "file-drift": "state_drift"}.get(
                    kind
                ),
            )
            assert tree(scene.project) == frozen
            scene.env["KICAD_CLI_SCOPE_FILE"] = str(ROOT / "scope.json")
            result["cases"][kind] = {"passed": True, "files_unchanged": True}
        result["status"] = "passed" if cleaning_birth else "partial"
    except BaseException as exc:
        result["failure"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if control:
            try:
                # Resume an owned hold before cleanup, retaining any original failure.
                if control.debug_pid is not None and control.debug_event is not None:
                    control.resume(expected_birth=control.debug_birth)
                    deadline = time.monotonic() + 120
                    while control.running() and time.monotonic() < deadline:
                        time.sleep(0.01)
                if control.running():
                    result.update(status="failed", cleanup_required_controlled_termination=True)
                result["tree_finalization"] = control.close()
                if not result["tree_finalization"]["natural_tree_complete"]:
                    result["status"] = "failed"
            except BaseException as exc:
                result.update(status="failed", cleanup_failure=type(exc).__name__ + ": " + str(exc))
                try:
                    control.stop()
                    control.close()
                except BaseException as stop_error:
                    result["unresolved_control_cleanup"] = str(stop_error)
            result["control_events"] = control.events
        result.update(finished_at=now(), final_files={"A": tree(scene.project), "C": tree(second)})
        put(ROOT / "RESULT.json", result)
    return result
