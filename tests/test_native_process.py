"""Harmless recorder counterexamples; not actual KiCad/native fault acceptance."""

import json
import sys

import pytest
from native_process import directory, run


def test_output_file_and_json_do_not_hide_a_failed_process_or_trigger_retry(tmp_path):
    evidence = directory("harmless-failed")
    report = evidence / "report.json"
    attempts = evidence / "attempts.txt"
    script = (
        "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "p.write_bytes(p.read_bytes()+b'one\\n' if p.exists() else b'one\\n'); "
        "pathlib.Path(sys.argv[2]).write_text('{}'); print('{\"ok\":true}'); sys.exit(7)"
    )
    with pytest.raises(AssertionError):
        run([sys.executable, "-B", "-c", script, str(attempts), str(report)], evidence)
    assert attempts.read_bytes() == b"one\n"
    assert report.read_bytes() == b"{}"
    recorded = json.loads((evidence / "process.call.json").read_bytes())
    assert recorded["exit"] == 7 and recorded["job_finalization"]["natural_tree_complete"]
    assert (evidence / "process.stdout").is_file() and (evidence / "process.stderr").is_file()


def test_one_successful_process_keeps_raw_evidence_and_empty_owned_tree():
    evidence = directory("harmless-success")
    record, stdout, stderr = run(
        [sys.executable, "-B", "-c", "print('owned harmless probe')"], evidence
    )
    assert record["exit"] == 0 and stdout.strip() == b"owned harmless probe" and stderr == b""
    assert record["job_finalization"]["natural_tree_complete"]
    assert record["job_finalization"]["after_cleanup"]["members"] == []
