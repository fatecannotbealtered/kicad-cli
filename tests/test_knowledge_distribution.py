"""Provider-neutral knowledge is resolvable in source and isolated distributions."""

import json
from pathlib import Path

from scripts.build_module_skills import INSTALL_NAMES, generate

ROOT = Path(__file__).resolve().parents[1]


def validate_index(index, boundary):
    data = json.loads(index.read_bytes())
    assert data["consumer"] == "agent_explicit_file_read"
    assert data["relative_path_base"] == "configuration_file_directory"
    ids = [entry["id"] for entry in data["entries"]]
    assert len(ids) == len(set(ids))
    for entry in data["entries"]:
        location = entry["location"]
        if location["kind"] == "file":
            path = (index.parent / location["path"]).resolve()
            assert path.is_relative_to(boundary.resolve())
            assert path.is_file(), path
        else:
            assert location["kind"] == "url"
            assert location["url"].startswith("https://docs.kicad.org/")
            assert entry["required_for_first_trial"] is False
    return data


def test_source_knowledge_has_no_stale_targets():
    validate_index(ROOT / "knowledge/INDEX.json", ROOT)
    sample = json.loads((ROOT / "knowledge/external.example.json").read_bytes())
    assert sample["entries"] and all(not e["enabled"] for e in sample["entries"])
    for entry in sample["entries"]:
        assert (ROOT / "knowledge" / entry["location"]["path"]).is_file()


def test_each_isolated_skill_has_its_own_knowledge(tmp_path):
    generate(tmp_path)
    for name in INSTALL_NAMES.values():
        folder = tmp_path / name
        validate_index(folder / "reference/knowledge/INDEX.json", folder)
        assert (folder / "reference/KNOWLEDGE.md").is_file()
        manifest = json.loads((folder / "RESOURCE-MANIFEST.json").read_bytes())
        assert "reference/knowledge/INDEX.json" in manifest
        assert "reference/knowledge/external.example.json" in manifest
        assert "reference/knowledge/team/SCHEMATIC_RULES.example.md" in manifest


def test_packaging_keeps_knowledge_and_excludes_private_channel_metadata(tmp_path):
    from kicad_cli.drc_baseline import _source
    from scripts.package_internal_release import package

    # Synthetic bytes test packaging structure only, never native execution.
    build = tmp_path / "build"
    (build / "dist").mkdir(parents=True)
    (build / "dist/kicad-cli").write_bytes(b"packaging-fixture-not-executable")
    (build / "work/dependency-notices").mkdir(parents=True)
    (build / "work/dependency-notices/LICENSE").write_text("synthetic license fixture")
    (build / "BUILD-RESULT.json").write_text(
        json.dumps(
            {
                "platform": "linux",
                "architecture": "x86_64",
                "product_source": "0" * 40,
            }
        )
    )
    sample = tmp_path / "sample"
    sample.mkdir()
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({"observation": {"binding": _source()}}))
    out = tmp_path / "out"
    package(build, out, sample, binding)
    bundle = out / "kicad-cli-internal"
    validate_index(bundle / "knowledge/INDEX.json", bundle)
    assert (bundle / "third_party/runtime/LICENSE").read_text() == "synthetic license fixture"
    assert not (bundle / "release/HUB-TARGET.json").exists()
    assert not (bundle / "deployment.local").exists()
    meta = json.loads((bundle / "package.json").read_bytes())
    assert "knowledge" in meta["files"] and "docs" in meta["files"]
    for name in INSTALL_NAMES.values():
        validate_index(bundle / "skills" / name / "reference/knowledge/INDEX.json", bundle)


def test_evidence_scrubber_removes_any_windows_user_prefix():
    from scripts.package_functional_candidate import scrub

    for value in (r"C:\Users\sample.user\work", "D:/Users/example/work"):
        assert scrub(value).startswith("<USER>")
    assert scrub("https://docs.kicad.org/") == "https://docs.kicad.org/"
    assert scrub({"confirm_token": "fixture-only"})["confirm_token"] == "<redacted>"


def test_knowledge_allowlist_does_not_copy_private_extra_files(tmp_path):
    from scripts.build_module_skills import KNOWLEDGE_FILES, copy_knowledge

    source, target = tmp_path / "source", tmp_path / "target"
    for name in KNOWLEDGE_FILES:
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("fixture")
    (source / "private-team.json").write_text("must not ship")
    copy_knowledge(target, source=source)
    assert not (target / "private-team.json").exists()
    assert {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()} == set(
        KNOWLEDGE_FILES
    )
