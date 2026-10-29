"""Offline orchestration fixtures. No subprocess, product, Native or CAD runs.

Tiny synthetic strings exercise durable phase/guard behavior. Fixture check and
image rows are deliberately synthetic; they are never engineering evidence.
"""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.trial_tools import eco
from bench.trial_tools import preparation as core
from bench.trial_tools import trial as frontend
from bench.trial_tools.identity import IdentityError, capture
from bench.trial_tools.reading import (
    STEM,
    ident,
    load,
    validate_renderer_warmup,
    write_new,
)


def put(path: Path, content="synthetic") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class HarmlessRunner:
    def __init__(self):
        self.calls = []
        self.failure = False
        self.action = None

    def __call__(self, argv, *, cwd, env, out, timeout, ready):
        self.calls.append(list(argv))
        out.mkdir(exist_ok=False)
        common = Path(env["KICAD_CONFIG_HOME"]) / "10.0/kicad_common.json"
        if not common.exists():
            put(common, json.dumps({"system": {"working_dir": str(cwd)}, "synthetic": True}))
        if self.action:
            self.action(argv, ready)
        if self.failure:
            raise RuntimeError("synthetic lost reply")
        stdout = {"ok": True, "schema_version": "1.0", "data": {"status": "succeeded"}}
        if len(argv) > 3 and argv[1:3] == ["-B", "-S"]:
            stdout = {
                "schema": "physical-footprint-eco-native-read/v1",
                "native_version": "10.0.6",
                "input_unchanged": True,
                "project_unchanged": True,
                "synthetic": True,
            }
        put(out / "stdout", json.dumps(stdout))
        put(out / "stderr", "")
        if "--output" in argv:
            target = Path(argv[argv.index("--output") + 1])
            if target.suffix == ".json":
                put(
                    target,
                    '{"$schema":"https://schemas.kicad.org/fixture.json",'
                    '"violations":[],"synthetic":true}',
                )
            elif target.suffix == ".xml":
                put(target, '<export version="synthetic"><components/></export>')
            elif target.suffix == ".svg":
                put(target, '<svg xmlns="http://www.w3.org/2000/svg"/>')
            else:
                put(target / "fixture.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
        if any(Path(value).name == "svg_reading.py" for value in argv):
            raw = Path(argv[argv.index("--raw") + 1])
            derivative = Path(argv[argv.index("--derivative") + 1])
            proof = Path(argv[argv.index("--proof") + 1])
            output = Path(argv[argv.index("--out") + 1])
            if "derive" in argv:
                derivative.write_bytes(raw.read_bytes())
                write_new(
                    proof,
                    {
                        "synthetic": True,
                        "raw_sha256": ident(raw)["sha256"],
                        "derivative_sha256": ident(derivative)["sha256"],
                    },
                )
            write_new(
                output,
                {
                    "schema": "finite-PCB-reading-check/v1",
                    "status": "geometry_preserved_pending_actual_view",
                    "synthetic": True,
                    "raw_sha256": ident(raw)["sha256"],
                    "derivative_sha256": ident(derivative)["sha256"],
                    "all_original_elements_preserved": True,
                    "every_primitive_inside_reading_viewport": True,
                    "margin_mm": "2",
                    "geometry_or_opacity_edits": 0,
                    "actual_view_performed": False,
                },
            )
        if argv[-1] == "render-bound":
            plan_path = Path(argv[2])
            plan = load(plan_path)
            image = Path(plan["directory"]) / "images/warmup.png"

            def chunk(name, data):
                return (
                    struct.pack(">I", len(data))
                    + name
                    + data
                    + struct.pack(">I", zlib.crc32(name + data))
                )

            image.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"\0\xff\xff\xff\xff"))
                + chunk(b"IEND", b"")
            )
            view = plan["views"][0]
            write_new(
                Path(plan["directory"]) / "RESULT.json",
                {
                    "schema": "finite-sharp-host-renderer/v1",
                    "phase": "render-bound",
                    "synthetic": True,
                    "plan_sha256": ident(plan_path)["sha256"],
                    "node": plan["node"],
                    "method": ident(argv[1]),
                    "child_dispatches": 0,
                    "KiCad_Native_calls": 0,
                    "formal_engineering_writes": 0,
                    "outputs": [
                        {
                            "name": "warmup",
                            "native": view["native"],
                            "derivative": view["derivative"],
                            "independent_geometry_check": view["check"],
                            "layers": view["layers"],
                            "mirrored": False,
                            "output": ident(image),
                            "width": 1,
                            "height": 1,
                            "channels": 4,
                            "density": 576,
                        }
                    ],
                },
            )
        return {
            "trace": {
                "timed_out": False,
                "job_active_before_close": 0,
                "job_finalization": {
                    "natural_tree_complete": True,
                    "controlled_termination": False,
                    "control_events": [],
                },
            },
            "classification": {
                "observation_complete": True,
                "may_validate_functional_output": True,
                "stop_required": False,
            },
        }


