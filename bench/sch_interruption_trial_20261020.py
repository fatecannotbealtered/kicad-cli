"""Single public SCH field-write interruption, prospectively separate from move refusals."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ruff: noqa: E402
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "bench")]
from bench import interruption_trial_20261020 as base
from bench.interruption_acceptance.controller import run
from bench.trial_tools.identity import capture, file_identity, verify_inventory

NAME = "actual-sch-01"
ORIGINAL = Path("D:/personalSpace/kicad-move-safety-20261016/frozen-engineering-input-01")
SYMBOL = "c806d962-a396-5331-9be1-d73cc9a1d527"
KEY = "real-sch-interruption-r4-field-once-20261020"


def field(raw):
    """Independent token/span reader: one exact property token, no product parser."""
    text = raw.decode("utf-8")
    stack, roots, consumed = [], [], 0
    for match in base.TOKENS.finditer(text):
        if text[consumed : match.start()].strip():
            raise ValueError("Unparsed schematic text")
        consumed = match.end()
        token = match[0]
        if token == "(":
            node = [match.start(), None, []]
            (stack[-1][2] if stack else roots).append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ValueError("Unbalanced schematic")
            stack.pop()[1] = match.end()
        else:
            if not stack:
                raise ValueError("Atom outside schematic")
            stack[-1][2].append(json.loads(token) if token.startswith('"') else token)
    if stack or len(roots) != 1 or roots[0][2][0] != "kicad_sch" or text[consumed:].strip():
        raise ValueError("Unsupported schematic")
    matches = []
    for symbol in base.node_children(roots[0], "symbol"):
        props = base.node_children(symbol, "property")
        if any(p[2][1:3] == ["Reference", "R4"] for p in props):
            matches.append((symbol, props))
    if len(matches) != 1 or base.one(matches[0][0], "uuid")[2][1] != SYMBOL:
        raise ValueError("Ambiguous or wrong R4 symbol identity")
    values = [p for p in matches[0][1] if p[2][1] == "Value"]
    if len(values) != 1:
        raise ValueError("Ambiguous Value property")
    value = values[0]
    tokens = list(base.TOKENS.finditer(text, value[0], value[1]))
    start, end = tokens[3].span()
    masked = (text[:start] + '"only-R4-Value"' + text[end:]).encode()
    return {
        "uuid": SYMBOL,
        "value": value[2][2],
        "span": [start, end],
        "protected_sha256": hashlib.sha256(masked).hexdigest(),
    }


def inputs(root):
    root = Path(root).resolve(strict=True)
    cache = root / "controller-host-cache"
    if (
        not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
        or Path(sys.pycache_prefix or "") != cache
        or list(cache.iterdir())
    ):
        raise ValueError("Declared -B -S and original empty cache required")
    cycle = root / NAME
    env = dict(base.read(root / "preparation-02/ENVIRONMENT.json")["environment"])
    env.update(
        KICAD_CLI_STATE_DIR=str(cycle / "driver-state"),
        KICAD_CLI_SCOPE_FILE=str(cycle / "scope.json"),
        KICAD10_FOOTPRINT_DIR=str(cycle / "project/fixture.pretty"),
        TEMP=str(cycle / "runtime-temp"),
        TMP=str(cycle / "runtime-temp"),
    )
    exe = root / "baseline-package/candidate/kicad-cli.exe"
    if (
        base.sha(exe) != base.EXE_SHA
        or base.read(root / "preparation-02/READY.json")["ready"] is not True
    ):
        raise ValueError("Wrong artifact or preparation")
    return root, cycle, env, exe


def discover(root):
    root, cycle, env, exe = inputs(root)
    output, trace = base.run_observed(
        cycle, "sch-reference", [str(exe), "reference", "--command", "sch edit", "--compact"], env
    )
    if base.read(output / "stdout").get("ok") is not True:
        raise ValueError("Public reference missing")
    base.put(
        cycle / "DISCOVERY-FINISH.json",
        {
            "at": base.now(),
            "reply": base.ident(output / "stdout"),
            "Job": trace["job_finalization"],
        },
    )
    return {"phase": "public-reference", "reply": str(output / "stdout")}


def freeze(root):
    root, cycle, env, exe = inputs(root)
    before = capture(cycle / "project")
    old = capture(ORIGINAL)
    if {k: v["sha256"] for k, v in before["files"].items()} != {
        k: v["sha256"] for k, v in old["files"].items()
    }:
        raise ValueError("Not a fresh exact original engineering copy")
    raw = (cycle / "project/edit.kicad_sch").read_bytes()
    parsed = field(raw)
    if parsed["value"] != "1k":
        raise ValueError("Wrong original R4 Value")
    a, b = parsed["span"]
    text = raw.decode()
    expected = (text[:a] + '"1.1k"' + text[b:]).encode()
    if (
        hashlib.sha256(expected).hexdigest()
        != "647d11b114aa615a39e883b2c47630bfc454337dcf2d7096312b4ebd596283f9"
    ):
        raise ValueError("Independent frozen exact edit differs")
    qualified = base.read(root / "sch-witness-qualification-02/RESULT.json")
    if qualified["status"] != "PASS" or qualified["method_sha256"] != base.sha(__file__):
        raise ValueError("Field checker not harmless-qualified")
    access = base.read(cycle / "ACCESS-PREPARATION.json")
    scope = base.read(cycle / "SCOPE-VALIDATION.json")
    if (
        not access["restricted_owned_cycle"]
        or not scope["valid"]
        or scope["scope_sha256"] != base.sha(cycle / "scope.json")
    ):
        raise ValueError("Preparation or validated exact scope missing")
    controller = base.read(root / "controller-qualification/QUALIFICATION.json")
    if not controller["qualified"] or controller["controller_sha256"] != base.sha(
        REPO / "bench/interruption_acceptance/controller.py"
    ):
        raise ValueError("Controller changed")
    prior = base.read(root / "actual-02/FROZEN-PLAN.json")
    guards = [
        item
        for item in prior["guards"]
        if "actual-02" not in Path(item.get("root", item.get("path"))).parts
    ]
    # Current-chain inputs only; the earlier move refusal is evidence, never reused state.
    fixed = [
        Path(__file__),
        Path(base.__file__),
        root / "PUBLIC-SCH-QUERY-GUIDE.md",
        root / "sch-witness-qualification-02/PLAN.json",
        root / "sch-witness-qualification-02/RESULT.json",
        cycle / "scope.json",
        cycle / "SCOPE-VALIDATION.json",
        cycle / "ACCESS-PREPARATION.json",
        cycle / "changes.json",
        cycle / "DISCOVERY-FINISH.json",
    ]
    guards.extend(capture(p) for p in fixed)
    plan = {
        "schema": "single-real-public-sch-field-interruption/v1",
        "at": base.now(),
        "baseline": "b67eb6e65037d1c0af2bf142777e3dac1534ca6b",
        "product_source": "b045b2a48626f53fa5523b6c12bb940e7307b17b",
        "EXE_sha256": base.EXE_SHA,
        "environment": env,
        "guards": guards,
        "original_project": before,
        "request": {
            "op": "set",
            "ref": "R4",
            "uuid": SYMBOL,
            "old_value": "1k",
            "value": "1.1k",
            "idempotency_key": KEY,
        },
        "expected_saved_sha256": hashlib.sha256(expected).hexdigest(),
        "protected_lexical_sha256": parsed["protected_sha256"],
        "allowed_engineering_change": "Only exact R4 Value token in root SCH; PCB and seven "
        "other files byte/identity protected. No sync or new electrical intent qualification.",
        "phase_target": prior["phase_target"],
        "stage_requirements": prior["stage_requirements"],
        "confirm_budget": 1,
        "preview_budget": 1,
        "automatic_retry": False,
        "controller_qualification": base.ident(
            root / "controller-qualification/QUALIFICATION.json"
        ),
        "prior_move_refusal": base.ident(root / "actual-02/REFUSAL-FINISH.json"),
        "not_covered": [
            "move interruption",
            "before/mid-save",
            "unknown resolve",
            "power loss/fsync",
            "all fault stages",
            "manufacturing",
            "F4/source recovery01",
        ],
        "after_attempt": "Original state/original ID public query and readback only; "
        "succeeded needs no resolve or replay.",
    }
    base.guards(plan)
    base.put(cycle / "FROZEN-PLAN.json", plan)
    base.put(
        cycle / "INTENT.json",
        {
            k: plan[k]
            for k in (
                "request",
                "expected_saved_sha256",
                "protected_lexical_sha256",
                "allowed_engineering_change",
                "not_covered",
            )
        },
    )
    return {"phase": "frozen", "plan": base.ident(cycle / "FROZEN-PLAN.json")}


def current(root):
    root, cycle, env, exe = inputs(root)
    plan = base.read(cycle / "FROZEN-PLAN.json")
    if env != plan["environment"]:
        raise ValueError("Environment drift")
    base.guards(plan)
    return root, cycle, env, exe, plan


def verify_saved(plan, *, expected=True):
    result = capture(plan["original_project"]["root"])
    before = plan["original_project"]
    if (
        set(result["files"]) != set(before["files"])
        or result["directories"] != before["directories"]
    ):
        raise ValueError("Engineering membership/directory identity changed")
    for name, row in before["files"].items():
        if name != "edit.kicad_sch" and result["files"][name] != row:
            raise ValueError("Protected engineering file changed: " + name)
    if expected:
        if result["files"]["edit.kicad_sch"]["sha256"] != plan["expected_saved_sha256"]:
            raise ValueError("Saved bytes differ from independent exact expected edit")
        found = field((Path(before["root"]) / "edit.kicad_sch").read_bytes())
        if (
            found["value"] != "1.1k"
            or found["protected_sha256"] != plan["protected_lexical_sha256"]
        ):
            raise ValueError("Wrong field or protected SCH change")
    return result


def preview(root):
    root, cycle, env, exe, plan = current(root)
    verify_inventory(plan["original_project"])
    argv = [
        str(exe),
        "sch",
        "edit",
        "--schematic",
        str(cycle / "project/edit.kicad_sch"),
        "--changes",
        str(cycle / "changes.json"),
        "--dry-run",
        "--compact",
    ]
    output, trace = base.run_observed(cycle, "preview", argv, env)
    reply = base.read(output / "stdout")
    if not reply.get("ok") or not reply["data"]["preview"]["can_apply"]:
        raise ValueError("Preview refused")
    data = reply["data"]
    files = data["preview"]["extensions"]["files"]
    if (
        len(files) != 1
        or files[0]["path"] != str(cycle / "project/edit.kicad_sch")
        or files[0]["intended"] != plan["expected_saved_sha256"]
    ):
        raise ValueError("Preview write set differs from frozen field intent")
    verify_inventory(plan["original_project"])
    base.guards(plan)
    base.put(
        cycle / "PREVIEW-FINISH.json",
        {
            "at": base.now(),
            "reply": base.ident(output / "stdout"),
            "operation_id": data["preview"]["operation_id"],
            "Job": trace["job_finalization"],
        },
    )
    return {
        "phase": "review-required",
        "reply": str(output / "stdout"),
        "operation_id": data["preview"]["operation_id"],
        "expires_at": data["preview"]["expires_at"],
    }


def interrupt(root, ack):
    root, cycle, env, exe, plan = current(root)
    finish = base.read(cycle / "PREVIEW-FINISH.json")
    reply_path = Path(finish["reply"]["path"])
    if ack != base.sha(reply_path) or base.ident(reply_path) != finish["reply"]:
        raise ValueError("Exact preview acknowledgement missing")
    data = base.read(reply_path)["data"]
    if datetime.fromisoformat(data["preview"]["expires_at"].replace("Z", "+00:00")) <= datetime.now(
        timezone.utc
    ):
        raise ValueError("Original TTL expired; no retry or replacement preview")
    identifier = data["preview"]["operation_id"]
    verify_inventory(plan["original_project"])
    rows = base.stage_rows(cycle / "driver-state")
    planned = next(r for r in rows["operations"] if r["operation_id"] == identifier)
    if (
        planned["status"] != "planned"
        or len(rows["operations"]) != 1
        or rows["consumed_tokens"]
        or rows["scope_usage"]
    ):
        raise ValueError("Original operation state is not untouched")
    candidates = [capture(Path(row["candidate"])) for row in data["preview"]["extensions"]["files"]]
    confirm = {
        "at": base.now(),
        "plan": base.ident(cycle / "FROZEN-PLAN.json"),
        "operation_id": identifier,
        "planned_rows": rows,
        "database_identity": file_identity(cycle / "driver-state/operations.sqlite3"),
        "candidates": candidates,
        "dispatch_budget": 1,
    }
    base.put(cycle / "CONFIRM-BINDING.json", confirm)
    base.put(
        cycle / "ATTEMPT-START.json",
        {"at": base.now(), "operation_id": identifier, "real_confirm_budget": 1},
    )
    held = {}

    def predicate(raw):
        try:
            value = base.public_json(raw)
        except (ValueError, UnicodeError):
            return False
        if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
            return False
        receipt = value["data"].get("receipt")
        if (
            value.get("ok") is True
            and isinstance(receipt, dict)
            and receipt.get("operation_id") == identifier
        ):
            held.update(response=value, sha256=hashlib.sha256(raw).hexdigest())
            return True
        return False

    def witness(meta):
        base.guards(plan)
        for candidate in candidates:
            verify_inventory(candidate)
        engineering = verify_saved(plan)
        identity = file_identity(cycle / "driver-state/operations.sqlite3")
        if any(identity[k] != confirm["database_identity"][k] for k in ("dev", "file_id")):
            raise ValueError("Original state file replaced")
        current_rows = base.stage_rows(cycle / "driver-state")
        matches = [r for r in current_rows["operations"] if r["operation_id"] == identifier]
        if len(matches) != 1:
            raise ValueError("Original operation missing or ambiguous")
        receipt, extra = base.receipt_witness(
            matches[0], planned, held["response"], meta, KEY, plan["expected_saved_sha256"]
        )
        if (
            held["sha256"] != meta["buffer_sha256"]
            or meta["stdout_prior_bytes"] != 0
            or not meta["debug_event_held"]
            or not meta["stdout_kernel_object_verified"]
        ):
            raise ValueError("Held stdout boundary incomplete")
        private = cycle / "interruption/private/prekill-stage.json"
        base.put(
            private,
            {"at": base.now(), "rows": current_rows, "engineering": engineering, "boundary": meta},
        )
        return {
            "qualified": True,
            "operation_id": identifier,
            "status_before_kill": matches[0]["status"],
            "writer_pid": extra["writer_pid"],
            "saved_schematic_sha256": base.sha(cycle / "project/edit.kicad_sch"),
            "receipt_sha256": hashlib.sha256(base.canonical(receipt)).hexdigest(),
            "witness": base.ident(private),
            "no_power_loss_or_fsync_claim": True,
        }

    argv = [
        str(exe),
        "sch",
        "edit",
        "--schematic",
        str(cycle / "project/edit.kicad_sch"),
        "--changes",
        str(cycle / "changes.json"),
        "--confirm",
        data["confirm_token"],
        "--idempotency-key",
        KEY,
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
    base.put(
        cycle / "POST-ATTEMPT.json",
        {
            "at": base.now(),
            "operation_id": identifier,
            "result": base.ident(cycle / "interruption/result.json"),
            "engineering": capture(cycle / "project"),
            "rows": base.stage_rows(cycle / "driver-state"),
            "no_more_writes": True,
        },
    )
    base.put(cycle / "PUBLIC-QUERY-ENVIRONMENT.json", {"environment": env})
    base.put(
        cycle / "QUERY-REQUEST.json",
        {
            "operation_id": identifier,
            "state": str(cycle / "driver-state"),
            "scope_file": str(cycle / "scope.json"),
            "exe": str(exe),
            "EXE_sha256": base.EXE_SHA,
            "environment_file": str(cycle / "PUBLIC-QUERY-ENVIRONMENT.json"),
            "intent": str(cycle / "INTENT.json"),
            "before_project_manifest": str(cycle / "FROZEN-PLAN.json"),
            "allowed_next_action": "Original-ID query and saved readback only. "
            "No confirm/preview/resolve/new state.",
        },
    )
    return {
        "phase": "stopped-no-more-writes",
        "operation_id": identifier,
        "matched": result.get("matched"),
        "qualified_interruption": result.get("qualified_interruption"),
    }


def query(root, identifier):
    root, cycle, env, exe, plan = current(root)
    if identifier != base.read(cycle / "QUERY-REQUEST.json")["operation_id"]:
        raise ValueError("Wrong original ID")
    before = capture(cycle / "project")
    output, trace = base.run_observed(
        cycle,
        "independent-public-query",
        [str(exe), "operation", "show", "--id", identifier, "--compact"],
        env,
    )
    verify_inventory(before)
    base.guards(plan)
    return {
        "reply": str(output / "stdout"),
        "trace": str(output / "TRACE.json"),
        "original_operation_id": identifier,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("discover", "freeze", "preview", "interrupt", "query"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--ack-preview-sha256")
    parser.add_argument("--operation-id")
    args = parser.parse_args()
    if args.phase == "interrupt":
        value = interrupt(args.root, args.ack_preview_sha256)
    elif args.phase == "query":
        value = query(args.root, args.operation_id)
    else:
        value = {"discover": discover, "freeze": freeze, "preview": preview}[args.phase](args.root)
    print(json.dumps({"ok": True, "data": value}))
