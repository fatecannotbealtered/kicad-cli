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

from kicad_cli import envelope, main, registry  # noqa: E402
from kicad_cli.commands import layout  # noqa: E402


def invoke(argv: list[str], monkeypatch, capsys) -> tuple[dict, int, list[dict]]:
    """Only replace business handlers; parsing and dispatch remain production code.

    These substitutes must not manufacture live FCC trace evidence.
    """
    commands = registry.build()
    reached = []

    def capture(opts):
        reached.append(opts)
        envelope.ok(opts)

    for command in commands:
        command["handler"] = capture
    monkeypatch.setattr(registry, "build", lambda: commands)
    monkeypatch.setattr(main, "_trace", lambda _path: None)
    monkeypatch.delenv("KICAD_CLI_STRICT", raising=False)
    with pytest.raises(SystemExit) as exc:
        main.main(argv)
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out), exc.value.code, reached


@pytest.mark.parametrize("value", ["false", "False", "0"])
@pytest.mark.parametrize("equals", [False, True])
def test_false_is_false_at_the_payload_boundary(value, equals, monkeypatch, capsys):
    flag = [f"--no-verify={value}"] if equals else ["--no-verify", value]
    doc, code, reached = invoke(
        ["board", "widen", "--board", "b.kicad_pcb", *flag], monkeypatch, capsys
    )
    assert code == 0 and doc["ok"]
    assert reached[0]["no-verify"] is False
    assert reached[0]["no_verify"] is False
    assert layout._flag(reached[0], "no-verify") == []


@pytest.mark.parametrize("flag", [["--no-verify"], ["--no-verify=true"], ["--no-verify", "1"]])
def test_true_booleans_are_forwarded(flag, monkeypatch, capsys):
    _, code, reached = invoke(["board", "widen", "--board", "b", *flag], monkeypatch, capsys)
    assert code == 0
    assert layout._flag(reached[0], "no-verify") == ["--no-verify"]


def test_boolean_before_command_does_not_swallow_the_command(monkeypatch, capsys):
    _, code, reached = invoke(["--compact", "reference", "--quiet", "false"], monkeypatch, capsys)
    assert code == 0
    assert reached[0]["compact"] is True
    assert reached[0]["quiet"] is False


def test_repeated_layers_accumulate_instead_of_silently_replacing(monkeypatch, capsys):
    _, code, reached = invoke(
        ["fab", "gerber", "--board", "b", "--layers", "F.Cu,B.Cu", "--layers", "Edge.Cuts"],
        monkeypatch,
        capsys,
    )
    assert code == 0
    assert reached[0]["layers"] == "F.Cu,B.Cu,Edge.Cuts"


@pytest.mark.parametrize(
    "argv",
    [
        ["reference", "extra"],
        ["board", "audit", "--board"],
        ["board", "audit"],
        ["board", "audit", "--board="],
        ["board", "move", "--board", "b"],
        ["sch", "audit"],
        ["board", "audit", "--board", "a", "--board", "b"],
        ["board", "widen", "--board", "b", "--ignore-lock", "--ignore_lock=false"],
        ["board", "widen", "--board", "b", "--no-verify=maybe"],
        ["board", "widen", "--board", "b", "--no-verify", "maybe"],
        ["board", "widen", "--board", "b", "--dry-run", "--confirm", "ct_stale"],
        ["board", "widen", "--board", "b", "--dry-run", "true", "--confirm", "ct_stale"],
        ["board", "widen", "--board", "b", "--dry-run=false", "--confirm", "ct_stale"],
        ["reference", "--confirm", "ct_stale"],
        ["reference", "--dry-run"],
        ["reference", "--json", "--format", "text"],
        ["reference", "--format", "xml"],
        ["reference", "--fields="],
        ["fab", "gerber", "--board", "b", "--layers", "F.Cu,,B.Cu"],
        ["board", "widen", "--", "--board", "b"],
        ["board", "widen", "--board", "b", "--"],
        ["board", "route", "--board", "b", "--mode", "unknown"],
        ["board", "route", "--board", "b", "--nets", "GND"],
        ["board", "route", "--board", "b", "--mode", "full", "--nets", "GND"],
        ["board", "route", "--board", "b", "--classes", "Power"],
        ["board", "route", "--board", "b", "--mode", "full", "--ripup"],
        ["board", "route", "--board", "b", "--mode", "repair", "--no-verify"],
        ["board", "route", "--board", "b", "--mode", "rewidth", "--no-restore"],
        ["board", "route", "--board", "b", "--mode", "rewidth", "--nets", "A", "--classes", "B"],
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
        ["board", "plane", "--board", "b", "--step", value], monkeypatch, capsys
    )
    assert code == 2 and not reached
    assert doc["error"]["details"]["param"] == "step"


def test_valid_defaults_and_typed_values_reach_the_handler(monkeypatch, capsys):
    _, code, reached = invoke(["board", "audit", "--board", "b", "--oz", "2"], monkeypatch, capsys)
    assert code == 0
    assert reached[0]["oz"] == 2.0
    assert reached[0]["dt"] == 10.0


def test_rewidth_target_order_is_preserved(monkeypatch, capsys):
    _, code, reached = invoke(
        ["board", "route", "--board", "b", "--mode", "rewidth", "--nets", "B,A"],
        monkeypatch,
        capsys,
    )
    assert code == 0
    assert reached[0]["nets"] == "B,A"


def test_unknown_option_names_the_command_accepted_options(monkeypatch, capsys):
    doc, code, reached = invoke(
        ["board", "rewidth", "--board", "b", "--nets", "GND"], monkeypatch, capsys
    )
    assert code == 2 and not reached
    assert doc["error"]["details"]["unknown"] == ["--nets"]
    assert "classes" in doc["error"]["details"]["accepted"]


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
    doc, code, reached = invoke(["board", "widen", "--", "--board", "b"], monkeypatch, capsys)
    assert reached == [] and code == 2
    assert doc["error"]["code"] == "E_USAGE"
    assert "--option=--value" in doc["error"]["details"]["hint"]


def test_the_named_escape_hatch_actually_works(monkeypatch, capsys):
    """A hint that does not work is worse than no hint."""
    _, code, reached = invoke(
        ["board", "widen", "--board=--odd-name.kicad_pcb"], monkeypatch, capsys
    )
    assert code == 0
    assert reached[0]["board"] == "--odd-name.kicad_pcb"
