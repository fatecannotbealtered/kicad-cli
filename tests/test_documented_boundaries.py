"""Regression guards for the documented scope, not proof of geometry safety."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("path", "required", "obsolete"),
    [
        ("README.md", "Reads connection, open-document and board status only", "| Live editing |"),
        ("README_zh.md", "只读连接、打开的文档和板状态", "| 实时编辑 |"),
        ("skills/kicad-cli/SKILL.md", "read-only IPC status", "Show the work on screen, undoable"),
        (
            "skills/kicad-cli/reference/cautions.md",
            "does not create or modify items",
            "Changes appear on screen and go through KiCad's undo stack",
        ),
    ],
)
def test_live_status_is_not_advertised_as_editing(path, required, obsolete):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert required in text
    assert obsolete not in text


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "README_zh.md",
        "SECURITY.md",
        "SECURITY_zh.md",
        "skills/kicad-cli/SKILL.md",
        "skills/kicad-cli/reference/cautions.md",
    ],
)
def test_development_limits_are_visible_on_current_entry_points(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "unpublishable" in text
    assert "safe to run unattended" not in text
    assert "每一条都走确认门禁、写完自跑 DRC" not in text


def test_pending_release_work_survives_queue_cleanup():
    text = (ROOT / "docs/DEVELOPMENT_STATUS.md").read_text(encoding="utf-8")
    for item in ("CONFIRM", "DRC", "TRANSACTION", "CONTRACT", "EVIDENCE"):
        assert f"| {item} | Pending |" in text
    for path in ("README.md", "README_zh.md", "SECURITY.md", "SECURITY_zh.md"):
        assert "docs/DEVELOPMENT_STATUS.md" in (ROOT / path).read_text(encoding="utf-8")


def test_development_skill_discovers_before_using_unreleased_selector():
    text = (ROOT / "skills/kicad-cli/SKILL.md").read_text(encoding="utf-8")
    assert "after plain `reference`" in text
    assert "an npm install does not" in text
    assert "Matching version strings alone are insufficient" in text