@pytest.fixture
def trial_fixture(tmp_path):
    package = tmp_path / "package"
    for name in core.PROJECT_FILES:
        put(package / "physical-footprint-eco/starting-project" / name, "0805 synthetic " + name)
    methods = package / "physical-footprint-eco/methods"
    for name in (
        "saved_board_native_read.py",
        "requirements/BASELINE-INPUTS.json",
        "requirements/PHYSICAL-PIN-INTENT42.json",
    ):
        put(methods / name, "{}")
    for name in (
        "integration",
        "observer02_support",
        "observer02_metadata",
        "observer02_owned_process",
        "observer02_win_debugging",
        "observer02_native_observation",
        "observer02_process_tree",
    ):
        put(package / "trial_tools/resources/native-observer" / (name + ".py"), "# never executed")
    external = tmp_path / "external"
    dependencies = {
        name: str(put(external / (name + ".exe"), name))
        for name in ("product_exe", "official_cli", "native_python")
    }
    for name in ("sdk_root", "spec_root"):
        root = external / name
        put(root / "fixture.py", "# no imports")
        dependencies[name] = str(root)
    dependencies["host_python"] = dependencies["native_python"]
    reading = package / "trial_tools/resources/reading-tools"
    reading.mkdir(parents=True)
    source = Path(core.__file__).parent
    for name in ("render_pcb.cjs", "svg_reading.py"):
        shutil.copyfile(source / "resources/reading-tools" / name, reading / name)
    shutil.copyfile(source / "reading.py", package / "trial_tools/reading.py")
    node, sharp = (
        put(external / "node.exe", "synthetic Node"),
        put(external / "sharp.cjs", "synthetic Sharp"),
    )
    dependencies["dependency_files"] = [str(node), str(sharp)]
    runtime = {
        "schema": "trial-current-reading-runtime/v1",
        "node": ident(node),
        "sharp_entry": str(sharp),
        "runtime_files": [ident(sharp)],
        "system_files": [],
        "aliases": [],
        "synthetic": True,
    }
    write_new(
        reading / "pcb-qualification.json",
        {
            "method": ident(reading / "render_pcb.cjs"),
            "node": ident(node),
            "qualification": {"status": "passed_bounded_host_renderer_fixture"},
            "synthetic": True,
        },
    )
    configuration = {"renderer_warmup": True, "renderer_runtime": runtime}
    runner = HarmlessRunner()
    ready = core.prepare(package, tmp_path / "owned", dependencies, configuration, _runner=runner)
    trial = eco.Trial(ready.work_root, _runner=runner)
    trial.state.mkdir()
    for name in core.PROJECT_FILES:
        put(trial.work / "project" / name, "original synthetic " + name)
    put(trial.work / "scope.json", '{"synthetic":true}')
    put(trial.work / "intent/changes.json", '[{"synthetic":true}]')
    write_new(
        trial.work / "intent/PLAN.json", {"synthetic": True, "edit_expected_sha256": "unused"}
    )
    trial.context = core.freeze_context(
        ready,
        inputs={
            "project": trial.work / "project",
            "scope": trial.work / "scope.json",
            "intent": trial.work / "intent",
            "eco-method": Path(eco.__file__).absolute(),
        },
    )
    trial._save_context()
    runner.calls.clear()
    return trial, runner


