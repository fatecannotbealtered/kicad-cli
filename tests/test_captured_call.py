"""Public recording guardrails; actual execution uses a harmless owned child."""

import json
import os
import sys

import pytest

from bench.persistence_acceptance.captured_call import main
from bench.persistence_acceptance.support import sha


def argv(tmp_path):
    return [
        "--exe",
        sys.executable,
        "--sha256",
        sha(sys.executable),
        "--cwd",
        str(tmp_path),
        "--out",
        str(tmp_path / "new-call"),
    ]


def test_default_only_plans_and_does_not_start_or_create_output(tmp_path, capsys):
    assert main(argv(tmp_path) + ["--", "-c", "raise SystemExit(99)"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "plan_only"
    assert not (tmp_path / "new-call").exists()


@pytest.mark.parametrize("failure", ["hash", "authorization", "existing-output"])
def test_invalid_recording_setup_refuses_before_launch(tmp_path, failure):
    args = argv(tmp_path)
    if failure == "hash":
        args[args.index("--sha256") + 1] = "0" * 64
    elif failure == "authorization":
        args += ["--execute"]
    else:
        (tmp_path / "new-call").mkdir()
        (tmp_path / "new-call/keep").write_text("original")
    with pytest.raises(SystemExit) as error:
        main(args + ["--", "-c", "raise SystemExit(99)"])
    assert error.value.code == 2
    assert not (tmp_path / "new-call/public.call.json").exists()
    if failure == "existing-output":
        assert (tmp_path / "new-call/keep").read_text() == "original"


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows owned Job completion")
def test_real_owned_child_records_output_exit_and_empty_job(tmp_path):
    assert (
        main(
            argv(tmp_path)
            + [
                "--execute",
                "--authorized-by",
                "harmless test child",
                "--",
                "-c",
                "print('{\"ok\":true}')",
            ]
        )
        == 0
    )
    record = json.loads((tmp_path / "new-call/public.call.json").read_bytes())
    assert record["exit"] == 0 and record["job_finalization"]["natural_tree_complete"]
    assert record["root_identity"]["creation_filetime"]
    assert record["root_identity"]["owned_job_member"]
    assert record["root_identity"]["assigned_before_resume"]
    assert record["job_finalization"]["after_cleanup"]["members"] == []
    assert json.loads((tmp_path / "new-call/public.stdout").read_bytes()) == {"ok": True}


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows captured output")
def test_exit_zero_with_invalid_json_is_a_recording_failure(tmp_path):
    assert (
        main(
            argv(tmp_path)
            + ["--execute", "--authorized-by", "harmless child", "--", "-c", "print('not-json')"]
        )
        == 1
    )
    record = json.loads((tmp_path / "new-call/public.call.json").read_bytes())
    assert record["exit"] == 0 and record["stdout_parse"] == "invalid"
    assert record["job_finalization"]["natural_tree_complete"]
