"""Fixed-source checks reject changed SDK or seed bytes without network access."""

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_fixed_specs import check

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("target", [".agent/CLI-SPEC.md", "kicad_cli/_driver_sdk/tokens.py"])
def test_fixed_sources_refuse_modified_bytes(tmp_path, target):
    seed = json.loads((ROOT / "contract/spec-lock.json").read_bytes())
    pin = json.loads((ROOT / "kicad_cli/driver_spec/PIN.json").read_bytes())
    names = set(seed["seed"]["files"])
    names.update("kicad_cli/" + path for path in pin["files"])
    names.update(
        {
            "contract/spec-lock.json",
            ".agent/SPEC_VERSION",
            "kicad_cli/driver_spec/PIN.json",
            "kicad_cli/driver_runtime.py",
        }
    )
    for name in names:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    assert check(tmp_path) == []
    path = tmp_path / target
    path.write_bytes(path.read_bytes() + b"\n# unapproved drift\n")
    assert any(target.removeprefix("kicad_cli/") in problem for problem in check(tmp_path))
