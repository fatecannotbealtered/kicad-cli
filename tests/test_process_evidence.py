"""Real owned processes exercise durable terminal records, including timeouts."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from change_support import Run
from process_evidence import run_recorded
from run_power_regression import negative_erc


@pytest.mark.parametrize(
    "code,execution",
    [
        ("print('complete')", "completed"),
        ("import sys; print('partial',flush=True); sys.exit(7)", "nonzero_exit"),
        ("import time; print('partial',flush=True); time.sleep(20)", "timeout"),
    ],
    ids=["success", "nonzero", "timeout"],
)
def test_terminal_records_preserve_raw_output_and_bindings(tmp_path, code, execution):
    record, _ = run_recorded(
        tmp_path,
        "call",
        [sys.executable, "-u", "-c", code],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1.5,
        binding={"source": "test"},
    )
    assert record["state"] == "finished" and record["execution"] == execution
    assert record["started_at"] <= record["finished_at"] and record["duration_seconds"] > 0
    assert record["program"][0]["sha256"] == record["program_after"][0]["sha256"]
    assert (tmp_path / "call.stdout").read_bytes()
    saved = json.loads((tmp_path / "call.call.json").read_bytes())
    assert saved["stdout_sha256"] == record["stdout_sha256"]
    if execution == "timeout":
        assert record["status"] == "failed"
        assert "root reaped" in record["cleanup"]


@pytest.mark.parametrize(
    "text,status",
    [
        (None, "missing"),
        ("{broken", "invalid"),
        ("{}", "invalid"),
        ('{"sheets":[{"violations":[]}]}', "valid"),
    ],
    ids=["missing", "bad-json", "wrong-shape", "valid"],
)
def test_exit_zero_is_not_a_report(tmp_path, text, status):
    target = tmp_path / "erc.json"
    command = (
        "print('raw stays')"
        if text is None
        else (
            f"from pathlib import Path; Path({str(target)!r}).write_text({text!r}); "
            "print('raw stays')"
        )
    )
    record, _ = run_recorded(
        tmp_path,
        "native",
        [sys.executable, "-c", command],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        report={"path": str(target), "kind": "erc"},
    )
    assert record["exit"] == 0 and record["report"]["status"] == status
    assert record["status"] == ("passed" if status == "valid" else "failed")
    assert (tmp_path / "native.stdout").read_text().strip() == "raw stays"


def test_parse_failure_does_not_replace_raw_stdout(tmp_path):
    record, value = run_recorded(
        tmp_path,
        "bad",
        [sys.executable, "-c", "print('{broken')"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        stdout_json=True,
    )
    assert record["status"] == "failed" and value is None
    assert (tmp_path / "bad.stdout").read_text().strip() == "{broken"
    assert record["stdout_parse"] == "invalid"


def test_call_started_record_exists_while_child_runs(tmp_path):
    journal = tmp_path / "live.call.json"
    code = (
        f"import json; from pathlib import Path; "
        f"assert json.loads(Path({str(journal)!r}).read_bytes())['state']=='started'; "
        "print('seen')"
    )
    record, _ = run_recorded(
        tmp_path,
        "live",
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
    )
    assert record["status"] == "passed"


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows crash exit and job semantics")
def test_windows_crash_is_recorded(tmp_path):
    code = (
        "import ctypes; k=ctypes.windll.kernel32; "
        "k.GetCurrentProcess.restype=ctypes.c_void_p; "
        "k.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint32]; "
        "print('before crash',flush=True); k.TerminateProcess(k.GetCurrentProcess(), 0xC0000409)"
    )
    record, _ = run_recorded(
        tmp_path,
        "crash",
        [sys.executable, "-u", "-c", code],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
    )
    assert record["execution"] == "crashed" and record["exit"] == 0xC0000409
    assert (tmp_path / "crash.stdout").read_text().strip() == "before crash"


def test_run_call_failure_retains_source_binding(tmp_path):
    import change_support

    from scripts.fcc_evidence import source_manifest

    run = Run.__new__(Run)
    run.root, run.events, run.env = tmp_path, [], os.environ.copy()
    run.cwd = tmp_path
    run.cli = [sys.executable, "-u", "-c"]
    run.binding = {"source_commit": "controlled"}
    run.source_files = source_manifest(change_support.REPO)
    with pytest.raises(RuntimeError, match="timeout"):
        run.call(
            "timeout",
            ["import time; print('partial',flush=True); time.sleep(20)"],
            json_output=False,
            timeout=1.5,
        )
    assert json.loads((tmp_path / "events.json").read_bytes())[0]["execution"] == "timeout"
    assert json.loads((tmp_path / "events.json").read_bytes())[0]["cwd"] == str(tmp_path)
    assert (
        json.loads((tmp_path / "source-after-failure.json").read_bytes())["failure_call"]
        == "timeout"
    )


def test_negative_intent_is_saved_before_native_failure(tmp_path):
    def fail(name, args):
        record = json.loads((tmp_path / "negative-intent.json").read_bytes())
        assert record["status"] == "passed_rejection"
        raise RuntimeError("controlled native crash")

    trial = SimpleNamespace(root=tmp_path, binding={}, official_call=fail)
    with pytest.raises(RuntimeError, match="controlled"):
        negative_erc(
            trial,
            tmp_path,
            tmp_path / "x.kicad_sch",
            {"named_nets": {"RESET": ["R1.2"]}},
            {"RESET": []},
            True,
        )
    assert (
        json.loads((tmp_path / "negative-intent.json").read_bytes())["status"] == "passed_rejection"
    )
    assert (
        json.loads((tmp_path / "negative-native-erc.json").read_bytes())["status"]
        == "failed_or_incomplete"
    )
    assert json.loads((tmp_path / "result.json").read_bytes())["whole_workflow_passed"] is False


def test_preexisting_report_cannot_satisfy_another_call(tmp_path):
    report = tmp_path / "old.json"
    report.write_text('{"sheets":[]}')
    record, _ = run_recorded(
        tmp_path,
        "stale",
        [sys.executable, "-c", "print('not launched')"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=5,
        report={"path": str(report), "kind": "erc"},
    )
    assert record["execution"] == "refused_preexisting_report"
    assert record["exit"] is None and record["status"] == "failed"
    assert report.read_text() == '{"sheets":[]}'


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows process-tree cleanup")
def test_timeout_cannot_leave_a_descendant_writer(tmp_path):
    marker = tmp_path / "late"
    child = (
        "import time; from pathlib import Path; time.sleep(3); "
        f"Path({str(marker)!r}).write_text('escaped')"
    )
    parent = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "print('spawned',flush=True); time.sleep(20)"
    )
    record, _ = run_recorded(
        tmp_path,
        "tree",
        [sys.executable, "-u", "-c", parent],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1.5,
    )
    assert record["execution"] == "timeout"
    import time

    time.sleep(2)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows owned Job finalization")
@pytest.mark.parametrize("root_exit", [0, 7])
def test_root_exit_with_living_child_is_not_natural_completion(tmp_path, root_exit):
    ready = tmp_path / "child-ready"
    child = (
        "from pathlib import Path; import time; "
        f"Path({str(ready)!r}).write_text('ready'); time.sleep(30)"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        f"p=Path({str(ready)!r}); deadline=time.monotonic()+10\n"
        "while not p.exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
        f"assert p.exists(); sys.exit({root_exit})"
    )
    record, _ = run_recorded(
        tmp_path,
        "child-survival",
        [sys.executable, "-c", parent],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=15,
    )
    assert record["execution"] == "owned_descendants_remaining"
    assert record["status"] == "failed"
    final = record["job_finalization"]
    assert final["natural_tree_complete"] is False
    assert final["controlled_termination"] is True
    assert final["before_close"]["members"]
    assert all(
        p["owned_job_member"] and p["creation_filetime"] for p in final["before_close"]["members"]
    )
    assert final["after_cleanup"]["members"] == []


@pytest.mark.skipif(os.name != "nt", reason="Standalone Windows controller support")
def test_job_inspection_needs_no_developer_or_sibling_package(tmp_path):
    import shutil
    import subprocess

    shutil.copyfile(
        Path(__file__).parents[1] / "bench/owned_process.py", tmp_path / "owned_process.py"
    )
    code = (
        "import sys,json;sys.path.insert(0,sys.argv[1]); "
        "from owned_process import WindowsJob; "
        "print(json.dumps(WindowsJob().close()))"
    )
    process = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["natural_tree_complete"] and result["after_cleanup"]["members"] == []
