"""Public command removal contract; fixtures are owned synthetic board extras."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from driver_test_utils import grant

from kicad_cli import sync_readback
from kicad_cli.fileformat.sexpr import Document, copy

ROOT = Path(__file__).resolve().parents[1]


def quote(value):
    return json.dumps(value)


def selection(board):
    fp = next(item for item in sync_readback.observe(board).objects if item["ref"] == "TP1")
    return {
        "schematic_root_uuid": Document.load(board.with_suffix(".kicad_sch"))
        .root.find("uuid")
        .value(1),
        "schematic_sha256": hashlib.sha256(
            board.with_suffix(".kicad_sch").read_bytes()
        ).hexdigest(),
        "reference": fp["ref"],
        "pcb_uuid": fp["id"],
        "schematic_path": fp["path"],
        "footprint": fp["fpid"],
        "saved_semantics_sha256": fp["saved_semantics_sha256"],
        "pads": [{"uuid": p["id"], "number": p["number"], "net": p["net"]} for p in fp["pads"]],
        "expect_schematic_absent": True,
    }


def invoke(scene, args, *, shim="", future=False):
    env, board, request = scene
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    assert not binary or not shim, "Fault injection is source-only; final EXE recovery is separate"
    if future:
        from test_document_cleanup_runtime import FUTURE

        shim = FUTURE + shim
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    if shim:
        prefix = [
            sys.executable,
            "-B",
            "-c",
            shim + "\nfrom kicad_cli.main import main; main(__import__('sys').argv[1:])",
        ]
    process = subprocess.run(
        [*prefix, *args, "--compact"],
        cwd=board.parent if binary else ROOT,
        env=env,
        capture_output=True,
        timeout=120,
    )
    return process, json.loads(process.stdout) if process.stdout else None


def sync(scene, *flags, removal=True, **kwargs):
    _, board, request = scene
    args = [
        "project",
        "sync",
        "--board",
        str(board),
        "--schematic",
        str(board.with_suffix(".kicad_sch")),
    ]
    if removal:
        args += ["--remove-footprint", str(request)]
    return invoke(scene, [*args, *flags], **kwargs)


@pytest.fixture
def scene(tmp_path, record_property):
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        record_property(
            "fcc_artifact_sha256", hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        )
    shutil.copytree(
        ROOT / "tests/fixtures/sch_edit",
        tmp_path,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.py", "__pycache__"),
    )
    board = tmp_path / "edit.kicad_pcb"
    document = Document.load(board)
    template = next(
        node for node in document.root.find_all("footprint") if node.find("path") is not None
    )
    extra = copy(template)
    for node in extra.walk("uuid"):
        node.set(1, quote(str(uuid.uuid4())))
    extra.find("path").set(1, quote("/" + str(uuid.uuid4())))
    for prop in extra.find_all("property"):
        if prop.value(1) == "Reference":
            prop.set(2, quote("TP1"))
    extra.find("at").set(1, "200")
    extra.find("at").set(2, "200")
    for pad in extra.find_all("pad"):
        net = pad.find("net")
        if net is not None:
            pad.remove(net)
    document.root.append(extra)
    document.save(board)
    request = tmp_path / "remove.json"
    request.write_text(json.dumps(selection(board)), encoding="utf-8")
    env = dict(
        os.environ,
        **grant(tmp_path),
        KICAD_CONFIG_HOME=str(tmp_path / "configuration"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env, board, request


def test_explicit_remove_preserves_unselected_objects_and_default_retains(scene):
    env, board, request = scene
    original = board.read_bytes()
    before = sync_readback.observe(board)
    process, retained = sync(scene, "--dry-run", removal=False)
    assert process.returncode == 0 and retained["ok"], retained
    assert (
        "TP1"
        in retained["data"]["preview"]["extensions"]["support_boundary"][
            "retained_unmatched_references"
        ]
    )
    process, planned = sync(scene, "--dry-run")
    assert process.returncode == 0 and planned["ok"], planned
    assert board.read_bytes() == original
    preview = planned["data"]["preview"]
    assert len(preview["changes"]) == 1 and preview["changes"][0]["action"] == "delete"
    assert not any(
        row["ref"] == "TP1" for row in preview["extensions"]["rows"].get("not_removed", [])
    )
    assert preview["extensions"]["rows"]["removed"] == [
        {
            "ref": "TP1",
            "id": preview["changes"][0]["id"],
            "why": "Explicit identity-bound isolated-footprint selection",
        }
    ]
    assert preview["extensions"]["removal"]["copper_impact"]["contacts"] == []
    process, result = sync(scene, "--confirm", planned["data"]["confirm_token"])
    assert process.returncode == 0 and result["ok"], result
    assert result["data"]["receipt"]["outcome"]["verification"]["status"] == "passed"
    assert result["data"]["receipt"]["extensions"]["rows"] == preview["extensions"]["rows"]
    after = sync_readback.observe(board)
    assert after.objects == [p for p in before.objects if p["ref"] != "TP1"]
    assert after.protected_sha256 == before.protected_sha256
    saved = board.read_bytes()
    process, replay = sync(scene, "--confirm", planned["data"]["confirm_token"])
    assert process.returncode != 0 and not replay["ok"]
    assert board.read_bytes() == saved


@pytest.mark.parametrize(
    "fault",
    [
        "unauthorized",
        "wrong-uuid",
        "wrong-root",
        "board-only",
        "same-ref-other-object",
        "wrong-pad",
        "locked",
        "source-present",
        "connected-net",
        "unknown-field",
        "missing-field",
        "track-contact",
    ],
)
def test_explicit_removal_refusals_do_not_write(scene, fault):
    env, board, request = scene
    chosen = json.loads(request.read_bytes())
    if fault == "unauthorized":
        scope = json.loads(Path(env["KICAD_CLI_SCOPE_FILE"]).read_bytes())
        scope["effects"] = ["observe"]
        Path(env["KICAD_CLI_SCOPE_FILE"]).write_text(json.dumps(scope))
    elif fault == "wrong-uuid":
        chosen["pcb_uuid"] = str(uuid.uuid4())
    elif fault == "wrong-root":
        chosen["schematic_root_uuid"] = str(uuid.uuid4())
    elif fault == "wrong-pad":
        chosen["pads"][0]["uuid"] = str(uuid.uuid4())
    elif fault == "unknown-field":
        chosen["delete_all"] = True
    elif fault == "missing-field":
        del chosen["schematic_path"]
    elif fault == "source-present":
        original = next(p for p in sync_readback.observe(board).objects if p["ref"] == "R1")
        chosen.update(
            reference=original["ref"],
            pcb_uuid=original["id"],
            schematic_path=original["path"],
            footprint=original["fpid"],
            saved_semantics_sha256=original["saved_semantics_sha256"],
            pads=[
                {"uuid": p["id"], "number": p["number"], "net": p["net"]} for p in original["pads"]
            ],
        )
    else:
        document = Document.load(board)
        fp = next(
            node
            for node in document.root.find_all("footprint")
            if node.find("uuid").value(1) == chosen["pcb_uuid"]
        )
        if fault == "locked":
            fp.append(copy(Document.parse("(locked yes)").root))
        elif fault == "board-only":
            fp.find("attr").append("board_only")
        elif fault == "same-ref-other-object":
            fp.find("uuid").set(1, quote(str(uuid.uuid4())))
        elif fault == "connected-net":
            fp.find_all("pad")[0].append(copy(Document.parse('(net "GND")').root))
        elif fault == "track-contact":
            document.root.append(
                copy(
                    Document.parse(
                        "(segment (start 199 200) (end 201 200) (width 0.25) "
                        '(layer "F.Cu") (net 0) (uuid "' + str(uuid.uuid4()) + '"))'
                    ).root
                )
            )
        document.save(board)
        if fault != "same-ref-other-object":
            chosen = selection(board)
    request.write_text(json.dumps(chosen))
    before = {p: p.read_bytes() for p in board.parent.glob("*.kicad_*")}
    process, result = sync(scene, "--dry-run")
    assert process.returncode != 0 and not result["ok"], result
    assert result["error"]["code"] in {"E_FORBIDDEN", "E_VALIDATION"}, result
    assert result["error"]["details"]["dispatch_state"] == "not_started", result
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("fault", ["selection", "board", "authorization"])
def test_removal_confirmation_rechecks_inputs_and_scope(scene, fault):
    env, board, request = scene
    process, planned = sync(scene, "--dry-run")
    assert process.returncode == 0, planned
    if fault == "authorization":
        path = Path(env["KICAD_CLI_SCOPE_FILE"])
        scope = json.loads(path.read_bytes())
        scope["effects"] = ["observe"]
        path.write_text(json.dumps(scope))
    else:
        path = board if fault == "board" else request
        path.write_bytes(path.read_bytes() + b"\n")
    actual = board.read_bytes()
    process, refused = sync(scene, "--confirm", planned["data"]["confirm_token"])
    assert process.returncode != 0 and refused["error"]["code"] in {"E_CONFLICT", "E_FORBIDDEN"}
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert board.read_bytes() == actual


@pytest.mark.skipif(
    bool(os.environ.get("KICAD_CLI_TEST_BINARY")),
    reason="Source fault injection; EXE recovery separately executed",
)
@pytest.mark.parametrize(
    "fault", ["save-exit", "cleanup-exit", "cleanup-error", "reply-exit", "resolve-cleanup-exit"]
)
def test_removal_lost_reply_cleanup_and_resolution_never_rewrite_board(scene, fault):
    env, board, request = scene
    before = board.read_bytes()
    process, planned = sync(scene, "--dry-run")
    assert process.returncode == 0, planned
    operation = planned["data"]["preview"]["operation_id"]
    cleanup_hook = (
        "from pathlib import Path; import os; original_unlink=Path.unlink;\n"
        "def cut(path,*args,**kwargs):\n"
        " result=original_unlink(path,*args,**kwargs)\n"
        " if path.name.endswith('.backup'):\n"
        + (
            "  raise PermissionError('synthetic delete failure after deletion')\n"
            if fault == "cleanup-error"
            else "  os._exit(92)\n"
        )
        + " return result\nPath.unlink=cut\n"
    )
    if fault in {"save-exit", "resolve-cleanup-exit"}:
        shim = (
            "from kicad_cli.fileformat.sexpr import Document; import os; save=Document.save;\n"
            "def cut(self,path):\n save(self,path)\n os._exit(91)\nDocument.save=cut\n"
        )
    elif fault == "reply-exit":
        shim = (
            "from kicad_cli.sync_driver_adapter import SyncAdapter; import os; "
            "apply=SyncAdapter.apply;\ndef cut(self,*args):\n result=apply(self,*args)\n "
            "os._exit(93)\nSyncAdapter.apply=cut\n"
        )
    else:
        shim = cleanup_hook
    process, result = sync(scene, "--confirm", planned["data"]["confirm_token"], shim=shim)
    assert process.returncode != 0
    if fault == "cleanup-error":
        assert result["error"]["code"] == "E_PARTIAL", result
        assert result["error"]["details"]["write_state"] == "applied", result
        assert result["error"]["details"]["receipt"]["outcome"]["persistence"] == "saved", result
    saved = board.read_bytes()
    assert saved != before and not any(
        p["ref"] == "TP1" for p in sync_readback.observe(board).objects
    )
    for attempt in range(2 if fault == "resolve-cleanup-exit" else 1):
        process, shown = invoke(scene, ["operation", "show", "--id", operation], future=True)
        assert process.returncode == 0, shown
        actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
        assert actual["completion"] == "completed" and actual["outcome"] == "applied", actual
        assert actual["cleanup"]["status"] == "attributable", actual
        resolve_args = [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            actual["evidence_file"],
        ]
        process, resolved = invoke(
            scene,
            resolve_args,
            future=True,
            shim=cleanup_hook if fault == "resolve-cleanup-exit" and attempt == 0 else "",
        )
        if fault == "resolve-cleanup-exit" and attempt == 0:
            assert process.returncode == 92
        else:
            assert process.returncode == 0 and resolved["ok"], resolved
        assert board.read_bytes() == saved
    archives = list(
        (Path(env["KICAD_CLI_STATE_DIR"]) / "recovery" / operation).glob("baseline-*.kicad_pcb")
    )
    assert any(path.read_bytes() == before for path in archives), (
        "Original baseline must remain archived"
    )
    process, repeated = invoke(scene, resolve_args, future=True)
    assert process.returncode != 0 and not repeated["ok"]
    assert board.read_bytes() == saved
