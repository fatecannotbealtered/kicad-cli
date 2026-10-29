"""Exercise the real command boundary without needing an installed KiCad."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import arguments, envelope, registry  # noqa: E402
from kicad_cli.commands import layout  # noqa: E402


def invoke(argv: list[str], monkeypatch, capsys) -> tuple[dict, int, list[dict]]:
    """Only replace business handlers; parsing and dispatch remain production code.

    These substitutes must not manufacture live FCC trace evidence.
    """
    # Isolated legacy tokenizer unit tests; no driver or native/FCC claim.
    commands = registry.build()
    for c in commands:
        if c["path"] == "board analyze reference-plane":
            next(p for p in c["params"] if p["name"] == "step")["exclusive_minimum"] = 0
    reached = []

    def capture(opts):
        reached.append(opts)
        envelope.ok(opts)

    for command in commands:
        command["handler"] = capture
    monkeypatch.delenv("KICAD_CLI_STRICT", raising=False)
    with pytest.raises(SystemExit) as exc:
        envelope.configure()
        command, opts = arguments.parse(argv, commands, registry.GLOBAL_OPTIONS)
        command["handler"](opts)
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out), exc.value.code, reached


@pytest.mark.parametrize("value", ["false", "False", "0"])
@pytest.mark.parametrize("equals", [False, True])
def test_false_is_false_at_the_payload_boundary(value, equals, monkeypatch, capsys):
    flag = [f"--allow-routed={value}"] if equals else ["--allow-routed", value]
    doc, code, reached = invoke(
        ["board", "footprint", "place", "--board", "b.kicad_pcb", *flag], monkeypatch, capsys
    )
    assert code == 0 and doc["ok"]
    assert reached[0]["allow-routed"] is False
    assert reached[0]["allow_routed"] is False
    assert layout._flag(reached[0], "allow-routed") == []


@pytest.mark.parametrize(
    "flag", [["--allow-routed"], ["--allow-routed=true"], ["--allow-routed", "1"]]
)
def test_true_booleans_are_forwarded(flag, monkeypatch, capsys):
    _, code, reached = invoke(
        ["board", "footprint", "place", "--board", "b", *flag], monkeypatch, capsys
    )
    assert code == 0
    assert layout._flag(reached[0], "allow-routed") == ["--allow-routed"]


def test_boolean_before_command_does_not_swallow_the_command(monkeypatch, capsys):
    _, code, reached = invoke(["--compact", "reference", "--quiet", "false"], monkeypatch, capsys)
    assert code == 0
    assert reached[0]["compact"] is True
    assert reached[0]["quiet"] is False


def test_repeated_layers_accumulate_instead_of_silently_replacing(monkeypatch, tmp_path):
    import shutil

    from driver_test_utils import grant

    from kicad_cli import driver_runtime, export_native

    board = tmp_path / "board.kicad_pcb"
    shutil.copyfile(REPO / "tests/fixtures/sch_edit/edit.kicad_pcb", board)
    env = dict(os.environ, **grant(tmp_path, effects=["artifact"], outputs=[str(tmp_path)]))
    monkeypatch.setattr(export_native, "probe", lambda: {"version": "explicit SDK stub"})
    with driver_runtime.create_cli(environ=env, home=tmp_path) as cli:
        result = cli.run(
            [
                "fab",
                "gerber",
                "--board",
                str(board),
                "--layers",
                "F.Cu,B.Cu",
                "--layers",
                "Edge.Cuts",
                "--dry-run",
            ]
        )
    assert result.exit_code == 0 and result.envelope["ok"], result.envelope
    assert result.envelope["data"]["preview"]["extensions"]["layers"] == [
        "F.Cu",
        "B.Cu",
        "Edge.Cuts",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["reference", "extra"],
        ["board", "analyze", "ampacity", "--board"],
        ["board", "analyze", "ampacity"],
        ["board", "analyze", "ampacity", "--board="],
        ["board", "footprint", "move", "--board", "b"],
        ["sch", "check"],
        ["board", "analyze", "ampacity", "--board", "a", "--board", "b"],
        ["board", "footprint", "place", "--board", "b", "--allow-routed", "--allow_routed=false"],
        ["board", "footprint", "place", "--board", "b", "--no-verify=maybe"],
        ["board", "footprint", "place", "--board", "b", "--no-verify", "maybe"],
        ["board", "footprint", "place", "--board", "b", "--dry-run", "--confirm", "ct_stale"],
        [
            "board",
            "footprint",
            "place",
            "--board",
            "b",
            "--dry-run",
            "true",
            "--confirm",
            "ct_stale",
        ],
        ["board", "footprint", "place", "--board", "b", "--dry-run=false", "--confirm", "ct_stale"],
        ["reference", "--confirm", "ct_stale"],
        ["reference", "--dry-run"],
        ["reference", "--json", "--format", "text"],
        ["reference", "--format", "xml"],
        ["reference", "--fields="],
        ["board", "footprint", "place", "--", "--board", "b"],
        ["board", "footprint", "place", "--board", "b", "--"],
        ["board", "route", "unknown", "--board", "b"],
        ["board", "route", "rebuild", "--board", "b", "--nets", "GND"],
        ["board", "route", "rebuild", "--board", "b", "--ripup"],
        ["board", "route", "repair", "--board", "b", "--no-verify"],
        ["board", "route", "rewidth", "--board", "b", "--no-restore"],
    ],
)
def test_invalid_requests_never_reach_a_handler(argv, monkeypatch, capsys):
    doc, code, reached = invoke(argv, monkeypatch, capsys)
    assert reached == [], doc
    assert code == 2
    assert doc["error"]["code"] == "E_USAGE"
    assert doc["error"]["retryable"] is False
    assert "confirm_token" not in doc["error"]["details"]


@pytest.mark.parametrize("value", ["0", "-0.1", "nan", "inf", "-inf", "garbage"])
def test_invalid_step_is_rejected_before_any_expensive_sampling(value, monkeypatch, capsys):
    doc, code, reached = invoke(
        ["board", "analyze", "reference-plane", "--board", "b", "--step", value],
        monkeypatch,
        capsys,
    )
    assert code == 2 and not reached
    assert doc["error"]["details"]["param"] == "step"


def test_valid_defaults_and_typed_values_reach_the_handler(monkeypatch, capsys):
    _, code, reached = invoke(
        ["board", "analyze", "ampacity", "--board", "b", "--oz", "2"], monkeypatch, capsys
    )
    assert code == 0
    assert reached[0]["oz"] == 2.0
    assert reached[0]["dt"] == 10.0


def test_rewidth_target_order_is_preserved():
    from kicad_cli import driver_runtime

    with driver_runtime.create_cli() as cli:
        command = cli.commands[("board", "route", "rewidth")]
        params = cli._params(command, {"board": ["b"], "nets": ["B,A"]}, None)
        assert params["nets"] == ["B", "A"]


def test_unknown_option_names_the_command_accepted_options(monkeypatch, capsys):
    doc, code, reached = invoke(
        ["board", "route", "rewidth", "--board", "b", "--made-up", "GND"], monkeypatch, capsys
    )
    assert code == 2 and not reached
    assert "made-up" in str(doc["error"]["details"])
    assert "classes" in str(doc["error"]["details"])


def test_actual_process_rejects_dry_run_plus_confirm_without_dispatch_or_file_change(tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text("sentinel: do not modify", encoding="utf-8")
    trace = tmp_path / "dispatch.txt"
    env = dict(os.environ, KICAD_CLI_TRACE=str(trace), PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kicad_cli.main",
            "board",
            "widen",
            "--board",
            str(board),
            "--dry-run",
            "--confirm",
            "ct_stale",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=15,
    )
    doc = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert doc["error"]["code"] == "E_USAGE"
    assert not trace.exists()
    assert board.read_text(encoding="utf-8") == "sentinel: do not modify"


def test_double_dash_names_the_escape_hatch_it_is_not(monkeypatch, capsys):
    """`--` reads as "the rest are values" everywhere else, and cannot here.

    There are no positional values to hand it: the only positionals are command
    path segments, so taking `--` pushed the remaining options into the command
    path and reported them as an unknown command -- an error about the wrong
    thing, three steps from the cause.
    """
    doc, code, reached = invoke(
        ["board", "footprint", "place", "--", "--board", "b"], monkeypatch, capsys
    )
    assert reached == [] and code == 2
    assert doc["error"]["code"] == "E_USAGE"
    assert "--option=--value" in doc["error"]["details"]["hint"]


def test_the_named_escape_hatch_actually_works(monkeypatch, capsys):
    """A hint that does not work is worse than no hint."""
    _, code, reached = invoke(
        ["board", "footprint", "place", "--board=--odd-name.kicad_pcb"], monkeypatch, capsys
    )
    assert code == 0
    assert reached[0]["board"] == "--odd-name.kicad_pcb"
