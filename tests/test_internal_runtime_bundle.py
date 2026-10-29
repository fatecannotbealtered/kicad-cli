"""The runtime bundle includes only fixed assets with current distribution permission."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

import build


def test_internal_runtime_is_complete_and_keeps_authentication(tmp_path):
    stage = tmp_path / "runtime"
    authorization = build.ROOT / "third_party/tool-driver/DISTRIBUTION.json"
    result = build.internal_runtime(build.ROOT / "kicad_cli", stage, authorization)
    manifest = result["manifest"]
    assert len(manifest["files"]) == 37
    assert len(list((stage / "_driver_sdk").glob("*.py"))) == 9
    assert len(list((stage / "driver_spec").rglob("*.py"))) == 3
    assert len(list((stage / "driver_spec/schemas").glob("*.schema.json"))) == 22
    for relative, record in manifest["files"].items():
        assert hashlib.sha256((stage / relative).read_bytes()).hexdigest() == record["sha256"]
    assert manifest["public_distribution"] is True
    assert manifest["npm_publication"] is False
    assert manifest["license_model"] == "Apache-2.0-with-preserved-MIT-baseline"
    notices = stage / "runtime-notices"
    for name in ("DRIVER-APACHE-LICENSE.txt", "PRODUCT-LICENSE.txt"):
        assert "Apache License" in (notices / name).read_text(encoding="utf-8")
    assert "MIT License" in (notices / "BASELINE-MIT-LICENSE.txt").read_text(encoding="utf-8")
    assert manifest["sdk_python_in_pyz"] is False
    assert "sqlite3" in result["hidden"]
    assert not list(stage.rglob("*.pyc"))


@pytest.mark.parametrize(
    "change", ["commit", "asset_hash", "license_model", "npm", "asset_license", "license_path"]
)
def test_internal_runtime_refuses_unbound_authorization(tmp_path, change):
    source = build.ROOT / "third_party/tool-driver/DISTRIBUTION.json"
    grant = json.loads(source.read_bytes())
    if change == "commit":
        grant["commit"] = "0" * 40
    elif change == "asset_hash":
        grant["assets"][0]["sha256"] = "0" * 64
    elif change == "license_model":
        grant["license_model"] = "unlicensed"
    elif change == "npm":
        grant["publication_policy"]["npm_publication_enabled"] = True
    elif change == "asset_license":
        grant["assets"][0]["license_basis"] = "MIT"
    else:
        grant["assets"][0]["license_path"] = "unexpected/LICENSE"
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(grant), encoding="utf-8")
    with pytest.raises(ValueError):
        build.internal_runtime(build.ROOT / "kicad_cli", tmp_path / "stage", authorization)


def test_internal_runtime_refuses_changed_source_and_unknown_schema(tmp_path):
    package = tmp_path / "package"
    pin = json.loads((build.ROOT / "kicad_cli/driver_spec/PIN.json").read_bytes())
    for relative in [*pin["files"], "driver_spec/PIN.json"]:
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(build.ROOT / "kicad_cli" / relative, target)
    unknown = package / "driver_spec/schemas/unknown.schema.json"
    unknown.write_text("{}", encoding="utf-8")
    authorization = build.ROOT / "third_party/tool-driver/DISTRIBUTION.json"
    with pytest.raises(ValueError, match="schema inventory"):
        build.internal_runtime(package, tmp_path / "unknown-stage", authorization)
    unknown.unlink()
    (package / "_driver_sdk/runtime.py").write_text(
        "raise RuntimeError('tampered')", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="source bytes"):
        build.internal_runtime(package, tmp_path / "changed-stage", authorization)


def test_product_and_independent_skills_ship_their_license():
    root = build.ROOT
    product_license = (root / "LICENSE").read_bytes()
    assert b"Apache License" in product_license
    package = json.loads((root / "package.json").read_bytes())
    assert package["license"] == "Apache-2.0"
    for skill in (root / "skills").iterdir():
        if not (skill / "SKILL.md").is_file():
            continue
        assert "license: Apache-2.0" in (skill / "SKILL.md").read_text(encoding="utf-8")
        assert (skill / "LICENSE").read_bytes() == product_license
        manifest = json.loads((skill / "RESOURCE-MANIFEST.json").read_bytes())
        assert manifest["LICENSE"] == hashlib.sha256(product_license).hexdigest()


def test_build_dependency_notices_are_complete_and_hashed(tmp_path):
    from scripts.collect_runtime_notices import collect

    destination = tmp_path / "notices"
    manifest = collect(build.ROOT, destination)
    components = {record["component"] for record in manifest["files"]}
    assert {
        "CPython",
        "pyinstaller",
        "jsonschema",
        "referencing",
        "rpds-py",
        "attrs",
        "typing-extensions",
        "jsonschema-specifications",
    } <= components
    for record in manifest["files"]:
        raw = (destination / record["path"]).read_bytes()
        assert raw and hashlib.sha256(raw).hexdigest() == record["sha256"]
    with pytest.raises(FileExistsError):
        collect(build.ROOT, destination)
