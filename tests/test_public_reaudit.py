"""Retained file stability, process absence and complete observation are independent."""

import json

from bench.persistence_acceptance.public_reaudit import review
from bench.persistence_acceptance.support import put, sha


def scene(root, *, complete=True, original_status="passed", child=None, native_allowed=False):
    root.mkdir()
    binary = root / "candidate.exe"
    binary.write_bytes(b"synthetic candidate identity")
    (root / "project").mkdir()
    folder = root / "calls/example"
    folder.mkdir(parents=True)
    for name in ("stdout", "stderr"):
        (folder / name).write_bytes(b"")
    binding = {"exe_sha256": sha(binary)}
    put(
        root / "PUBLIC-RESULT.json",
        {"binding": binding, "cases": [{"id": "example", "status": original_status}]},
    )
    put(root / "PUBLIC-START.json", {"binding": binding, "project_files": {}})
    put(
        root / "PUBLIC-MATRIX.json",
        {"cases": [{"id": "example", "native_allowed": native_allowed}]},
    )
    put(
        root / "SETUP.json",
        {
            "environment": {
                "KICAD_CLI_PYTHON": str(root / "native-python.exe"),
                "KICAD_CLI_OFFICIAL": str(root / "official.exe"),
            }
        },
    )
    processes = [{"image": str(binary), "owned": True, "creation_filetime": 1234, "exit": 0}]
    if child:
        processes.append(child)
    put(
        folder / "TRACE.json",
        {
            "complete_tree": complete,
            "processes": processes,
            "stdout_sha256": sha(folder / "stdout"),
            "stderr_sha256": sha(folder / "stderr"),
        },
    )
    report = root / "PUBLIC-RESULT.json"
    value = json.loads(report.read_bytes())
    value["cases"][0]["trace_sha256"] = sha(folder / "TRACE.json")
    report.write_text(json.dumps(value), encoding="utf-8")
    return binary


def test_unchanged_files_do_not_upgrade_incomplete_observation(tmp_path):
    root = tmp_path / "original"
    binary = scene(root, complete=False)
    row = review(root, binary, tmp_path)["cases"][0]
    assert row["project_unchanged_now"] and row["no_native_or_unclassified_process_observed"]
    assert not row["observer_complete"] and row["review_status"] == "failed_or_unproven"


def test_complete_observation_does_not_rewrite_original_failure(tmp_path):
    root = tmp_path / "original"
    binary = scene(root, original_status="failed")
    row = review(root, binary, tmp_path)["cases"][0]
    assert row["observer_complete"] and row["review_status"] == "failed_or_unproven"


def test_native_permission_does_not_exempt_ambiguous_shell(tmp_path):
    root = tmp_path / "original"
    binary = scene(
        root,
        native_allowed=True,
        child={
            "image": str(tmp_path / "System32/cmd.exe"),
            "metadata": {},
            "owned": True,
            "creation_filetime": 2345,
            "exit": 0,
        },
    )
    row = review(root, binary, tmp_path)["cases"][0]
    assert row["observer_complete"] and not row["no_native_or_unclassified_process_observed"]
    assert row["review_status"] == "failed_or_unproven"


def test_trace_binding_is_required(tmp_path):
    root = tmp_path / "original"
    binary = scene(root)
    path = root / "PUBLIC-RESULT.json"
    report = json.loads(path.read_bytes())
    del report["cases"][0]["trace_sha256"]
    path.write_text(json.dumps(report), encoding="utf-8")
    assert review(root, binary, tmp_path)["cases"][0]["review_status"] != "retained_pass"


def test_changed_trace_is_not_its_own_authority(tmp_path):
    root = tmp_path / "original"
    binary = scene(
        root,
        child={
            "image": str(tmp_path / "unknown.exe"),
            "owned": True,
            "creation_filetime": 2345,
            "exit": 0,
        },
    )
    path = root / "calls/example/TRACE.json"
    trace = json.loads(path.read_bytes())
    trace["processes"].pop()
    path.write_text(json.dumps(trace), encoding="utf-8")
    assert review(root, binary, tmp_path)["cases"][0]["review_status"] != "retained_pass"


def test_replacing_output_and_trace_hash_cannot_rebind_original_case(tmp_path):
    root = tmp_path / "original"
    binary = scene(root)
    (root / "calls/example/stdout").write_bytes(b"substituted output")
    path = root / "calls/example/TRACE.json"
    trace = json.loads(path.read_bytes())
    trace["stdout_sha256"] = sha(root / "calls/example/stdout")
    path.write_text(json.dumps(trace), encoding="utf-8")
    assert review(root, binary, tmp_path)["cases"][0]["review_status"] != "retained_pass"


def test_changed_completeness_cannot_upgrade_original_trace(tmp_path):
    root = tmp_path / "original"
    binary = scene(root, complete=False)
    path = root / "calls/example/TRACE.json"
    trace = json.loads(path.read_bytes())
    trace["complete_tree"] = True
    path.write_text(json.dumps(trace), encoding="utf-8")
    row = review(root, binary, tmp_path)["cases"][0]
    assert row["reason"] == "trace_differs_from_original_binding"
    assert row["observer_complete"] is None