def candidate_fixture(trial):
    candidate = put(trial.work / "review" / (STEM + ".kicad_pcb"), "synthetic candidate bytes")
    future = (datetime.now(timezone.utc) + timedelta(minutes=9)).isoformat().replace("+00:00", "Z")
    preview = {
        "ok": True,
        "data": {
            "expires_at": future,
            "confirm_token": "synthetic-authority",
            "preview": {"operation_id": "synthetic-original-operation", "can_apply": True},
        },
    }
    write_new(trial.state / "sync-preview.reply.json", preview)
    trial.bind("reply:sync-preview", trial.state / "sync-preview.reply.json")
    trial.bind("review:candidate", candidate)
    checks = {}
    for name in ("physical", "hole"):
        path = trial.work / "output/candidate/checks" / (name + ".json")
        write_new(path, {"status": "passed", "synthetic": True})
        trial.bind("check:" + name, path)
        checks[name] = ident(path)
    images = [
        ident(
            put(
                trial.work / "output/candidate/images" / (str(i) + ".png"),
                "synthetic image " + str(i),
            )
        )
        for i in range(7)
    ]
    for row in images:
        trial.bind(row["path"], row["path"])
    request_path = trial._review_request(
        "candidate", checks, images, "synthetic-original-operation", ident(candidate)
    )
    request = load(request_path)
    acknowledgment = {
        "schema": "trial-actual-view-ack/v1",
        "request_sha256": ident(request_path)["sha256"],
        "operation_id": request["operation_id"],
        "reviewer": "synthetic offline fixture; not actual human viewing",
        "statement": request["statement_required"],
        "verdict": "accepted",
        "images": [
            {"path": row["path"], "sha256": row["sha256"], "actual_viewed": True} for row in images
        ],
    }
    ack_path = trial.work / "synthetic-ack.json"
    write_new(ack_path, acknowledgment)
    return candidate, request_path, ack_path


def allow_synthetic_save(argv, ready):
    if "--confirm" in argv:
        source = ready.work_root / "review" / (STEM + ".kicad_pcb")
        (ready.work_root / "project" / source.name).write_bytes(source.read_bytes())


def test_one_confirmation_then_query_and_repeat_zero_processes(trial_fixture):
    trial, runner = trial_fixture
    candidate_fixture(trial)
    trial.review_record(trial.work / "synthetic-ack.json")
    runner.action = allow_synthetic_save
    first = trial.confirm()
    count = len(runner.calls)
    second = trial.confirm()
    assert first["status"] == "exact_candidate_saved"
    assert second["reused_checkpoint"] and second["new_processes"] == 0
    assert len(runner.calls) == count == 2
    assert sum("--confirm" in argv for argv in runner.calls) == 1
    assert runner.calls[1][-3:] == ["--id", "synthetic-original-operation", "--compact"]


def test_unknown_confirmation_cannot_replay_but_original_query_remains(trial_fixture):
    trial, runner = trial_fixture
    candidate_fixture(trial)
    trial.review_record(trial.work / "synthetic-ack.json")
    runner.action, runner.failure = allow_synthetic_save, True
    with pytest.raises(RuntimeError, match="lost reply"):
        trial.confirm()
    attempts = len(runner.calls)
    with pytest.raises(core.TrialError):
        trial.confirm()
    assert len(runner.calls) == attempts == 1
    runner.failure = False
    result = trial.query("sync")
    assert result["operation_id"] == "synthetic-original-operation"
    assert result["new_confirm_calls"] == 0 and not result["formal_context_advanced"]
    assert runner.calls[-1][1:3] == ["operation", "show"]
    assert sum("--confirm" in argv for argv in runner.calls) == 1


@pytest.mark.parametrize(
    "drift",
    [
        "new-project-file",
        "project-content",
        "config-new-file",
        "config-content",
        "approval",
        "candidate-missing",
    ],
)
def test_preconfirm_drift_refuses_without_any_process(trial_fixture, drift):
    trial, runner = trial_fixture
    candidate, _, ack = candidate_fixture(trial)
    trial.review_record(ack)
    if drift == "new-project-file":
        put(trial.work / "project/new.kicad_sch", "new undeclared file")
    elif drift == "project-content":
        put(trial.work / "project/channel_b.kicad_sch", "wrong protected bytes")
    elif drift == "config-new-file":
        put(Path(trial.ready.record["profiles"]["consumer"]["config"]) / "new.json", "{}")
    elif drift == "config-content":
        put(
            Path(trial.ready.record["profiles"]["consumer"]["config"]) / "10.0/kicad_common.json",
            '{"changed":true}',
        )
    elif drift == "approval":
        put(ack, '{"verdict":"replaced"}')
    else:
        candidate.unlink()
    with pytest.raises((core.TrialError, IdentityError)):
        trial.confirm()
    assert not runner.calls


