"""A detected KiCad root must not break ordinary embedded-symbol editing."""

import json
import shutil
from pathlib import Path

from kicad_cli import engineering_inputs, kicad_env


def test_schematic_edit_captures_installation_resources_without_name_shadowing(
    tmp_path, monkeypatch
):
    fixture = Path(__file__).parent / "fixtures/sch_edit"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    changes = project / "changes.json"
    changes.write_text(json.dumps({"changes": [{"op": "set", "ref": "R1", "value": "12k"}]}))
    root = tmp_path / "cad"
    root.mkdir()
    monkeypatch.setattr(kicad_env, "find_kicad_root", lambda: str(root))
    monkeypatch.setattr(
        engineering_inputs.lib_tables, "default_config_dir", lambda: tmp_path / "profile"
    )
    captured, primary = engineering_inputs.capture(
        "sch edit", {"schematic": str(project / "edit.kicad_sch"), "changes": str(changes)}
    )
    assert primary == project / "edit.kicad_sch"
    assert any(path.parent == kicad_env.resources(root) / "symbols" for path in captured.files)
    captured.validate()
