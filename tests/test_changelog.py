"""The changelog command answers "what is different about the build in front of me".

Both regressions here shipped: the parser skipped the Unreleased section, so the
answer described the last tagged release instead of the current code, and it
read only a bullet's first line, so every entry came out cut off mid-sentence.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from kicad_cli.commands.changelog import _newer, _parse  # noqa: E402

SAMPLE = """# Changelog

## [Unreleased]

### Added
- A thing that needed more than one line to describe, so the sentence
  continues here and should arrive whole.
- A short thing.

### Fixed
- Another fix.

## [0.1.0] - 2026-09-14

### Added
- The first release.
"""


def test_unreleased_is_an_entry() -> None:
    entries = _parse(SAMPLE)
    assert [e["version"] for e in entries] == ["Unreleased", "0.1.0"]
    assert entries[0]["date"] is None


def test_a_wrapped_bullet_arrives_whole() -> None:
    entries = _parse(SAMPLE)
    added = entries[0]["changes"]["added"]
    assert added[0].endswith("should arrive whole."), added[0]
    assert "continues here" in added[0]
    assert len(added) == 2, "the continuation line must not become its own entry"


def test_since_keeps_unreleased() -> None:
    """Unreleased has no version number to compare and is newer than anything
    tagged, so filtering by a released version must not drop it."""
    assert _newer("Unreleased", "0.1.0") is True
    assert _newer("0.1.0", "0.1.0") is False
    assert _newer("0.2.0", "0.1.0") is True


def test_the_repos_own_changelog_parses() -> None:
    entries = _parse((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert entries, "the project's changelog produced no entries"
    for e in entries:
        if e["version"] == "Unreleased":
            # Empty immediately after a release: the version bump rolls every
            # Unreleased item into the new heading and leaves this one bare.
            # That is the correct state, unlike a released section with nothing
            # under it, which means content was lost.
            continue
        assert e["changes"], f"{e['version']} has a heading but no content"
        for items in e["changes"].values():
            for text in items:
                assert not text.endswith(","), f"entry looks truncated: {text!r}"
