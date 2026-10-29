"""Actual fixed-file checker observations and command-level rollback counterexamples."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kicad_cli import __version__, drc_baseline, errors, registry
from kicad_cli.fileformat import drc

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests/fixtures/drc_baseline"
OLD = "11111111-1111-4111-8111-111111111111"
NEW = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "test.kicad_pcb"
    shutil.copyfile(FIXTURES / "before.kicad_pcb", path)
    shutil.copyfile(FIXTURES / "track-width-only.kicad_pro", path.with_suffix(".kicad_pro"))
    return path


def observed_pair(path):
    before_count, before = drc_baseline.capture(path)
    shutil.copyfile(FIXTURES / "replacement.kicad_pcb", path)
    after_count, after = drc_baseline.capture(path)
    assert (before_count, after_count) == (1, 1)
    return before, after


def test_equal_count_replacement_is_a_new_actual_finding(board):
    before, after = observed_pair(board)
    result = drc_baseline.compare(before, after)
    assert result["status"] == "failed", result
    assert len(result["new_findings"]) == len(result["removed_findings"]) == 1
    assert result["new_findings"][0]["identity"]["items"][0]["uuid"] == NEW
    assert result["removed_findings"][0]["identity"]["items"][0]["uuid"] == OLD
    assert (
        result["final"]["provenance"]["input"]["sha256"]
        == hashlib.sha256(board.read_bytes()).hexdigest()
    )


def test_lower_count_does_not_hide_a_new_finding(board):
    before, after = observed_pair(board)
    before.violations.append(copy.deepcopy(before.violations[0]))
    assert len(before.violations) == 2 > len(after.violations)
    assert drc_baseline.compare(before, after)["status"] == "failed"


def test_repeated_identity_is_compared_as_a_multiset(board):
    _, report = drc_baseline.capture(board)
    doubled = copy.deepcopy(report)
    doubled.violations.append(copy.deepcopy(report.violations[0]))
    result = drc_baseline.compare(report, doubled)
    assert result["status"] == "failed"
    assert result["new_findings"][0]["occurrences"] == 1


def test_item_order_and_display_text_are_not_finding_identity(board):
    _, report = drc_baseline.capture(board)
    report.violations[0].items.append(
        drc.Item("pad", NEW, "Pad named by user", (12000000, 10000000))
    )
    changed = copy.deepcopy(report)
    changed.violations[0].message = "Different rendering of the same rule measurement"
    changed.violations[0].items.reverse()
    assert drc_baseline.compare(report, changed)["status"] == "passed"


def test_position_changes_under_the_same_uuid_are_new_findings(board):
    _, report = drc_baseline.capture(board)
    changed = copy.deepcopy(report)
    item = changed.violations[0].items[0]
    changed.violations[0].items = [
        drc.Item(item.kind, item.uuid, item.description, (11000000, 10000000))
    ]
    assert drc_baseline.compare(report, changed)["status"] == "failed"


def test_excluded_and_warning_findings_are_retained_but_outside_error_gate(board):
    _, report = drc_baseline.capture(board)
    changed = copy.deepcopy(report)
    changed.violations.extend(
        [
            drc.Violation(
                "clearance",
                "error",
                "excluded new item",
                [drc.Item("track", NEW, "Untrusted board text", (10000000, 10000000))],
                excluded=True,
                comment="user exclusion",
            ),
            drc.Violation("text_height", "warning", "warning only", []),
        ]
    )
    result = drc_baseline.compare(report, changed)
    assert result["status"] == "passed"
    assert len(result["final"]["violations"]) == 3
    assert result["final"]["violations"][1]["comment"] == "user exclusion"


@pytest.mark.parametrize("missing", ["uuid", "items", "source", "version", "completion"])
def test_missing_identity_or_source_cannot_be_a_pass(board, missing):
    _, report = drc_baseline.capture(board)
    changed = copy.deepcopy(report)
    if missing == "uuid":
        item = changed.violations[0].items[0]
        changed.violations[0].items = [drc.Item(item.kind, "", item.description, item.position)]
    elif missing == "items":
        changed.violations[0].items = []
    elif missing == "source":
        changed.provenance = {}
    elif missing == "version":
        changed.provenance["source"].pop("version")
    else:
        changed.provenance["source"]["completion"] = "timeout"
    assert drc_baseline.compare(report, changed)["status"] == "incomplete"


@pytest.mark.parametrize("field", ["rules", "dependencies", "coverage"])
def test_rule_dependency_or_coverage_changes_invalidate_comparison(board, field):
    _, report = drc_baseline.capture(board)
    changed = copy.deepcopy(report)
    changed.provenance[field] = {"different": True}
    assert drc_baseline.compare(report, changed)["status"] == "incomplete"


def test_custom_checker_cannot_be_promoted_to_native_by_a_label(board):
    _, report = drc_baseline.capture(board)
    changed = copy.deepcopy(report)
    changed.provenance["source"]["kind"] = "native_checker"
    assert drc_baseline.compare(report, changed)["status"] == "incomplete"
    assert report.provenance["source"]["official_native"] is False


def test_unstructured_custom_rule_origin_cannot_hide_a_replacement_error(board):
    _, report = drc_baseline.capture(board)
    report.custom_rules = {"read": True, "rules": 2}
    changed = copy.deepcopy(report)
    report.violations[0].message = "first custom rule on this track"
    changed.violations[0].message = "second custom rule on this track"
    result = drc_baseline.compare(report, changed)
    assert result["status"] == "incomplete"
    assert any("custom rule names/origins" in reason for reason in result["incomplete_reasons"])


def test_malformed_present_project_does_not_fall_back_to_defaults(board):
    board.with_suffix(".kicad_pro").write_text("{bad json", encoding="utf-8")
    with pytest.raises(ValueError):
        drc_baseline.capture(board)


def test_input_drift_while_checker_runs_is_incomplete(board, monkeypatch):
    real = drc.check

    def drifting(*args, **kwargs):
        report = real(*args, **kwargs)
        board.write_bytes(board.read_bytes() + b"\n")
        return report

    monkeypatch.setattr(drc, "check", drifting)
    with pytest.raises(drc_baseline.Incomplete, match="changed while"):
        drc_baseline.capture(board)


def test_checker_read_failure_keeps_known_origin_input_and_unknown_completion(board, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("staged read denial")

    monkeypatch.setattr(drc, "check", denied)
    with pytest.raises(drc_baseline.Incomplete) as caught:
        drc_baseline.capture(board)
    provenance = caught.value.provenance
    assert provenance["source"]["version"] == __version__
    assert provenance["source"]["completion"] == "unknown"
    assert len(provenance["source"]["code_sha256"]) == 64
    assert provenance["input"]["sha256"] == hashlib.sha256(board.read_bytes()).hexdigest()
    assert provenance["coverage"] is None


def test_default_coverage_retains_unchecked_and_partial_rules(board):
    board.with_suffix(".kicad_pro").unlink()
    _, report = drc_baseline.capture(board)
    coverage = report.provenance["coverage"]
    assert "connection_width" in coverage["not_checked"]
    assert "clearance" in coverage["partial"]
    assert report.provenance["rules"]["project_policy"].startswith("explicit engine defaults")


def test_report_binds_actual_source_inputs_defaults_and_declared_coverage(board):
    _, report = drc_baseline.capture(board)
    provenance = report.provenance
    assert provenance["source"]["origin"] == "kicad_cli.fileformat.drc"
    assert provenance["source"]["kind"] == "custom_checker"
    assert provenance["source"]["version"] == __version__
    assert len(provenance["source"]["code_sha256"]) == 64
    assert provenance["source"]["completion"] == "completed"
    assert provenance["coverage"]["checked_rules"] == ["track_width"]
    assert provenance["coverage"]["whole_kicad_drc"] is False
    assert provenance["rules"]["minimums_mm"]["min_track_width"] == 0.2
    assert (
        provenance["dependencies"][0]["sha256"]
        == hashlib.sha256(board.with_suffix(".kicad_pro").read_bytes()).hexdigest()
    )
    assert provenance["dependencies"][1]["present"] is False


def run(path, *, mode="repair", token=None, fault="", command="route", extra=()):
    state = path.parent / "state"
    state.mkdir(exist_ok=True)
    env = dict(
        os.environ,
        KICAD_CLI_STRICT="1",
        KICAD_CLI_STATE=str(state),
        TEMP=str(state),
        TMP=str(state),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    task = "rewidth" if command == "rewidth" else "rebuild" if mode == "full" else mode
    # These fault probes preserve the existing domain algorithm/gate tests.
    # Public driver candidates run in a separate isolated child, so patching
    # the parent router cannot inject a public saved-file failure any more.
    # CLI-level actual effects/recovery are tested in test_driver_power_runtime.
    params = {"board": str(path), "mode": "full" if task == "rebuild" else task}
    if task == "rewidth":
        params["classes"] = "Wide"
    if token:
        params["confirm"] = token
    params.update({flag.removeprefix("--"): True for flag in extra})
    script = (
        textwrap.dedent(fault) + "\nimport json,sys\nfrom kicad_cli import envelope\n"
        "from kicad_cli.native import route\nenvelope.configure(compact=True)\n"
        "params=json.loads(sys.argv[1])\nroute.run(params['board'],params)"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", script, json.dumps(params)],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert "contract violation" not in proc.stderr.decode("utf-8"), proc.stderr
    result = json.loads(proc.stdout.decode("utf-8"))
    assert proc.returncode == (0 if result["ok"] else errors.exit_code(result["error"]["code"])), (
        result
    )
    return result


def substitute_saved_board():
    return f"""
