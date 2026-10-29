"""Harmless subprocess proofs for reviewed plan continuation; no KiCad is invoked."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bench.candidate_regression import EXE_MODULES, collection_modules, sha

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_exe_ids_select_their_exact_new_modules_without_importing_them(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("test_new_sync_exe.py", "test_new_candidate_exe.py"):
        (tests / name).write_text(
            "raise RuntimeError('Selection must never import or execute this fixture')\n",
            encoding="utf-8",
        )
    selected = [
        "tests/test_new_sync_exe.py::test_sync[one]",
        "tests/test_new_candidate_exe.py::test_candidate",
        "tests/test_new_sync_exe.py::test_sync[two]",
    ]
    assert collection_modules({"mode": "exe", "selected_ids": selected}, tmp_path) == [
        "tests/test_new_candidate_exe.py",
        "tests/test_new_sync_exe.py",
    ]


@pytest.mark.parametrize(
    "nodeid",
    [
        "tests/../outside.py::test_case",
        "tests\\test_probe.py::test_case",
        "tests/missing.py::test_case",
        "tests/test_probe.py",
        "tests//test_probe.py::test_case",
    ],
)
def test_explicit_exe_module_selection_rejects_nonexact_or_escaping_paths(tmp_path, nodeid):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_probe.py").write_text("# never imported\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("# never imported\n", encoding="utf-8")
    with pytest.raises(ValueError):
        collection_modules({"mode": "exe", "selected_ids": [nodeid]}, tmp_path)


@pytest.mark.parametrize("mode", ["source", "exe"])
@pytest.mark.parametrize("selected", [[], "tests/test_probe.py::test_case"])
def test_explicit_module_selection_rejects_empty_or_nonlist_ids(tmp_path, mode, selected):
    with pytest.raises(ValueError, match="nonempty list"):
        collection_modules({"mode": mode, "selected_ids": selected}, tmp_path)


def test_exe_default_module_list_and_source_default_are_preserved(tmp_path):
    assert collection_modules({"mode": "exe"}, tmp_path) == [
        "tests/" + name + ".py" for name in EXE_MODULES
    ]
    assert collection_modules({"mode": "source"}, tmp_path) == ["tests/"]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def case(tmp_path):
    source = tmp_path / "独立源码 空格"
    source.mkdir()
    for folder, names in {
        "bench": ["candidate_regression.py", "process_evidence.py", "owned_process.py"],
        "scripts": ["fcc_evidence.py"],
    }.items():
        (source / folder).mkdir()
        for name in names:
            shutil.copyfile(ROOT / folder / name, source / folder / name)
    (source / "tests").mkdir()
    (source / "tests/test_probe.py").write_text(
        "import os\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.mark.parametrize('variant', ['alpha', 'beta'])\n"
        "def test_harmless(variant):\n"
        "    with Path(os.environ['PROBE_LOG']).open('a', encoding='utf-8') as f:\n"
        "        f.write(variant + '\\n')\n",
        encoding="utf-8",
    )
    (source / "tests/test_unselected_native_consumer.py").write_text(
        "raise RuntimeError('An unselected native consumer must never be imported')\n",
        encoding="utf-8",
    )
    (source / "docs/fcc").mkdir(parents=True)
    for name, value in {
        "package.json": {},
        "docs/fcc/behavior-inventory.json": {},
    }.items():
        write_json(source / name, value)
    (source / "build.py").write_text("# harmless fixture\n", encoding="utf-8")
    (source / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Plan proof",
            "-c",
            "user.email=plan@example.invalid",
            "commit",
            "-qm",
            "Harmless plan fixture",
        ],
    ):
        subprocess.run(command, cwd=source, check=True, capture_output=True)
    binary = tmp_path / "artifact.exe"
    binary.write_bytes(b"not executed; artifact-binding proof")
    dependency = tmp_path / "dependency"
    dependency.mkdir()
    (dependency / "sdk.py").write_text("# pinned harmless dependency\n", encoding="utf-8")
    for root in (source / "bench", dependency):
        (root / "__pycache__").mkdir()
        (root / "__pycache__/identity-proof.cpython-312.pyc").write_bytes(b"frozen cache identity")
    config_home = tmp_path / "configuration"
    config_home.mkdir()
    write_json(config_home / "kicad_common.json", {"fixture": True})
    config = {
        "source": str(source),
        "output": str(tmp_path / "reviewed plan"),
        "mode": "source",
        "binary": str(binary),
        "scope": "harmless-proof-only",
        "selected_ids": [
            "tests/test_probe.py::test_harmless[alpha]",
            "tests/test_probe.py::test_harmless[beta]",
        ],
        "environment": {
            "PROBE_LOG": str(tmp_path / "executed.txt"),
            "KICAD_CONFIG_HOME": str(config_home),
            "PYTHONIOENCODING": "utf-8",
        },
        "dependency_paths": [str(dependency)],
        "timeout_seconds": 30,
    }
    config_file = tmp_path / "config.json"
    write_json(config_file, config)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("PYTHONPATH", None)
    return source, config, config_file, env


def invoke(case, extra=()):
    source, config, config_file, env = case
    write_json(config_file, config)
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "bench.candidate_regression",
            "--config",
            str(config_file),
            *extra,
        ],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def freeze(case):
    result = invoke(case)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = Path(case[1]["output"]) / "FROZEN-TESTS.json"
    assert not Path(case[1]["environment"]["PROBE_LOG"]).exists()
    case[1]["output"] = str(plan.parent.parent / "执行 新目录")
    return plan, sha(plan)


def test_original_plan_executes_in_new_output_without_refreezing(case):
    plan, digest = freeze(case)
    original = plan.read_bytes()
    result = invoke(case, ["--execute", "--plan", str(plan), "--plan-sha256", digest])
    assert result.returncode == 0, result.stdout + result.stderr
    output = Path(case[1]["output"])
    assert (output / "FROZEN-TESTS.json").read_bytes() == original
    assert Path(case[1]["environment"]["PROBE_LOG"]).read_text().splitlines() == ["alpha", "beta"]
    assert (output / "EXECUTION-REVALIDATED.json").is_file()
    assert (output / "FINISH.json").is_file() and (output / "junit.xml").is_file()
    collection = json.loads((plan.parent / "collection.call.json").read_bytes())
    execution = json.loads((output / "pytest.call.json").read_bytes())
    assert collection["role"] == "plan-collection-python-not-test-execution"
    assert execution["role"] == "serial-regression-runner"
    assert collection["execution"] == execution["execution"] == "completed"
    if os.name == "nt":
        assert collection["job_finalization"]["natural_tree_complete"]
        assert execution["job_finalization"]["natural_tree_complete"]
    assert not (output / "collection.call.json").exists()


@pytest.mark.parametrize(
    "drift",
    [
        "plan",
        "source",
        "method",
        "artifact",
        "selected_ids",
        "variant_order",
        "environment",
        "inherited_environment",
        "pytest_plugins",
        "pytest_addopts",
        "config",
        "configuration_bytes",
        "dependency_bytes",
        "dependency_added",
        "source_cache",
        "dependency_cache",
    ],
)
def test_drift_refuses_before_test_process_or_execution_collection(case, drift):
    plan, digest = freeze(case)
    source, config, _, env = case
    if drift == "plan":
        value = json.loads(plan.read_bytes())
        value["ids"][0]["nodeid"] += "-tampered"
        write_json(plan, value)
    elif drift in {"source", "method"}:
        path = source / (
            "tests/test_probe.py" if drift == "source" else "bench/candidate_regression.py"
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n# drift\n")
    elif drift == "artifact":
        Path(config["binary"]).write_bytes(b"changed artifact")
    elif drift == "selected_ids":
        config["selected_ids"].pop()
    elif drift == "variant_order":
        config["selected_ids"].reverse()
    elif drift == "environment":
        config["environment"]["NEW_BEHAVIOR_SETTING"] = "changed"
    elif drift == "inherited_environment":
        env["UNDECLARED_BEHAVIOR_SETTING"] = "changed"
    elif drift == "pytest_plugins":
        env["PYTEST_PLUGINS"] = "undeclared_external_plugin"
    elif drift == "pytest_addopts":
        config["environment"]["PYTEST_ADDOPTS"] = "-p undeclared_external_plugin"
    elif drift == "config":
        config["max_failures"] = 2
    elif drift == "configuration_bytes":
        write_json(Path(config["environment"]["KICAD_CONFIG_HOME"]) / "kicad_common.json", {})
    elif drift == "dependency_bytes":
        (Path(config["dependency_paths"][0]) / "sdk.py").write_bytes(b"changed dependency")
    elif drift == "dependency_added":
        (Path(config["dependency_paths"][0]) / "new.py").write_bytes(b"new dependency")
    elif drift in {"source_cache", "dependency_cache"}:
        root = source if drift == "source_cache" else Path(config["dependency_paths"][0])
        cache = root / ("bench/__pycache__" if drift == "source_cache" else "__pycache__")
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "identity-proof.cpython-312.pyc").write_bytes(
            b"changed pre-existing cache identity"
        )
    result = invoke(case, ["--execute", "--plan", str(plan), "--plan-sha256", digest])
    assert result.returncode != 0
    output = Path(config["output"])
    refusal = json.loads((output / "REFUSED.json").read_bytes())
    assert refusal["test_processes_started"] == refusal["collection_processes_started"] == 0
    assert not (output / "pytest.call.json").exists()
    assert not (output / "collection.call.json").exists()
    assert not Path(config["environment"]["PROBE_LOG"]).exists()
    if drift == "source_cache":
        assert "source_cache_identity" in refusal["reason"]


@pytest.mark.parametrize(
    "nodeid",
    [
        "tests/../build.py::test_harmless",
        "tests\\test_probe.py::test_harmless[alpha]",
        "tests/test_probe.py",
        "tests/missing.py::test_harmless",
    ],
)
def test_selected_source_modules_reject_nonexact_or_escaping_paths(case, nodeid):
    case[1]["selected_ids"] = [nodeid]
    result = invoke(case)
    assert result.returncode != 0
    output = Path(case[1]["output"])
    assert (output / "REFUSED.json").is_file()
    assert not (output / "collection.call.json").exists()
    assert not Path(case[1]["environment"]["PROBE_LOG"]).exists()


def test_execute_requires_explicit_original_plan_and_digest(case):
    result = invoke(case, ["--execute"])
    assert result.returncode != 0 and "requires both --plan" in result.stderr
    assert not Path(case[1]["output"]).exists()


@pytest.mark.parametrize("drift", ["exact_variant", "artifact_during_execution_collection"])
def test_execution_collection_revalidates_before_setup(case, drift):
    if drift == "artifact_during_execution_collection":
        source, config, _, _ = case
        with (source / "tests/test_probe.py").open("a", encoding="utf-8") as stream:
            stream.write(
                "\nif os.environ.get('CANDIDATE_REGRESSION_PLAN'):\n"
                f"    Path({config['binary']!r}).write_bytes(b'collection-time artifact drift')\n"
            )
    plan, _ = freeze(case)
    if drift == "exact_variant":
        value = json.loads(plan.read_bytes())
        value["ids"][0]["nodeid"] = "tests/test_probe.py::test_harmless[wrong-variant]"
        write_json(plan, value)
    result = invoke(case, ["--execute", "--plan", str(plan), "--plan-sha256", sha(plan)])
    assert result.returncode != 0
    output = Path(case[1]["output"])
    execution = json.loads((output / "pytest.call.json").read_bytes())
    assert execution["execution"] == "nonzero_exit"
    text = "".join(
        (output / ("pytest." + stream)).read_text(encoding="utf-8")
        for stream in ("stdout", "stderr")
    )
    assert "drift since frozen plan" in text
    assert not Path(case[1]["environment"]["PROBE_LOG"]).exists()
    assert not (output / "EXECUTION-REVALIDATED.json").exists()


def refuse_original(case, plan, digest, marker):
    result = invoke(case, ["--execute", "--plan", str(plan), "--plan-sha256", digest])
    assert result.returncode != 0, result.stdout + result.stderr
    output = Path(case[1]["output"])
    refused = json.loads((output / "REFUSED.json").read_bytes())
    assert refused["collection_processes_started"] == refused["test_processes_started"] == 0
    assert not (output / "pytest.call.json").exists()
    assert not (output / "collection.call.json").exists()
    assert not marker.exists()
    assert not Path(case[1]["environment"]["PROBE_LOG"]).exists()


@pytest.mark.parametrize(
    "location", ["conftest.py", "tests/conftest.py", "tests/nested/conftest.py"]
)
@pytest.mark.parametrize("mutation", ["add", "modify", "delete", "replace_with_directory"])
def test_conftest_input_drift_refuses_before_pytest_launch(case, location, mutation):
    source = case[0]
    path = source / location
    path.parent.mkdir(parents=True, exist_ok=True)
    if location.startswith("tests/nested/"):
        (source / "tests/nested/test_probe.py").write_bytes(
            (source / "tests/test_probe.py").read_bytes()
        )
        case[1]["selected_ids"] = [
            nodeid.replace("tests/test_probe.py", "tests/nested/test_probe.py")
            for nodeid in case[1]["selected_ids"]
        ]
    if mutation != "add":
        path.write_text("# reviewed conftest\n", encoding="utf-8")
    plan, digest = freeze(case)
    marker = path.parent / "injected-autouse-marker.txt"
    if mutation in {"add", "modify"}:
        path.write_text(
            "from pathlib import Path\nimport pytest\n"
            "@pytest.fixture(autouse=True)\n"
            "def injected():\n"
            f"    Path({str(marker)!r}).write_text('unexpected execution', encoding='utf-8')\n",
            encoding="utf-8",
        )
    else:
        path.unlink()
        if mutation == "replace_with_directory":
            path.mkdir()
    refuse_original(case, plan, digest, marker)


@pytest.mark.parametrize(
    "name",
    [
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
        "pyproject.toml",
        "pytest.toml",
        ".pytest.toml",
    ],
)
@pytest.mark.parametrize("mutation", ["add", "modify", "delete", "replace_with_directory"])
def test_pytest_configuration_inventory_drift_refuses_before_launch(case, name, mutation):
    path = case[0] / name
    if name == "pyproject.toml" and mutation == "add":
        # The actual config must already exist, so this exercises a new nested
        # candidate rather than pretending a missing active config was frozen.
        path = case[0] / "tests" / name
    if name == "pyproject.toml":
        content = "[tool.pytest.ini_options]\n"
    else:
        content = "[tool:pytest]\n" if name == "setup.cfg" else "[pytest]\n"
    if mutation != "add":
        path.write_text(content, encoding="utf-8")
    plan, digest = freeze(case)
    if mutation in {"add", "modify"}:
        path.write_text(content + "# changed input\n", encoding="utf-8")
    else:
        path.unlink()
        if mutation == "replace_with_directory":
            path.mkdir()
    refuse_original(case, plan, digest, path.parent / "config-injected-marker.txt")


def test_parent_configuration_and_conftest_cannot_implicitly_change_execution(case):
    plan, digest = freeze(case)
    marker = case[0].parent / "parent-autouse-marker.txt"
    (case[0].parent / "pytest.ini").write_text(
        "[pytest]\naddopts = --unknown-parent-option\n", encoding="utf-8"
    )
    (case[0].parent / "conftest.py").write_text(
        "from pathlib import Path\nimport pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def injected():\n"
        f"    Path({str(marker)!r}).write_text('parent loaded', encoding='utf-8')\n",
        encoding="utf-8",
    )
    result = invoke(case, ["--execute", "--plan", str(plan), "--plan-sha256", digest])
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
    assert Path(case[1]["environment"]["PROBE_LOG"]).read_text().splitlines() == ["alpha", "beta"]
    frozen = json.loads(plan.read_bytes())
    assert frozen["binding"]["pytest_profile"]["confcutdir"] == str(case[0].resolve())
    assert frozen["pytest_context"]["inipath"] == str(case[0] / "pyproject.toml")


@pytest.mark.parametrize("option", ["addopts", "pythonpath", "required_plugins"])
@pytest.mark.parametrize("table", ["tool.pytest.ini_options", "tool.pytest"])
def test_implicit_pytest_startup_dependencies_are_refused_during_preflight(case, option, table):
    (case[0] / "pyproject.toml").write_text(
        f'[{table}]\n{option} = "undeclared-dependency"\n', encoding="utf-8"
    )
    result = invoke(case)
    assert result.returncode != 0
    assert (Path(case[1]["output"]) / "REFUSED.json").is_file()
    assert not (Path(case[1]["output"]) / "collection.call.json").exists()


def test_undeclared_conftest_plugin_is_refused_before_import(case):
    marker = case[0].parent / "plugin-import-marker.txt"
    (case[0] / "conftest.py").write_text(
        "pytest_plugins = ['undeclared_plugin']\n", encoding="utf-8"
    )
    (case[0] / "undeclared_plugin.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    result = invoke(case)
    assert result.returncode != 0
    assert not (Path(case[1]["output"]) / "collection.call.json").exists()
    assert not marker.exists()


@pytest.mark.parametrize("mutation", ["add", "modify", "delete", "replace_with_directory"])
def test_declared_external_plugin_dependency_drift_refuses_before_import(case, mutation):
    dependency = Path(case[1]["dependency_paths"][0])
    plugin = dependency / "declared_plugin.py"
    plugin.write_text("# reviewed external pytest plugin\n", encoding="utf-8")
    case[1]["pytest_plugin_modules"] = ["declared_plugin"]
    case[1]["environment"]["PYTHONPATH"] = str(dependency)
    plan, digest = freeze(case)
    marker = dependency / "plugin-import-marker.txt"
    if mutation == "add":
        (dependency / "new_executable.py").write_text("# unreviewed dependency\n", encoding="utf-8")
    elif mutation == "modify":
        plugin.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
            encoding="utf-8",
        )
    else:
        plugin.unlink()
        if mutation == "replace_with_directory":
            plugin.mkdir()
    refuse_original(case, plan, digest, marker)


def test_root_executable_addition_is_bound_even_without_git_tracking(case):
    plan, digest = freeze(case)
    path = case[0] / "new_fixture_helper.py"
    path.write_text("# newly executable input\n", encoding="utf-8")
    refuse_original(case, plan, digest, path.with_suffix(".marker"))


def test_explicit_external_plugin_continues_original_plan_in_new_output(case):
    dependency = Path(case[1]["dependency_paths"][0])
    marker = case[0].parent / "declared-plugin-executed.txt"
    (dependency / "declared_plugin.py").write_text(
        "from pathlib import Path\nimport pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def declared_fixture():\n"
        f"    with Path({str(marker)!r}).open('a', encoding='utf-8') as stream:\n"
        "        stream.write('declared fixture\\n')\n",
        encoding="utf-8",
    )
    (case[0] / "conftest.py").write_text("pytest_plugins = ['declared_plugin']\n", encoding="utf-8")
    case[1]["pytest_plugin_modules"] = ["declared_plugin"]
    case[1]["environment"]["PYTHONPATH"] = str(dependency)
    plan, digest = freeze(case)
    assert not marker.exists()
    result = invoke(case, ["--execute", "--plan", str(plan), "--plan-sha256", digest])
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text().splitlines() == ["declared fixture", "declared fixture"]


def test_previous_plan_format_is_refused_without_silent_refreeze(case):
    plan, _ = freeze(case)
    value = json.loads(plan.read_bytes())
    value["format"] = "kicad-cli.candidate-regression-plan.v2"
    write_json(plan, value)
    refuse_original(case, plan, sha(plan), plan.parent / "old-plan-executed.txt")


def test_programmatic_plugin_requires_a_bound_implementation_origin(case):
    external = case[0].parent / "undeclared_external_plugin.py"
    marker = case[0].parent / "undeclared-plugin-test-executed.txt"
    external.write_text(
        "from pathlib import Path\nimport pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def unexpected_fixture():\n"
        f"    Path({str(marker)!r}).write_text('unexpected fixture')\n",
        encoding="utf-8",
    )
    (case[0] / "conftest.py").write_text(
        "import importlib.util\n"
        "def pytest_configure(config):\n"
        f"    spec = importlib.util.spec_from_file_location('external_plugin', {str(external)!r})\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n"
        "    config.pluginmanager.register(module, 'external_plugin')\n",
        encoding="utf-8",
    )
    result = invoke(case)
    assert result.returncode != 0
    output = Path(case[1]["output"])
    call = json.loads((output / "collection.call.json").read_bytes())
    assert call["execution"] == "nonzero_exit"
    assert "undeclared executable origin" in (output / "collection.stdout").read_text(
        encoding="utf-8"
    )
    assert not (output / "FROZEN-TESTS.json").exists()
    assert not marker.exists()
    assert not Path(case[1]["environment"]["PROBE_LOG"]).exists()
