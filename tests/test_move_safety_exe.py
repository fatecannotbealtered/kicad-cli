"""Frozen public-EXE safety cases; no source-driver or tested-reader fallback.

PREDECLARED CASES: locked [modern, legacy-bare, mixed] refuse before native;
R1:98.5,100 is the fixed blocked short/mask/open negative, never a write;
R4:99.5,110 is the separately declared positive, independently frozen by root.
Drift [lock, file, project-rules, dru-added, configuration, candidate, report,
library] rejects an original passing preview before engineering write and
without another native invocation. Spent move token [without-key, same-key,
different-key] refuses; fresh token + original key replays the original receipt.
Request [parameters, path, scope-content, scope-revoked] refuses before dispatch.
Invalid explicit official dependency refuses without starting a native call.
An explicitly frozen legacy 21c EXE may issue one preview in this case's new
store; that fresh legacy token must refuse on the new EXE without dispatch.

All source input, method, expected variant IDs and the explicitly selected new
EXE must be frozen by the v3 controller before running. Only disposable new
scope/state/config directories are opened. The native consumers launched by the
public preview are counted separately. These tests provide independent lexical
protection/pose and own-store evidence; full native saved readback and visual
acceptance are separate root-controlled engineering requirements.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from driver_test_utils import grant
from move_safety_transport import SafetyMoveExe

R1_UUID = "2d4aa815-63ab-5983-950d-99c5287f15a2"
R1_PAD1 = "b6399d66-3ccf-5183-bd56-8dee1659878f"
R1_PAD2 = "4af096ca-59e1-5018-a93c-c3aaa5cd3957"
SIG_TRACK = "4b209b2e-134d-5ded-a039-51cc9692dac5"
BASELINE_SHA = "59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f"
POSITIVE = "R4:99.5,110"
POSITIVE_INTENT_SHA = "334317ceb7164d3c2015c98e7cff013a8c61890676ed706700559f60f425a952"
NEGATIVE = "R1:98.5,100"
SOURCE_FILES = {
    "edit.kicad_pcb",
    "edit.kicad_sch",
    "edit_child.kicad_sch",
    "edit.kicad_pro",
    "fp-lib-table",
    "fixture.pretty/R_0603.kicad_mod",
    "fixture.pretty/R_0805.kicad_mod",
    "fixture.pretty/SO8.kicad_mod",
}
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owned final-EXE safety acceptance")

CASE_EXPECTATIONS = {
    "test_fresh_legacy_artifact_token_cannot_authorize_new_artifact": [None],
    "test_public_reference_exposes_move_preflight_schema": [None],
    "test_locked_selection_refuses_before_native": ["modern", "legacy-bare", "late-bare", "mixed"],
    "test_ambiguous_lock_encoding_refuses_before_native": [None],
    "test_fixed_r1_short_mask_and_disconnect_block_before_save": [None],
    "test_declared_positive_saves_only_r4_translation": [None],
    "test_frozen_preflight_drift_refuses_without_recheck": [
        "lock",
        "file",
        "project-rules",
        "dru-added",
        "configuration",
        "candidate",
        "report",
        "library",
    ],
    "test_spent_move_confirmation_never_dispatches_twice": [
        "without-key",
        "same-key",
        "different-key",
    ],
    "test_fresh_token_and_original_key_retrieve_receipt_without_writing": [None],
    "test_request_and_authorization_drift_refuse": [
        "parameters",
        "path",
        "scope-content",
        "scope-revoked",
    ],
    "test_invalid_official_dependency_is_incomplete_without_native": [None],
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def files(root):
    """Hash every engineering file and retain write metadata, including same-byte rewrites."""
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = {
                "sha256": digest(path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
    return result


@dataclass
class Expression:
    start: int
    end: int = -1
    values: list = field(default_factory=list)

    @property
    def name(self):
        return self.values[0] if self.values and isinstance(self.values[0], str) else None

    def children(self, name):
        return [v for v in self.values if isinstance(v, Expression) and v.name == name]

    def one(self, name):
        matches = self.children(name)
        assert len(matches) == 1, (self.name, name, len(matches))
        return matches[0]


def expressions(raw):
    """Method-local span reader; imports none of the tested writer/readback code."""
    text, stack, roots, consumed = raw.decode("utf-8"), [], [], 0
    for match in TOKENS.finditer(text):
        assert not text[consumed : match.start()].strip()
        consumed = match.end()
        token = match[0]
        if token == "(":
            item = Expression(match.start())
            (stack[-1].values if stack else roots).append(item)
            stack.append(item)
        elif token == ")":
            assert stack
            stack.pop().end = match.end()
        else:
            assert stack
            # These fixtures use JSON-compatible quoted atoms. Reject another
            # encoding rather than silently interpreting an unsupported escape.
            stack[-1].values.append(json.loads(token) if token.startswith('"') else token)
    assert not stack and len(roots) == 1 and not text[consumed:].strip()
    assert roots[0].name == "kicad_pcb"
    return text, roots[0]


def footprint_nodes(raw):
    text, root = expressions(raw)
    rows = {}
    for fp in root.children("footprint"):
        refs = [p.values[2] for p in fp.children("property") if p.values[1] == "Reference"]
        assert len(refs) == 1 and refs[0] not in rows
        rows[refs[0]] = fp
    return text, rows


def pose_rows(raw):
    _text, rows = footprint_nodes(raw)
    observed = {}
    for ref, fp in rows.items():
        at = fp.one("at").values
        nm = [Decimal(value) * 1_000_000 for value in at[1:3]]
        assert all(v == v.to_integral_value() for v in nm)
        locks = fp.children("locked")
        observed[ref] = {
            "id": fp.one("uuid").values[1],
            "position_nm": [int(v) for v in nm],
            "rotation_deg": Decimal(at[3]) if len(at) > 3 else Decimal(0),
            "layer": fp.one("layer").values[1],
            "path": fp.one("path").values[1],
            "locked": bool(locks and locks[0].values[1:] == ["yes"]),
        }
    return observed


def protected_bytes(raw, references):
    """Mask only each selected footprint's direct pose; every other byte remains."""
    text, rows = footprint_nodes(raw)
    spans = [rows[ref].one("at") for ref in references]
    for item in sorted(spans, key=lambda v: v.start, reverse=True):
        text = text[: item.start] + "(selected-pose)" + text[item.end :]
    return text.encode("utf-8")


