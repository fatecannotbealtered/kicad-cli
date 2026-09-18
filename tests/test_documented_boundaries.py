"""Regression guards for the documented scope, not proof of geometry safety."""

from __future__ import annotations

import json
import re
import sys
import types
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
    """Guard against a blocker being deleted, not against it being described better.

    This used to require the exact string `| CONFIRM | Pending |`, so narrowing a
    blocker to what actually remains failed the suite -- a test that fires on
    progress rather than on regression. What must not happen is a row quietly
    vanishing, or one being marked done while its work is outstanding.
    """
    text = (ROOT / "docs/DEVELOPMENT_STATUS.md").read_text(encoding="utf-8")
    for item in ("CONFIRM", "DRC", "TRANSACTION", "CONTRACT", "EVIDENCE"):
        row = re.search(rf"^\| {item} \| ([^|]+)\|", text, re.MULTILINE)
        assert row, f"release blocker {item} is no longer listed"
        assert row.group(1).strip().startswith("Pending"), item
    for path in ("README.md", "README_zh.md", "SECURITY.md", "SECURITY_zh.md"):
        assert "docs/DEVELOPMENT_STATUS.md" in (ROOT / path).read_text(encoding="utf-8")


def test_development_skill_discovers_before_using_unreleased_selector():
    text = (ROOT / "skills/kicad-cli/SKILL.md").read_text(encoding="utf-8")
    assert "after plain `reference`" in text
    assert "an npm install does not" in text
    assert "Matching version strings alone are insufficient" in text


class _FakeBoard:
    name = "fake.kicad_pcb"

    def get_footprints(self):
        return []

    get_tracks = get_vias = get_zones = get_nets = get_selection = get_footprints

    def get_copper_layer_count(self):
        return 2

    def get_active_layer(self):
        return 0

    def get_layer_name(self, _layer):
        return "F.Cu"


class _FakeKiCad:
    def get_version(self):
        return "10.0.6"

    def get_open_documents(self, _kind):
        return []

    def get_board(self):
        return _FakeBoard()


@pytest.fixture
def _substituted_ipc(monkeypatch):
    """Stand in for KiCad's IPC client, so the success path runs with no KiCad.

    The only existing coverage of this path asserts nothing unless KiCad happens
    to be open on the machine running the suite, which is how an editing claim
    survived in the payload after the documentation retracted it.
    """
    kipy = types.ModuleType("kipy")
    kipy.KiCad = _FakeKiCad
    doc_types = types.ModuleType("kipy.proto.common.types")
    doc_types.DocumentType = types.SimpleNamespace(
        DOCTYPE_PCB=1, DOCTYPE_SCHEMATIC=2, DOCTYPE_PROJECT=3
    )
    for name, module in {
        "kipy": kipy,
        "kipy.proto": types.ModuleType("kipy.proto"),
        "kipy.proto.common": types.ModuleType("kipy.proto.common"),
        "kipy.proto.common.types": doc_types,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_live_status_payload_does_not_advertise_editing(_substituted_ipc, capsys):
    """The machine contract has to retract the claim the READMEs retracted.

    An agent decides what to ask for next from `capabilities`, not from a
    README, so this is the copy that matters.
    """
    from kicad_cli import envelope
    from kicad_cli.commands import live

    envelope.configure()
    with pytest.raises(SystemExit):
        live.status({})
    data = json.loads(capsys.readouterr().out)["data"]

    capabilities = data["capabilities"]
    assert capabilities["writes"] == []
    assert capabilities["reads"]
    # The retracted claim was keyed, not prose: asserting on the keys keeps this
    # from tripping over the sentence that now denies it ("adds no entry to
    # KiCad's undo stack" contains every word the claim did).
    assert "edits_are_undoable" not in capabilities
    assert "visible" not in capabilities
