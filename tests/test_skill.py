"""Generated module Skills must install alone and describe the actual runtime."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from kicad_cli import __version__
from kicad_cli.driver_runtime import declarations
from kicad_cli.module_contract import MODULES

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", list(MODULES))
def test_generated_skill_is_self_contained_and_bound(name, tmp_path):
    from scripts.build_module_skills import INSTALL_NAMES, generate

    generate(tmp_path, name)
    folder = tmp_path / INSTALL_NAMES[name]
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert f"name: {folder.name}" in text and f'version: "{__version__}"' in text
    assert len(text.splitlines()) < 500
    assert "../../" not in text
    assert "reference/common.md" in text
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if not target.startswith(("http", "#")):
            assert (folder / target).is_file()
    binding = json.loads((folder / "MODULE-BINDING.json").read_bytes())
    assert binding["module"] == name and binding["install_name"] == folder.name
    assert binding["skill_dependencies"] == []
    assert binding["capability"] == MODULES[name]
    manifest = json.loads((folder / "RESOURCE-MANIFEST.json").read_bytes())
    assert all(
        hashlib.sha256((folder / p).read_bytes()).hexdigest() == v for p, v in manifest.items()
    )
    assert all(b"\r\n" not in (folder / p).read_bytes() for p in manifest)
    paths = {c["path"] for c in declarations()["reference"]["commands"]}
    assert set(binding["capability"]["commands"]) <= paths
    selected = [
        command
        for command in declarations()["reference"]["commands"]
        if command["path"] in binding["capability"]["commands"]
    ]
    assert (
        binding["command_contract_sha256"]
        == hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    )
    prompts = json.loads((folder / "test-prompts.json").read_bytes())
    assert len(prompts) >= 6
    readonly = next(p for p in prompts if "只检查" in p["prompt"])
    assert readonly["modules"] == ["kicad-review"] and not readonly["may_write_when_authorized"]


def test_shared_rules_are_generated_from_one_source():
    from scripts.build_module_skills import INSTALL_NAMES

    common = (ROOT / "docs/modules/common.md").read_bytes()
    for name in MODULES:
        path = ROOT / "skills" / INSTALL_NAMES[name] / "reference/common.md"
        assert path.read_bytes() == common
    entry = (ROOT / "skills/kicad-cli/SKILL.md").read_text(encoding="utf-8")
    assert len(entry.splitlines()) < 100
    assert "libfile_" not in entry


def test_each_module_has_concrete_unexecuted_agent_scenarios():
    from scripts.build_module_skills import INSTALL_NAMES, SCENARIOS

    assert set(SCENARIOS) == set(MODULES)
    for module, cases in SCENARIOS.items():
        assert len(cases) >= 3
        assert len({case["id"] for case in cases}) == len(cases)
        assert all(case["prompt"] and case["expected"] for case in cases)
        folder = ROOT / "skills" / INSTALL_NAMES[module]
        prompts = json.loads((folder / "test-prompts.json").read_bytes())
        assert all(case["agent_evaluation"] == "not_run" for case in prompts)
        assert {case["id"] for case in cases} <= {case["id"] for case in prompts}
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        assert "reference --index" in text
        assert "STOP CHECKPOINT" in text and "## Eval Scenarios" in text
        assert "test-prompts.json" in text and "npx skills add" in text
    assert {case["scenario"] for case in SCENARIOS["kicad-pcb"]} >= {
        "read-only-diagnostics",
        "authorized-straight-patch",
        "exact-duplicate-cleanup",
        "missing-project-rules",
        "invalid-endpoint",
        "plane-preservation-conflict",
        "identity-repair",
        "unknown-outcome",
        "locked-copper",
    }


def test_generated_skill_resources_are_current(tmp_path):
    from scripts.build_module_skills import INSTALL_NAMES, generate

    generate(tmp_path)
    for install_name in INSTALL_NAMES.values():
        fresh = tmp_path / install_name
        checked = ROOT / "skills" / install_name
        manifest = json.loads((fresh / "RESOURCE-MANIFEST.json").read_bytes())
        assert list(manifest) == sorted(manifest), "Manifest key order must be OS-independent"
        for path in fresh.rglob("*"):
            if path.is_file():
                assert path.read_bytes() == (checked / path.relative_to(fresh)).read_bytes()
