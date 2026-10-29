"""Provider-neutral knowledge is resolvable in source and isolated distributions."""

import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("target_os", "architecture", "binary_name"),
    [
        ("win32", "AMD64", "kicad-cli.exe"),
        ("darwin", "arm64", "kicad-cli"),
        ("darwin", "x86_64", "kicad-cli"),
        ("linux", "x86_64", "kicad-cli"),
    ],
)
def test_packaging_keeps_user_resources_and_excludes_development_material(
    tmp_path, target_os, architecture, binary_name
):
    from kicad_cli import __version__
    from kicad_cli.drc_baseline import _source
    from scripts.package_internal_release import package

    # Synthetic bytes test packaging structure only, never native execution.
    build = tmp_path / "build"
    (build / "dist").mkdir(parents=True)
    (build / "dist" / binary_name).write_bytes(b"packaging-fixture-not-executable")
    (build / "work/dependency-notices").mkdir(parents=True)
    (build / "work/dependency-notices/LICENSE").write_text("synthetic license fixture")
    (build / "BUILD-RESULT.json").write_text(
        json.dumps(
            {
                "platform": target_os,
                "architecture": architecture,
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
    for excluded in ("qualification", "examples/arc-scope", "docs/modules/DELIVERY.md", "tests"):
        assert not (bundle / excluded).exists()
    assert not list(bundle.rglob("test-prompts.json"))
    meta = json.loads((bundle / "package.json").read_bytes())
    assert "knowledge" in meta["files"] and "docs" in meta["files"]
    assert "qualification" not in meta["files"]
    assert meta["version"] == __version__
    assert (bundle / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert (bundle / "NOTICE_zh.md").read_bytes() == (ROOT / "NOTICE_zh.md").read_bytes()
    assert (bundle / "examples/library/sn74lvc1g14").is_dir()
    assert (bundle / "examples/functional-schmitt/intent.json").is_file()
    example = (bundle / "examples/functional-schmitt/README.md").read_text(encoding="utf-8")
    assert "CONTINUATION-INTENT-BEFORE.json" not in example
    assert "没有仿真、实物、上电或电气测量" in example
    assert "CONTINUATION-INTENT-BEFORE.json" in (
        ROOT / "bench/functional-schmitt-notes.md"
    ).read_text(encoding="utf-8")
    config = json.loads((bundle / "bin/runtime-config.example.json").read_bytes())
    assert config["format_version"] == 1 and config["kicad_root"] and config["profile_dir"]
    files = {
        p.relative_to(bundle).as_posix(): p.read_bytes() for p in bundle.rglob("*") if p.is_file()
    }
    manifest = json.loads(files["FILE-MANIFEST.json"])
    assert set(manifest) == files.keys() - {"FILE-MANIFEST.json"}
    assert all(hashlib.sha256(files[name]).hexdigest() == value for name, value in manifest.items())
    for name in ("README.md", "README_zh.md", "NOTICE.md", "NOTICE_zh.md"):
        for link in re.findall(r"\]\(([^)]+)\)", files[name].decode("utf-8")):
            if not link.startswith(("http", "#")):
                assert (bundle / link).is_file(), link
    for name in INSTALL_NAMES.values():
        folder = bundle / "skills" / name
        validate_index(folder / "reference/knowledge/INDEX.json", folder)
        bound = json.loads((folder / "MODULE-BINDING.json").read_bytes())
        assert bound["source_code_sha256"] == _source()["code_sha256"]
        assert bound["executable_sha256"] == hashlib.sha256(files["bin/" + binary_name]).hexdigest()
        assert bound["capability"]["regression_group"]
        skill_manifest = json.loads((folder / "RESOURCE-MANIFEST.json").read_bytes())
        for path, expected in skill_manifest.items():
            assert hashlib.sha256((folder / path).read_bytes()).hexdigest() == expected
    for path, content in files.items():
        if path.endswith((".md", ".json")):
            for retired in (
                b"test-prompts.json",
                b"Eval Scenarios",
                b"agent_evaluation",
                b"not_run",
            ):
                assert retired not in content, path
    # Inspect every produced archive, not only the pre-archive directory.
    for archive in [*out.glob("*.zip"), *out.glob("*.tgz"), *out.glob("*.tar.gz")]:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as packed:
                contents = {name: packed.read(name) for name in packed.namelist()}
        else:
            with tarfile.open(archive) as packed:
                contents = {
                    item.name: packed.extractfile(item).read() for item in packed if item.isfile()
                }
        assert {name.split("/", 1)[1]: value for name, value in contents.items()} == files
    # Rejection fixtures and development delivery notes stay available in source.
    assert (ROOT / "examples/arc-scope/request.json").is_file()
    assert (ROOT / "docs/modules/DELIVERY.md").is_file()


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
