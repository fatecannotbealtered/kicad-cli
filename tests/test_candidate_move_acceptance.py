"""Public final-EXE move contracts on new, explicitly owned saved-board copies.

These checks never fall back to create_cli.run or launch KiCad. They consume
saved bytes and the new operation store separately from the public receipt.
Installed-KiCad geometry, connectivity, DRC and full-board viewing are a
separate engineering acceptance, not an assertion made by this module.
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
from move_exe_transport import FinalMoveExe

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/sch_edit"
R1_UUID = "2d4aa815-63ab-5983-950d-99c5287f15a2"
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owned final-EXE acceptance")


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
def move_scene(tmp_path, record_property):
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if not binary:
        pytest.skip("Requires the explicitly frozen final EXE; source fallback is forbidden")
    project = tmp_path / "保存移动 中文 空格"
    shutil.copytree(FIXTURE, project, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    source_files = files(FIXTURE)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("KICAD", "PYTHON")) and k != "KIPRJMOD"
    }
    env.update(
        grant(tmp_path, scope_id="owned-final-exe-move"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        KICAD_CONFIG_HOME=str(tmp_path / "configuration"),
        KICAD_CLI_DRIVER_SDK_PATH=os.environ["KICAD_CLI_ACCEPT_DRIVER_SDK"],
    )
    Path(env["KICAD_CONFIG_HOME"]).mkdir()
    if "KICAD_CLI_ROOT" in os.environ:
        env["KICAD_CLI_ROOT"] = os.environ["KICAD_CLI_ROOT"]
    transport = FinalMoveExe(binary, tmp_path, env, tmp_path / "public-exe-calls", record_property)

    board = project / "edit.kicad_pcb"

    def args(moves="R1:101.25,100.5", *, target=None):
        return ["board", "footprint", "move", "--board", str(target or board), "--moves", moves]

    scene = SimpleNamespace(
        root=project,
        board=board,
        env=env,
        state=Path(env["KICAD_CLI_STATE_DIR"]),
        scope=Path(env["KICAD_CLI_SCOPE_FILE"]),
        call=transport.call,
        args=args,
    )
    yield scene
    assert files(FIXTURE) == source_files, "The original saved fixture changed"


def preview(s, args=None):
    before = files(s.root)
    code, result = s.call(*(args or s.args()), "--dry-run")
    assert code == 0 and result["ok"], result
    assert files(s.root) == before
    data = result["data"]
    plan = data["preview"]
    assert plan["can_apply"] and data["confirm_token"]
    assert plan["target"]["locator"] == s.board.resolve().as_uri()
    assert plan["base_snapshot"]["revision"] == digest(s.board)
    record = operation(store_rows(s.state), plan["operation_id"])
    assert len(store_rows(s.state)["operations"]) == 1
    assert record["status"] == "planned" and record["started_at"] is None
    assert json.loads(record["plan"]) == plan
    no_dispatch(store_rows(s.state))
    return data


def assert_refused(s, args, *, code, error, token=None, conflict=None):
    before = files(s.root)
    flags = ["--confirm", token] if token else ["--dry-run"]
    actual_code, result = s.call(*args, *flags)
    assert actual_code == code and not result["ok"] and result["error"]["code"] == error, result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    if conflict:
        assert result["error"]["details"]["conflict"] == conflict
    assert files(s.root) == before
    no_dispatch(store_rows(s.state))
    return result


TRANSLATIONS = [
    pytest.param("R1:101.25,100.5", {"R1": [101250000, 100500000]}, id="single"),
    pytest.param(
        "R1:101.25,100.5;R2:105.5,100.75",
        {"R1": [101250000, 100500000], "R2": [105500000, 100750000]},
        id="multiple",
    ),
    pytest.param("R1:100.0000019,100.5", {"R1": [100000001, 100500000]}, id="quantisation"),
    pytest.param("R1:100,100", {"R1": [100000000, 100000000]}, id="no-op"),
    pytest.param(
        " ; R1 : 101.25 , 100.5 ; ;", {"R1": [101250000, 100500000]}, id="spaces-empty-segments"
    ),
]


@pytest.mark.parametrize("moves,expected_nm", TRANSLATIONS)
def test_preview_confirm_saved_translation(move_scene, moves, expected_nm):
    s = move_scene
    before_files, raw = files(s.root), s.board.read_bytes()
    original = pose_rows(raw)
    assert not any(original[ref]["locked"] for ref in expected_nm)
    data = preview(s, s.args(moves))
    plan = data["preview"]
    assert [c["id"] for c in plan["changes"]] == [original[r]["id"] for r in sorted(expected_nm)]
    assert all(c["action"] == "translate" for c in plan["changes"])
    requested_mm = {}
    for part in moves.split(";"):
        if part.strip():
            ref, xy = part.split(":")
            requested_mm[ref.strip()] = [Decimal(v.strip()) for v in xy.split(",")]
    for change, ref in zip(plan["changes"], sorted(expected_nm), strict=True):
        prior = change["before"]
        assert prior["ref"] == ref and prior["id"] == original[ref]["id"]
        assert prior["position_nm"] == original[ref]["position_nm"]
        assert [Decimal(str(v)) * 1_000_000 for v in prior["position_mm"]] == [
            Decimal(v) for v in original[ref]["position_nm"]
        ]
        assert Decimal(str(prior["rotation_deg"])) == original[ref]["rotation_deg"]
        assert prior["layer"] == original[ref]["layer"]
        assert [Decimal(str(v)) for v in change["after"]["position_mm"]] == requested_mm[ref]
    feedback = plan["extensions"]["engineering_feedback"]
    assert feedback["geometry_check"] == "not_performed"
    assert feedback["board_contains_copper"] is True
    assert feedback["requires_visual_review"] and feedback["unsupported"]
    assert any("copper" in value.lower() for value in plan["unknowns"])
    code, result = s.call(
        *s.args(moves), "--confirm", data["confirm_token"], "--idempotency-key", "move-once"
    )
    assert code == 0 and result["ok"], result
    receipt = result["data"]["receipt"]
    assert receipt["operation_id"] == plan["operation_id"] and receipt["plan_id"] == plan["plan_id"]
    assert receipt["authorization_scope_id"] == "owned-final-exe-move"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["snapshot_before"]["revision"] == hashlib.sha256(raw).hexdigest()
    assert receipt["snapshot_after"]["revision"] == digest(s.board)
    assert receipt["extensions"]["units"] == {"position": "nm", "position_tolerance_nm": 1}
    assert receipt["extensions"]["source_after"]["completed"] is True
    assert receipt["extensions"]["source_after"]["input_sha256"] == digest(s.board)
    assert receipt["extensions"]["readback"]["snapshot"]["revision"] == digest(s.board)
    assert receipt["extensions"]["failures"] == []
    for unchecked in ("DRC", "connectivity", "manufacturing"):
        assert unchecked in receipt["extensions"]["coverage"]["not_checked"]
    saved = pose_rows(s.board.read_bytes())
    for ref, original_pose in original.items():
        wanted = dict(original_pose, position_nm=expected_nm.get(ref, original_pose["position_nm"]))
        assert saved[ref] == wanted
    assert {
        row["ref"]: row["position_nm"] for row in receipt["extensions"]["observed"]
    } == expected_nm
    assert protected_bytes(raw, expected_nm) == protected_bytes(s.board.read_bytes(), expected_nm)
    assert {k: v for k, v in files(s.root).items() if k != s.board.name} == {
        k: v for k, v in before_files.items() if k != s.board.name
    }
    expected_change = "not_applied" if moves == "R1:100,100" else "applied"
    assert receipt["outcome"]["change"] == expected_change
    assert receipt["outcome"]["persistence"] == (
        "unchanged" if expected_change == "not_applied" else "saved"
    )
    rows = store_rows(s.state)
    record = operation(rows, plan["operation_id"])
    assert record["status"] == "succeeded" and json.loads(record["receipt"]) == receipt
    assert record["started_at"] is not None and record["finished_at"] is not None
    assert len(rows["consumed_tokens"]) == 1
    assert rows["scope_usage"] == [{"scope_id": "owned-final-exe-move", "used": 1}]
    current = files(s.root)
    code, shown = s.call("operation", "show", "--id", plan["operation_id"])
    assert code == 0 and shown["data"]["operation_status"]["status"] == "succeeded"
    assert shown["data"]["operation_status"]["receipt"] == receipt and files(s.root) == current


@pytest.mark.parametrize("parameter", ["board", "moves"])
def test_missing_required_parameter(move_scene, parameter):
    s = move_scene
    args = s.args()
    index = args.index("--" + parameter)
    del args[index : index + 2]
    assert_refused(s, args, code=2, error="E_USAGE")


@pytest.mark.parametrize("parameter", ["board", "moves"])
def test_repeated_scalar_parameter_refuses(move_scene, parameter):
    s = move_scene
    value = str(s.board) if parameter == "board" else "R1:101.25,100.5"
    assert_refused(s, [*s.args(), "--" + parameter, value], code=2, error="E_USAGE")


@pytest.mark.parametrize("defect", ["missing-file", "wrong-kind", "malformed-board"])
def test_board_input_refusals(move_scene, defect):
    s = move_scene
    target = s.root / "absent.kicad_pcb"
    if defect == "wrong-kind":
        target = s.root / "edit.kicad_sch"
    elif defect == "malformed-board":
        target = s.root / "malformed.kicad_pcb"
        target.write_bytes(b"(kicad_pcb (version 20260206) (footprint")
    assert_refused(
        s,
        s.args(target=target),
        code=3 if defect == "missing-file" else 2,
        error="E_NOT_FOUND" if defect == "missing-file" else "E_VALIDATION",
    )


INVALID_MOVES = [
    pytest.param("", id="empty"),
    pytest.param(":1,2", id="empty-reference"),
    pytest.param("R1:1", id="missing-coordinate"),
    pytest.param("R1:1,2,90", id="rotation-coordinate"),
    pytest.param("R1:1mm,2", id="coordinate-unit-suffix"),
    pytest.param("R1:nan,0", id="nan"),
    pytest.param("R1:inf,0", id="positive-infinity"),
    pytest.param("R1:1e308,0", id="nanometre-overflow"),
    pytest.param("R1:1,2;R1:3,4", id="duplicate-reference"),
]


@pytest.mark.parametrize("moves", INVALID_MOVES)
def test_invalid_move_input_refuses_without_write(move_scene, moves):
    assert_refused(move_scene, move_scene.args(moves), code=2, error="E_VALIDATION")


@pytest.mark.parametrize(
    "defect", ["absent", "case-sensitive", "ambiguous-reference", "duplicate-uuid"]
)
def test_exact_reference_and_identity_boundaries(move_scene, defect):
    s = move_scene
    moves = "R1:101.25,100.5"
    if defect == "absent":
        moves = "ABSENT:101.25,100.5"
    elif defect == "case-sensitive":
        moves = "r1:101.25,100.5"
    elif defect == "ambiguous-reference":
        s.board.write_bytes(s.board.read_bytes().replace(b'"R2"', b'"R1"'))
    else:
        other = pose_rows(s.board.read_bytes())["R2"]["id"]
        s.board.write_bytes(s.board.read_bytes().replace(other.encode(), R1_UUID.encode()))
    invalid = defect in {"ambiguous-reference", "duplicate-uuid"}
    assert_refused(
        s,
        s.args(moves),
        code=2 if invalid else 3,
        error="E_VALIDATION" if invalid else "E_NOT_FOUND",
    )


@pytest.mark.parametrize(
    "flag,value",
    [("--rotate", "90"), ("--layer", "B.Cu"), ("--unlock", None)],
    ids=["rotation", "layer-change", "unlock"],
)
def test_unsupported_move_options_refuse(move_scene, flag, value):
    extra = [flag] + ([value] if value else [])
    assert_refused(move_scene, [*move_scene.args(), *extra], code=2, error="E_USAGE")


@pytest.mark.parametrize("condition", ["locked", "outside-board", "overlapping-footprint"])
def test_preview_discloses_unchecked_protection_limits(move_scene, condition):
    """Accepted previews are evidence of absent checks, not safe-placement passes."""
    s = move_scene
    moves = "R1:101.25,100.5"
    if condition == "locked":
        raw = s.board.read_bytes()
        _text, rows = footprint_nodes(raw)
        insert = rows["R1"].one("layer").end
        text = raw.decode("utf-8")
        s.board.write_text(
            text[:insert] + "\n\t\t(locked yes)" + text[insert:], encoding="utf-8", newline=""
        )
        assert pose_rows(s.board.read_bytes())["R1"]["locked"]
    elif condition == "outside-board":
        moves = "R1:-1000,-1000"
    else:
        xy = pose_rows(s.board.read_bytes())["R2"]["position_nm"]
        moves = "R1:" + ",".join(str(Decimal(v) / 1_000_000) for v in xy)
    data = preview(s, s.args(moves))
    feedback = data["preview"]["extensions"]["engineering_feedback"]
    assert feedback["geometry_check"] == "not_performed"
    assert feedback["requires_visual_review"]
    no_dispatch(store_rows(s.state))


@pytest.mark.parametrize(
    "drift",
    [
        "board-path",
        "coordinates",
        "board-bytes",
        "footprint-uuid",
        "scope-content",
        "scope-revoked",
        "scope-root",
    ],
)
def test_confirmation_drift_refuses_before_write(move_scene, drift):
    s = move_scene
    data = preview(s)
    args, token = s.args(), data["confirm_token"]
    error, code = "E_CONFLICT", 6
    conflict = {
        "board-path": "token_mismatch",
        "coordinates": "token_mismatch",
        "board-bytes": "state_drift",
        "footprint-uuid": "state_drift",
        "scope-content": "scope_changed",
        "scope-root": "scope_changed",
        "scope-revoked": None,
    }[drift]
    if drift == "board-path":
        other = s.root / "other.kicad_pcb"
        shutil.copyfile(s.board, other)
        args = s.args(target=other)
    elif drift == "coordinates":
        args = s.args("R1:102.25,100.5")
    elif drift == "board-bytes":
        s.board.write_bytes(s.board.read_bytes() + b"\n")
    elif drift == "footprint-uuid":
        s.board.write_bytes(
            s.board.read_bytes().replace(R1_UUID.encode(), b"9d4aa815-63ab-5983-950d-99c5287f15a2")
        )
    else:
        scope = json.loads(s.scope.read_bytes())
        if drift == "scope-content":
            scope["notes"] += " changed after preview"
        elif drift == "scope-revoked":
            scope["revoked"] = True
        elif drift == "scope-root":
            scope["roots"] = [str(s.root / "unauthorized")]
        s.scope.write_text(json.dumps(scope), encoding="utf-8")
        if drift == "scope-revoked":
            error, code = "E_FORBIDDEN", 4
    assert_refused(s, args, code=code, error=error, token=token, conflict=conflict)
    record = operation(store_rows(s.state), data["preview"]["operation_id"])
    assert record["status"] == "planned" and record["receipt"] is None


def test_absent_authorization_does_not_plan_or_write(move_scene):
    s = move_scene
    s.scope.unlink()
    assert_refused(s, s.args(), code=4, error="E_FORBIDDEN")
    assert store_rows(s.state)["operations"] == []


def test_native_project_lock_is_busy_and_preserved(move_scene):
    s = move_scene
    lock = s.root / "~edit.kicad_pro.lck"
    lock.write_text('{"hostname":"owned fixture host","username":"test"}', encoding="utf-8")
    result = assert_refused(s, s.args(), code=7, error="E_BUSY")
    assert result["error"]["retryable"] is True
    assert result["error"]["details"]["lock_files"] == [lock.name]
    assert lock.is_file()


def saved_once(s):
    data = preview(s)
    code, result = s.call(
        *s.args(), "--confirm", data["confirm_token"], "--idempotency-key", "one-move"
    )
    assert code == 0 and result["ok"], result
    assert result["data"]["receipt"]["outcome"]["persistence"] == "saved"
    assert pose_rows(s.board.read_bytes())["R1"]["position_nm"] == [101250000, 100500000]
    return data, result["data"]["receipt"]


@pytest.mark.parametrize(
    "key", [None, "one-move", "other-key"], ids=["without-key", "same-key", "different-key"]
)
def test_spent_move_token_refuses_without_new_write(move_scene, key):
    """Move currently requires a fresh plan token for keyed receipt retrieval."""
    s = move_scene
    data, _receipt = saved_once(s)
    before, state = files(s.root), store_rows(s.state)
    flags = ["--idempotency-key", key] if key else []
    code, refused = s.call(*s.args(), "--confirm", data["confirm_token"], *flags)
    assert code == 6 and not refused["ok"] and refused["error"]["code"] == "E_CONFLICT", refused
    assert refused["error"]["details"]["conflict"] == "token_used"
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == before and store_rows(s.state) == state


def test_fresh_plan_same_key_returns_receipt_without_new_engineering_write(move_scene):
    s = move_scene
    original, receipt = saved_once(s)
    before = files(s.root)
    # preview()'s no-dispatch check deliberately applies only before an initial
    # operation. This second preview follows one known completed operation.
    code, second = s.call(*s.args(), "--dry-run")
    assert code == 0 and second["ok"] and files(s.root) == before
    next_plan = second["data"]["preview"]
    assert next_plan["operation_id"] != original["preview"]["operation_id"]
    before_state = store_rows(s.state)
    code, replay = s.call(
        *s.args(), "--confirm", second["data"]["confirm_token"], "--idempotency-key", "one-move"
    )
    assert code == 0 and replay["ok"], replay
    assert replay["data"]["receipt"] == dict(receipt, replayed=True, request_id="one-move")
    assert files(s.root) == before
    after = store_rows(s.state)
    prior = operation(before_state, receipt["operation_id"])
    assert operation(after, receipt["operation_id"]) == prior
    superseded = operation(after, next_plan["operation_id"])
    assert superseded["status"] == "failed" and superseded["started_at"] is None
    assert json.loads(superseded["extra"]) == {
        "stage": "superseded_by_replay",
        "dispatch_state": "not_started",
        "replay_of": receipt["operation_id"],
    }
    assert len(after["operations"]) == len(before_state["operations"]) == 2
    assert len(after["consumed_tokens"]) == len(before_state["consumed_tokens"]) + 1
    assert (
        after["scope_usage"]
        == before_state["scope_usage"]
        == [{"scope_id": "owned-final-exe-move", "used": 1}]
    )


def test_reused_key_changed_request_refuses_before_new_write(move_scene):
    s = move_scene
    _original, receipt = saved_once(s)
    args = s.args("R1:102.25,100.5")
    preview_before = files(s.root)
    code, planned = s.call(*args, "--dry-run")
    assert code == 0 and planned["ok"] and files(s.root) == preview_before
    before, state = files(s.root), store_rows(s.state)
    code, refused = s.call(
        *args, "--confirm", planned["data"]["confirm_token"], "--idempotency-key", "one-move"
    )
    assert code == 6 and not refused["ok"] and refused["error"]["code"] == "E_CONFLICT"
    assert refused["error"]["details"]["conflict"] == "idempotency_mismatch"
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == before and store_rows(s.state) == state
    assert operation(state, receipt["operation_id"])["status"] == "succeeded"