from pathlib import Path
from kicad_cli.native import route
from kicad_cli.fileformat.board import Board
def save_replacement(self):
    route.write.begin(self.path)
    Path(self.path).write_bytes(Path({str(FIXTURES / "replacement.kicad_pcb")!r}).read_bytes())
    return Board.load(self.path)
route.Layout.fill_and_save = save_replacement
"""


@pytest.mark.parametrize("mode", ["full", "repair", "rewidth"])
def test_grid_modes_roll_back_equal_count_replacement_from_actual_saved_reports(board, mode):
    original = board.read_bytes()
    project = board.with_suffix(".kicad_pro").read_bytes()
    asked = run(board, mode=mode)
    result = run(
        board,
        mode=mode,
        token=asked["error"]["details"]["confirm_token"],
        fault=substitute_saved_board(),
    )
    assert result["error"]["code"] == "E_INTEGRITY", result
    details = result["error"]["details"]
    assert details["errors_baseline"] == details["errors_final"] == 1
    assert details["finding_comparison"]["status"] == "failed"
    assert details["write_state"] == "rolled_back"
    assert board.read_bytes() == original
    assert board.with_suffix(".kicad_pro").read_bytes() == project
    assert not list(board.parent.glob("*.kicad-cli.*"))


def test_domain_rewidth_also_uses_the_identity_gate(board):
    asked = run(board, command="rewidth")
    result = run(
        board,
        command="rewidth",
        token=asked["error"]["details"]["confirm_token"],
        fault=substitute_saved_board(),
    )
    assert result["error"]["code"] == "E_INTEGRITY", result
    assert (
        result["error"]["details"]["errors_baseline"] == result["error"]["details"]["errors_final"]
    )


def test_no_verify_is_not_reported_as_passed(board):
    asked = run(board, mode="rewidth", extra=["--no-verify"])
    result = run(
        board,
        mode="rewidth",
        extra=["--no-verify"],
        token=asked["error"]["details"]["confirm_token"],
        fault=substitute_saved_board(),
    )
    assert result["ok"], result
    assert result["data"]["verify"]["ran"] is False
    assert result["data"]["verify"]["finding_comparison"]["status"] == "not_run"


def test_identity_incomplete_after_save_rolls_back(board):
    original = board.read_bytes()
    asked = run(board)
    fault = (
        substitute_saved_board()
        + f"""
