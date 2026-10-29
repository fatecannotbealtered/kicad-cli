"""Exercise the real F5 receipt reader with contradictory and incomplete receipts."""

import hashlib
import json

import pytest

from bench.persistence_acceptance.f5 import read_facts, verdict


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value).encode())


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def witness(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    rows, frozen, current = [], {}, {}
    for name, old, new, actual in (
        ("a.sch", b"old a", b"new a", b"new a"),
        ("b.sch", b"old b", b"new b", b"old b"),
    ):
        path = project / name
        path.write_bytes(actual)
        rows.append({"path": str(path), "baseline": digest(old), "intended": digest(new)})
        frozen[name], current[str(path)] = digest(old), digest(actual)
        archive = tmp_path / "state/recovery/op-test" / ("baseline-" + name)
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(old)
    (project / "protected").write_bytes(b"protected")
    frozen["protected"] = digest(b"protected")
    save(tmp_path / "SETUP.json", {"binding": {"exe_sha256": "test-binding"}})
    save(tmp_path / "FROZEN.json", {"files": frozen})
    save(
        tmp_path / "FROZEN-WITNESS.json",
        {"journal": {"value": {"files": rows}}, "current": current},
    )
    operation = {"operation_id": "op-test", "status": "unknown"}
    responses = {
        "fresh-show": {
            "ok": True,
            "data": {
                "operation_status": operation
                | {
                    "extensions": {
                        "recovery_readback": {
                            "outcome": "partial",
                            "cleanup": {"status": "attributable"},
                        }
                    }
                }
            },
        },
        "unresolved-preview": {"ok": False, "error": {"code": "E_CONFLICT"}},
        "resolve": {
            "ok": True,
            "data": {"operation_status": operation | {"resolution": {"outcome": "partial"}}},
        },
        "resolved-show": {
            "ok": True,
            "data": {"operation_status": operation | {"resolution": {"outcome": "partial"}}},
        },
        "duplicate-resolve": {"ok": False, "error": {"code": "E_VALIDATION"}},
        "later-preview": {
            "ok": True,
            "data": {"preview": {"operation_id": "new-plan", "can_apply": True}},
        },
    }

    def write(name, value, code=None):
        body = tmp_path / "calls" / (name + ".stdout")
        save(body, value)
        save(
            body.with_name(name + ".call.json"),
            {
                "exit": code
                if code is not None
                else 0
                if value["ok"]
                else 6
                if name == "unresolved-preview"
                else 2,
                "source_binding": {"exe_sha256": "test-binding"},
                "execution": "completed" if value["ok"] else "nonzero_exit",
                "stdout_sha256": digest(body.read_bytes()),
                "command": [],
                "started_at": "2026-10-13T00:00:00Z",
                "finished_at": "2026-10-13T00:00:00Z",
            },
        )

    for name, value in responses.items():
        write(name, value)
    return tmp_path, responses, write


def test_preserved_partial_receipts_pass_while_top_status_remains_unknown(witness):
    root, _, _ = witness
    facts = read_facts(root, stage_hit=True, operation="op-test")
    assert facts["resolved_top_level_status"] == "unknown"
    assert verdict(facts)["status"] == "passed"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "applied",
        "not_applied",
        "unattributable",
        "registration-failed",
        "missing-resolution",
        "missing-query-resolution",
        "missing-block-check",
        "missing-release-check",
        "still-blocked",
        "duplicate-accepted",
    ],
)
def test_receipt_variants_cannot_masquerade_as_a_complete_loop(witness, mutation):
    root, responses, write = witness
    if mutation in {"unknown", "applied", "not_applied", "unattributable"}:
        row = responses["fresh-show"]["data"]["operation_status"]["extensions"]["recovery_readback"]
        if mutation == "unattributable":
            row["cleanup"]["status"] = "unknown"
        else:
            row["outcome"] = mutation
        write("fresh-show", responses["fresh-show"])
    elif mutation in {"missing-resolution", "missing-query-resolution"}:
        name = "resolve" if mutation == "missing-resolution" else "resolved-show"
        responses[name]["data"]["operation_status"].pop("resolution")
        write(name, responses[name])
    elif mutation in {"missing-block-check", "missing-release-check"}:
        name = "unresolved-preview" if mutation == "missing-block-check" else "later-preview"
        (root / "calls" / (name + ".call.json")).unlink()
    elif mutation == "registration-failed":
        write("resolve", {"ok": False, "error": {"code": "E_VALIDATION"}}, 2)
    elif mutation == "still-blocked":
        write("later-preview", {"ok": False, "error": {"code": "E_CONFLICT"}}, 6)
    else:
        write("duplicate-resolve", responses["resolve"])
    facts = read_facts(root, stage_hit=True, operation="op-test")
    assert verdict(facts)["status"] == "failed"
