"""A frozen finding gate needs actual shipped source with verified hashes."""

from __future__ import annotations

import json
import sys
import zipfile

import pytest

import build
from kicad_cli import __version__, drc_baseline


def package(tmp_path):
    root = tmp_path / "package"
    for name in ["drc_baseline.py", "fileformat/drc/__init__.py", "fileformat/drc/settings.py"]:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# An observed file: " + name, encoding="utf-8")
    return root


def frozen(monkeypatch, root):
    monkeypatch.setattr(drc_baseline, "__file__", str(root / "drc_baseline.py"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_packaged_source_fingerprint_matches_the_observed_source_files(tmp_path, monkeypatch):
    root = package(tmp_path)
    monkeypatch.setattr(drc_baseline, "__file__", str(root / "drc_baseline.py"))
    source = drc_baseline._source()
    archive = root / "checker-sources.zip"
    build.archive_checker_sources(root, archive, __version__)
    frozen(monkeypatch, root)
    shipped = drc_baseline._source()
    assert shipped["code_sha256"] == source["code_sha256"]
    assert len(shipped["source_archive_sha256"]) == 64
    assert shipped["official_native"] is False
    assert "not running-bytecode" in shipped["source_fingerprint_basis"]


@pytest.mark.parametrize(
    "fault", ["changed_file", "wrong_version", "missing_required", "bad_manifest", "duplicate"]
)
def test_invalid_packaged_sources_refuse_a_checker_identity(tmp_path, monkeypatch, fault):
    root = package(tmp_path)
    archive = root / "checker-sources.zip"
    build.archive_checker_sources(root, archive, __version__)
    with zipfile.ZipFile(archive) as packed:
        entries = {name: packed.read(name) for name in packed.namelist()}
    manifest = json.loads(entries["source-manifest.json"])
    if fault == "changed_file":
        entries["drc_baseline.py"] += b"\n# changed after packaging"
    elif fault == "wrong_version":
        manifest["version"] = "old-candidate"
    elif fault == "missing_required":
        entries.pop("fileformat/drc/settings.py")
        manifest["files"].pop("fileformat/drc/settings.py")
    elif fault == "bad_manifest":
        manifest = []
    entries["source-manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(archive, "w") as packed:
        for name, content in entries.items():
            packed.writestr(name, content)
        if fault == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                packed.writestr("drc_baseline.py", entries["drc_baseline.py"])
    frozen(monkeypatch, root)
    with pytest.raises(drc_baseline.Incomplete):
        drc_baseline._source()


def test_missing_frozen_source_archive_does_not_fall_back_to_a_label(tmp_path, monkeypatch):
    root = package(tmp_path)
    frozen(monkeypatch, root)
    with pytest.raises(drc_baseline.Incomplete, match="archive"):
        drc_baseline._source()
