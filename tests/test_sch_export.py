"""Public artifact contract with an explicit preparation stub; native trials are separate."""

import os

import pytest
from driver_test_utils import grant

from kicad_cli import driver_runtime, engineering_backend, engineering_driver_adapter
from kicad_cli.move_transaction import digest


@pytest.fixture
def case(tmp_path, monkeypatch):
    source = tmp_path / "one.kicad_sch"
    source.write_text(
        '(kicad_sch (version 20250114) (generator "eeschema") '
        '(uuid "a3653c74-d5d8-46da-b07b-548e5ac75bbc") (paper "A4") (lib_symbols))'
    )
    source.with_suffix(".kicad_pro").write_text("{}")
    output = tmp_path / "out"
    output.mkdir()
    env = dict(
        os.environ, **grant(tmp_path, effects=["observe", "artifact"], outputs=[str(tmp_path)])
    )
    monkeypatch.setattr(engineering_backend, "observe_schematic", lambda: {"explicit_stub": True})
    monkeypatch.setattr(engineering_backend, "validate", lambda _value: None)

    def prepared(self, call, inputs, path):
        stage = self.state_dir / "engineering-trials" / call.operation_id / "candidate"
        stage.mkdir(parents=True)
        name = output / "one.svg"
        candidate = stage / name.name
        candidate.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0L1 1"/></svg>'
        )
        return stage, {
            "files": [
                {
                    "path": str(name),
                    "candidate": str(candidate),
                    "intended": digest(candidate),
                    "baseline": "absent",
                }
            ],
            "primary_candidate": str(stage / path.name),
            "result": {"explicit_preparation_stub": True},
            "finding_comparison": None,
        }

    monkeypatch.setattr(engineering_driver_adapter.EngineeringAdapter, "_trial", prepared)
    return source, output, env


def call(case, *flags):
    source, output, env = case
    with driver_runtime.create_cli(environ=env, home=source.parent) as cli:
        result = cli.run(
            [
                "sch",
                "export",
                "--schematic",
                str(source),
                "--out",
                str(output),
                "--file-format",
                "svg",
                *flags,
            ]
        )
        cli.validator.validate("response", result.envelope)
        return result.envelope


def test_artifact_receipt_is_valid_and_source_preserved(case):
    source, output, _ = case
    before = source.read_bytes()
    preview = call(case, "--dry-run")["data"]
    result = call(case, "--confirm", preview["confirm_token"], "--idempotency-key", "export-one")
    assert result["ok"], result
    receipt = result["data"]["receipt"]
    assert receipt["effects"] == ["artifact"]
    assert receipt["outcome"]["change"] == "not_applied"
    assert receipt["outcome"]["persistence"] == "not_applicable"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert not receipt["changed_items"]
    assert receipt["artifacts"][0]["sha256"] == digest(output / "one.svg")
    assert source.read_bytes() == before
    replay = call(case, "--confirm", preview["confirm_token"], "--idempotency-key", "export-one")
    assert replay["ok"] and replay["data"]["receipt"]["replayed"]
    assert source.read_bytes() == before


@pytest.mark.parametrize("drift", ["source", "project", "output"])
def test_stale_export_refuses_before_writing(case, drift):
    source, output, _ = case
    preview = call(case, "--dry-run")["data"]
    path = {
        "source": source,
        "project": source.with_suffix(".kicad_pro"),
        "output": output / "one.svg",
    }[drift]
    path.write_bytes(path.read_bytes() + b"\n" if path.exists() else b"owned output")
    before = source.read_bytes()
    result = call(case, "--confirm", preview["confirm_token"])
    assert not result["ok"] and result["error"]["code"] == "E_CONFLICT", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert source.read_bytes() == before
    if drift != "output":
        assert not (output / "one.svg").exists()


@pytest.mark.parametrize("content,accepted", [("{}", True), ('{"board": {}}', False)])
def test_explicit_initialization_only_accepts_empty_project(
    tmp_path, monkeypatch, content, accepted
):
    from kicad_cli import boardgen
    from kicad_cli.engineering_inputs import capture
    from kicad_cli.sync_inputs import InputError

    netlist = tmp_path / "one.net"
    netlist.write_text("(export (version E))")
    board = tmp_path / "one.kicad_pcb"
    project = board.with_suffix(".kicad_pro")
    project.write_text(content)
    # This test isolates the project admission rule, not netlist/land resolution.
    monkeypatch.setattr(boardgen, "plan", lambda *a: {"components": []})
    request = {
        "netlist": str(netlist),
        "out": str(board),
        "initialize-empty-project": True,
        "pitch": 10,
        "margin": 10,
    }
    if accepted:
        inputs, primary = capture("board from-netlist", request)
        assert primary == board and inputs.files[project] == content.encode()
    else:
        with pytest.raises(InputError, match="exactly empty project"):
            capture("board from-netlist", request)
    assert project.read_text() == content and not board.exists()
