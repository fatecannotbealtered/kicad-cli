"""Safe defaults, publication boundaries and truthful phase classification."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bench.persistence_acceptance import evidence, support

REPO = Path(__file__).resolve().parents[1]


def config(tmp_path):
    binary = tmp_path / "not-executable.exe"
    binary.write_bytes(b"must never execute")
    data = {
        "test_root": str(tmp_path / "owned"),
        "exe": str(binary),
        "exe_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "build_source_commit": "a" * 40,
        "sdk": str(tmp_path / "sdk"),
        "native_root": str(tmp_path / "native"),
        "configuration": str(tmp_path / "configuration"),
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


@pytest.mark.parametrize(
    "case",
    ["F5-partial-save", "candidate-diagnosis", "permission-selfcheck", "minimal-saved-recovery"],
)
def test_default_entry_never_creates_or_executes(tmp_path, case):
    path, _ = config(tmp_path)
    out = tmp_path / "owned/new-attempt"
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "bench.persistence_acceptance",
            "run",
            "--config",
            str(path),
            "--out",
            str(out),
            "--case",
            case,
        ],
        cwd=REPO,
        capture_output=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["action"] == "plan_only" and not result["owned_termination_enabled"]
    assert not out.parent.exists()


@pytest.mark.parametrize("condition", ["outside-root", "existing-output", "exe-drift", "no-owner"])
def test_unsafe_execution_rejected_before_copy(tmp_path, monkeypatch, condition):
    path, data = config(tmp_path)
    out = tmp_path / "owned/new-attempt"
    if condition == "outside-root":
        out = tmp_path / "outside"
    elif condition == "existing-output":
        out.mkdir(parents=True)
    elif condition == "exe-drift":
        Path(data["exe"]).write_bytes(b"changed after binding")
    for key in ("ROOT", "BINARY", "SETTINGS"):
        monkeypatch.setattr(support, key, getattr(support, key))
    with pytest.raises(ValueError):
        support.configure(
            path, out, execute=True, authorized_by=None if condition == "no-owner" else "test owner"
        )
    assert not (out / "project").exists()


def test_f5_cannot_relabel_old_pending_only_f1():
    prior = json.loads(
        (REPO / "docs/evidence/persistence-boundaries-20261013/prior-F0-F4.json").read_bytes()
    )
    f1 = copy.deepcopy(next(row for row in prior["cases"] if row["case"].startswith("F1")))
    assert evidence.stage_hit(f1)
    f1["case"] = "F5-partial-save"
    assert not evidence.stage_hit(f1)
    f1["frozen_file_states"][0]["observed"] = f1["frozen_file_states"][0]["intended"]
    assert not evidence.stage_hit(f1)
    f1["process"].update(birth_rechecked_before_attach=True, creation_filetime=123)
    assert evidence.stage_hit(f1)
    f1["authentication"]["locally_verified"] = False
    assert not evidence.stage_hit(f1)


def test_f1_accepts_actual_mixed_save_without_a_remaining_pending_file():
    prior = json.loads(
        (REPO / "docs/evidence/persistence-boundaries-20261013/prior-F0-F4.json").read_bytes()
    )
    row = copy.deepcopy(next(row for row in prior["cases"] if row["case"].startswith("F1")))
    row["pending_files"] = []
    assert not evidence.stage_hit(row)
    row["frozen_file_states"][0]["observed"] = row["frozen_file_states"][0]["intended"]
    assert evidence.stage_hit(row)
    row["case"] = "unrecognized-case"
    assert not evidence.stage_hit(row)


@pytest.mark.parametrize(
    "value",
    [
        {"confirm_token": "redacted"},
        {"hmac": "redacted"},
        {"argv": []},
        {"nested": [{"note": "ct_example"}]},
        {"note": "D:/private/example"},
    ],
)
def test_export_refuses_secrets_and_local_paths(value):
    with pytest.raises(ValueError):
        evidence.assert_publishable(value)


def test_public_test_ids_are_not_mistaken_for_confirmation_tokens():
    evidence.assert_publishable({"test_id": "test_public_contract_edges::test_selection"})


@pytest.mark.skipif(sys.platform != "win32", reason="External controller is Windows-only")
def test_controller_default_refuses_before_any_os_action(tmp_path):
    from bench.persistence_acceptance.win32_control import OwnedProcess

    with pytest.raises(PermissionError, match="disabled by default"):
        OwnedProcess(["never-start"], tmp_path, {}, tmp_path / "out", tmp_path / "err")
    assert list(tmp_path.iterdir()) == []