def store_rows(state):
    """Read only this test's declared new SQLite store, without creating a store."""
    database = state / "operations.sqlite3"
    if not database.exists():
        return {"operations": [], "consumed_tokens": [], "scope_usage": []}
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        result = {}
        for name in ("operations", "consumed_tokens", "scope_usage"):
            result[name] = [dict(row) for row in db.execute("SELECT * FROM " + name)]
    return result


def operation(rows, identifier):
    matches = [r for r in rows["operations"] if r["operation_id"] == identifier]
    assert len(matches) == 1
    return matches[0]


def no_dispatch(rows):
    assert not rows["consumed_tokens"] and not rows["scope_usage"]
    assert all(
        row["status"] == "planned" and row["started_at"] is None for row in rows["operations"]
    )


@pytest.fixture
def safety_scene(tmp_path, record_property):
    required = (
        "KICAD_CLI_TEST_BINARY",
        "KICAD_CLI_MOVE_SAFETY_BINARY_SHA256",
        "KICAD_CLI_MOVE_SAFETY_INPUT",
        "KICAD_CLI_ACCEPT_DRIVER_SDK",
        "KICAD_CLI_ROOT",
    )
    assert all(os.environ.get(key) for key in required), "Frozen safety inputs are required"
    source = Path(os.environ["KICAD_CLI_MOVE_SAFETY_INPUT"])
    assert source.is_absolute() and source.is_dir() and not source.is_symlink()
    original = files(source)
    assert set(original) == SOURCE_FILES
    assert digest(source / "edit.kicad_pcb") == BASELINE_SHA
    record_property("move_safety_input_sha256", BASELINE_SHA)
    record_property("move_safety_positive_intent_sha256", POSITIVE_INTENT_SHA)
    project = tmp_path / "安全移动 中文 空格"
    shutil.copytree(source, project)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("KICAD", "PYTHON")) and key != "KIPRJMOD"
    }
    config = tmp_path / "configuration"
    config.mkdir()
    native = Path(os.environ["KICAD_CLI_ROOT"]).resolve(strict=True)
    official = Path(os.environ.get("KICAD_CLI_OFFICIAL", str(native / "bin/kicad-cli.exe")))
    assert official.is_absolute() and official.is_file()
    env.update(
        grant(tmp_path, scope_id="owned-move-safety-exe"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        KICAD_CONFIG_HOME=str(config),
        KICAD_CLI_ROOT=str(native),
        KICAD_CLI_OFFICIAL=str(official),
        KICAD_CLI_DRIVER_SDK_PATH=os.environ["KICAD_CLI_ACCEPT_DRIVER_SDK"],
    )
    transport = SafetyMoveExe(
        os.environ["KICAD_CLI_TEST_BINARY"],
        tmp_path,
        env,
        tmp_path / "public-exe-calls",
        record_property,
    )
    scene = SimpleNamespace(
        root=project,
        board=project / "edit.kicad_pcb",
        env=transport.env,
        config=config,
        state=Path(env["KICAD_CLI_STATE_DIR"]),
        scope=Path(env["KICAD_CLI_SCOPE_FILE"]),
        transport=transport,
        call=transport.call,
    )
    scene.args = lambda moves=POSITIVE, target=None: [
        "board",
        "footprint",
        "move",
        "--board",
        str(target or scene.board),
        "--moves",
        moves,
    ]
    yield scene
    assert files(source) == original, "The declared original saved source changed"
    record_property("move_safety_exe_calls", len(transport.calls))
    record_property(
        "move_safety_direct_pe_processes",
        sum(len(c["direct_pe_processes"]) for c in transport.calls),
    )
    record_property(
        "move_safety_official_native_calls",
        sum(len(c["official_native_processes"]) for c in transport.calls),
    )


def native_calls(s):
    return sum(len(row["official_native_processes"]) for row in s.transport.calls)


def locked_bytes(raw, ref, encoding):
    text, rows = footprint_nodes(raw)
    fp = rows[ref]
    assert not fp.children("locked") and "locked" not in fp.values
    insertion = " (locked yes)" if encoding == "modern" else " locked"
    if encoding == "late-bare":
        point = fp.end - 1
        return (text[:point] + " locked" + text[point:]).encode("utf-8")
    # Insert after the footprint library atom; a legacy bare lock stays a
    # direct footprint atom instead of accidentally locking a nested child.
    found = re.match(r'\(footprint\s+"(?:\\.|[^"\\])*"', text[fp.start : fp.end])
    assert found
    point = fp.start + found.end()
    return (text[:point] + insertion + text[point:]).encode("utf-8")


def fresh_preview(s, *, moves=POSITIVE, expected="passed"):
    before, config_before, calls = files(s.root), files(s.config), native_calls(s)
    code, value = s.call(*s.args(moves), "--dry-run")
    assert code == 0 and value["ok"], value
    assert files(s.root) == before
    assert files(s.config) == config_before
    data, plan = value["data"], value["data"]["preview"]
    preflight = plan["extensions"]["preflight"]
    assert preflight["status"] == expected
    assert plan["can_apply"] is (expected == "passed")
    assert preflight["source"]["official_native"] is True
    assert preflight["source"]["kind"] == "official_native"
    assert preflight["source"]["version"] == "10.0.6"
    connection = preflight["comparison"]["connection_changes"]
    assert connection["blocking_new_count"] == len(connection["blocking_new"])
    aliases = connection["equivalent_anchor_replacements"]
    assert connection["equivalent_anchor_replacement_count"] == len(aliases)
    if expected == "passed":
        assert connection["blocking_new"] == []
        assert len(connection["reported_connection_edges"]["new"]) == len(aliases)
    for alias in aliases:
        assert alias["before"] in connection["reported_connection_edges"]["removed"]
        assert alias["after"] in connection["reported_connection_edges"]["new"]
        proof = alias["proof"]
        assert proof["net_disjoint_from_moved_pad_nets"] is True
        assert proof["net"] not in proof["moved_pad_nets"]
        assert proof["reported_position_version"] == "10.0.6"
        assert proof["old_reported_position_nm"] == next(
            item["reported_position_nm"]
            for item in alias["before"]["items"]
            if item["uuid"] == proof["old_anchor_uuid"]
        )
        assert proof["new_reported_position_nm"] == next(
            item["reported_position_nm"]
            for item in alias["after"]["items"]
            if item["uuid"] == proof["new_anchor_uuid"]
        )
        assert proof["junction_position_nm"] == proof["anchor_nm"]
        assert proof["unchanged_objects"]
        assert all(
            row["before_sha256"] == row["after_sha256"]
            and row["geometry_and_machine_metadata_equal"] is True
            for row in proof["unchanged_objects"]
        )
    assert preflight["baseline_sha256"] == digest(s.board)
    assert (
        digest(preflight["staging"]["stages"]["candidate"]["board"])
        == preflight["candidate_sha256"]
    )
    assert preflight["engineering_write_started"] is False
    assert preflight["artifacts"]
    assert (
        plan["extensions"]["engineering_feedback"]["geometry_check"]
        == "official_native_drc_limited"
    )
    assert plan["extensions"]["engineering_feedback"]["can_apply"] is (expected == "passed")
    assert plan["extensions"]["engineering_feedback"]["copper_follows_footprint"] is False
    assert native_calls(s) - calls == 4, "Version + preparation + baseline + candidate, no retry"
    if expected == "passed":
        assert data["confirm_token"]
        assert not preflight["comparison"]["new_findings"]
        assert not preflight["comparison"]["connection_changes"]["blocking_new"]
    return data


def refused_confirm(s, data, *, args=None, error="E_CONFLICT", code=6):
    before, calls = files(s.root), native_calls(s)
    rows = store_rows(s.state)
    actual, result = s.call(*(args or s.args()), "--confirm", data["confirm_token"])
    assert actual == code and not result["ok"] and result["error"]["code"] == error, result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == before
    assert native_calls(s) == calls, "Old confirmation must not silently recheck a new candidate"
    assert store_rows(s.state) == rows
    no_dispatch(rows)
    return result


def test_public_reference_exposes_move_preflight_schema(safety_scene):
    s = safety_scene
    before = files(s.root)
    code, result = s.call("reference", "--command", "board footprint move")
    assert code == 0 and result["ok"], result
    uri = "kicad-cli://schemas/move-preflight/v1"
    assert result["data"]["command"]["preflight_schema"] == uri
    schema = result["data"]["json_schemas"][uri]
    assert schema["properties"]["source"]["properties"]["official_native"]["const"] is True
    assert set(schema["required"]) >= {
        "source",
        "formal",
        "configuration",
        "comparison",
        "artifacts",
    }
    assert files(s.root) == before and native_calls(s) == 0


def test_fresh_legacy_artifact_token_cannot_authorize_new_artifact(safety_scene, record_property):
    """One new legacy preview only; never reuse any historical store or token."""
    from move_exe_transport import ARTIFACT_SHA256, FinalMoveExe

    s = safety_scene
    legacy_binary = Path(os.environ["KICAD_CLI_MOVE_LEGACY_BINARY"])
    assert legacy_binary.is_absolute() and legacy_binary.is_file()
    assert digest(legacy_binary) == ARTIFACT_SHA256
    assert s.transport.artifact_sha256 != ARTIFACT_SHA256
    before = files(s.root)
    old_output = s.root.parent / "legacy-public-exe-calls"
    legacy = FinalMoveExe(
        legacy_binary,
        s.root.parent,
        s.env,
        old_output,
        lambda name, value: record_property("legacy_" + name, value),
    )
    code, preview = legacy.call(*s.args(), "--dry-run")
    assert code == 0 and preview["ok"] and preview["data"]["confirm_token"], preview
    assert files(s.root) == before
    assert preview["data"]["preview"]["can_apply"] is True
    assert "preflight" not in preview["data"]["preview"]["extensions"]
    trace = json.loads((old_output / "001/TRACE.json").read_bytes())
    legacy_pe = [row for row in trace["processes"] if row["image_sha256"] == ARTIFACT_SHA256]
    native = [
        row
        for row in trace["processes"]
        if row["image_sha256"] != ARTIFACT_SHA256
        and Path(row["image"]).name.casefold() == "kicad-cli.exe"
    ]
    assert legacy_pe and not native
    record_property("legacy_move_safety_exe_calls", 1)
    record_property("legacy_move_safety_direct_pe_processes", len(legacy_pe))
    record_property("legacy_move_safety_official_native_calls", 0)
    rows = store_rows(s.state)
    no_dispatch(rows)
    assert len(rows["operations"]) == 1
    refused = refused_confirm(s, preview["data"])
    assert refused["error"]["details"]["conflict"] == "token_mismatch"
    assert store_rows(s.state) == rows and native_calls(s) == 0


@pytest.mark.parametrize("encoding", ["modern", "legacy-bare", "late-bare", "mixed"])
def test_locked_selection_refuses_before_native(safety_scene, encoding):
    s = safety_scene
    s.board.write_bytes(locked_bytes(s.board.read_bytes(), "R1", encoding))
    before = files(s.root)
    moves = NEGATIVE if encoding != "mixed" else NEGATIVE + ";" + POSITIVE
    code, result = s.call(*s.args(moves), "--dry-run")
    assert code == 2 and not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    details = result["error"]["details"]
    assert details["dispatch_state"] == "not_started" and details["reason"] == "locked_footprint"
    assert details["locked_targets"] == [
        {"ref": "R1", "id": R1_UUID, "identity_kind": "uuid", "locked": True}
    ]
    assert files(s.root) == before and native_calls(s) == 0
    assert not (s.state / "move-preflights").exists()
    no_dispatch(store_rows(s.state))


def test_ambiguous_lock_encoding_refuses_before_native(safety_scene):
    s = safety_scene
    raw = locked_bytes(s.board.read_bytes(), "R1", "modern")
    assert raw.count(b"(locked yes)") == 1
    s.board.write_bytes(raw.replace(b"(locked yes)", b"(locked no)(locked yes)", 1))
    before = files(s.root)
    code, result = s.call(*s.args(NEGATIVE), "--dry-run")
    assert code == 2 and not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    assert result["error"]["details"]["reason"] == "ambiguous_lock_encoding"
    assert result["error"]["details"]["targets"] == [
        {"ref": "R1", "id": R1_UUID, "identity_kind": "uuid", "locked": None}
    ]
    assert files(s.root) == before and native_calls(s) == 0
    assert not (s.state / "move-preflights").exists()
    no_dispatch(store_rows(s.state))


def test_fixed_r1_short_mask_and_disconnect_block_before_save(safety_scene):
    s = safety_scene
    data = fresh_preview(s, moves=NEGATIVE, expected="blocked")
    risk = data["preview"]["extensions"]["preflight"]["comparison"]
    findings = {row["type"]: row for row in risk["new_findings"]}
    assert {"shorting_items", "solder_mask_bridge"} <= findings.keys()
    for name in ("shorting_items", "solder_mask_bridge"):
        items = {row["uuid"]: row for row in findings[name]["items"]}
        assert set(items) == {R1_PAD2, SIG_TRACK}
        assert items[R1_PAD2]["object"]["ref"] == "R1"
        assert items[R1_PAD2]["object"]["number"] == "2"
        assert items[R1_PAD2]["object"]["net"] == "/OUT"
        assert items[SIG_TRACK]["object"]["net"] == "/SIG"
        assert items[R1_PAD2]["reported_position_nm"] == [99412500, 100000000]
    edges = risk["connection_changes"]["reported_connection_edges"]
    assert any(
        {item["uuid"] for item in row["items"]} == {R1_PAD1, SIG_TRACK} for row in edges["new"]
    )
    assert risk["connection_changes"]["complete_connectivity_graph"] is False
    assert risk["ignored_checks"] and risk["not_checked"]
    assert data["confirm_token"], "A blocked preview token still must never dispatch"
    refused_confirm(s, data, args=s.args(NEGATIVE))
    assert digest(s.board) == BASELINE_SHA


def saved_positive(s):
    before_files, raw = files(s.root), s.board.read_bytes()
    prior = pose_rows(raw)
    assert prior["R4"]["position_nm"] == [100000000, 110000000]
    assert not prior["R4"]["locked"]
    data = fresh_preview(s)
    preflight = data["preview"]["extensions"]["preflight"]
    expected_counts = {"violations": 0, "unconnected_items": 7, "schematic_parity": 0}
    assert preflight["comparison"]["counts"]["before"] == expected_counts
    assert preflight["comparison"]["counts"]["after"] == expected_counts
    candidate = Path(preflight["staging"]["stages"]["candidate"]["board"]).read_bytes()
    assert protected_bytes(candidate, ["R4"]) == protected_bytes(raw, ["R4"])
    expected = dict(prior)
    expected["R4"] = dict(prior["R4"], position_nm=[99500000, 110000000])
    assert pose_rows(candidate) == expected
    calls = native_calls(s)
    code, result = s.call(
        *s.args(), "--confirm", data["confirm_token"], "--idempotency-key", "one-safe-move"
    )
    assert code == 0 and result["ok"], result
    assert native_calls(s) == calls
    receipt = result["data"]["receipt"]
    assert receipt["operation_id"] == data["preview"]["operation_id"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "saved"
    assert s.board.read_bytes() == candidate and pose_rows(s.board.read_bytes()) == expected
    after = files(s.root)
    assert after.keys() == before_files.keys()
    assert all(after[name] == row for name, row in before_files.items() if name != "edit.kicad_pcb")
    record = operation(store_rows(s.state), receipt["operation_id"])
    assert record["status"] == "succeeded" and record["started_at"]
    assert json.loads(record["receipt"]) == receipt
    return data, receipt


def test_declared_positive_saves_only_r4_translation(safety_scene):
    s = safety_scene
    _data, receipt = saved_positive(s)
    before = files(s.root)
    code, query = s.call("operation", "show", "--id", receipt["operation_id"])
    assert code == 0 and query["ok"]
    status = query["data"]["operation_status"]
    assert status["status"] == "succeeded" and status["receipt"] == receipt
    assert files(s.root) == before
    assert pose_rows(s.board.read_bytes())["R4"]["position_nm"] == [99500000, 110000000]


@pytest.mark.parametrize(
    "drift",
    [
        "lock",
        "file",
        "project-rules",
        "dru-added",
        "configuration",
        "candidate",
        "report",
        "library",
    ],
)
def test_frozen_preflight_drift_refuses_without_recheck(safety_scene, drift):
    s = safety_scene
    data = fresh_preview(s)
    preflight = data["preview"]["extensions"]["preflight"]
    if drift == "lock":
        s.board.write_bytes(locked_bytes(s.board.read_bytes(), "R4", "modern"))
    elif drift == "file":
        s.board.write_bytes(s.board.read_bytes() + b"\n")
    elif drift == "project-rules":
        path = s.board.with_suffix(".kicad_pro")
        value = json.loads(path.read_bytes())
        value.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {})[
            "min_clearance"
        ] = 0.333
        path.write_text(json.dumps(value), encoding="utf-8")
    elif drift == "dru-added":
        path = s.board.with_suffix(".kicad_dru")
        assert not path.exists()
        path.write_text(
            '(version 1)\n(rule "new rule" (constraint clearance (min 0.4mm)))\n', encoding="utf-8"
        )
    elif drift == "configuration":
        path = s.config / "10.0/kicad_common.json"
        assert not path.exists(), "Initialization must only touch declared preparation copies"
        path.parent.mkdir()
        value = {"environment": {"vars": {"MOVE_SAFETY_CHANGED": "after-preview"}}}
        path.write_text(json.dumps(value), encoding="utf-8")
    elif drift == "candidate":
        path = Path(preflight["staging"]["stages"]["candidate"]["board"])
        path.write_bytes(path.read_bytes() + b"\n")
    elif drift == "report":
        path = Path(preflight["formal"]["candidate"]["report"])
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        path = s.root / "fixture.pretty/R_0805.kicad_mod"
        path.write_bytes(path.read_bytes() + b"\n")
    refused_confirm(s, data)


@pytest.mark.parametrize(
    "key", [None, "one-safe-move", "other"], ids=["without-key", "same-key", "different-key"]
)
def test_spent_move_confirmation_never_dispatches_twice(safety_scene, key):
    s = safety_scene
    data, _receipt = saved_positive(s)
    before, state, calls = files(s.root), store_rows(s.state), native_calls(s)
    flags = ["--idempotency-key", key] if key else []
    code, value = s.call(*s.args(), "--confirm", data["confirm_token"], *flags)
    assert code == 6 and not value["ok"] and value["error"]["code"] == "E_CONFLICT"
    assert value["error"]["details"]["conflict"] == "token_used"
    assert value["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == before and store_rows(s.state) == state and native_calls(s) == calls


def test_fresh_token_and_original_key_retrieve_receipt_without_writing(safety_scene):
    s = safety_scene
    original, receipt = saved_positive(s)
    data = fresh_preview(s)
    assert data["preview"]["operation_id"] != original["preview"]["operation_id"]
    before, state, calls = files(s.root), store_rows(s.state), native_calls(s)
    code, replay = s.call(
        *s.args(), "--confirm", data["confirm_token"], "--idempotency-key", "one-safe-move"
    )
    assert code == 0 and replay["ok"], replay
    assert replay["data"]["receipt"] == dict(receipt, replayed=True, request_id="one-safe-move")
    assert files(s.root) == before and native_calls(s) == calls
    after = store_rows(s.state)
    assert operation(after, receipt["operation_id"]) == operation(state, receipt["operation_id"])
    superseded = operation(after, data["preview"]["operation_id"])
    assert superseded["status"] == "failed" and superseded["started_at"] is None
    assert json.loads(superseded["extra"])["stage"] == "superseded_by_replay"
    assert (
        after["scope_usage"]
        == state["scope_usage"]
        == [{"scope_id": "owned-move-safety-exe", "used": 1}]
    )
    assert len(after["consumed_tokens"]) == len(state["consumed_tokens"]) + 1


@pytest.mark.parametrize("drift", ["parameters", "path", "scope-content", "scope-revoked"])
def test_request_and_authorization_drift_refuse(safety_scene, drift):
    s = safety_scene
    data = fresh_preview(s)
    args, error, code = s.args(), "E_CONFLICT", 6
    if drift == "parameters":
        args = s.args("R4:99.75,110")
    elif drift == "path":
        path = s.root / "another.kicad_pcb"
        shutil.copyfile(s.board, path)
        args = s.args(target=path)
    else:
        value = json.loads(s.scope.read_bytes())
        value["notes"] += " changed after preview"
        if drift == "scope-revoked":
            value["revoked"] = True
            error, code = "E_FORBIDDEN", 4
        s.scope.write_text(json.dumps(value), encoding="utf-8")
    refused_confirm(s, data, args=args, error=error, code=code)


def test_invalid_official_dependency_is_incomplete_without_native(safety_scene):
    s = safety_scene
    s.env["KICAD_CLI_OFFICIAL"] = str(s.config / "missing-official.exe")
    before = files(s.root)
    code, result = s.call(*s.args(), "--dry-run")
    assert code == 4 and not result["ok"] and result["error"]["code"] == "E_CONFIG", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == before and native_calls(s) == 0
    no_dispatch(store_rows(s.state))
