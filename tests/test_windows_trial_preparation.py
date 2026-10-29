"""Offline fixtures: no CAD, product or KiCad Native process is launched.

The injected private runner writes tiny synthetic consumer/configuration files.
Its records are marked synthetic, even if the fixture claims otherwise. These
tests establish guard behavior and never establish Native preparation evidence.
Two Windows-only cases launch a Host Python child for read-only A/W shell-folder
API probes. They do not create shell folders or modify registry configuration.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import bench.trial_tools.identity as identity_module
import bench.trial_tools.preparation as preparation_module
from bench.trial_tools.identity import (
    IdentityError,
    capture,
    file_identity,
    tree_inventory,
    verify_aliases,
    verify_inventory,
)
from bench.trial_tools.preparation import (
    PRODUCT_SHA256,
    PROJECT_FILES,
    FrozenContext,
    TrialError,
    freeze_context,
    guarded_run,
    load_ready,
    prepare,
    verify_ready,
)
from bench.trial_tools.reading import ident


def _write(path: Path, text: str = "fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class HarmlessConsumer:
    """Private fake, not a product feature or a Native success alternative."""

    def __init__(self, *, interrupt_at=None, action=None, abnormal=False):
        self.calls = []
        self.interrupt_at = interrupt_at
        self.action = action
        self.abnormal = abnormal

    def __call__(self, argv, *, cwd, env, out, timeout, ready):
        self.calls.append({"argv": argv, "cwd": str(cwd), "env": env, "out": str(out)})
        if len(self.calls) == self.interrupt_at:
            raise InterruptedError("synthetic interruption")
        out.mkdir(exist_ok=False)
        _write(out / "stderr", "")
        _write(
            out / "stdout",
            json.dumps(
                {
                    "schema": "physical-footprint-eco-native-read/v1",
                    "native_version": "10.0.6",
                    "input_unchanged": True,
                    "project_unchanged": True,
                }
            ),
        )
        config = Path(env["KICAD_CONFIG_HOME"]) / "10.0"
        config.mkdir(exist_ok=True)
        for name in ("3d_viewer", "cvpcb", "eeschema", "fpedit", "pcbnew", "symbol_editor"):
            if not (config / (name + ".json")).exists():
                _write(config / (name + ".json"), '{"rules": {"severity": "error"}}')
        if not (config / "kicad_common.json").exists():
            _write(
                config / "kicad_common.json",
                json.dumps({"system": {"working_dir": str(cwd)}, "rules": "strict"}),
            )
            _write(config / "sym-lib-table", "(sym_lib_table (lib (name Original)))")
            _write(config / "fp-lib-table", "(fp_lib_table (lib (name Original)))")
        if "--output" in argv:
            target = Path(argv[argv.index("--output") + 1])
            if target.suffix == ".json":
                _write(
                    target, '{"$schema":"https://schemas.kicad.org/fixture.json","violations":[]}'
                )
            elif target.suffix == ".xml":
                _write(target, '<export version="fixture"><components/></export>')
            elif target.suffix == ".svg":
                _write(target, '<svg xmlns="http://www.w3.org/2000/svg"/>')
            else:
                target.mkdir(exist_ok=False)
                _write(target / "fixture.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
        if any(Path(value).name == "svg_reading.py" for value in argv):
            raw = Path(argv[argv.index("--raw") + 1])
            derivative = Path(argv[argv.index("--derivative") + 1])
            proof = Path(argv[argv.index("--proof") + 1])
            target = Path(argv[argv.index("--out") + 1])
            if "derive" in argv:
                derivative.write_bytes(raw.read_bytes())
                _write(
                    proof,
                    json.dumps(
                        {
                            "synthetic": True,
                            "raw_sha256": ident(raw)["sha256"],
                            "derivative_sha256": ident(derivative)["sha256"],
                        }
                    ),
                )
            _write(
                target,
                json.dumps(
                    {
                        "schema": "finite-PCB-reading-check/v1",
                        "synthetic": True,
                        "status": "geometry_preserved_pending_actual_view",
                        "raw_sha256": ident(raw)["sha256"],
                        "derivative_sha256": ident(derivative)["sha256"],
                        "all_original_elements_preserved": True,
                        "every_primitive_inside_reading_viewport": True,
                        "margin_mm": "2",
                        "geometry_or_opacity_edits": 0,
                        "actual_view_performed": False,
                    }
                ),
            )
        if argv[-1] == "render-bound":
            plan_path = Path(argv[2])
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
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
            _write(
                Path(plan["directory"]) / "RESULT.json",
                json.dumps(
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
                    }
                ),
            )
            cache = Path(env["LOCALAPPDATA"]) / "fontconfig/cache/synthetic.cache-12"
            if not cache.exists():
                _write(cache, "synthetic font cache initialized once")
        if self.action:
            self.action(argv, cwd, env, out, ready)
        return {
            "synthetic": False,
            "trace": {
                "timed_out": self.abnormal,
                "job_active_before_close": 0,
                "job_finalization": {
                    "natural_tree_complete": not self.abnormal,
                    "controlled_termination": False,
                    "control_events": [],
                },
            },
            "classification": {
                "observation_complete": True,
                "may_validate_functional_output": not self.abnormal,
                "stop_required": self.abnormal,
            },
        }


@pytest.fixture
def sandbox(tmp_path):
    package = tmp_path / "package"
    original = package / "physical-footprint-eco/starting-project"
    for name in PROJECT_FILES:
        _write(original / name, "original-0805-" + name)
    methods = package / "physical-footprint-eco/methods"
    _write(methods / "saved_board_native_read.py", "# harmless placeholder, never executed")
    _write(methods / "requirements/BASELINE-INPUTS.json", "{}")
    _write(methods / "requirements/PHYSICAL-PIN-INTENT42.json", "{}")
    observer = package / "trial_tools/resources/native-observer"
    for name in (
        "integration",
        "observer02_support",
        "observer02_metadata",
        "observer02_owned_process",
        "observer02_win_debugging",
        "observer02_native_observation",
        "observer02_process_tree",
    ):
        _write(observer / (name + ".py"), "# harmless observer placeholder, never executed")
    native = tmp_path / "external/native/bin"
    sdk, spec = tmp_path / "external/sdk", tmp_path / "external/spec"
    sdk.mkdir(parents=True)
    spec.mkdir()
    dependencies = {
        "official_cli": _write(native / "kicad-cli.exe", "official-fixture"),
        "native_python": _write(native / "python.exe", "native-fixture"),
        "product_exe": _write(package / "candidate/kicad-cli.exe", "product-fixture"),
        "sdk_root": sdk,
        "spec_root": spec,
        "dependency_files": [],
        "aliases": [],
    }
    dependencies["host_python"] = dependencies["native_python"]
    node = _write(tmp_path / "external/renderer/node.exe", "synthetic Node placeholder")
    sharp = _write(tmp_path / "external/renderer/sharp.cjs", "synthetic Sharp placeholder")
    dependencies["dependency_files"] = [node, sharp]
    reading = package / "trial_tools/resources/reading-tools"
    reading.mkdir(parents=True)
    source = Path(preparation_module.__file__).parent / "resources/reading-tools"
    for name in ("render_pcb.cjs", "svg_reading.py"):
        shutil.copyfile(source / name, reading / name)
    _write(
        reading / "pcb-qualification.json",
        json.dumps(
            {
                "method": ident(reading / "render_pcb.cjs"),
                "node": ident(node),
                "qualification": {"status": "passed_bounded_host_renderer_fixture"},
                "synthetic": True,
            }
        ),
    )
    return package, tmp_path / "owned-work", dependencies, original


def _fixture_configuration(sandbox, overrides=None):
    node, sharp = sandbox[2]["dependency_files"][:2]
    result = {
        "renderer_warmup": True,
        "renderer_runtime": {
            "schema": "trial-current-reading-runtime/v1",
            "node": ident(node),
            "sharp_entry": str(sharp),
            "runtime_files": [ident(sharp)],
            "system_files": [],
            "aliases": [],
            "synthetic": True,
        },
    }
    result.update(overrides or {})
    return result


def _prepare(sandbox, *, config=None, runner=None):
    package, work, dependencies, _ = sandbox
    consumer = runner or HarmlessConsumer()
    return prepare(
        package, work, dependencies, _fixture_configuration(sandbox, config), _runner=consumer
    ), consumer


def _formal_context(ready):
    formal = ready.work_root / "formal"
    formal.mkdir()
    _write(formal / "board.kicad_pcb", "protected formal CAD bytes")
    (ready.work_root / "logs/formal").mkdir()
    return freeze_context(ready, inputs={"formal": formal, "validator": Path(__file__).absolute()})


def _run_formal(context, runner, *, allowed_input_changes=None):
    return guarded_run(
        [context.ready.record["dependencies"]["paths"]["official_cli"], "fixture-read-only"],
        context=context,
        profile="consumer",
        cwd=context.ready.work_root / "formal",
        out=context.ready.work_root / "logs/formal/one",
        _runner=runner,
        allowed_input_changes=allowed_input_changes,
    )


def test_fresh_preparation_initializes_actual_consumer_categories_without_formal_cad_writes(
    sandbox,
):
    original = tree_inventory(sandbox[3])
    ready, fake = _prepare(sandbox)
    assert len(fake.calls) == 20
    assert ready.record["preparation_call_counts"] == {
        role: {"attempted": count, "completed": count}
        for role, count in {"native": 12, "host": 4, "node": 4}.items()
    }
    assert ready.record["status"] == "READY"
    assert ready.record["synthetic"] is True
    assert ready.record["formal_cad_writes"] == 0
    assert tree_inventory(sandbox[3]) == original
    assert not (ready.work_root / "formal").exists()
    assert set(ready.record["preparation_input"]["files"]) == set(PROJECT_FILES)
    assert all(call["result"]["synthetic"] for call in ready.record["preparation_calls"])
    for profile in ("writer", "consumer"):
        calls = [
            call
            for call in fake.calls
            if Path(call["env"]["KICAD_CONFIG_HOME"]).parent.name == profile
        ]
        assert len(calls) == 10
        native = [
            call
            for call in ready.record["preparation_calls"]
            if call["profile"] == profile and call["role"] == "native"
        ]
        assert len(native) == 6
        assert any("--schematic-parity" in call["argv"] for call in native)
        assert any("kicadxml" in call["argv"] for call in native)
        assert native[-1]["argv"][1:5] == ready.python_flags(profile)
        for key in (
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "KICAD_CONFIG_HOME",
            "KICAD_CLI_STATE_DIR",
        ):
            assert Path(calls[0]["env"][key]).is_relative_to(ready.profile_root(profile))
        assert "10.0/eeschema.json" in ready.record["configuration"][profile]["files"]


def test_preparation_pcb_svg_command_declares_single_file_and_finite_plot_flags(sandbox):
    ready, fake = _prepare(sandbox)
    calls = [call for call in fake.calls if call["argv"][1:4] == ["pcb", "export", "svg"]]
    assert len(calls) == 2
    for call in calls:
        argv = call["argv"]
        assert "--mode-single" in argv and "--exclude-drawing-sheet" in argv
        assert argv[argv.index("--page-size-mode") + 1] == "1"
        assert argv[argv.index("--layers") + 1] == "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts"
        target = Path(argv[argv.index("--output") + 1])
        assert target.name == "pcb.svg" and target.is_file()
        assert target.is_relative_to(ready.work_root / "output/preparation")


@pytest.mark.parametrize(
    "consumer,kind,accepted",
    [
        ("sch-svg", "directory", True),
        ("pcb-svg", "file", True),
        ("sch-svg", "file", False),
        ("pcb-svg", "directory", False),
    ],
)
def test_preparation_svg_consumer_distinguishes_sch_directory_from_pcb_file(
    tmp_path, consumer, kind, accepted
):
    target = tmp_path / "consumer-output.svg"
    if kind == "file":
        _write(target, '<svg xmlns="http://www.w3.org/2000/svg"/>')
    else:
        _write(target / "sheet.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
    result = {"output_inventory": {str(target): capture(target)}}
    if accepted:
        preparation_module._verify_preparation_consumer(consumer, result)
    else:
        with pytest.raises(TrialError) as rejected:
            preparation_module._verify_preparation_consumer(consumer, result)
        assert rejected.value.code == "E_TRIAL_OUTPUT"


def test_repeat_ready_checks_current_inventory_and_starts_zero_commands(sandbox):
    ready, fake = _prepare(sandbox)
    package, work, dependencies, _ = sandbox
    repeated = prepare(package, work, dependencies, _fixture_configuration(sandbox), _runner=fake)
    assert repeated.record == ready.record
    assert len(fake.calls) == 20
    assert verify_ready(load_ready(work))["native_calls"] == 0


def test_renderer_initial_cache_is_frozen_before_the_stability_nodes(sandbox):
    ready, _ = _prepare(sandbox)
    qualification = ready.record["renderer_preparation"]
    assert qualification["status"] == "passed_preparation_only"
    baseline = json.loads(
        Path(qualification["baseline_record"]["path"]).read_text(encoding="utf-8")
    )
    for profile in ("writer", "consumer"):
        files = baseline["profile_configuration"][profile]["home"]["files"]
        assert "AppData/Local/fontconfig/cache/synthetic.cache-12" in files
        assert (
            ready.record["profile_configuration"][profile]
            == baseline["profile_configuration"][profile]
        )
        for phase in ("initial", "stability"):
            assert qualification[phase][profile]["preparation_only"] is True
            assert qualification[phase][profile]["engineering_acceptance"] is False
            assert qualification[phase][profile]["actual_view_performed"] is False


@pytest.mark.parametrize("mutation", ["added", "deleted", "content", "replacement"])
def test_renderer_stability_rejects_complete_cache_inventory_drift_without_refreezing(
    sandbox, mutation
):
    def mutate_repeat(argv, cwd, env, out, ready):
        if out.name != "renderer-node-stability":
            return
        root = Path(env["LOCALAPPDATA"]) / "fontconfig/cache"
        target = root / "synthetic.cache-12"
        if mutation == "added":
            _write(root / "new-cache-12", "new font cache")
        elif mutation == "deleted":
            target.unlink()
        elif mutation == "content":
            target.write_text("changed font cache", encoding="utf-8")
        else:
            original = target.stat()
            replacement = _write(root / "replacement", target.read_text(encoding="utf-8"))
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            os.replace(replacement, target)

    fake = HarmlessConsumer(action=mutate_repeat)
    with pytest.raises(TrialError) as rejected:
        _prepare(sandbox, runner=fake)
    assert rejected.value.code == "E_TRIAL_RENDERER_STABILITY" and len(fake.calls) == 19
    work = sandbox[1]
    assert (work / "INCOMPLETE.json").exists() and not (work / "READY.json").exists()
    assert (work / "state/RENDERER-CONFIGURATION-BASELINE.json").exists()
    with pytest.raises(TrialError, match="incomplete"):
        prepare(sandbox[0], work, sandbox[2], _fixture_configuration(sandbox), _runner=fake)
    assert len(fake.calls) == 19


def test_renderer_native_complete_job_does_not_replace_bound_output_validation(sandbox):
    def corrupt_result(argv, cwd, env, out, ready):
        if out.name == "renderer-node-initial":
            path = Path(argv[2]).parent / "render/RESULT.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["plan_sha256"] = "0" * 64
            path.write_text(json.dumps(value), encoding="utf-8")

    fake = HarmlessConsumer(action=corrupt_result)
    with pytest.raises(ValueError, match="incomplete or bound"):
        _prepare(sandbox, runner=fake)
    assert len(fake.calls) == 15
    assert not (sandbox[1] / "READY.json").exists()


def test_renderer_first_node_failure_stops_without_any_stability_retry(sandbox):
    fake = HarmlessConsumer()

    def fail_first_node(argv, cwd, env, out, ready):
        if out.name == "renderer-node-initial":
            fake.abnormal = True

    fake.action = fail_first_node
    with pytest.raises(TrialError) as rejected:
        _prepare(sandbox, runner=fake)
    assert rejected.value.code == "E_TRIAL_NATIVE_STOP" and len(fake.calls) == 15
    assert not (sandbox[1] / "state/RENDERER-CONFIGURATION-BASELINE.json").exists()
    assert not (sandbox[1] / "READY.json").exists()


@pytest.mark.parametrize("configuration", [{"renderer_warmup": False}, {"renderer_runtime": None}])
def test_renderer_warmup_cannot_be_disabled_or_left_unqualified(sandbox, configuration):
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        _prepare(sandbox, config=configuration, runner=fake)
    assert rejected.value.code == "E_TRIAL_CONFIG" and not fake.calls and not sandbox[1].exists()


def test_renderer_missing_prospective_provider_refuses_before_all_consumers(sandbox):
    config = _fixture_configuration(sandbox)
    sandbox[2]["dependency_files"].pop(0)
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        prepare(sandbox[0], sandbox[1], sandbox[2], config, _runner=fake)
    assert (
        rejected.value.code == "E_TRIAL_DEPENDENCY" and not fake.calls and not sandbox[1].exists()
    )


def test_old_ready_without_renderer_qualification_is_not_upgraded(sandbox):
    ready, fake = _prepare(sandbox)
    path = ready.work_root / "READY.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    del value["renderer_preparation"]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TrialError, match="lacks mandatory actual renderer"):
        load_ready(ready.work_root)
    assert len(fake.calls) == 20


@pytest.mark.parametrize("artifact", ["plan", "declaration"])
@pytest.mark.parametrize("entry", ["load_ready", "repeat_prepare"])
def test_ready_rechecks_renderer_plan_and_declaration_without_starting_a_consumer(
    sandbox, artifact, entry
):
    ready, initial = _prepare(sandbox)
    target = Path(ready.record["renderer_preparation"]["initial"]["consumer"][artifact]["path"])
    target.write_bytes(target.read_bytes() + b"\n ")
    retry = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        if entry == "load_ready":
            load_ready(ready.work_root)
        else:
            prepare(
                sandbox[0], sandbox[1], sandbox[2], _fixture_configuration(sandbox), _runner=retry
            )
    assert rejected.value.code == "E_TRIAL_CONTEXT"
    assert rejected.value.details["artifact"] == artifact
    assert len(initial.calls) == 20 and not retry.calls


def test_ready_rejects_same_byte_renderer_declaration_replacement(sandbox):
    ready, initial = _prepare(sandbox)
    target = Path(
        ready.record["renderer_preparation"]["initial"]["consumer"]["declaration"]["path"]
    )
    observed = target.stat()
    replacement = target.with_name("replacement.json")
    replacement.write_bytes(target.read_bytes())
    os.utime(replacement, ns=(observed.st_atime_ns, observed.st_mtime_ns))
    os.replace(replacement, target)
    with pytest.raises(IdentityError):
        load_ready(ready.work_root)
    assert len(initial.calls) == 20


def test_existing_bytecode_in_owned_prefix_refuses_before_a_formal_command(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    prefix = Path(ready.record["profiles"]["consumer"]["bytecode_empty"])
    stale = _write(prefix / "stale.cpython-311.pyc", "synthetic pre-existing bytecode")
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        _run_formal(context, fake)
    assert rejected.value.code == "E_TRIAL_BYTECODE" and not fake.calls
    assert stale.is_file() and context.stopped
    assert rejected.value.post_failure_observation["bytecode_prefixes"]["consumer"]["differences"][
        "added"
    ] == ["stale.cpython-311.pyc"]


def test_bytecode_written_during_preparation_is_preserved_and_stops_the_cycle(sandbox):
    def add_bytecode(argv, cwd, env, out, ready):
        _write(Path(env["PYTHONPYCACHEPREFIX"]) / "new.pyc", "synthetic unwanted bytecode")

    fake = HarmlessConsumer(action=add_bytecode)
    with pytest.raises(TrialError) as rejected:
        _prepare(sandbox, runner=fake)
    assert rejected.value.code == "E_TRIAL_BYTECODE" and len(fake.calls) == 1
    assert (sandbox[1] / "profiles/writer/cache/bytecode-empty/new.pyc").is_file()
    assert (sandbox[1] / "INCOMPLETE.json").exists()
    assert not (sandbox[1] / "READY.json").exists()


def test_empty_prefix_directory_replacement_is_refused_without_clearing_it(sandbox):
    ready, _ = _prepare(sandbox)
    prefix = Path(ready.record["profiles"]["consumer"]["bytecode_empty"])
    original = prefix.with_name("retained-original-empty-prefix")
    prefix.rename(original)
    prefix.mkdir()
    with pytest.raises(TrialError) as rejected:
        verify_ready(ready)
    assert rejected.value.code == "E_TRIAL_BYTECODE"
    assert prefix.is_dir() and original.is_dir()


def test_native_and_host_python_configurations_use_registered_empty_prefixes(sandbox, monkeypatch):
    package, work, dependencies, _ = sandbox
    host = _write(work.parent / "host/python.exe", "harmless Host interpreter placeholder")
    dependencies["dependency_files"].append(host)
    dependencies["host_python"] = host
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(work.parent / "unowned-prefix"))
    ready, prep_fake = _prepare(sandbox)
    for profile in ("writer", "consumer"):
        prefix = Path(ready.record["profiles"][profile]["bytecode_empty"])
        assert ready.env(profile)["PYTHONPYCACHEPREFIX"] == str(prefix)
        assert ready.env(profile)["PYTHONDONTWRITEBYTECODE"] == "1"
        assert set(ready.record["bytecode_prefixes"][profile]["directories"]) == {"."}
        assert ready.record["bytecode_prefixes"][profile]["files"] == {}
        native = [
            call
            for call in prep_fake.calls
            if call["argv"][0] == str(dependencies["native_python"])
            and call["env"]["PYTHONPYCACHEPREFIX"] == str(prefix)
        ]
        assert len(native) == 1 and native[0]["argv"][1:5] == ready.python_flags(profile)
    context = _formal_context(ready)
    fake = HarmlessConsumer()
    guarded_run(
        [host, *ready.python_flags("consumer"), "-c", "# synthetic, never executed"],
        context=context,
        profile="consumer",
        cwd=work / "formal",
        out=work / "logs/formal/host",
        _runner=fake,
    )
    assert (
        fake.calls[0]["env"]["PYTHONPYCACHEPREFIX"] == ready.env("consumer")["PYTHONPYCACHEPREFIX"]
    )
    assert fake.calls[0]["argv"][1:5] == ready.python_flags("consumer")


def test_caller_cannot_override_the_owned_bytecode_prefix(sandbox):
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        _prepare(sandbox, config={"environment": {"PYTHONPYCACHEPREFIX": "outside"}}, runner=fake)
    assert rejected.value.code == "E_TRIAL_CONFIG" and not fake.calls
    assert not sandbox[1].exists()


def test_python_argv_cannot_override_the_owned_bytecode_prefix(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        guarded_run(
            [
                ready.record["dependencies"]["paths"]["native_python"],
                "-B",
                "-S",
                "-X",
                "pycache_prefix=unowned",
                "-c",
                "# synthetic, never executed",
            ],
            context=context,
            profile="consumer",
            cwd=ready.work_root / "formal",
            out=ready.work_root / "logs/formal/one",
            _runner=fake,
        )
    assert rejected.value.code == "E_TRIAL_BYTECODE" and not fake.calls
    assert context.stopped


def test_declared_renamed_host_python_requires_the_owned_python_flags(sandbox):
    host = _write(sandbox[1].parent / "host/python311.exe", "harmless renamed Host placeholder")
    sandbox[2]["host_python"] = host
    ready, _ = _prepare(sandbox)
    assert ready.record["dependencies"]["paths"]["host_python"] == str(host)
    context = _formal_context(ready)
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        guarded_run(
            [host, "-c", "# synthetic, never executed"],
            context=context,
            profile="consumer",
            cwd=ready.work_root / "formal",
            out=ready.work_root / "logs/formal/one",
            _runner=fake,
        )
    assert rejected.value.code == "E_TRIAL_BYTECODE" and not fake.calls
    assert context.stopped


def test_profile_uses_coherent_owned_knownfolder_and_explicit_kicad_roots(sandbox):
    ready, _ = _prepare(sandbox)
    for profile in ("writer", "consumer"):
        paths = ready.record["profiles"][profile]
        env = ready.env(profile)
        home = Path(paths["home"])
        assert Path(env["USERPROFILE"]) == home
        assert Path(env["HOME"]) == home
        assert Path(env["APPDATA"]) == home / "AppData/Roaming"
        assert Path(env["LOCALAPPDATA"]) == home / "AppData/Local"
        assert Path(env["KICAD_DOCUMENTS_HOME"]) == home / "Documents"
        assert Path(env["KICAD_CACHE_HOME"]) == Path(paths["cache"])
        for domain in ("home", "roaming", "local", "documents"):
            assert Path(paths[domain]).is_dir()
        assert "Documents" in ready.record["profile_configuration"][profile]["home"]["directories"]


@pytest.mark.skipif(os.name != "nt", reason="Finite Windows shell-folder API fixture")
@pytest.mark.parametrize("coherent", [False, True])
def test_windows_knownfolder_api_with_owned_coherent_and_parallel_layouts(tmp_path, coherent):
    """Host WinAPI only; no KiCad, no registry write and no CSIDL_FLAG_CREATE."""
    profile = tmp_path / "owned-profile"
    home = profile / "home"
    home.mkdir(parents=True)
    if coherent:
        roaming, local = home / "AppData/Roaming", home / "AppData/Local"
        (home / "Documents").mkdir()
    else:
        roaming, local = profile / "roaming", profile / "local"
    roaming.mkdir(parents=True)
    local.mkdir(parents=True)
    prefix = tmp_path / "host-bytecode-empty"
    prefix.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    env.update(
        {
            "USERPROFILE": str(home),
            "HOME": str(home),
            "APPDATA": str(roaming),
            "LOCALAPPDATA": str(local),
            "PYTHONPYCACHEPREFIX": str(prefix),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    code = (
        "import ctypes as C,json\n"
        "shell=C.WinDLL('shell32')\n"
        "shell.SHGetFolderPathW.argtypes=[C.c_void_p,C.c_int,C.c_void_p,C.c_uint,C.c_wchar_p]\n"
        "shell.SHGetFolderPathA.argtypes=[C.c_void_p,C.c_int,C.c_void_p,C.c_uint,C.c_char_p]\n"
        "shell.SHGetFolderPathW.restype=shell.SHGetFolderPathA.restype=C.c_long\n"
        "rows={}\n"
        "for key in (5,26,28):\n"
        " w=C.create_unicode_buffer(260);a=C.create_string_buffer(260)\n"
        " wh=shell.SHGetFolderPathW(None,key,None,0,w)\n"
        " ah=shell.SHGetFolderPathA(None,key,None,0,a)\n"
        " rows[str(key)]={'wide_hr':wh,'wide':w.value,'ansi_hr':ah,"
        "'ansi':a.value.decode('mbcs')}\n"
        "print(json.dumps(rows))\n"
    )
    process = subprocess.run(
        [sys.executable, "-B", "-S", "-X", "pycache_prefix=" + str(prefix), "-c", code],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        check=True,
        timeout=30,
    )
    rows = json.loads(process.stdout.decode("utf-8"))
    expected = {"5": home / "Documents", "26": roaming, "28": local}
    for key, path in expected.items():
        row = rows[key]
        if coherent:
            assert row["wide_hr"] == row["ansi_hr"] == 0
            assert Path(row["wide"]) == Path(row["ansi"]) == path
        else:
            assert row["wide_hr"] != 0 and row["ansi_hr"] != 0
            assert row["wide"] == row["ansi"] == ""
    assert list(prefix.iterdir()) == []


def test_incomplete_preparation_stops_and_never_resumes(sandbox):
    package, work, dependencies, _ = sandbox
    fake = HarmlessConsumer(interrupt_at=3)
    with pytest.raises(InterruptedError):
        prepare(package, work, dependencies, _fixture_configuration(sandbox), _runner=fake)
    assert (work / "INCOMPLETE.json").is_file()
    assert not (work / "READY.json").exists()
    with pytest.raises(TrialError, match="incomplete"):
        prepare(package, work, dependencies, _fixture_configuration(sandbox), _runner=fake)
    assert len(fake.calls) == 3


def test_native_timeout_remains_incomplete_and_is_not_retried(sandbox):
    fake = HarmlessConsumer(abnormal=True)
    with pytest.raises(TrialError) as result:
        _prepare(sandbox, runner=fake)
    assert result.value.code == "E_TRIAL_NATIVE_STOP"
    assert len(fake.calls) == 1
    assert (sandbox[1] / "INCOMPLETE.json").exists()
    assert not (sandbox[1] / "READY.json").exists()


def test_missing_dependency_refuses_before_any_command_or_owned_directory(sandbox):
    package, work, dependencies, _ = sandbox
    dependencies["native_python"] = work.parent / "missing-python.exe"
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as result:
        prepare(package, work, dependencies, _runner=fake)
    assert result.value.code == "E_TRIAL_DEPENDENCY"
    assert result.value.details[0]["repair"]
    assert not fake.calls and not work.exists()


@pytest.mark.parametrize(
    "condition,names,reason",
    [
        (
            "same_artifact",
            {"official_cli", "product_exe"},
            "official_cli_matches_product_executable",
        ),
        (
            "different_bins",
            {"official_cli", "native_python"},
            "official_cli_and_native_python_bins_differ",
        ),
        ("nonadjacent_sdk_spec", {"sdk_root", "spec_root"}, "sdk_and_spec_roots_are_not_adjacent"),
    ],
)
def test_dependency_layout_refusals_have_safe_locator_and_repair_before_any_command(
    sandbox, condition, names, reason
):
    package, work, dependencies, _ = sandbox
    if condition == "same_artifact":
        dependencies["official_cli"] = dependencies["product_exe"]
    elif condition == "different_bins":
        dependencies["native_python"] = _write(
            work.parent / "other-native/bin/python.exe", "harmless alternative Native placeholder"
        )
    else:
        alternate = work.parent / "other-spec-parent/spec"
        alternate.mkdir(parents=True)
        dependencies["spec_root"] = alternate
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        prepare(package, work, dependencies, _runner=fake)
    error = rejected.value
    assert error.code == "E_TRIAL_DEPENDENCY" and not fake.calls and not work.exists()
    assert {row["name"] for row in error.details} == names
    for row in error.details:
        assert set(row) == {"name", "path", "reason", "repair"}
        assert row["path"] == str(dependencies[row["name"]])
        assert row["reason"] == reason and row["repair"]
        assert all(not any(char in value for char in "\n\r\0") for value in row.values())


def test_existing_unregistered_directory_is_not_overwritten(sandbox):
    sandbox[1].mkdir()
    sentinel = _write(sandbox[1] / "keep.txt", "keep")
    with pytest.raises(TrialError) as result:
        _prepare(sandbox)
    assert result.value.code == "E_TRIAL_OWNERSHIP"
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("mutation", ["added", "deleted", "same_bytes_replacement", "content"])
def test_formal_call_rejects_complete_configuration_drift_and_does_not_advance(sandbox, mutation):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    formal_before = tree_inventory(ready.work_root / "formal")
    config = Path(ready.record["profiles"]["consumer"]["config"]) / "10.0"

    def mutate(argv, cwd, env, out, selected_ready):
        target = config / "eeschema.json"
        if mutation == "added":
            _write(config / "not-in-old-inventory.json", "{}")
        elif mutation == "deleted":
            target.unlink()
        elif mutation == "same_bytes_replacement":
            replacement = _write(config / "new-temp.json", target.read_text(encoding="utf-8"))
            before = file_identity(target)
            source_stat = target.stat()
            os.utime(replacement, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            os.replace(replacement, target)
            assert file_identity(target)["file_id"] != before["file_id"]
            assert file_identity(target)["mtime_ns"] == before["mtime_ns"]
        else:
            target.write_text('{"rules":{"severity":"ignore"}}', encoding="utf-8")

    with pytest.raises(TrialError) as result:
        _run_formal(context, HarmlessConsumer(action=mutate))
    assert result.value.code == "E_TRIAL_CONFIG_DRIFT"
    assert context.stopped
    assert tree_inventory(ready.work_root / "formal") == formal_before
    assert ready.record["formal_cad_writes"] == 0


@pytest.mark.parametrize("name", ["sym-lib-table", "fp-lib-table", "pcbnew.json", "eeschema.json"])
def test_rules_and_library_table_drift_prevents_a_formal_command(sandbox, name):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    target = Path(ready.record["profiles"]["writer"]["config"]) / "10.0" / name
    target.write_text("drift", encoding="utf-8")
    fake = HarmlessConsumer()
    with pytest.raises(TrialError):
        _run_formal(context, fake)
    assert not fake.calls
    assert context.stopped


def test_profile_home_configuration_is_bound_as_well_as_kicad_config(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    _write(Path(ready.record["profiles"]["writer"]["roaming"]) / "other-settings.json", "{}")
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as result:
        _run_formal(context, fake)
    assert result.value.code == "E_TRIAL_CONFIG_DRIFT" and not fake.calls


def test_aliases_with_equal_bytes_but_different_file_identity_are_refused(sandbox):
    package, work, dependencies, _ = sandbox
    canonical = dependencies["official_cli"]
    alias = _write(canonical.with_name("copy-cli.exe"), canonical.read_text(encoding="utf-8"))
    dependencies["aliases"] = [{"canonical": canonical, "aliases": [alias]}]
    fake = HarmlessConsumer()
    with pytest.raises(IdentityError, match="same current file"):
        prepare(package, work, dependencies, _runner=fake)
    assert not fake.calls and not work.exists()


def test_current_hardlink_alias_identity_is_observed_and_recorded(tmp_path):
    original = _write(tmp_path / "original.bin", "same bytes")
    alias = tmp_path / "alias.bin"
    os.link(original, alias)
    proof = verify_aliases([{"canonical": original, "aliases": [alias]}])
    assert proof[0]["identity"]["file_id"] == proof[0]["aliases"][0]["identity"]["file_id"]


def test_repeat_ready_detects_new_configuration_without_starting_a_command(sandbox):
    ready, fake = _prepare(sandbox)
    _write(Path(ready.record["profiles"]["consumer"]["config"]) / "10.0/new-subsystem.json", "{}")
    with pytest.raises(TrialError) as rejected:
        prepare(sandbox[0], sandbox[1], sandbox[2], _fixture_configuration(sandbox), _runner=fake)
    assert rejected.value.code == "E_TRIAL_CONFIG_DRIFT" and len(fake.calls) == 20


@pytest.mark.parametrize(
    "name", ["board.kicad_dru", "fp-lib-table", "sym-lib-table", "board.kicad_pro"]
)
def test_project_rule_and_library_input_drift_is_not_permitted(sandbox, name):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    target = _write(ready.work_root / "formal" / name, "frozen project rule/library")
    context = freeze_context(ready, inputs={"formal": target.parent})
    target.write_text("unapproved project rule/library", encoding="utf-8")
    fake = HarmlessConsumer()
    with pytest.raises(IdentityError):
        _run_formal(context, fake)
    assert context.stopped and not fake.calls


def test_predeclared_working_dir_metadata_migration_is_finite_and_preserves_rules(sandbox):
    allowance = {
        "profile": "consumer",
        "path": "10.0/kicad_common.json",
        "json_pointer": "/system/working_dir",
        "kind": "owned_work_path",
    }
    ready, _ = _prepare(sandbox, config={"metadata_allowances": [allowance]})
    context = _formal_context(ready)
    target = Path(ready.record["profiles"]["consumer"]["config"]) / allowance["path"]
    document = json.loads(target.read_text(encoding="utf-8"))
    document["system"]["working_dir"] = str(ready.work_root / "formal")
    target.write_text(json.dumps(document), encoding="utf-8")
    result = _run_formal(context, HarmlessConsumer())
    assert result["config_metadata_changes"]["before"][0]["json_pointer"] == "/system/working_dir"
    document["rules"] = "ignore"
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(TrialError) as rejected:
        verify_ready(ready)
    assert rejected.value.code == "E_TRIAL_CONFIG_DRIFT"


def test_default_working_dir_policy_is_strict(sandbox):
    ready, _ = _prepare(sandbox)
    target = Path(ready.record["profiles"]["consumer"]["config"]) / "10.0/kicad_common.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    document["system"]["working_dir"] = str(ready.work_root)
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(TrialError) as rejected:
        verify_ready(ready)
    assert rejected.value.code == "E_TRIAL_CONFIG_DRIFT"


def test_working_dir_allowance_does_not_allow_other_json_text_to_change(sandbox):
    allowance = {
        "profile": "consumer",
        "path": "10.0/kicad_common.json",
        "json_pointer": "/system/working_dir",
        "kind": "owned_work_path",
    }
    ready, _ = _prepare(sandbox, config={"metadata_allowances": [allowance]})
    target = Path(ready.record["profiles"]["consumer"]["config"]) / allowance["path"]
    document = json.loads(target.read_text(encoding="utf-8"))
    document["system"]["working_dir"] = str(ready.work_root)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    with pytest.raises(TrialError) as rejected:
        verify_ready(ready)
    assert rejected.value.code == "E_TRIAL_CONFIG_DRIFT"


def test_authorized_exact_input_change_advances_only_after_finite_contract(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    target = ready.work_root / "formal/board.kicad_pcb"

    def exact_change(before, after):
        return (
            set(before["files"]) == set(after["files"]) == {"board.kicad_pcb"}
            and target.read_text(encoding="utf-8") == "one authorized atom"
        )

    def write_authorized_fixture(argv, cwd, env, out, selected_ready):
        target.write_text("one authorized atom", encoding="utf-8")

    result = _run_formal(
        context,
        HarmlessConsumer(action=write_authorized_fixture),
        allowed_input_changes={"formal": exact_change},
    )
    assert not context.stopped
    assert result["inputs_after"] == context.inputs
    resumed = FrozenContext.from_dict(context.as_dict())
    assert resumed.inputs == context.inputs


def test_unexpected_formal_input_change_stops_without_refreshing_baseline(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    before = context.as_dict()["inputs"]

    def alter(argv, cwd, env, out, selected_ready):
        _write(Path(cwd) / "board.kicad_pcb", "unauthorized fixture mutation")

    with pytest.raises(TrialError) as rejected:
        _run_formal(context, HarmlessConsumer(action=alter))
    assert rejected.value.code == "E_TRIAL_INPUT_DRIFT"
    assert context.stopped and context.inputs == before


def test_failed_native_call_records_post_configuration_and_input_drift(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    before = context.as_dict()["inputs"]

    def alter_during_failed_fixture(argv, cwd, env, out, selected_ready):
        _write(Path(env["KICAD_CONFIG_HOME"]) / "10.0/new-on-failure.json", "{}")
        _write(Path(cwd) / "board.kicad_pcb", "changed during failed synthetic call")

    fake = HarmlessConsumer(abnormal=True, action=alter_during_failed_fixture)
    with pytest.raises(TrialError) as rejected:
        _run_formal(context, fake)
    error = rejected.value
    assert error.code == "E_TRIAL_NATIVE_STOP" and len(fake.calls) == 1
    assert context.stopped and context.inputs == before
    record = json.loads(Path(error.failure_record).read_text(encoding="utf-8"))
    post = record["post_failure_observation"]
    assert record["original_failure"]["code"] == "E_TRIAL_NATIVE_STOP"
    assert post["configuration"]["consumer/config"]["differences"]["added"] == [
        "10.0/new-on-failure.json"
    ]
    assert "board.kicad_pcb" in post["inputs"]["formal"]["differences"]["changed"]
    assert post["baseline_advanced"] is False and post["native_calls"] == 0
    assert post["termination_inferred_from_files"] is False


def test_failed_call_keeps_original_error_when_a_post_observation_is_incomplete(
    sandbox, monkeypatch
):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    before = context.as_dict()["inputs"]
    actual_capture = preparation_module.capture

    def incomplete_formal_capture(value):
        if Path(value) == ready.work_root / "formal":
            raise PermissionError("synthetic post-capture failure")
        return actual_capture(value)

    monkeypatch.setattr(preparation_module, "capture", incomplete_formal_capture)
    with pytest.raises(TrialError) as rejected:
        _run_formal(context, HarmlessConsumer(abnormal=True))
    error = rejected.value
    assert error.code == "E_TRIAL_NATIVE_STOP" and context.inputs == before
    post = error.post_failure_observation
    assert post["status"] == "INCOMPLETE"
    assert post["inputs"]["formal"]["error"]["type"] == "PermissionError"
    assert post["configuration"]["consumer/config"]["status"] == "OBSERVED"
    record = json.loads(Path(error.failure_record).read_text(encoding="utf-8"))
    assert record["original_failure"]["code"] == "E_TRIAL_NATIVE_STOP"
    assert record["post_failure_observation"]["status"] == "INCOMPLETE"


def test_missing_consumer_output_still_records_post_input_observation(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    before = context.as_dict()["inputs"]

    def alter_without_output(argv, cwd, env, out, selected_ready):
        _write(Path(cwd) / "board.kicad_pcb", "changed but required output was missing")

    fake = HarmlessConsumer(action=alter_without_output)
    with pytest.raises(TrialError) as rejected:
        guarded_run(
            [ready.record["dependencies"]["paths"]["official_cli"], "fixture-read-only"],
            context=context,
            profile="consumer",
            cwd=ready.work_root / "formal",
            out=ready.work_root / "logs/formal/one",
            _runner=fake,
            expected_outputs=[ready.work_root / "output/required-but-missing.json"],
        )
    error = rejected.value
    assert error.code == "E_TRIAL_OUTPUT" and len(fake.calls) == 1
    assert context.inputs == before and context.stopped
    assert (
        "board.kicad_pcb"
        in error.post_failure_observation["inputs"]["formal"]["differences"]["changed"]
    )


def test_inventory_records_both_ctime_api_domains_and_detects_new_files(tmp_path):
    root = tmp_path / "config"
    _write(root / "a.json", "{}")
    expected = capture(root)
    identity = expected["files"]["a.json"]
    assert {
        "dev",
        "file_id",
        "size",
        "sha256",
        "mtime_ns",
        "path_ctime_ns",
        "handle_ctime_ns",
    } <= set(identity)
    _write(root / "new.json", "{}")
    with pytest.raises(IdentityError) as rejected:
        verify_inventory(expected)
    assert rejected.value.details["added"] == ["new.json"]


def test_path_stat_and_handle_stat_ctime_are_not_cross_compared(tmp_path, monkeypatch):
    path = _write(tmp_path / "one.bin", "unchanged")
    actual_fstat = os.fstat

    def separate_ctime_api_domain(descriptor):
        value = actual_fstat(descriptor)
        return SimpleNamespace(
            **{key: getattr(value, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")},
            st_ctime_ns=value.st_ctime_ns + 123456,
        )

    monkeypatch.setattr(identity_module.os, "fstat", separate_ctime_api_domain)
    observed = file_identity(path)
    assert observed["handle_ctime_ns"] != observed["path_ctime_ns"]
    assert observed["sha256"]


def _synthetic_refusal_scene(tmp_path, monkeypatch):
    """Pure policy rows; these are never supplied as persisted observer evidence."""
    product = str(tmp_path / "candidate.exe")
    argv = [product, "project", "sync", "owned/project.kicad_sch", "--confirm", "spent-token"]
    root = {
        "pid": 1,
        "creation_filetime": 100,
        "process_instance_id": "1-100",
        "image": product,
        "image_sha256": PRODUCT_SHA256,
        "exit": 6,
        "identity_status": "bound",
        "metadata": {"CommandLine": json.dumps(argv)},
    }
    worker = {
        "pid": 2,
        "creation_filetime": 200,
        "process_instance_id": "2-200",
        "image": product,
        "image_sha256": PRODUCT_SHA256,
        "exit": 6,
        "identity_status": "bound",
        "parent_process_instance_id": "1-100",
        "parent_binding": "observed-parent",
        "metadata": {"ParentProcessId": 1, "CommandLine": json.dumps(argv)},
    }
    helper_path = str(tmp_path / "System32/conhost.exe")
    helper_argv = ["\\??\\" + helper_path, "0x4"]
    helper = {
        "pid": 3,
        "creation_filetime": 150,
        "process_instance_id": "3-150",
        "image": helper_path,
        "image_sha256": "a" * 64,
        "exit": 0,
        "identity_status": "bound",
        "parent_process_instance_id": "1-100",
        "parent_binding": "observed-parent",
        "metadata": {"ParentProcessId": 1, "CommandLine": json.dumps(helper_argv)},
    }
    trace = {
        "observation_schema": 4,
        "root_process_instance_id": "1-100",
        "exit": 6,
        "processes": [root, worker, helper],
        "timed_out": False,
        "job_active_before_close": 0,
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
    ready = SimpleNamespace(
        record={
            "dependencies": {
                "paths": {"product_exe": product},
                "files": {product: {"sha256": PRODUCT_SHA256}},
            },
            "public_refusal_helpers": [
                {
                    "path": helper_path,
                    "argv": helper_argv,
                    "parent": "root",
                    "identity": {"sha256": "a" * 64},
                    "max_count": 1,
                    "exit": 0,
                }
            ],
        }
    )
    declaration = {
        "code": "E_CONFLICT",
        "exit_code": 6,
        "reason": "token_used",
        "operation_id": "known-success-operation",
    }
    monkeypatch.setattr(preparation_module, "_windows_argv", json.loads)
    return ready, argv, generic, trace, declaration


def test_finite_sdk_refusal_policy_preserves_generic_and_requires_later_semantic_validation(
    tmp_path, monkeypatch
):
    ready, argv, generic, trace, declaration = _synthetic_refusal_scene(tmp_path, monkeypatch)
    original = json.loads(json.dumps(generic))
    decision, policy = preparation_module._exact_public_sdk_refusal(
        generic, trace, argv, ready=ready, declaration=declaration
    )
    assert generic == original and trace["processes"][1]["exit"] == 6
    assert decision["may_validate_functional_output"] and not decision["stop_required"]
    assert decision["functional_output_verified"] is False
    assert policy["admitted"] and policy["functional_output_verified"] is False


@pytest.mark.parametrize(
    "mismatch",
    [
        "artifact",
        "parent",
        "birth",
        "argv",
        "unknown_child",
        "critical",
        "helper_exit",
        "helper_argv",
        "duplicate_helper",
        "native_exception",
    ],
)
def test_finite_sdk_refusal_policy_does_not_exempt_other_child_or_native_failures(
    tmp_path, monkeypatch, mismatch
):
    ready, argv, generic, trace, declaration = _synthetic_refusal_scene(tmp_path, monkeypatch)
    root, worker, helper = trace["processes"]
    if mismatch == "artifact":
        root["image_sha256"] = "b" * 64
    elif mismatch == "parent":
        worker["parent_process_instance_id"] = "other-instance"
    elif mismatch == "birth":
        worker["creation_filetime"] = 99
    elif mismatch == "argv":
        worker["metadata"]["CommandLine"] = json.dumps([*argv, "--different"])
    elif mismatch == "unknown_child":
        trace["processes"].append({"image": "native-python.exe", "exit": 0})
    elif mismatch == "critical":
        generic["observation_complete"] = False
    elif mismatch == "helper_exit":
        helper["exit"] = 6
    elif mismatch == "helper_argv":
        helper["metadata"]["CommandLine"] = json.dumps([helper["image"], "0x5"])
    elif mismatch == "duplicate_helper":
        trace["processes"].append(dict(helper))
    else:
        worker["exit"] = 0xC0000409
    decision, policy = preparation_module._exact_public_sdk_refusal(
        generic, trace, argv, ready=ready, declaration=declaration
    )
    assert decision is generic and decision["stop_required"]
    assert not policy["admitted"] and policy["issues"]


def test_a_nonzero_expected_exit_without_the_finite_declaration_never_launches(sandbox):
    ready, _ = _prepare(sandbox)
    context = _formal_context(ready)
    fake = HarmlessConsumer()
    with pytest.raises(TrialError) as rejected:
        guarded_run(
            [ready.record["dependencies"]["paths"]["product_exe"]],
            context=context,
            profile="writer",
            cwd=ready.work_root / "formal",
            out=ready.work_root / "logs/formal/refusal",
            expected_exit_codes=(6,),
            _runner=fake,
        )
    assert rejected.value.code == "E_TRIAL_CONTRACT" and not fake.calls
