"""Retained failure signatures are independent of test IDs and copied paths."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from native_quarantine import HASH_KEY, PATH_KEY, blocked_attempt, digest


def registry(tmp_path):
    image, schematic = tmp_path / "native.exe", tmp_path / "a.kicad_sch"
    image.write_bytes(b"harmless image fixture")
    schematic.write_bytes(b"frozen schematic fixture")
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "blocked": [
                    {
                        "id": "retained-failure",
                        "image_sha256": digest(image),
                        "input_sha256": digest(schematic),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return image, schematic, path, {PATH_KEY: str(path), HASH_KEY: digest(path)}


def test_default_does_not_change_normal_test_execution():
    assert blocked_attempt(None, {}) is None


@pytest.mark.parametrize("format", [[], ["--format", "kicadsexpr"], ["--format", "kicadxml"]])
def test_renamed_copy_and_output_format_cannot_replay_the_failure(tmp_path, format):
    image, original, _, env = registry(tmp_path)
    copy = tmp_path / "renamed.kicad_sch"
    copy.write_bytes(original.read_bytes())
    value = blocked_attempt(
        [image, "sch", "export", "netlist", *format, "-o", "new.net", copy], env
    )
    assert value["id"] == "retained-failure" and value["process_launches"] == 0


@pytest.mark.parametrize("changed", ["image", "input", "verb"])
def test_unrelated_native_operation_is_not_the_same_failure(tmp_path, changed):
    image, schematic, _, env = registry(tmp_path)
    args = [image, "sch", "export", "netlist", schematic]
    if changed == "image":
        image.write_bytes(b"different image")
    elif changed == "input":
        schematic.write_bytes(b"different input")
    else:
        args[3] = "svg"
    assert blocked_attempt(args, env) is None


def test_relative_input_cannot_hash_a_different_working_directory(tmp_path):
    image, schematic, _, env = registry(tmp_path)
    with pytest.raises(ValueError, match="Ambiguous native"):
        blocked_attempt([image, "sch", "export", "netlist", schematic.name], env)


@pytest.mark.parametrize(
    "broken", ["missing-hash", "relative-registry", "drift", "missing-entry", "bad-hash"]
)
def test_invalid_registry_refuses_before_launch(tmp_path, broken):
    image, schematic, path, env = registry(tmp_path)
    if broken == "missing-hash":
        del env[HASH_KEY]
    elif broken == "relative-registry":
        env[PATH_KEY] = "registry.json"
    elif broken == "drift":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        value = json.loads(path.read_bytes())
        if broken == "missing-entry":
            value["blocked"] = []
        else:
            value["blocked"][0]["input_sha256"] = "unknown"
        path.write_text(json.dumps(value), encoding="utf-8")
        env[HASH_KEY] = digest(path)
    with pytest.raises(ValueError):
        blocked_attempt([image, "sch", "export", "netlist", schematic], env)


def test_source_helper_skips_before_recorder_or_native_dispatch(tmp_path, monkeypatch):
    import native_process
    import process_evidence

    image, schematic, _, env = registry(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(process_evidence, "run_recorded", lambda *a, **kw: pytest.fail("launched"))
    with pytest.raises(pytest.skip.Exception, match="quarantined before launch"):
        native_process.run([image, "sch", "export", "netlist", schematic], tmp_path)
    assert json.loads((tmp_path / "QUARANTINED.json").read_bytes())["process_launches"] == 0


def test_quarantine_controls_never_enter_application_environment(tmp_path, monkeypatch):
    import native_process
    import process_evidence

    image, _, _, env = registry(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    captured = {}

    def record(folder, name, command, **kwargs):
        captured.update(kwargs["env"])
        (folder / "process.stdout").write_bytes(b"{}")
        (folder / "process.stderr").write_bytes(b"")
        return {"execution": "completed", "exit": 0}, None

    monkeypatch.setattr(process_evidence, "run_recorded", record)
    native_process.run([image, "--version"], tmp_path)
    assert PATH_KEY not in captured and HASH_KEY not in captured
    assert all(native_process.os.environ[key] == value for key, value in env.items())
