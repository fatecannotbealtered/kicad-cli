"""Finite prospective providers. Harmless files only, never product/Native."""

import copy
import hashlib
import json
from pathlib import Path, PureWindowsPath

import pytest

from bench.trial_tools import eco
from bench.trial_tools import preparation as core


@pytest.fixture
def provider_case(tmp_path):
    package = tmp_path / "package"
    resources = package / "trial_tools/resources"
    resources.mkdir(parents=True)
    labels = [
        "cmd.exe",
        "msctf.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "wldp.dll",
        "native/_ctypes.pyd",
        "native/ffi-8.dll",
    ]
    rows = []
    for label in labels:
        path = tmp_path / "providers" / label
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("harmless:" + label).encode()
        path.write_bytes(raw)
        rows.append(
            {
                "label": label,
                "path": str(path),
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    cmd = rows[0]["path"]
    conhost = str(tmp_path / "providers/conhost.exe")
    manifest = {
        "schema": "prospective-trial-provider-errata/v1",
        "files": rows,
        "public_refusal_helpers": [
            {"path": conhost, "argv": ["\\??\\" + conhost, "0x4"], "parent": "root"},
            {"path": cmd, "argv": [cmd, "/c", "ver"], "parent": "worker"},
        ],
    }
    path = resources / "TRIAL-PROVIDER-ERRATA.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return package, path, manifest


def test_prospective_seven_files_and_finite_helpers_are_explicit_before_prepare(provider_case):
    package, _, manifest = provider_case
    deps, config, selected, expected = {}, {}, set(), {}
    eco.qualify_provider_errata(package, deps, config, selected, expected, {})
    assert selected == {row["path"] for row in manifest["files"]}
    assert len(expected) == 7
    assert config["public_refusal_helpers"] == manifest["public_refusal_helpers"]
    assert set(deps["prospective_provider_errata"]["files"]) == {
        row["label"] for row in manifest["files"]
    }
    assert "no historical" in deps["prospective_provider_errata"]["scope"]


@pytest.mark.parametrize("mutation", ["missing", "bytes", "size"])
def test_missing_or_changed_provider_refuses_without_consumers(provider_case, mutation):
    package, _, manifest = provider_case
    p = Path(manifest["files"][0]["path"])
    if mutation == "missing":
        p.unlink()
    elif mutation == "bytes":
        p.write_bytes(b"x" * p.stat().st_size)
    else:
        p.write_bytes(b"different size")
    with pytest.raises(core.TrialError, match="provider"):
        eco.qualify_provider_errata(package, {}, {}, set(), {}, {})
    assert not (package / "owned-work").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-row",
        "duplicate-label",
        "non-scalar-label",
        "missing-path",
        "array-path",
        "relative-path",
    ],
)
def test_finite_provider_contract_cannot_silently_shrink(provider_case, mutation):
    package, path, manifest = provider_case
    manifest = copy.deepcopy(manifest)
    if mutation == "missing-row":
        manifest["files"].pop()
    elif mutation == "duplicate-label":
        manifest["files"][1]["label"] = manifest["files"][0]["label"]
    elif mutation == "non-scalar-label":
        manifest["files"][1]["label"] = ["invalid"]
    elif mutation == "missing-path":
        del manifest["files"][1]["path"]
    elif mutation == "array-path":
        manifest["files"][1]["path"] = ["invalid"]
    else:
        manifest["files"][1]["path"] = "relative-library.dll"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(core.TrialError, match="seven-provider"):
        eco.qualify_provider_errata(package, {}, {}, set(), {}, {})


def test_explicit_helper_optout_is_preserved_for_preflight_refusal(provider_case):
    package, _, _ = provider_case
    config = {"public_refusal_helpers": []}
    eco.qualify_provider_errata(package, {}, config, set(), {}, {})
    assert config["public_refusal_helpers"] == []


def test_shipped_seven_provider_manifest_has_real_path_and_finite_helper_schema():
    resource = Path(eco.__file__).parent / "resources/TRIAL-PROVIDER-ERRATA.json"
    value = json.loads(resource.read_bytes())
    assert value["schema"] == "prospective-trial-provider-errata/v1"
    assert len(value["files"]) == 7
    assert all(
        isinstance(row["path"], str) and PureWindowsPath(row["path"]).is_absolute()
        for row in value["files"]
    )
    assert len({row["path"].casefold() for row in value["files"]}) == 7
    assert len(value["public_refusal_helpers"]) == 2
    assert {row["parent"] for row in value["public_refusal_helpers"]} == {"root", "worker"}