def test_expired_preview_is_not_renewed_or_confirmed(trial_fixture, monkeypatch):
    trial, runner = trial_fixture
    candidate_fixture(trial)
    trial.review_record(trial.work / "synthetic-ack.json")
    # Clock injection exercises expiry without changing a frozen authority file.
    expired = load(trial.state / "sync-preview.reply.json")
    with pytest.raises(core.TrialError, match="expired"):
        eco.unexpired(expired, now=datetime.now(timezone.utc) + timedelta(hours=1))

    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=1)

    monkeypatch.setattr(eco, "datetime", LaterClock)
    with pytest.raises(core.TrialError, match="expired"):
        trial.confirm()
    assert not runner.calls


def test_wrong_check_result_cannot_authorize_confirm(trial_fixture):
    trial, runner = trial_fixture
    _, request, ack = candidate_fixture(trial)
    trial.review_record(ack)
    # A changed failed report is refused by the frozen guard before dispatch.
    put(Path(load(request)["checks"]["physical"]["path"]), '{"status":"rejected","synthetic":true}')
    with pytest.raises((core.TrialError, IdentityError)):
        trial.confirm()
    assert not runner.calls


@pytest.mark.parametrize(
    "change",
    [
        "pending",
        "false-view",
        "numeric-view",
        "wrong-image",
        "missing-image",
        "wrong-operation",
        "wrong-request",
    ],
)
def test_acknowledgment_requires_exact_explicit_actual_views(trial_fixture, change):
    trial, runner = trial_fixture
    _, request_path, ack_path = candidate_fixture(trial)
    request, ack = load(request_path), load(ack_path)
    if change == "pending":
        ack["verdict"] = "pending"
    elif change == "false-view":
        ack["images"][0]["actual_viewed"] = False
    elif change == "numeric-view":
        ack["images"][0]["actual_viewed"] = 1
    elif change == "wrong-image":
        ack["images"][0]["sha256"] = "0" * 64
    elif change == "missing-image":
        ack["images"].pop()
    elif change == "wrong-operation":
        ack["operation_id"] = "another operation"
    else:
        ack["request_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        eco.validate_acknowledgment(request, ident(request_path)["sha256"], ack)
    assert not runner.calls


def test_project_validator_rejects_extra_file_and_protected_replacement(tmp_path):
    root = tmp_path / "project"
    target, protected = put(root / "target", "old"), put(root / "protected", "fixed")
    before = capture(root)
    target.write_text("allowed", encoding="utf-8")
    assert eco.exact_one_file_change(before, capture(root), "target", eco.digest(b"allowed"))
    put(root / "new", "extra")
    assert not eco.exact_one_file_change(before, capture(root), "target", eco.digest(b"allowed"))
    (root / "new").unlink()
    replacement = put(tmp_path / "replacement", "fixed")
    replacement.replace(protected)
    assert not eco.exact_one_file_change(before, capture(root), "target", eco.digest(b"allowed"))


def test_guard_rejects_write_outside_exact_allowed_atom(trial_fixture):
    trial, runner = trial_fixture
    expected = eco.digest(b"declared")
    runner.action = lambda argv, ready: put(
        ready.work_root / "project/channel_b.kicad_sch", "unexpected"
    )

    def validator(before, after):
        return eco.exact_one_file_change(before, after, "channel_a.kicad_sch", expected)

    # The fixture validator source is deliberately frozen before dispatch.
    trial.bind("test-validator", Path(__file__).absolute())
    with pytest.raises(core.TrialError, match="outside"):
        trial.run(
            "synthetic-outside-write",
            [trial.dependencies["product_exe"]],
            profile="writer",
            allowed={"project": validator},
        )
    assert len(runner.calls) == 1 and trial.context.stopped


def test_review_phase_repeat_keeps_original_ack_and_zero_processes(trial_fixture):
    trial, runner = trial_fixture
    _, _, ack = candidate_fixture(trial)
    first = trial.review_record(ack)
    second = trial.review_record(ack)
    assert first["review_record"] == second["review_record"]
    assert second["reused_checkpoint"] and not runner.calls


def test_optional_repeat_never_handles_unknown_or_missing_success(trial_fixture):
    trial, runner = trial_fixture
    candidate_fixture(trial)
    with pytest.raises(ValueError, match="original success"):
        trial.repeat_confirm_check()
    assert not runner.calls


def test_optional_repeat_requires_real_original_receipt_and_query(trial_fixture):
    trial, runner = trial_fixture
    candidate_fixture(trial)
    trial.review_record(trial.work / "synthetic-ack.json")
    runner.action = allow_synthetic_save
    trial.confirm()
    count = len(runner.calls)
    with pytest.raises(ValueError, match="receipt and operation query"):
        trial.repeat_confirm_check()
    assert len(runner.calls) == count


def test_eco_python_consumer_uses_verified_empty_prefix_policy(trial_fixture):
    trial, runner = trial_fixture
    flags = trial.ready.python_flags("consumer")
    result = trial.run(
        "synthetic-python-prefix", [trial.dependencies["native_python"], *flags, "-c", "pass"]
    )
    assert runner.calls[0][1:5] == flags
    prefix = Path(flags[3].split("=", 1)[1])
    assert prefix.is_dir() and not list(prefix.iterdir())
    assert result["synthetic"] is True


def test_eco_python_without_prefix_refuses_before_fake_dispatch(trial_fixture):
    trial, runner = trial_fixture
    with pytest.raises(core.TrialError, match="owned -B -S -X"):
        trial.run(
            "synthetic-python-missing-prefix",
            [trial.dependencies["native_python"], "-B", "-S", "-c", "pass"],
        )
    assert not runner.calls


def test_prepare_missing_dependency_exposes_only_safe_location_and_repair(
    tmp_path, monkeypatch, capsys
):
    missing = str(tmp_path / "missing/kicad-cli.exe")

    def refuse(_args):
        raise core.TrialError(
            "E_TRIAL_DEPENDENCY",
            "Missing dependency",
            details=[
                {
                    "name": "official_cli",
                    "path": missing,
                    "repair": "Supply the existing official KiCad executable at an explicit path.",
                    "reason": "File is missing",
                    "raw_trace": "must-not-be-public",
                    "env": {"TOKEN": "must-not-be-public"},
                }
            ],
        )

    monkeypatch.setattr(frontend, "dispatch", refuse)
    result = frontend.main(
        [
            "prepare",
            "--work",
            str(tmp_path / "new-owned"),
            "--dependencies",
            "not-read.json",
            "--configuration",
            "not-read.json",
        ]
    )
    text = capsys.readouterr().out
    envelope = json.loads(text)
    details = envelope["error"]["details"]
    assert result == 1 and envelope["ok"] is False
    assert details["dependencies"] == [
        {
            "name": "official_cli",
            "path": missing,
            "repair": "Supply the existing official KiCad executable at an explicit path.",
            "reason": "File is missing",
        }
    ]
    assert details["private_evidence"] is None and details["formal_cad_writes"] == 0
    assert details["formal_operation_id"] is None
    assert "new owned root and new plan" in details["action"]
    assert "must-not-be-public" not in text and "raw_trace" not in text


def test_prepare_native_stop_keeps_trace_env_and_token_private(tmp_path, monkeypatch, capsys):
    work = tmp_path / "incomplete-owned"
    put(work / "OWNERSHIP.json", '{"synthetic":true}')
    put(work / "INCOMPLETE.json", '{"status":"INCOMPLETE","synthetic":true}')
    (work / "state").mkdir()
    secret = "synthetic-private-authority-token"

    def stopped(_args):
        raise core.TrialError(
            "E_TRIAL_NATIVE_STOP",
            "Synthetic stop " + secret,
            details={
                "raw_trace": {"argv": ["--confirm", secret]},
                "env": {"TOKEN": secret},
            },
        )

    monkeypatch.setattr(frontend, "dispatch", stopped)
    result = frontend.main(
        [
            "prepare",
            "--work",
            str(work),
            "--dependencies",
            "not-read.json",
            "--configuration",
            "not-read.json",
        ]
    )
    text = capsys.readouterr().out
    envelope = json.loads(text)
    details = envelope["error"]["details"]
    assert result == 1 and envelope["error"]["code"] == "E_TRIAL_NATIVE_STOP"
    assert secret not in text and "raw_trace" not in text and '"env"' not in text
    assert "dependencies" not in details and details["formal_cad_writes"] == 0
    assert "INCOMPLETE" in details["action"] and "Do not automatically retry" in details["action"]
    assert secret in Path(details["private_evidence"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("case", ["missing-external-provider", "runtime-pin-mismatch"])
def test_real_complete_request_provider_failure_is_diagnostic_before_prepare(
    tmp_path, monkeypatch, capsys, case
):
    """Tiny resource declarations only; no Native executable or fake run starts."""
    # This preflight fixture needs a regular caller file, not a POSIX venv symlink.
    # No interpreter is executed; all provider bytes in this test are synthetic.
    caller = put(tmp_path / "providers/synthetic-host-python", "synthetic caller bytes")
    monkeypatch.setattr(eco, "sys", SimpleNamespace(executable=str(caller)))
    package = tmp_path / "small-package"
    resources = package / "trial_tools/resources"
    work = tmp_path / "never-created-owned"
    if case == "missing-external-provider":
        path = tmp_path / "providers/missing-library.dll"
        write_new(
            resources / "EXTERNAL-DEPENDENCIES.json",
            {
                "files": [
                    {
                        "path": str(path),
                        "sha256": "0" * 64,
                        "size": 123,
                    }
                ]
            },
        )
    else:
        path = put(tmp_path / "providers/synthetic-node.exe", "synthetic runtime bytes")
        write_new(resources / "EXTERNAL-DEPENDENCIES.json", {"files": []})
        write_new(resources / "PRIOR-ALIAS-PROJECTION.json", {"alias_groups": []})
        write_new(
            resources / "reading-tools/RUNTIME-123.json",
            {
                "node": {"path": str(path), "sha256": "0" * 64, "size": path.stat().st_size},
                "files": [],
                "sharp_entry": str(tmp_path / "never-read-sharp.cjs"),
            },
        )
        write_new(resources / "reading-tools/SYSTEM-52.json", {"files": []})
    dependency_file, config_file = tmp_path / "dependencies.json", tmp_path / "config.json"
    write_new(dependency_file, {})
    write_new(config_file, {})
    prepare_calls = []

    def unexpected_prepare(*args, **kwargs):
        prepare_calls.append((args, kwargs))
        raise AssertionError("Preparation must not start on failed provider preflight")

    monkeypatch.setattr(frontend, "PACKAGE", package)
    monkeypatch.setattr(core, "prepare", unexpected_prepare)
    exit_code = frontend.main(
        [
            "prepare",
            "--work",
            str(work),
            "--dependencies",
            str(dependency_file),
            "--configuration",
            str(config_file),
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert exit_code == 1 and envelope["error"]["code"] == "E_TRIAL_DEPENDENCY"
    details = envelope["error"]["details"]
    problem = details["dependencies"][0]
    assert problem["path"] == str(path)
    assert "Restore the explicitly declared provider" in problem["repair"]
    assert problem["expected"]["sha256"] == "0" * 64
    if case == "runtime-pin-mismatch":
        assert problem["name"] == "renderer_provider"
        assert problem["actual"]["sha256"] == eco.digest(path.read_bytes())
    else:
        assert problem["name"] == "external_provider"
    assert details["formal_cad_writes"] == 0 and details["private_evidence"] is None
    assert not prepare_calls and not work.exists()


@pytest.fixture
def real_c10_source():
    """The actual packaged original with both C10 and C11 using 0805."""
    package = Path(core.__file__).parent.parent
    original = package / "physical-footprint-eco/starting-project/channel_a.kicad_sch"
    checker = package / "physical-footprint-eco/methods/check_footprint_eco.py"
    raw = original.read_bytes()
    assert raw.count(eco.OLD_FP) == 2
    return raw, checker, package


def test_real_c10_and_c11_shared_0805_changes_only_c10_exact_atom(real_c10_source, monkeypatch):
    raw, checker, package = real_c10_source
    calls = []
    monkeypatch.setattr(core, "guarded_run", lambda *a, **kw: calls.append((a, kw)))
    binding = eco.c10_source_edit_binding(raw, checker, raw)
    target = binding["target"]
    assert target["symbol_uuid"] == "4a31788e-5fe5-5b63-ab18-344d72fdff73"
    assert target["reference"] == target["instance"]["reference"] == "C10"
    start, end = target["footprint_value_span"]
    replacement = binding["after_token_utf8"].encode("utf-8")
    expected = raw[:start] + replacement + raw[end:]
    assert raw[start:end] == b'"' + eco.OLD_FP + b'"'
    assert expected[:start] == raw[:start] and expected[start + len(replacement) :] == raw[end:]
    assert expected.count(eco.OLD_FP) == 1 and expected.count(eco.NEW_FP) == 1
    assert binding["expected_sha256"] == eco.digest(expected)
    # Independent historical saved bytes are a cross-check only. The expected
    # bytes above were derived solely from original + the one requested atom.
    assert expected == (package / "physical-footprint-eco/project/channel_a.kicad_sch").read_bytes()
    c11 = raw.index(b'(property "Reference" "C11"')
    assert expected[c11:] == raw[c11:]
    assert binding["engineering_files_written"] == 0 and not calls


def test_c10_byte_span_handles_non_ascii_before_target(real_c10_source):
    raw, checker, _ = real_c10_source
    modified = raw.replace(b"(kicad_sch", b"(kicad_sch ; " + "中文前缀".encode() + b"\n", 1)
    binding = eco.c10_source_edit_binding(modified, checker, raw)
    start, end = binding["target"]["footprint_value_span"]
    assert modified[start:end] == b'"' + eco.OLD_FP + b'"'
    predicted = modified[:start] + binding["after_token_utf8"].encode() + modified[end:]
    assert "中文前缀".encode() in predicted[:start]
    assert eco.digest(predicted) == binding["expected_sha256"]
    assert "UTF-8 byte offsets" in binding["span_units"]


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate-C10",
        "duplicate-uuid",
        "duplicate-footprint",
        "duplicate-reference-property",
        "fake-library-C10-only",
        "wrong-footprint",
        "wrong-uuid",
        "wrong-instance-reference",
        "unquoted-footprint",
        "malformed-source",
    ],
)
def test_real_c10_ambiguous_or_wrong_targets_refuse_without_cad(
    real_c10_source, monkeypatch, fault
):
    raw, checker, _ = real_c10_source
    binding = eco.c10_source_edit_binding(raw, checker, raw)
    target = binding["target"]
    start, end = target["footprint_value_span"]
    if fault == "duplicate-C10":
        changed = raw.replace(b'(property "Reference" "C11"', b'(property "Reference" "C10"', 1)
    elif fault == "duplicate-uuid":
        insertion = b'(text "synthetic duplicate" (uuid "' + target["symbol_uuid"].encode() + b'"))'
        close = raw.rfind(b")")
        changed = raw[:close] + insertion + raw[close:]
    elif fault in ("duplicate-footprint", "duplicate-reference-property"):
        key = (
            "footprint_property_span"
            if fault == "duplicate-footprint"
            else "reference_property_span"
        )
        begin, finish = target[key]
        changed = raw[:begin] + raw[begin:finish] + raw[begin:]
    elif fault == "fake-library-C10-only":
        changed = raw.replace(b'(property "Reference" "C10"', b'(property "Reference" "X10"', 1)
        changed = changed.replace(b'(property "Reference" "C"', b'(property "Reference" "C10"', 1)
    elif fault == "wrong-footprint":
        changed = raw[:start] + b'"Capacitor_SMD:C_0402_1005Metric"' + raw[end:]
    elif fault == "wrong-uuid":
        changed = raw.replace(
            target["symbol_uuid"].encode(), b"00000000-0000-0000-0000-000000000001", 1
        )
    elif fault == "wrong-instance-reference":
        changed = raw.replace(b'(reference "C10")', b'(reference "C11")', 1)
    elif fault == "unquoted-footprint":
        changed = raw[:start] + eco.OLD_FP + raw[end:]
    else:
        changed = raw[: raw.rfind(b")")]
    assert changed != raw
    calls = []
    monkeypatch.setattr(core, "guarded_run", lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(ValueError):
        eco.c10_source_edit_binding(changed, checker, raw)
    assert not calls


def test_library_reference_does_not_shadow_real_c10_instance(real_c10_source):
    raw, checker, _ = real_c10_source
    with_library_name = raw.replace(b'(property "Reference" "C"', b'(property "Reference" "C10"', 1)
    binding = eco.c10_source_edit_binding(with_library_name, checker, raw)
    assert binding["target"]["symbol_uuid"] == "4a31788e-5fe5-5b63-ab18-344d72fdff73"


def test_public_preparation_cannot_disable_renderer_warmup(tmp_path):
    with pytest.raises(core.TrialError, match="cannot be disabled"):
        eco.complete_request(tmp_path / "not-read", {}, {"renderer_warmup": False})


def test_synthetic_renderer_initial_and_stability_are_bound_preparation_only(trial_fixture):
    trial, runner = trial_fixture
    counts = trial.ready.record["preparation_call_counts"]
    assert {role: counts[role]["completed"] for role in ("native", "host", "node")} == {
        "native": 12,
        "host": 4,
        "node": 4,
    }
    assert len(trial.ready.record["preparation_calls"]) == 20
    for profile in ("writer", "consumer"):
        root = trial.work / "output/preparation" / profile / "renderer-warmup"
        declaration = load(root / "DECLARATION.json")
        assert len(declaration["host_tasks"]) == 2
        for task in declaration["host_tasks"]:
            assert task["argv"][1:5] == trial.ready.python_flags(profile)
        plans = [load(root / name / "PLAN.json") for name in ("initial", "stability")]
        assert plans[0]["fixed_files"] == plans[1]["fixed_files"]
        assert plans[0]["views"] == plans[1]["views"]
        assert plans[0]["directory"] != plans[1]["directory"]
        for name in ("initial", "stability"):
            node_plan = {
                "plan": ident(root / name / "PLAN.json"),
                "declaration": ident(root / "DECLARATION.json"),
                "round": name,
            }
            result = validate_renderer_warmup(trial.ready, declaration, node_plan)
            assert result["preparation_only"] and not result["engineering_acceptance"]
            assert result["actual_view_performed"] is False
    assert not runner.calls  # fixture cleared the recorded synthetic preparation calls


@pytest.mark.parametrize("repeat", [False, True])
def test_prepare_frontend_classifies_native_host_node_and_current_calls(
    tmp_path, monkeypatch, capsys, repeat
):
    work = tmp_path / "owned"
    if repeat:
        put(work / "READY.json", '{"synthetic":true}')
    dependencies, configuration = tmp_path / "deps.json", tmp_path / "config.json"
    write_new(dependencies, {})
    write_new(configuration, {})
    ready = SimpleNamespace(
        work_root=work,
        record={
            "preparation_call_counts": {
                role: {"attempted": count, "completed": count}
                for role, count in (("native", 12), ("host", 4), ("node", 4))
            },
            "preparation_calls": [{} for _ in range(20)],
            "synthetic": True,
        },
    )
    monkeypatch.setattr(frontend, "complete_request", lambda _p, d, c: (d, c))
    monkeypatch.setattr(core, "prepare", lambda *a, **kw: ready)
    code = frontend.main(
        [
            "prepare",
            "--work",
            str(work),
            "--dependencies",
            str(dependencies),
            "--configuration",
            str(configuration),
        ]
    )
    result = json.loads(capsys.readouterr().out)["data"]
    assert code == 0 and result["preparation_only"]
    assert result["completed_consumer_calls"] == {"native": 12, "host": 4, "node": 4, "total": 20}
    assert result["reused_ready"] is repeat
    assert result["new_consumer_calls"] == (
        {"native": 0, "host": 0, "node": 0, "total": 0}
        if repeat
        else result["completed_consumer_calls"]
    )


def test_repeat_missing_helper_qualification_refuses_before_attempt_and_process(
    trial_fixture, monkeypatch
):
    trial, runner = trial_fixture
    candidate_fixture(trial)
    put(trial.state / "confirm.complete.json", '{"synthetic":true}')
    receipt = {"operation_id": "synthetic-original-operation"}
    original_load = eco.load

    def documents(path):
        if Path(path).name == "sync-confirm.reply.json":
            return {"data": {"receipt": receipt}}
        if Path(path).name == "sync-show.reply.json":
            return {
                "data": {
                    "operation_status": {
                        "status": "succeeded",
                        "operation_id": receipt["operation_id"],
                        "receipt": receipt,
                    }
                }
            }
        return original_load(path)

    monkeypatch.setattr(eco, "load", documents)
    with pytest.raises(core.TrialError) as raised:
        trial.repeat_confirm_check()
    assert raised.value.code == "E_TRIAL_HELPER_QUALIFICATION"
    assert raised.value.details["new_processes"] == 0 and not runner.calls
    assert trial.context.stopped is False
    assert not (trial.state / "repeat-confirm-check.attempt.json").exists()
