"""One prospective R4 final-EXE interruption; no product imports or replay path.

All raw replies, tokens, scopes and state remain in the new owned private cycle.
The saved-board intent is lexical and independently frozen before the preview.
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "bench")]
from bench.native_observation import inspect_trace
from bench.persistence_acceptance.process_tree import observe
from bench.trial_tools.identity import capture, file_identity, verify_inventory

BASE = "59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f"
EXE_SHA = "7ded617e0a06efba811bd50b2ea5ab6c12ee0b1bd879121920d1cdb0f95ceb98"
R4_UUID = "6c5beb16-fbc7-5af2-918d-da26a4b5251c"
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def ident(path):
    return {"path": str(Path(path)), **file_identity(path)}


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def public_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate public JSON member")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Nonfinite public JSON constant: " + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def node_children(node, name):
    return [x for x in node[2] if isinstance(x, list) and x[2] and x[2][0] == name]


def one(node, name):
    rows = node_children(node, name)
    if len(rows) != 1:
        raise ValueError("Ambiguous/missing lexical node: " + name)
    return rows[0]


def lexical(raw):
    """Span parser used only by the independent method, never the product writer."""
    text = raw.decode("utf-8")
    stack, roots, consumed = [], [], 0
    for match in TOKENS.finditer(text):
        if text[consumed : match.start()].strip():
            raise ValueError("Unparsed board input")
        consumed = match.end()
        token = match[0]
        if token == "(":
            node = [match.start(), None, []]
            (stack[-1][2] if stack else roots).append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ValueError("Unbalanced board")
            stack.pop()[1] = match.end()
        else:
            if not stack:
                raise ValueError("Board atom outside expression")
            stack[-1][2].append(json.loads(token) if token.startswith('"') else token)
    if stack or len(roots) != 1 or text[consumed:].strip() or roots[0][2][0] != "kicad_pcb":
        raise ValueError("Invalid bounded board")
    refs = {}
    for fp in node_children(roots[0], "footprint"):
        names = [p[2][2] for p in node_children(fp, "property") if p[2][1] == "Reference"]
        if len(names) != 1 or names[0] in refs:
            raise ValueError("Ambiguous footprint reference")
        refs[names[0]] = fp
    fp = refs["R4"]
    if one(fp, "uuid")[2][1] != R4_UUID:
        raise ValueError("Wrong R4 identity")
    at = one(fp, "at")
    xy = [int(Decimal(v) * 1_000_000) for v in at[2][1:3]]
    masked = (text[: at[0]] + "(only-requested-R4-pose)" + text[at[1] :]).encode()
    return {
        "footprints": len(refs),
        "position_nm": xy,
        "at_span": at[:2],
        "protected_sha256": hashlib.sha256(masked).hexdigest(),
        "original_at": text[at[0] : at[1]],
    }


def inputs(root):
    root = Path(root).resolve(strict=True)
    caller_cache = root / "controller-host-cache"
    if (
        not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
        or not sys.pycache_prefix
        or Path(sys.pycache_prefix) != caller_cache
        or not caller_cache.is_dir()
        or list(caller_cache.iterdir())
    ):
        raise ValueError("Use declared -B -S and original empty Host pycache_prefix")
    cycle = root / "actual-02"
    ready = read(root / "preparation-02/READY.json")
    if ready.get("ready") is not True:
        raise ValueError("Corrected preparation is not READY; do not dispatch")
    env = read(root / "preparation-02/ENVIRONMENT.json")["environment"]
    env = {str(k): str(v) for k, v in env.items()}
    runtime_temp = cycle / "runtime-temp"
    runtime_temp.mkdir(exist_ok=True)
    exe = root / "baseline-package/candidate/kicad-cli.exe"
    if sha(exe) != EXE_SHA:
        raise ValueError("Wrong final executable")
    env.update(
        KICAD_CLI_STATE_DIR=str(cycle / "driver-state"),
        KICAD_CLI_SCOPE_FILE=str(cycle / "scope.json"),
        TEMP=str(runtime_temp),
        TMP=str(runtime_temp),
        KICAD_CLI_DRIVER_SDK_PATH="D:/personalSpace/kicad-contract-recovery-20261013/external-dependencies/_driver_sdk",
        KICAD10_FOOTPRINT_DIR=str(cycle / "project/fixture.pretty"),
    )
    return root, cycle, env, exe


def snapshot_project(project):
    return capture(project)


def verify_except_board(expected, board_sha=None):
    current = capture(expected["root"])
    if (
        set(current["files"]) != set(expected["files"])
        or current["directories"] != expected["directories"]
    ):
        raise ValueError("Engineering members/identities changed")
    for name, row in expected["files"].items():
        if name != "edit.kicad_pcb" and current["files"][name] != row:
            raise ValueError("Protected non-board engineering file changed: " + name)
    if board_sha and current["files"]["edit.kicad_pcb"]["sha256"] != board_sha:
        raise ValueError("Board does not match frozen candidate")
    return current


def stage_rows(state):
    db = Path(state) / "operations.sqlite3"
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {
            name: [dict(row) for row in connection.execute("SELECT * FROM " + name + " ORDER BY 1")]
            for name in ("operations", "consumed_tokens", "scope_usage")
        }


def receipt_witness(record, planned, envelope, boundary, expected_key, expected_revision):
    """Pure exact-operation/receipt predicate, tested without any product dispatch."""
    stable = (
        "operation_id",
        "plan_id",
        "capability_id",
        "command",
        "kind",
        "batch_id",
        "item_index",
        "item_key",
        "target_key",
        "request_digest",
        "scope_id",
        "effects",
        "plan",
        "created_at",
        "plan_expires_at",
    )
    if any(record[k] != planned[k] for k in stable):
        raise ValueError("Original operation, plan/build/scope/request identity drift")
    if (
        record["status"] != "succeeded"
        or not record["receipt"]
        or record["idempotency_key"] != expected_key
    ):
        raise ValueError("No original successful committed request")
    receipt, extra = json.loads(record["receipt"]), json.loads(record["extra"])
    frozen_plan = json.loads(planned["plan"])
    if (
        not boundary["instance_id"].startswith(str(boundary["pid"]) + ":")
        or extra["writer_pid"] != boundary["pid"]
    ):
        raise ValueError("Held process is not the original writer instance")
    if envelope.get("ok") is not True or receipt != envelope["data"]["receipt"]:
        raise ValueError("Held exact stdout receipt differs from committed original")
    for key in (
        "operation_id",
        "plan_id",
        "capability_id",
        "target",
        "authorization_scope_id",
        "effects",
    ):
        if receipt[key] != frozen_plan[key]:
            raise ValueError("Receipt identity differs from frozen public preview")
    if (
        receipt["request_id"] != expected_key
        or receipt["snapshot_before"] != frozen_plan["base_snapshot"]
    ):
        raise ValueError("Receipt request/base snapshot differs")
    if receipt["snapshot_after"]["revision"] != expected_revision:
        raise ValueError("Receipt saved revision differs")
    if any(
        receipt["outcome"][k] != v
        for k, v in {"execution": "completed", "change": "applied", "persistence": "saved"}.items()
    ):
        raise ValueError("Receipt is not a complete saved change")
    if (
        record["lease_until"] is not None
        or record["resolution"] is not None
        or record["finished_at"] is None
    ):
        raise ValueError("Operation is not cleanly settled")
    return receipt, extra


def ensure_scope(cycle):
    path = cycle / "scope.json"
    if path.exists():
        value = read(path)
        if value["scope_id"] != "owned-real-interruption-r4-20261020-02" or value["roots"] != [
            str(cycle)
        ]:
            raise ValueError("Existing scope is not this new owned cycle")
        return
    put(
        path,
        {
            "scope_id": "owned-real-interruption-r4-20261020-02",
            "tool": "kicad-cli",
            "granted_by": "Explicit human authorization for one controlled disposable interruption",
            "granted_at": now(),
            "expires_at": None,
            "effects": ["observe", "change", "persist"],
            "dangerous": False,
            "roots": [str(cycle)],
            "outputs": [],
            "notes": "One R4 translation only. One confirm. "
            "No retry, old state, resolve or replay.",
        },
    )


def discover(root):
    root, cycle, env, exe = inputs(root)
    if read(cycle / "ACCESS-PREPARATION.json")["restricted_owned_cycle"] is not True:
        raise ValueError("New owned cycle access preparation missing")
    ensure_scope(cycle)
    results = {}
    for name, command in (
        ("move-reference", "board footprint move"),
        ("query-reference", "operation show"),
    ):
        output, trace = run_observed(
            cycle, name, [str(exe), "reference", "--command", command, "--compact"], env
        )
        if read(output / "stdout").get("ok") is not True:
            raise ValueError("Actual public command discovery failed")
        results[command] = ident(output / "stdout")
    put(
        cycle / "DISCOVERY-FINISH.json",
        {"at": now(), "references": results, "engineering": capture(cycle / "project")},
    )
    return {"stage": "public-discovery-complete", "references": results}


def run_observed(cycle, name, argv, env, timeout=180):
    output = cycle / "calls" / name
    output.parent.mkdir(parents=True, exist_ok=True)
    trace = observe(argv, cycle, env, output, timeout=timeout)
    classification = inspect_trace(output, allowed_exit_codes={0})
    put(output / "CLASSIFICATION.json", classification)
    if not classification.get("may_validate_functional_output"):
        raise RuntimeError("Native/public call observation failed; preserve and stop")
    if not trace["job_finalization"]["natural_tree_complete"]:
        raise RuntimeError("Incomplete Job")
    return output, trace


def guards(plan):
    for item in plan["guards"]:
        verify_inventory(item)


def freeze(root):
    root, cycle, env, exe = inputs(root)
    board = cycle / "project/edit.kicad_pcb"
    if sha(board) != BASE or (cycle / "FROZEN-PLAN.json").exists():
        raise ValueError("Expected unchanged original and never-used formal cycle")
    if (cycle / "driver-state").exists() and any(stage_rows(cycle / "driver-state").values()):
        raise ValueError("Discovery state already contains operations/consumption/usage")
    if not (cycle / "DISCOVERY-FINISH.json").exists():
        raise ValueError("Actual public command contracts were not read")
    pose = lexical(board.read_bytes())
    if pose["position_nm"] != [100000000, 110000000]:
        raise ValueError("Unexpected original R4 position")
    text = board.read_text(encoding="utf-8")
    start, end = pose["at_span"]
    # In-memory independent expectation, never a private PCB writer.
    expected_raw = (text[:start] + "(at 99.5 110)" + text[end:]).encode()
    expected_sha = hashlib.sha256(expected_raw).hexdigest()
    if expected_sha != "06d9017e89e9e86533041df9d554289435df6edbe8852569d350acfeddd4176c":
        raise ValueError("Independent frozen expectation differs from retained fixture intent")
    ensure_scope(cycle)
    scope_validation = read(cycle / "SCOPE-VALIDATION.json")
    if scope_validation["valid"] is not True or scope_validation["scope_sha256"] != sha(
        cycle / "scope.json"
    ):
        raise ValueError("Exact current scope has not passed fixed public Schema validation")
    qualification = read(root / "controller-qualification/QUALIFICATION.json")
    if not qualification["qualified"] or qualification["controller_sha256"] != sha(
        REPO / "bench/interruption_acceptance/controller.py"
    ):
        raise ValueError("Control method changed since harmless qualification")
    witness_qualification = read(root / "actual-witness-qualification-04/RESULT.json")
    if witness_qualification["status"] != "PASS" or witness_qualification["source_sha256"] != sha(
        __file__
    ):
        raise ValueError("Actual stage witness changed since harmless qualification")
    ready = read(root / "preparation-02/READY.json")
    protected = [Path(ready["profile_root"])]
    fixed = [
        exe,
        Path(__file__),
        REPO / "bench/interruption_acceptance/controller.py",
        REPO / "bench/saved_scene_native_read.py",
        REPO / "trial_tools/identity.py",
        root / "preparation-02/READY.json",
        root / "preparation-02/ENVIRONMENT.json",
        cycle / "scope.json",
    ]
    fixed.extend(
        [
            root / "controller-qualification/QUALIFICATION.json",
            root / "actual-witness-qualification-04/PLAN.json",
            root / "actual-witness-qualification-04/RESULT.json",
            root / "PUBLIC-QUERY-GUIDE.md",
            cycle / "ACCESS-PREPARATION.json",
            cycle / "SCOPE-VALIDATION.json",
        ]
    )
    fixed.append(root / "controller-host-cache")
    sdk = Path(env["KICAD_CLI_DRIVER_SDK_PATH"])
    native_bin = Path(env["KICAD_CLI_ROOT"]) / "bin"
    needed = [native_bin / "python.exe", native_bin / "kicad-cli.exe"]
    needed.extend(sorted(native_bin.glob("*.dll")))
    native_warm = read(root / "preparation-02/independent-native-read/stdout")
    for module in native_warm["startup"]["actual_module_sources"]:
        for key in ("path", "provider"):
            if key in module:
                needed.append(Path(module[key]))
    preparation_dependencies = read(root / "preparation-02/DEPENDENCIES-AFTER.json")
    needed.extend(Path(p) for p in preparation_dependencies["method_files"])
    system = Path(os.environ["SystemRoot"]) / "System32"
    needed.extend(
        system / name
        for name in ("KernelBase.dll", "kernel32.dll", "ntdll.dll", "cmd.exe", "conhost.exe")
    )
    host_bin = Path(sys.executable).parent
    needed.extend(
        p
        for p in (
            host_bin / "python312.dll",
            host_bin / "DLLs/_ctypes.pyd",
            host_bin / "DLLs/_sqlite3.pyd",
            host_bin / "DLLs/sqlite3.dll",
        )
        if p.is_file()
    )
    guard_paths = list(
        dict.fromkeys([*protected, *fixed, sdk, sdk.parent / "driver_spec", *needed])
    )
    plan = {
        "schema": "single-real-R4-process-interruption-plan/v1",
        "frozen_at": now(),
        "baseline": "b67eb6e65037d1c0af2bf142777e3dac1534ca6b",
        "product_source": "b045b2a48626f53fa5523b6c12bb940e7307b17b",
        "EXE_sha256": EXE_SHA,
        "controller_sha256": sha(fixed[2]),
        "controller_qualification": ident(root / "controller-qualification/QUALIFICATION.json"),
        "original_project": capture(cycle / "project"),
        "request": {
            "reference": "R4",
            "uuid": R4_UUID,
            "from_nm": [100000000, 110000000],
            "to_nm": [99500000, 110000000],
            "moves": "R4:99.5,110",
            "idempotency_key": "real-interruption-r4-once-20261020",
        },
        "expected_board_sha256": expected_sha,
        "protected_lexical_sha256": pose["protected_sha256"],
        "environment": env,
        "guards": [capture(p) for p in guard_paths],
        "mutable_outputs": [
            str(cycle / name) for name in ("driver-state", "calls", "interruption", "runtime-temp")
        ],
        "confirm_dispatch_budget": 1,
        "preview_dispatch_budget": 1,
        "automatic_retry": False,
        "temporary_environment_predeclared_relocation": {
            "from_preparation": read(root / "preparation-02/ENVIRONMENT.json")["environment"][
                "TEMP"
            ],
            "actual_runtime_temp": str(cycle / "runtime-temp"),
            "reason": "Owned mutable PyInstaller extraction/temp separate from the fully frozen "
            "configuration profile; no loaded-byte authentication claim.",
        },
        "native_source_scope": "Direct KiCad bin DLLs, exact tools, warm-observed independent "
        "reader Python sources/providers, observer methods, fixed SDK/spec, finite system/Host "
        "providers. Preparation's broader6695-file snapshot is not loaded-code coverage.",
        "phase_target": "saved board and independently observed committed succeeded receipt, "
        "before first complete success stdout WriteFile; actual process/whole-Job termination",
        "stage_requirements": [
            "owned debug event held before actual stdout kernel write",
            "exact stdout kernel object",
            "zero prior caller bytes",
            "original operation ID",
            "succeeded committed row/receipt with writer_pid same as breakpoint",
            "saved exact candidate/requested bytes",
            "all protected/config/method/input guards unchanged",
            "whole Job attribution before termination and complete EXIT/signaled/active0 afterward",
        ],
        "after_attempt": "No writes. Independent new public CLI original-ID query on same state. "
        "If succeeded, inspect saved board and stop; never confirm again or resolve it.",
        "DRC_reuse": "Official candidate report may cover exact saved bytes only while original "
        "rules/libraries/config/tools and candidate manifest remain bound; "
        "no generic all-board or power-loss claim.",
        "not_covered": [
            "before-save interruption",
            "mid-save interruption",
            "unknown outcome resolve",
            "power loss/fsync",
            "all fault stages",
            "manufacturing",
            "F4/source recovery01",
        ],
    }
    put(cycle / "FROZEN-PLAN.json", plan)
    put(
        cycle / "INTENT.json",
        {
            key: plan[key]
            for key in (
                "request",
                "expected_board_sha256",
                "protected_lexical_sha256",
                "DRC_reuse",
                "not_covered",
            )
        },
    )
    return {"stage": "frozen", "plan": ident(cycle / "FROZEN-PLAN.json")}


def current(root):
    root, cycle, env, exe = inputs(root)
    plan = read(cycle / "FROZEN-PLAN.json")
    if env != plan["environment"] or sha(exe) != plan["EXE_sha256"]:
        raise ValueError("Frozen environment/artifact drift")
    guards(plan)
    return root, cycle, env, exe, plan


def baseline(root):
    root, cycle, env, exe, plan = current(root)
    verify_inventory(plan["original_project"])
    command = [
        env["KICAD_CLI_PYTHON"],
        "-B",
        "-S",
        "-X",
        "pycache_prefix=" + env["PYTHONPYCACHEPREFIX"],
        str(REPO / "bench/saved_scene_native_read.py"),
        str(cycle / "project/edit.kicad_pcb"),
    ]
    output, trace = run_observed(cycle, "baseline-native", command, env)
    value = read(output / "stdout")
    if value["input_sha256"] != BASE or value["native_version"].split()[0] != "10.0.6":
        raise ValueError("Wrong baseline Native read")
    verify_inventory(plan["original_project"])
    guards(plan)
    put(
        cycle / "BASELINE-FINISH.json",
        {
            "at": now(),
            "plan": ident(cycle / "FROZEN-PLAN.json"),
            "native": ident(output / "stdout"),
            "Job": trace["job_finalization"],
        },
    )
    return {"stage": "baseline-complete", "native": ident(output / "stdout")}


def preview(root):
    root, cycle, env, exe, plan = current(root)
    if not (cycle / "BASELINE-FINISH.json").exists():
        raise ValueError("Formal baseline reader not complete")
    verify_inventory(plan["original_project"])
    argv = [
        str(exe),
        "board",
        "footprint",
        "move",
        "--board",
        str(cycle / "project/edit.kicad_pcb"),
        "--moves",
        plan["request"]["moves"],
        "--dry-run",
        "--compact",
    ]
    output, trace = run_observed(cycle, "preview", argv, env, 300)
    response = read(output / "stdout")
    if response.get("ok") is not True:
        raise ValueError("Public preview refused; do not confirm")
    data = response["data"]
    proposed = data["preview"]
    pf = proposed["extensions"]["preflight"]
    if (
        pf["status"] != "passed"
        or proposed["can_apply"] is not True
        or pf["candidate_sha256"] != plan["expected_board_sha256"]
    ):
        raise ValueError("Actual preview differs from predeclared finite positive")
    verify_inventory(plan["original_project"])
    guards(plan)
    rows = stage_rows(cycle / "driver-state")
    matches = [r for r in rows["operations"] if r["operation_id"] == proposed["operation_id"]]
    if (
        len(matches) != 1
        or matches[0]["status"] != "planned"
        or rows["consumed_tokens"]
        or rows["scope_usage"]
    ):
        raise ValueError("Preview state is not untouched original plan")
    put(
        cycle / "PREVIEW-FINISH.json",
        {
            "at": now(),
            "operation_id": proposed["operation_id"],
            "expires_at": proposed["expires_at"],
            "reply": ident(output / "stdout"),
            "candidate_sha256": pf["candidate_sha256"],
            "preflight": pf,
            "Job": trace["job_finalization"],
        },
    )
    # This is the test's human/agent review gate, never a claim that full images were viewed.
    return {
        "stage": "preview-review-required",
        "operation_id": proposed["operation_id"],
        "reply_path": str(output / "stdout"),
        "expires_at": proposed["expires_at"],
    }


def interrupt(root, ack):
    root, cycle, env, exe, plan = current(root)
    from bench.interruption_acceptance.controller import run

    preview_finish = read(cycle / "PREVIEW-FINISH.json")
    reply_path = Path(preview_finish["reply"]["path"])
    if ack != sha(reply_path) or ident(reply_path) != preview_finish["reply"]:
        raise ValueError("Exact public preview was not acknowledged")
    data = read(reply_path)["data"]
    if datetime.fromisoformat(data["preview"]["expires_at"].replace("Z", "+00:00")) <= datetime.now(
        timezone.utc
    ):
        raise ValueError(
            "Original preview TTL expired; no confirm dispatched and no replacement preview"
        )
    original_id = data["preview"]["operation_id"]
    verify_inventory(plan["original_project"])
    workspace = Path(data["preview"]["extensions"]["preflight"]["workspace"])
    confirm = {
        "at": now(),
        "original_plan": ident(cycle / "FROZEN-PLAN.json"),
        "preview": ident(reply_path),
        "operation_id": original_id,
        "preflight_workspace": capture(workspace),
        "planned_rows": stage_rows(cycle / "driver-state"),
        "database_identity": file_identity(cycle / "driver-state/operations.sqlite3"),
        "dispatch_budget": 1,
        "stage_target": plan["phase_target"],
    }
    put(cycle / "CONFIRM-BINDING.json", confirm)
    put(
        cycle / "ATTEMPT-START.json",
        {
            "at": now(),
            "operation_id": original_id,
            "same_state": str(cycle / "driver-state"),
            "real_confirm_budget": 1,
        },
    )
    captured = {}

    def predicate(raw):
        try:
            value = public_json(raw)
        except (ValueError, UnicodeError):
            return False
        if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
            return False
        receipt = value.get("data", {}).get("receipt", {})
        if not isinstance(receipt, dict):
            return False
        if value.get("ok") is True and receipt.get("operation_id") == original_id:
            captured["response"] = value
            captured["buffer_sha256"] = hashlib.sha256(raw).hexdigest()
            return True
        return False

    def witness(meta):
        guards(plan)
        verify_inventory(confirm["preflight_workspace"])
        engineering = verify_except_board(plan["original_project"], plan["expected_board_sha256"])
        observed = lexical((cycle / "project/edit.kicad_pcb").read_bytes())
        if (
            observed["position_nm"] != plan["request"]["to_nm"]
            or observed["protected_sha256"] != plan["protected_lexical_sha256"]
        ):
            raise ValueError("Requested geometry/protection not independently observed")
        db_now = file_identity(cycle / "driver-state/operations.sqlite3")
        if any(db_now[k] != confirm["database_identity"][k] for k in ("dev", "file_id")):
            raise ValueError("Original state database identity replaced")
        rows = stage_rows(cycle / "driver-state")
        matches = [r for r in rows["operations"] if r["operation_id"] == original_id]
        if len(matches) != 1 or matches[0]["status"] != "succeeded" or not matches[0]["receipt"]:
            raise ValueError("No committed original succeeded receipt")
        record = matches[0]
        planned = next(
            r for r in confirm["planned_rows"]["operations"] if r["operation_id"] == original_id
        )
        receipt, extra = receipt_witness(
            record,
            planned,
            captured["response"],
            meta,
            plan["request"]["idempotency_key"],
            plan["expected_board_sha256"],
        )
        if (
            captured["buffer_sha256"] != meta["buffer_sha256"]
            or meta["stdout_prior_bytes"] != 0
            or meta["debug_event_held"] is not True
        ):
            raise ValueError("Caller/pre-WriteFile boundary incomplete")
        if not meta["stdout_kernel_object_verified"]:
            raise ValueError("Numeric handle alone is not stdout identity proof")
        private = cycle / "interruption/private/prekill-stage.json"
        put(
            private,
            {
                "at": now(),
                "operation_id": original_id,
                "all_rows": rows,
                "engineering": engineering,
                "native_effect": observed,
                "boundary": meta,
                "scope": ident(cycle / "scope.json"),
            },
        )
        return {
            "qualified": True,
            "operation_id": original_id,
            "status_before_kill": record["status"],
            "writer_pid": extra["writer_pid"],
            "saved_board_sha256": sha(cycle / "project/edit.kicad_pcb"),
            "receipt_sha256": hashlib.sha256(canonical(receipt)).hexdigest(),
            "witness": ident(private),
            "no_power_loss_or_fsync_claim": True,
        }

    argv = [
        str(exe),
        "board",
        "footprint",
        "move",
        "--board",
        str(cycle / "project/edit.kicad_pcb"),
        "--moves",
        plan["request"]["moves"],
        "--confirm",
        data["confirm_token"],
        "--idempotency-key",
        plan["request"]["idempotency_key"],
        "--compact",
    ]
    result = run(
        argv,
        cycle,
        env,
        cycle / "interruption",
        predicate=predicate,
        stage_witness=witness,
        timeout=180,
        allow_owned_termination=True,
    )
    # Stop all writes regardless of hit/miss. Public query is a separate new process.
    put(
        cycle / "POST-ATTEMPT.json",
        {
            "at": now(),
            "operation_id": original_id,
            "result": ident(cycle / "interruption/result.json"),
            "matched": result.get("matched"),
            "engineering": capture(cycle / "project"),
            "rows": stage_rows(cycle / "driver-state"),
            "no_more_writes": True,
        },
    )
    put(
        cycle / "QUERY-REQUEST.json",
        {
            "operation_id": original_id,
            "state": str(cycle / "driver-state"),
            "scope_file": str(cycle / "scope.json"),
            "exe": str(exe),
            "EXE_sha256": EXE_SHA,
            "environment_file": str(cycle / "PUBLIC-QUERY-ENVIRONMENT.json"),
            "intent": str(cycle / "INTENT.json"),
            "before_project_manifest": str(cycle / "FROZEN-PLAN.json"),
            "recovery_document": str(REPO / "MOVE_REPLY_RECOVERY.md"),
            "allowed_next_action": "Public original-ID query and saved-board read only; "
            "no confirm/preview/resolve/replay or new state.",
        },
    )
    put(cycle / "PUBLIC-QUERY-ENVIRONMENT.json", {"environment": env})
    return {
        "stage": "attempt-stopped-no-more-writes",
        "operation_id": original_id,
        "matched": result.get("matched"),
        "qualified_interruption": result.get("qualified_interruption"),
        "result": str(cycle / "interruption/result.json"),
    }


def public_query(root, identifier):
    root, cycle, env, exe, plan = current(root)
    request = read(cycle / "QUERY-REQUEST.json")
    if identifier != request["operation_id"]:
        raise ValueError("Only exact original ID is permitted")
    before = capture(cycle / "project")
    output, trace = run_observed(
        cycle,
        "independent-public-query",
        [str(exe), "operation", "show", "--id", identifier, "--compact"],
        env,
    )
    verify_inventory(before)
    guards(plan)
    return {
        "reply": str(output / "stdout"),
        "classification": str(output / "CLASSIFICATION.json"),
        "trace": str(output / "TRACE.json"),
        "original_operation_id": identifier,
    }


def read_saved(root):
    root, cycle, env, exe, plan = current(root)
    before = capture(cycle / "project")
    command = [
        env["KICAD_CLI_PYTHON"],
        "-B",
        "-S",
        "-X",
        "pycache_prefix=" + env["PYTHONPYCACHEPREFIX"],
        str(REPO / "bench/saved_scene_native_read.py"),
        str(cycle / "project/edit.kicad_pcb"),
    ]
    output, trace = run_observed(cycle, "independent-saved-native", command, env)
    verify_inventory(before)
    guards(plan)
    return {
        "native_readback": str(output / "stdout"),
        "classification": str(output / "CLASSIFICATION.json"),
        "trace": str(output / "TRACE.json"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=("discover", "freeze", "baseline", "preview", "interrupt", "query", "read-saved"),
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--ack-preview-sha256")
    parser.add_argument("--operation-id")
    args = parser.parse_args()
    if args.phase == "interrupt":
        value = interrupt(args.root, args.ack_preview_sha256)
    elif args.phase == "query":
        value = public_query(args.root, args.operation_id)
    elif args.phase == "read-saved":
        value = read_saved(args.root)
    else:
        value = {"discover": discover, "freeze": freeze, "baseline": baseline, "preview": preview}[
            args.phase
        ](args.root)
    print(json.dumps({"ok": True, "data": value}, ensure_ascii=False))
