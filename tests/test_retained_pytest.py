"""Synthetic evidence checker inputs, never actual EXE/engineering acceptance."""

import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from bench.persistence_acceptance import retained_pytest as R

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows retained argv grammar")


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def token(secret, claims):
    body = json.dumps(claims).encode()

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    return "ct_" + encode(body) + "." + encode(hmac.new(secret, body, hashlib.sha256).digest())


@pytest.fixture
def scene(tmp_path):
    binary = tmp_path / "synthetic.exe"
    binary.write_bytes(b"synthetic fixture only")
    digest = R.sha(binary)
    put(
        tmp_path / "START.json",
        {
            "binary_sha256": digest,
            "explicit_environment": {"KICAD_CLI_TEST_BINARY": str(binary)},
            "argv": ["python", "-m", "pytest", "--basetemp", str(tmp_path / "tmp")],
        },
    )
    put(tmp_path / "FINISH.json", {"exit": 0, "job_finalization": {"natural_tree_complete": True}})
    (tmp_path / "junit.xml").write_text("synthetic result")
    reference = tmp_path / "reference"
    target = tmp_path / "tmp" / (R.SAVED.partition("::")[2][:30] + "0")
    state = target / "driver-state"
    for path in (reference / "state", state):
        path.mkdir(parents=True)
        (path / "confirm.secret").write_text("11" * 32)
    secret = bytes.fromhex("11" * 32)
    board = target / "edit.kicad_pcb"
    board.write_bytes(b"synthetic saved bytes")
    plan = {"synthetic": True}
    plan_digest = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    claims = {"binding": "binding-a", "op": "operation-a", "exp": 100, "plan_digest": plan_digest}
    signed = token(secret, claims)
    stdout = reference / "calls/call-01.stdout"
    put(stdout, {"data": {"confirm_token": token(secret, {"binding": "binding-a"})}})
    program = [{"sha256": digest}]
    put(
        reference / "calls/call-01.call.json",
        {
            "exit": 0,
            "execution": "completed",
            "program": program,
            "program_after": program,
            "stdout_sha256": R.sha(stdout),
        },
    )
    put(
        tmp_path / "PRIVATE-CALLS.json",
        {
            R.REFERENCE: [{"cwd": str(reference)}],
            R.SAVED: [
                {
                    "argv": subprocess.list2cmdline(
                        [str(binary), "project", "sync", "--board", str(board), "--confirm", signed]
                    )
                }
            ],
        },
    )
    con = sqlite3.connect(state / "operations.sqlite3")
    con.execute("create table operations(operation_id, plan, receipt, status)")
    con.execute("create table consumed_tokens(fingerprint,operation_id,consumed_at)")
    receipt = {
        "operation_id": "operation-a",
        "outcome": {"change": "applied", "persistence": "saved"},
        "snapshot_after": {"revision": R.sha(board)},
    }
    con.execute(
        "insert into operations values(?,?,?,?)",
        ("operation-a", json.dumps(plan), json.dumps(receipt), "succeeded"),
    )
    con.execute(
        "insert into consumed_tokens values(?,?,?)",
        (hashlib.sha256(signed.encode()).hexdigest(), "operation-a", 99),
    )
    con.commit()
    con.close()
    return tmp_path, digest, board, state, reference


def test_authenticates_expired_evidence_without_execution_or_mutation(scene):
    root, digest, board, state, _ = scene
    before = {str(p): R.sha(p) for p in root.rglob("*") if p.is_file()}
    result = R.review(root, digest)
    assert result["status"] == "authenticated_saved_operation" and result["token_expired_now"]
    assert result["confirmation_replays"] == result["new_product_invocations"] == 0
    assert before == {str(p): R.sha(p) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "drift",
    [
        "token",
        "reference-output",
        "reference-artifact",
        "plan",
        "consumed",
        "board",
        "different-binding",
        "redirected-locator",
    ],
)
def test_changed_or_missing_independent_proof_cannot_be_admitted(scene, drift):
    root, digest, board, state, reference = scene
    if drift == "token":
        path = root / "PRIVATE-CALLS.json"
        value = R.read(path)
        value[R.SAVED][0]["argv"] += "changed"
        put(path, value)
    elif drift == "reference-output":
        put(reference / "calls/call-01.stdout", {"data": {}})
    elif drift == "reference-artifact":
        path = reference / "calls/call-01.call.json"
        value = R.read(path)
        value["program_after"] = []
        put(path, value)
    elif drift == "board":
        board.write_bytes(b"external preserved change")
    elif drift == "different-binding":
        path = reference / "calls/call-01.stdout"
        put(
            path,
            {"data": {"confirm_token": token(bytes.fromhex("11" * 32), {"binding": "different"})}},
        )
        value = R.read(reference / "calls/call-01.call.json")
        value["stdout_sha256"] = R.sha(path)
        put(reference / "calls/call-01.call.json", value)
    elif drift == "redirected-locator":
        other = root / "another-case"
        shutil.copytree(board.parent, other)
        path = root / "PRIVATE-CALLS.json"
        value = R.read(path)
        value[R.SAVED][0]["argv"] = value[R.SAVED][0]["argv"].replace(
            str(board), str(other / board.name)
        )
        put(path, value)
    else:
        con = sqlite3.connect(state / "operations.sqlite3")
        con.execute(
            "update operations set plan='{}'" if drift == "plan" else "delete from consumed_tokens"
        )
        con.commit()
        con.close()
    before = {str(p): R.sha(p) for p in root.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, TypeError)):
        R.review(root, digest)
    assert before == {str(p): R.sha(p) for p in root.rglob("*") if p.is_file()}
