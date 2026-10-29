"""Real CLI protocol with explicit SDK stubs; native acceptance is separate."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest
from driver_test_utils import grant

from kicad_cli import driver_runtime, export_driver_adapter, export_native, export_readback

FIXTURE = Path(__file__).parent / "fixtures/sch_edit/edit.kicad_pcb"
PATHS = ["fab gerber", "fab drill", "board plot"]
IDENTITY = {"version": "explicit-SDK-stub", "python": "stub", "components": [], "payloads": {}}


def fake_sdk(identity, exporter, argv, child, **options):
    child(99999999)
    args = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("--")}
    directory = Path(args["--out"])
    if exporter == "drill":
        (directory / "board-PTH.drl").write_text(
            "M48\nMETRIC\nT1C0.300\n%\nG90\nG05\nT1\nX1.0Y2.0\nM30\n"
        )
        (directory / "board-drl.rpt").write_text(
            "Total plated holes count 1\nTotal unplated holes count 0\n"
        )
        map_fmt = args.get("--map", "pdf")
        if map_fmt != "none":
            write_plot(
                directory / ("board-map." + ("gbr" if map_fmt == "gerber" else map_fmt)), map_fmt
            )
    else:
        fmt = args["--fmt"]
        for layer in args["--layers"].split(","):
            write_plot(
                directory
                / ("board-" + layer.replace(".", "_") + "." + ("gbr" if fmt == "gerber" else fmt)),
                fmt,
            )
    return {
        "ok": True,
        "data": {
            "files": [{"file": str(p)} for p in sorted(directory.iterdir())],
            "loaded_identity": {k: v for k, v in identity.items() if k != "payloads"},
        },
    }


def write_plot(path, fmt):
    data = {
        "gerber": "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,1.0*%\nD10*\nX1000000Y2000000D03*\nM02*\n",
        "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10"><path d="M0 0L1 1"/></svg>',
        "pdf": "%PDF-1.5\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF\n",
        "dxf": "0\nSECTION\n2\nENTITIES\n0\nLINE\n0\nENDSEC\n0\nEOF\n",
    }
    path.write_text(data[fmt], encoding="utf-8")


@pytest.fixture
def case(tmp_path, monkeypatch):
    board = tmp_path / "board.kicad_pcb"
    shutil.copyfile(FIXTURE, board)
    env = dict(
        os.environ,
        **grant(tmp_path, effects=["observe", "artifact"], outputs=[str(tmp_path / "exports")]),
    )
    monkeypatch.setattr(export_native, "probe", lambda: copy.deepcopy(IDENTITY))
    monkeypatch.setattr(export_native, "run", fake_sdk)
    return board, env


def call(case, path, *flags, **options):
    board, env = case
    args = [
        *path.split(),
        "--board",
        str(board),
        "--out",
        str(board.parent / "exports" / path.replace(" ", "-")),
    ]
    if path == "board plot":
        args += ["--file-format", "svg", "--layers", "F.Cu,B.Cu"]
    with driver_runtime.create_cli(environ=env, home=board.parent, **options) as cli:
        result = cli.run([*args, *flags])
        cli.validator.validate("response", result.envelope)
        return result


def management(case, *args):
    board, env = case
    with driver_runtime.create_cli(environ=env, home=board.parent) as cli:
        result = cli.run(["operation", *args])
        cli.validator.validate("response", result.envelope)
        return result.envelope


def preview(case, path):
    result = call(case, path, "--dry-run")
    assert result.envelope["ok"], result.envelope
    assert result.exit_code == 0
    return result.envelope["data"]


@pytest.mark.parametrize("path", PATHS)
def test_each_export_has_real_files_receipt_known_id_replay_and_preservation(case, path):
    board, _ = case
    before = board.read_bytes()
    plan = preview(case, path)
    assert plan["preview"]["effects"] == ["artifact"]
    assert not Path(plan["preview"]["extensions"]["stage"]).exists()
    result = call(case, path, "--confirm", plan["confirm_token"])
    assert result.envelope["ok"], result.envelope
    receipt = result.envelope["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "not_applicable"
    assert receipt["artifacts"] and board.read_bytes() == before
    for artifact in receipt["artifacts"]:
        assert export_readback.consume(Path(artifact["path"]))["sha256"] == artifact["sha256"]
    status = management(case, "show", "--id", plan["preview"]["operation_id"])
    assert status["data"]["operation_status"]["receipt"] == receipt
    again = call(case, path, "--confirm", plan["confirm_token"])
    assert again.envelope["error"]["details"]["conflict"] == "token_used"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("condition", ["missing", "read-only", "outside-output"])
def test_export_scope_denied_before_native_or_sidecars(case, path, condition, monkeypatch):
    board, env = case
    grant_path = Path(env["KICAD_CLI_SCOPE_FILE"])
    record = json.loads(grant_path.read_bytes())
    if condition == "missing":
        grant_path.unlink()
    elif condition == "read-only":
        record["effects"] = ["observe"]
        grant_path.write_text(json.dumps(record))
    else:
        record["outputs"] = [str(board.parent / "other")]
        grant_path.write_text(json.dumps(record))
    monkeypatch.setattr(export_native, "run", lambda *a, **k: pytest.fail("SDK write dispatched"))
    result = call(case, path, "--dry-run").envelope
    assert result["error"]["code"] == "E_FORBIDDEN", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert not list(board.parent.glob("exports/*"))


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("which", ["board", "project", "rule", "sdk", "output", "scope"])
def test_preflight_drift_refuses_without_native_dispatch(case, path, which, monkeypatch):
    board, env = case
    data = preview(case, path)
    if which == "board":
        board.write_bytes(board.read_bytes() + b"\n")
    elif which in {"project", "rule"}:
        board.with_suffix(".kicad_pro" if which == "project" else ".kicad_dru").write_text(
            "{}" if which == "project" else "(version 1)"
        )
    elif which == "sdk":
        monkeypatch.setattr(export_native, "probe", lambda: dict(IDENTITY, version="changed"))
    elif which == "output":
        output = Path(data["preview"]["extensions"]["output"])
        output.mkdir(parents=True)
        (output / "keep.txt").write_text("user data")
    else:
        p = Path(env["KICAD_CLI_SCOPE_FILE"])
        r = json.loads(p.read_bytes())
        r["notes"] = "changed"
        p.write_text(json.dumps(r))
    monkeypatch.setattr(export_native, "run", lambda *a, **k: pytest.fail("SDK write dispatched"))
    result = call(case, path, "--confirm", data["confirm_token"]).envelope
    assert result["error"]["code"] == "E_CONFLICT", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert not Path(data["preview"]["extensions"]["stage"]).exists()


@pytest.mark.parametrize("path", PATHS)
def test_bad_native_outputs_remain_partial_artifacts_and_never_publish(case, path, monkeypatch):
    data = preview(case, path)

    def corrupt(*args, **kw):
        result = fake_sdk(*args, **kw)
        Path(result["data"]["files"][0]["file"]).write_text("truncated")
        return result

    monkeypatch.setattr(export_native, "run", corrupt)
    result = call(case, path, "--confirm", data["confirm_token"]).envelope
    assert result["error"]["code"] == "E_PARTIAL", result
    receipt = result["error"]["details"]["receipt"]
    assert receipt["artifacts"] and not receipt["extensions"]["published"]
    assert not Path(data["preview"]["extensions"]["output"]).exists()
    assert Path(data["preview"]["extensions"]["lock"]).exists()


@pytest.mark.parametrize("path", PATHS)
def test_source_edit_during_native_generation_keeps_partial_and_new_source(case, path, monkeypatch):
    board, _ = case
    data = preview(case, path)

    def drift(*args, **kw):
        result = fake_sdk(*args, **kw)
        board.write_bytes(board.read_bytes() + b"\n; external edit\n")
        return result

    monkeypatch.setattr(export_native, "run", drift)
    result = call(case, path, "--confirm", data["confirm_token"]).envelope
    assert result["error"]["code"] == "E_PARTIAL", result
    assert board.read_bytes().endswith(b"; external edit\n")
    assert not Path(data["preview"]["extensions"]["output"]).exists()


def test_exclusive_publication_never_replaces_new_foreign_directory(case, monkeypatch):
    data = preview(case, "fab gerber")
    output = Path(data["preview"]["extensions"]["output"])

    def interrupt(stage, plan):
        if stage == "before_publish":
            output.mkdir()
            (output / "keep.txt").write_text("foreign")

    monkeypatch.setattr(export_driver_adapter, "checkpoint", interrupt)
    result = call(case, "fab gerber", "--confirm", data["confirm_token"]).envelope
    assert result["error"]["code"] == "E_PARTIAL"
    assert [p.name for p in output.iterdir()] == ["keep.txt"]
    assert (output / "keep.txt").read_text() == "foreign"


def test_partial_file_publication_can_be_observed_and_registered_without_replay(case, monkeypatch):
    data = preview(case, "fab gerber")
    operation = data["preview"]["operation_id"]

    def interrupt(stage, plan):
        if stage.startswith("published_file:"):
            raise OSError("owned publication interruption")

    monkeypatch.setattr(export_driver_adapter, "checkpoint", interrupt)
    result = call(case, "fab gerber", "--confirm", data["confirm_token"]).envelope
    assert result["error"]["code"] == "E_PARTIAL", result
    assert len(result["error"]["details"]["receipt"]["artifacts"]) == 1
    status = management(case, "show", "--id", operation)["data"]["operation_status"]
    actual = status["extensions"]["recovery_readback"]
    assert actual["completion"] == "completed" and actual["outcome"] == "partial", actual
    # The in-process fixture is explicitly stopped for this recovery test.
    import kicad_cli.move_transaction as transaction

    monkeypatch.setattr(transaction, "process_alive", lambda pid: False)
    result = management(
        case,
        "resolve",
        "--id",
        operation,
        "--outcome",
        "partial",
        "--evidence",
        actual["evidence_file"],
    )
    assert result["ok"], result
    assert len(list(Path(data["preview"]["extensions"]["output"]).iterdir())) == 1
    assert not Path(data["preview"]["extensions"]["lock"]).exists()


def test_native_child_unknown_or_live_prevents_automatic_registration(case, monkeypatch):
    data = preview(case, "fab gerber")
    monkeypatch.setattr(
        export_driver_adapter,
        "checkpoint",
        lambda stage, plan: (
            (_ for _ in ()).throw(OSError("stop")) if stage == "before_publish" else None
        ),
    )
    call(case, "fab gerber", "--confirm", data["confirm_token"])
    monkeypatch.setattr(export_driver_adapter, "process_alive", lambda pid: True)
    status = management(case, "show", "--id", data["preview"]["operation_id"])
    readback = status["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert readback["completion"] == "incomplete" and readback["outcome"] == "unknown"


def test_decimal_drill_units_and_route_slots_are_consumed_from_actual_bytes():
    result = export_readback.excellon(
        "M48\r\nINCH\r\nT1C0.01\r\n%\r\nT1\r\nG00X1.0Y2.0\r\nM15\r\nG01X1.2Y2.0\r\nM16\r\nG05\r\nX3.0Y4.0\r\nM30\r\n"
    )
    assert result["holes"][0]["position_mm"] == [76.19999999999999, 101.6]
    assert result["slots"][0]["diameter_mm"] == 0.254
    with pytest.raises(ValueError):
        export_readback.excellon("M48\nMETRIC\nT1C0.3\n%\nT1\nX123Y123\nM30\n")


def test_recorded_idempotency_key_replays_receipt_without_generating_again(case, monkeypatch):
    data = preview(case, "fab gerber")
    first = call(
        case, "fab gerber", "--confirm", data["confirm_token"], "--idempotency-key", "known-export"
    )
    assert first.envelope["ok"], first.envelope
    monkeypatch.setattr(export_native, "run", lambda *a, **kw: pytest.fail("export repeated"))
    replay = call(
        case, "fab gerber", "--confirm", data["confirm_token"], "--idempotency-key", "known-export"
    )
    assert replay.envelope["ok"], replay.envelope
    assert replay.envelope["data"]["receipt"]["replayed"] is True
    refused = call(
        case,
        "fab gerber",
        "--layers",
        "F.Cu",
        "--confirm",
        data["confirm_token"],
        "--idempotency-key",
        "known-export",
    )
    assert refused.envelope["error"]["details"]["conflict"] == "idempotency_mismatch"


def test_terminal_journal_survives_loss_between_cleanup_and_reply(case, monkeypatch):
    data = preview(case, "fab gerber")

    def lost(stage, plan):
        if stage == "after_cleanup":
            raise RuntimeError("lost after actual cleanup")

    monkeypatch.setattr(export_driver_adapter, "checkpoint", lost)
    result = call(case, "fab gerber", "--confirm", data["confirm_token"]).envelope
    assert result["error"]["code"] == "E_OUTCOME_UNKNOWN", result
    assert not Path(data["preview"]["extensions"]["lock"]).exists()
    status = management(case, "show", "--id", data["preview"]["operation_id"])
    actual = status["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert actual["outcome"] == "applied" and actual["completion"] == "completed", actual


def test_path_dependent_rendered_text_refuses_copy_induced_change(case):
    board, _ = case
    raw = board.read_text(encoding="utf-8")
    board.write_text(
        raw.rsplit(")", 1)[0] + '\n(gr_text "${KIPRJMOD}" (at 1 1) (layer "F.SilkS"))\n)\n',
        encoding="utf-8",
    )
    result = call(case, "fab gerber", "--dry-run").envelope
    assert result["error"]["code"] == "E_VALIDATION", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
