"""Controlled file-set transaction faults; neither native nor process-kill tests.

These in-process shims witness partial landing and the existing observed rollback
and unknown guards for four new hierarchy outputs. Public EXE and actual native
acceptance remain separate qualifications.
"""

import hashlib
from pathlib import Path

import pytest

from kicad_cli.document_transaction import DocumentTransaction, names, owned, revision


def scene(tmp_path):
    outputs = tmp_path / "engineering"
    outputs.mkdir()
    files = []
    for name in ("root.kicad_sch", "a.kicad_sch", "b.kicad_sch", "root.net"):
        payload = ("controlled candidate " + name).encode()
        candidate = tmp_path / name
        candidate.write_bytes(payload)
        files.append(
            {
                "path": str(outputs / name),
                "candidate": str(candidate),
                "baseline": "absent",
                "intended": hashlib.sha256(payload).hexdigest(),
            }
        )
    return (
        outputs,
        files,
        DocumentTransaction(
            outputs / "root.kicad_sch",
            "owned-hierarchy-controlled-01",
            files,
            state_dir=tmp_path / "state",
            key=b"controlled source test key",
        ),
    )


@pytest.mark.parametrize("landed", [0, 1, 2, 3, 4])
def test_partial_creation_landing_is_observed_and_restored_to_exact_absence(tmp_path, landed):
    outputs, files, txn = scene(tmp_path)
    txn.arm()
    for row in files[:landed]:
        Path(row["path"]).write_bytes(Path(row["candidate"]).read_bytes())
    observed = {row["path"]: revision(row["path"]) for row in files}
    assert sum(value != "absent" for value in observed.values()) == landed
    assert (
        owned(txn.primary, txn.operation, files, state_dir=txn.state_dir, key=txn.key)[
            "operation_id"
        ]
        == txn.operation
    )
    txn.rollback()
    assert all(revision(row["path"]) == "absent" for row in files)
    assert not any(path.exists() for path in names(txn.primary))
    assert list(outputs.iterdir()) == []


def test_unknown_member_prevents_partial_rollback_and_preserves_all_evidence(tmp_path):
    _, files, txn = scene(tmp_path)
    txn.arm()
    Path(files[0]["path"]).write_bytes(Path(files[0]["candidate"]).read_bytes())
    Path(files[1]["path"]).write_bytes(b"unattributed external child write")
    before = {row["path"]: revision(row["path"]) for row in files}
    with pytest.raises(OSError, match="Unrecognized document bytes"):
        txn.rollback()
    assert {row["path"]: revision(row["path"]) for row in files} == before
    assert all(path.exists() for path in names(txn.primary))
    with pytest.raises(FileExistsError):
        DocumentTransaction(
            txn.primary, "another-operation", files, state_dir=txn.state_dir, key=txn.key
        ).arm()


def test_new_output_appearance_before_locking_refuses_and_preserves_external_file(tmp_path):
    outputs, files, txn = scene(tmp_path)
    existing = Path(files[2]["path"])
    existing.write_bytes(b"other owner won the target")
    with pytest.raises(ValueError, match="Document drift"):
        txn.arm()
    assert existing.read_bytes() == b"other owner won the target"
    assert list(outputs.iterdir()) == [existing]


def test_completed_creation_duplicate_arm_refuses_without_second_engineering_write(tmp_path):
    _, files, txn = scene(tmp_path)
    txn.arm()
    for row in files:
        Path(row["path"]).write_bytes(Path(row["candidate"]).read_bytes())
    txn.cleanup()
    before = {
        row["path"]: (revision(row["path"]), Path(row["path"]).stat().st_mtime_ns) for row in files
    }
    with pytest.raises(ValueError, match="Document drift"):
        DocumentTransaction(
            txn.primary, "another-operation", files, state_dir=txn.state_dir, key=txn.key
        ).arm()
    assert {
        row["path"]: (revision(row["path"]), Path(row["path"]).stat().st_mtime_ns) for row in files
    } == before