old_save = route.Layout.fill_and_save
def missing_id(self):
    board = old_save(self)
    path = Path(self.path)
    path.write_text(path.read_text(encoding='utf-8').replace({NEW!r}, ''), encoding='utf-8')
    return board
route.Layout.fill_and_save = missing_id
"""
    )
    result = run(board, token=asked["error"]["details"]["confirm_token"], fault=fault)
    assert result["error"]["code"] == "E_VERIFY_INCOMPLETE", result
    assert result["error"]["details"]["write_state"] == "rolled_back"
    assert board.read_bytes() == original


def test_unchanged_error_identity_passes_with_bounded_provenance(board):
    asked = run(board)
    result = run(board, token=asked["error"]["details"]["confirm_token"])
    assert result["ok"], result
    verify = result["data"]["verify"]
    assert verify["errors_baseline"] == verify["errors_final"] == 1
    assert verify["finding_comparison"]["status"] == "passed"
    assert verify["finding_comparison"]["final"]["provenance"]["source"]["kind"] == "custom_checker"
    assert "verify" in registry.SCHEMAS["board_route"]["untrusted_fields"]


def test_checker_failure_after_save_restores_and_retains_baseline(board):
    original = board.read_bytes()
    asked = run(board)
    fault = (
        substitute_saved_board()
        + """
from kicad_cli import drc_baseline
actual = drc_baseline.drc.check
def denied_after_saved(path, **kwargs):
    if Path(path).read_bytes() == Path("""
        + repr(str(FIXTURES / "replacement.kicad_pcb"))
        + """).read_bytes():
        raise PermissionError("staged checker denial after save")
    return actual(path, **kwargs)
drc_baseline.drc.check = denied_after_saved
"""
    )
    result = run(board, token=asked["error"]["details"]["confirm_token"], fault=fault)
    details = result["error"]["details"]
    assert result["error"]["code"] == "E_VERIFY_INCOMPLETE", result
    assert details["checker"]["source"]["completion"] == "unknown"
    assert (
        details["checker"]["input"]["sha256"]
        != details["baseline"]["provenance"]["input"]["sha256"]
    )
    assert details["write_state"] == "rolled_back"
    assert board.read_bytes() == original


def test_post_readback_input_drift_cannot_be_committed(board):
    original = board.read_bytes()
    asked = run(board)
    fault = """
from pathlib import Path
from kicad_cli import drc_baseline
actual = drc_baseline.require_saved_input
def drift(path, report):
    p = Path(path)
    p.write_bytes(p.read_bytes() + b"\\n")
    return actual(path, report)
drc_baseline.require_saved_input = drift
"""
    result = run(board, token=asked["error"]["details"]["confirm_token"], fault=fault)
    assert result["error"]["code"] == "E_VERIFY_INCOMPLETE", result
    assert result["error"]["details"]["finding_comparison"]["status"] == "incomplete"
    assert board.read_bytes() == original
