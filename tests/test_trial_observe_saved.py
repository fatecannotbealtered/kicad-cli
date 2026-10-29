"""Synthetic read-only policy controls; no Native, product or subprocess runs."""

import copy
import json
from types import SimpleNamespace

import pytest

from bench.trial_tools import observe_saved as observed
from bench.trial_tools.identity import IdentityError, capture


def test_original_expectations_are_not_replaced_after_drift(tmp_path):
    source = tmp_path / "original.kicad_pcb"
    source.write_bytes(b"original synthetic bytes")
    context = {
        "schema": observed.core.SCHEMA,
        "stopped": True,
        "inputs": {"project": capture(source)},
    }
    original = copy.deepcopy(context)
    source.write_bytes(b"different synthetic bytes")
    with pytest.raises(IdentityError):
        observed.verify_original_inputs(context)
    assert context == original
    source.unlink()
    with pytest.raises(IdentityError):
        observed.verify_original_inputs(context)


def test_rejected_destination_never_writes_old_failure_evidence(tmp_path, monkeypatch, capsys):
    writes = []

    def rejected(_work):
        raise ValueError("Original CAD context cannot be an observation destination")

    monkeypatch.setattr(observed, "SavedObservation", rejected)
    monkeypatch.setattr(observed, "write_new", lambda *a, **kw: writes.append((a, kw)))
    result = observed.main(
        [
            "plan",
            "--work",
            str(tmp_path / "old-work"),
            "--old-work",
            str(tmp_path / "other-old-work"),
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert result == 1 and envelope["ok"] is False and not writes
    assert envelope["error"]["details"]["private_evidence"] is None


def test_synthetic_ready_cannot_enter_real_observation_or_create_outputs(tmp_path, monkeypatch):
    ready = SimpleNamespace(record={"synthetic": True})
    calls, writes = [], []
    monkeypatch.setattr(observed.core, "load_ready", lambda _work: ready)
    monkeypatch.setattr(observed.core, "guarded_run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(observed, "write_new", lambda *a, **kw: writes.append((a, kw)))
    before = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="Synthetic READY"):
        observed.SavedObservation(tmp_path)
    assert not calls and not writes and set(tmp_path.iterdir()) == before


def test_controlled_fake_observation_retains_synthetic_marker(tmp_path, monkeypatch):
    method = str(observed.Path(observed.__file__).absolute())
    ready = SimpleNamespace(
        work_root=tmp_path,
        package_root=tmp_path / "package",
        record={"synthetic": True, "methods": {"files": {method: observed.file_identity(method)}}},
    )
    monkeypatch.setattr(observed.core, "load_ready", lambda _work: ready)
    fake_calls = []
    observation = observed.SavedObservation(
        tmp_path, _runner=lambda *a, **kw: fake_calls.append((a, kw))
    )
    assert observation.synthetic is True and not fake_calls
    observation.state.mkdir()
    (observation.state / "COMPLETE.json").write_text('{"synthetic":true}', encoding="utf-8")
    request = tmp_path / "output/observation/REVIEW-REQUEST.json"
    request.parent.mkdir(parents=True)
    request.write_text('{"synthetic":true}', encoding="utf-8")
    monkeypatch.setattr(observation, "reopen", lambda: {})
    result = observation.observe()
    assert result["synthetic"] is True and result["new_processes"] == 0 and not fake_calls


@pytest.mark.parametrize("name", ["F4", "source recovery01", "source-recovery1"])
def test_excluded_historical_names_refuse_before_filesystem(monkeypatch, name):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Excluded directories must not be opened")

    monkeypatch.setattr(observed, "ordinary_path", forbidden)
    with pytest.raises(ValueError, match="Excluded"):
        observed.permitted_old_path("D:/synthetic/" + name)


@pytest.fixture
def readonly_ready(tmp_path):
    paths = {
        name: str(tmp_path / (name + ".exe"))
        for name in ("official_cli", "native_python", "host_python", "product_exe")
    }
    return SimpleNamespace(
        package_root=tmp_path / "new-package",
        record={"dependencies": {"paths": paths}},
        python_flags=lambda _profile: [
            "-B",
            "-S",
            "-X",
            "pycache_prefix=" + str(tmp_path / "empty"),
        ],
    )


def test_fixed_official_xml_command_is_permitted_without_launch(readonly_ready, tmp_path):
    work = tmp_path / "new-work"
    output = work / "output/observation/checks/official.xml"
    argv = [
        readonly_ready.record["dependencies"]["paths"]["official_cli"],
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadxml",
        "--output",
        output,
        work / "observed-project/hier_dual_rc.kicad_sch",
    ]
    observed.require_readonly_argv(readonly_ready, work, "observation-xml", argv, [output])


@pytest.mark.parametrize(
    "kind", ["product-confirm", "free-python", "old-output", "unknown-command"]
)
def test_free_or_write_invocations_are_never_permitted(readonly_ready, tmp_path, kind):
    work = tmp_path / "new-work"
    deps = readonly_ready.record["dependencies"]["paths"]
    if kind == "product-confirm":
        name, argv = (
            "observation-xml",
            [deps["product_exe"], "project", "sync", "--confirm", "synthetic"],
        )
    elif kind == "free-python":
        name, argv = (
            "observation-native",
            [deps["native_python"], *readonly_ready.python_flags("consumer"), "-c", "pass"],
        )
    elif kind == "old-output":
        name, argv = (
            "observation-xml",
            [
                deps["official_cli"],
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                tmp_path / "old-work/original.xml",
                work / "observed-project/hier_dual_rc.kicad_sch",
            ],
        )
    else:
        name, argv = "repeat-confirm-negative", [deps["product_exe"]]
    with pytest.raises(ValueError):
        observed.require_readonly_argv(readonly_ready, work, name, argv, [])


@pytest.fixture
def refusal_trace(tmp_path, monkeypatch):
    product = str(tmp_path / "old-package/kicad-cli.exe")
    argv = [product, "project", "sync", "--confirm", "synthetic-private-token", "--compact"]
    root = {
        "process_instance_id": "root",
        "pid": 1,
        "creation_filetime": 100,
        "image": product,
        "image_sha256": observed.PRODUCT_SHA256,
        "exit": 6,
        "owned": True,
        "identity_status": "bound",
        "metadata": {"CommandLine": json.dumps(argv)},
    }
    worker = copy.deepcopy(root)
    worker.update(
        process_instance_id="worker",
        pid=2,
        creation_filetime=200,
        parent_process_instance_id="root",
        parent_binding="observed-parent",
    )
    worker["metadata"]["ParentProcessId"] = 1
    helpers = []
    rows = [root, worker]
    for name, role, parent, pid in (
        ("conhost.exe", "root", root, 3),
        ("cmd.exe", "worker", worker, 4),
    ):
        path = str(tmp_path / "system" / name)
        words = [path, "0x4"] if role == "root" else [path, "/c", "ver"]
        helpers.append({"path": path, "parent": role, "argv": words, "identity": {"sha256": name}})
        rows.append(
            {
                "process_instance_id": name,
                "pid": pid,
                "creation_filetime": 300,
                "image": path,
                "image_sha256": name,
                "exit": 0,
                "owned": True,
                "identity_status": "bound",
                "parent_process_instance_id": parent["process_instance_id"],
                "parent_binding": "observed-parent",
                "metadata": {"ParentProcessId": parent["pid"], "CommandLine": json.dumps(words)},
            }
        )
    trace = {
        "observation_schema": 4,
        "exceptions": [],
        "complete_tree": True,
        "timed_out": False,
        "job_active_before_close": 0,
        "exit": 6,
        "root_process_instance_id": "root",
        "processes": rows,
        "job_finalization": {
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
        },
    }
    generic = {
        "status": "unexpected_exit",
        "observation_complete": True,
        "issues": [],
        "second_chance_notifications": [],
        "fail_fast_status_notifications": [],
        "functional_output_verified": False,
        "may_validate_functional_output": False,
        "stop_required": True,
    }
    old = SimpleNamespace(
        record={
            "dependencies": {
                "paths": {"product_exe": product},
                "files": {product: {"sha256": observed.PRODUCT_SHA256}},
            }
        }
    )
    monkeypatch.setattr(observed, "require_repeat_helpers", lambda _ready: helpers)
    monkeypatch.setattr(observed.core, "_windows_argv", json.loads)
    return trace, generic, argv, old, SimpleNamespace()


def test_known_natural_refusal_transport_can_be_read_without_ttl_renewal(refusal_trace):
    observed.verify_known_refusal_trace(*refusal_trace)


@pytest.mark.parametrize(
    "fault",
    [
        "exception",
        "timeout",
        "active-job",
        "unknown-child",
        "wrong-parent",
        "native-exit",
        "wrong-token-argv",
    ],
)
def test_incomplete_unknown_or_native_fault_is_outside_scope(refusal_trace, fault):
    trace, generic, argv, old, new = refusal_trace
    if fault == "exception":
        trace["exceptions"] = [{"code": "synthetic"}]
    elif fault == "timeout":
        trace["timed_out"] = True
    elif fault == "active-job":
        trace["job_active_before_close"] = 1
    elif fault == "unknown-child":
        trace["processes"].append({"image": "unknown.exe"})
    elif fault == "wrong-parent":
        trace["processes"][-1]["parent_process_instance_id"] = "root"
    elif fault == "native-exit":
        trace["processes"][1]["exit"] = 0xC0000409
    else:
        words = json.loads(trace["processes"][1]["metadata"]["CommandLine"])
        words[-2] = "different-token"
        trace["processes"][1]["metadata"]["CommandLine"] = json.dumps(words)
    with pytest.raises(ValueError):
        observed.verify_known_refusal_trace(trace, generic, argv, old, new)
