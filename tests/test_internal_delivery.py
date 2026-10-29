"""Internal delivery scope cannot promote missing whole-product evidence."""

import json
from pathlib import Path

from kicad_cli.internal_release import SERIES, SUPPORTED, TAG, profile


def test_internal_scope_uses_the_single_product_version_without_promoting_readiness():
    from kicad_cli import __version__
    from kicad_cli.driver_runtime import declarations
    from scripts.check_release import validate

    package_version = json.loads((Path(__file__).parents[1] / "package.json").read_bytes())[
        "version"
    ]
    assert SERIES == __version__ == package_version and TAG == f"v{package_version}"
    assert profile()["public_distribution"] is True
    root = Path(__file__).parents[1]
    assert json.loads((root / "release/PROFILE.json").read_bytes()) == profile()
    delivery = json.loads((root / "docs/modules/DELIVERY-BINDING.json").read_bytes())
    assert delivery["product_version"] == delivery["series"] == __version__
    assert delivery["tag"] == TAG
    ref = declarations()["reference"]
    fixed = json.loads((Path(__file__).parents[1] / "kicad_cli/driver_contract.json").read_bytes())[
        "reference"
    ]["release_readiness"]
    assert ref["release_readiness"] == fixed
    assert ref["release_readiness"]["level"] == "unpublishable"
    assert ref["release_readiness"]["fcc_status"] == "missing"
    assert ref["extensions"]["internal_delivery"] == profile()
    assert profile()["qualification"]["status"] == "pending_exact_artifact_acceptance"
    assert "board route patch" in SUPPORTED and "board route rebuild" not in SUPPORTED
    assert validate({"ok": True, "schema_version": "1.0", "data": ref}, __version__, TAG)


def test_internal_scope_has_module_owners_and_complete_summaries():
    from kicad_cli.driver_runtime import MIGRATED, declarations
    from kicad_cli.module_contract import MODULES

    paths = {c["path"] for c in declarations()["reference"]["commands"]}
    owners = {path for module in MODULES.values() for path in module["commands"]}
    assert paths == MIGRATED == owners
    assert "changelog" in MODULES["kicad-cli"]["commands"]
    assert "project links repair" in MODULES["kicad-pcb"]["commands"]
    summaries = set()
    for module in MODULES.values():
        supported = set(module["internal_1_0_supported_commands"])
        assert supported == set(module["commands"]) & SUPPORTED
        summaries.update(supported)
    assert summaries == SUPPORTED


def test_real_discovery_and_doctor_validate_with_fixed_sdk(tmp_path, monkeypatch):
    from kicad_cli import driver_runtime, kicad_env

    # Exercise the real SDK/adapter output without probing an installed CAD app.
    monkeypatch.setattr(kicad_env, "find_python", lambda: None)
    monkeypatch.setattr(kicad_env, "find_official_cli", lambda: None)
    with driver_runtime.create_cli(home=tmp_path, environ={}) as cli:
        results = {}
        for command, schema in (("reference", "reference"), ("doctor", "doctor")):
            result = cli.run([command])
            assert result.envelope["ok"], result.envelope
            cli.validator.validate("response", result.envelope)
            cli.validator.validate(schema, result.envelope["data"])
            results[command] = result.envelope["data"]
        result = cli.run(["modules"])
        assert result.envelope["ok"], result.envelope
        cli.validator.validate("response", result.envelope)
        assert result.envelope["data"]["observation"]["internal_delivery"] == profile()
        assert results["reference"]["extensions"]["internal_delivery"] == profile()
        checks = {row["check"]: row for row in results["doctor"]["checks"]}
        assert checks["release_readiness"]["status"] == "fail"
        assert "unpublishable" in checks["release_readiness"]["message"]
        assert checks["internal_delivery"]["status"] == "warn"
