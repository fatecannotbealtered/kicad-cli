"""Authenticate one retained successful save; never run or replay a product command.

An unbound audit log can locate a signed token but cannot authenticate itself.
The token/store proof is compared with an independent original call record that
binds an actual EXE and its raw preview stdout. Secrets never enter the report.
"""

import argparse
import base64
import ctypes
import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path

from .support import put, read, sha

REFERENCE = "tests/test_standalone_failures.py::test_owned_workflow_refusals[repeat-confirm]"
SAVED = (
    "tests/test_sync_removal.py::"
    "test_explicit_remove_preserves_unselected_objects_and_default_retains"
)


def inside(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Retained evidence locator escapes its declared run")
    return path


def arguments(line):
    if not isinstance(line, str) or any(c in line for c in "\0\r\n"):
        raise ValueError("A complete Windows audit command line is required")
    shell, kernel = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(line, ctypes.byref(count))
    if not pointer:
        raise ValueError("Cannot parse retained Windows arguments")
    try:
        return list(pointer[: count.value])
    finally:
        kernel.LocalFree(pointer)


def authenticate(token, secret):
    if not isinstance(token, str) or not token.startswith("ct_") or token.count(".") != 1:
        raise ValueError("No retained signed token")
    body, signature = token[3:].split(".")

    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    raw = decode(body)
    if not hmac.compare_digest(hmac.new(secret, raw, hashlib.sha256).digest(), decode(signature)):
        raise ValueError("Retained token authentication failed")
    return json.loads(raw)


def review(directory, exe_sha256):
    directory = Path(directory).resolve()
    start, finish = read(directory / "START.json"), read(directory / "FINISH.json")
    if start["binary_sha256"] != exe_sha256 or finish["exit"] != 0:
        raise ValueError("Original run does not bind this successful artifact collection")
    binary = Path(start["explicit_environment"]["KICAD_CLI_TEST_BINARY"])
    if sha(binary) != exe_sha256 or not finish["job_finalization"]["natural_tree_complete"]:
        raise ValueError("Artifact changed or original owned runner did not end naturally")
    original_digest = finish.get("evidence_sha256", {}).get("PRIVATE-CALLS.json")
    if original_digest is not None and sha(directory / "PRIVATE-CALLS.json") != original_digest:
        raise ValueError("Original audit binding differs")
    calls = read(directory / "PRIVATE-CALLS.json")
    reference = inside(calls[REFERENCE][0]["cwd"], directory)
    record_path = reference / "calls/call-01.call.json"
    record = read(record_path)
    stdout = reference / "calls/call-01.stdout"
    if not (
        record["exit"] == 0
        and record["execution"] == "completed"
        and record["program"] == record["program_after"]
        and record["program"][0]["sha256"] == exe_sha256
        and record["stdout_sha256"] == sha(stdout)
    ):
        raise ValueError("Reference call/output is not independently bound to this EXE")
    reference_claims = authenticate(
        read(stdout)["data"]["confirm_token"],
        bytes.fromhex((reference / "state/confirm.secret").read_text()),
    )
    entry = next(row for row in calls[SAVED] if "--confirm" in arguments(row["argv"]))
    argv = arguments(entry["argv"])
    token = argv[argv.index("--confirm") + 1]
    board = inside(argv[argv.index("--board") + 1], directory)
    # The audit file is an untrusted locator. Bind it to the known pytest
    # fixture directory from the original invocation, not any signed save
    # elsewhere in the run. This verifier is intentionally specific to SAVED.
    invocation = start.get("argv", [])
    if invocation.count("--basetemp") != 1:
        raise ValueError("Original test invocation does not bind a unique fixture root")
    basetemp = inside(invocation[invocation.index("--basetemp") + 1], directory)
    expected = basetemp / (SAVED.partition("::")[2][:30] + "0") / "edit.kicad_pcb"
    if board != expected.resolve():
        raise ValueError("Unbound locator does not identify the original saved test fixture")
    state = inside(board.parent / "driver-state", directory)
    if (state / "operations.sqlite3-wal").exists():
        raise ValueError("An active/uncheckpointed store needs a separate consistent snapshot")
    before = {p.relative_to(state).as_posix(): sha(p) for p in state.rglob("*") if p.is_file()}
    board_before = sha(board)
    claims = authenticate(token, bytes.fromhex((state / "confirm.secret").read_text()))
    if claims["binding"] != reference_claims["binding"]:
        raise ValueError("Saved operation belongs to a different executable/tool binding")
    database = state / "operations.sqlite3"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        operation = dict(
            connection.execute(
                "select * from operations where operation_id=?", (claims["op"],)
            ).fetchone()
        )
        consumed = dict(
            connection.execute(
                "select * from consumed_tokens where fingerprint=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        )
    finally:
        connection.close()
    plan, receipt = json.loads(operation["plan"]), json.loads(operation["receipt"])
    canonical = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    if not (
        consumed["operation_id"] == operation["operation_id"] == claims["op"]
        and consumed["consumed_at"] <= claims["exp"]
        and operation["status"] == "succeeded"
        and hashlib.sha256(canonical).hexdigest() == claims["plan_digest"]
        and receipt["operation_id"] == operation["operation_id"]
        and receipt["outcome"]["change"] == "applied"
        and receipt["outcome"]["persistence"] == "saved"
        and receipt["snapshot_after"]["revision"] == board_before
    ):
        raise ValueError("Consumed operation/plan/actual saved board proof is incomplete")
    if (
        before != {p.relative_to(state).as_posix(): sha(p) for p in state.rglob("*") if p.is_file()}
        or sha(board) != board_before
    ):
        raise ValueError("Read-only original-state review detected drift")
    return {
        "format": "kicad-cli.retained-pytest-save.v1",
        "case_id": SAVED,
        "status": "authenticated_saved_operation",
        "exe_sha256": exe_sha256,
        "operation_id": operation["operation_id"],
        "receipt_outcome": {
            key: receipt["outcome"].get(key) for key in ("execution", "change", "persistence")
        },
        "verification_status": receipt["outcome"].get("verification", {}).get("status"),
        "actual_board_sha256": board_before,
        "tool_binding_matches": True,
        "signature_verified_locally": True,
        "consumed_before_expiry": True,
        "plan_digest_matches": True,
        "original_state_unchanged": True,
        "original_case_fixture_bound": True,
        "token_expired_now": claims["exp"] < time.time(),
        "original_audit_digest_bound": original_digest is not None,
        "reference_call_sha256": sha(record_path),
        "reference_stdout_sha256": sha(stdout),
        "original_junit_sha256": sha(directory / "junit.xml"),
        "new_product_invocations": 0,
        "confirmation_replays": 0,
        "limits": "Local HMAC audit is not public digital signing. Audit log only locates "
        "the authenticated token. Expiration is observed, never bypassed for execution.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--exe-sha256", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = review(args.run_dir, args.exe_sha256)
    put(args.out, result)
    print(json.dumps({"status": result["status"], "new_product_invocations": 0}))
