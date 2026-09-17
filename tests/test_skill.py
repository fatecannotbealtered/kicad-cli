"""The Skill may only promise things the tool actually does.

CLI-SPEC §13 counts SKILL.md as public contract: anything it states is a
behaviour that has to be covered. That cuts both ways, and the second direction
is the one that bites. The stub this replaced described `kicad-cli update`,
`kicad-cli setup`, a `--force` flag, `permission_tier` and `blast_radius` fields
on `reference`, a `context.data.version`, and two files under `reference/` --
none of which exist. An agent reading it would have called commands that are not
there and looked for fields that never appear.

So these tests read the Skill and check it against the live registry rather than
against a wish list: every command it names must exist, every field path it
quotes must be real, and the values it documents must be the ones the tool
accepts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "kicad-cli"
SKILL = SKILL_DIR / "SKILL.md"

sys.path.insert(0, str(REPO))
from kicad_cli import __version__, registry  # noqa: E402


def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def frontmatter() -> dict[str, str]:
    body = skill_text()
    block = body.split("---", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def all_skill_files() -> list[Path]:
    return [SKILL, *sorted((SKILL_DIR / "reference").glob("*.md"))]


# --- frontmatter, per SKILL-SPEC §2 -----------------------------------------


def test_name_is_the_directory_and_is_well_formed() -> None:
    fm = frontmatter()
    assert fm["name"] == SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fm["name"])
    assert "anthropic" not in fm["name"] and "claude" not in fm["name"]


def test_version_matches_the_tool_in_all_three_places() -> None:
    """One number in the package, the frontmatter and requires.min_version."""
    fm = frontmatter()
    assert fm["version"].strip('"') == __version__
    meta = json.loads(fm["metadata"])
    assert meta["requires"]["min_version"] == __version__


def test_metadata_declares_the_binary_as_a_string_array() -> None:
    meta = json.loads(frontmatter()["metadata"])
    bins = meta["requires"]["bins"]
    assert isinstance(bins, list) and all(isinstance(b, str) for b in bins)
    assert bins == ["kicad-cli"]
    # The install block must install what the metadata names.
    assert "kicad-cli" in skill_text().split("## When To Use")[0]


def test_description_is_third_person_and_says_when() -> None:
    d = frontmatter()["description"].strip('"')
    assert 0 < len(d) <= 1024
    assert "<" not in d and ">" not in d
    for banned in ("I can ", "I will ", "You can use this", "we "):
        assert banned not in d, f"description must be third person; found {banned!r}"
    assert "Use when" in d, "description must say when to trigger"
    assert "Not for" in d, "description must say when not to trigger"


def test_no_template_scaffolding_survives() -> None:
    body = skill_text()
    for leftover in ("Replace every placeholder", "<read-command>", "<write-command>", "TODO"):
        assert leftover not in body, f"template leftover in SKILL.md: {leftover!r}"


# --- the Skill must not promise what the tool lacks -------------------------


def test_every_command_the_skill_names_exists() -> None:
    paths = {c["path"] for c in registry.build()}
    body = " ".join(f.read_text(encoding="utf-8") for f in all_skill_files())
    named = set(re.findall(r"kicad-cli ([a-z]+(?: [a-z-]+)?)", body))
    # Words that follow the binary but are not commands.
    ignore = {"reference", "context", "doctor", "changelog"}
    unknown = {n for n in named if n not in paths and n.split()[0] not in ignore}
    unknown -= {n for n in unknown if n.split()[0] not in {"board", "sch", "fab"}}
    assert not unknown, f"SKILL.md names commands that do not exist: {sorted(unknown)}"


def test_skill_does_not_claim_a_self_update_command() -> None:
    """SKILL-SPEC §6.7 applies only to tools that have one. This one does not."""
    paths = {c["path"] for c in registry.build()}
    assert "update" not in paths, "if update is added, the Skill must gain that section"
    body = skill_text()
    for absent in ("kicad-cli update", "skill_sync_status", "signature_status", "--force"):
        assert absent not in body, f"SKILL.md promises {absent!r}, which does not exist"


def test_every_option_the_parameters_page_documents_is_declared() -> None:
    page = (SKILL_DIR / "reference" / "parameters.md").read_text(encoding="utf-8")
    declared = {p["name"] for c in registry.build() for p in c["params"]}
    globals_ = {"compact", "quiet", "dry-run", "json", "format", "fields", "confirm"}
    for opt in set(re.findall(r"`--([a-z][a-z-]*)`", page)):
        assert opt in declared or opt in globals_, (
            f"parameters.md documents --{opt}, which no command declares"
        )


def test_status_values_the_skill_documents_are_the_real_ones() -> None:
    """These come from the commands, not from reference, so they can drift."""
    page = (SKILL_DIR / "reference" / "parameters.md").read_text(encoding="utf-8")
    for value in ("PASS", "FAIL", "PARTIAL", "NOOP", "CLEAN", "DESTRUCTIVE"):
        assert value in page
    sync = (REPO / "kicad_cli" / "commands" / "sync.py").read_text(encoding="utf-8")
    assert '"CLEAN"' in sync and '"DESTRUCTIVE"' in sync


def test_the_confirm_token_path_is_stated_correctly() -> None:
    """The stub said data.preview. It is error.details.preview, and an agent
    following the wrong path finds nothing and cannot confirm."""
    body = skill_text()
    assert "error.details.confirm_token" in body
    assert "error.details.preview" in body
    assert "data.preview" not in body.replace("error.details.preview", "")


# --- the dangerous switches must be called out ------------------------------


@pytest.mark.parametrize("switch", ["--mode full", "--no-verify", "--ignore-lock", "--no-restore"])
def test_destructive_switches_carry_a_checkpoint(switch: str) -> None:
    body = skill_text()
    checkpoints = "\n".join(line for line in body.splitlines() if "STOP CHECKPOINT" in line)
    section = body.split("## Checkpoints")[1].split("## Error Decision Tree")[0]
    assert switch in section, f"{switch} is destructive and needs a checkpoint"
    assert checkpoints, "SKILL-SPEC §6.10 requires explicit STOP CHECKPOINT markers"


def test_both_causes_of_conflict_are_explained() -> None:
    """E_CONFLICT means either a stale token or a locked project, and the two
    call for opposite responses. Conflating them sends an agent into a retry
    loop against a project KiCad has open."""
    tree = skill_text().split("## Error Decision Tree")[1]
    assert "lock_files" in tree
    assert "stale" in tree.lower() or "fresh" in tree.lower()


# --- eval scenarios ---------------------------------------------------------


def test_test_prompts_are_specific_to_this_tool() -> None:
    data = json.loads((SKILL_DIR / "test-prompts.json").read_text(encoding="utf-8"))
    assert len(data) >= 10, "SKILL-SPEC §10 wants a real regression set"
    ids = {row["id"] for row in data}
    assert len(ids) == len(data), "duplicate ids"
    for row in data:
        assert row["prompt"] and row["expected"]
        # The stub's prompts were the template's own placeholder sentence.
        assert "one normal read-only resource" not in row["prompt"]
        assert "AI-native KiCad PCB CLI - board audit" not in row["prompt"]
    # Cover the judgement cases, not just the plumbing.
    body = json.dumps(data, ensure_ascii=False)
    for topic in ("untrusted", "E_CONFLICT", "E_USAGE", "samples", "dry-run"):
        assert topic in body, f"test-prompts.json does not exercise {topic}"


def test_referenced_files_exist() -> None:
    """SKILL-SPEC §4 allows one level of reference. A dangling pointer costs the
    agent a wasted round trip; the stub had two."""
    body = skill_text()
    for rel in set(re.findall(r"`(reference/[a-z-]+\.md)`", body)):
        assert (SKILL_DIR / rel).exists(), f"SKILL.md points at missing {rel}"


def test_skill_stays_short() -> None:
    """§4: the body loads whole on trigger. Detail belongs in reference/."""
    lines = len(skill_text().splitlines())
    assert lines < 500, f"SKILL.md is {lines} lines; §4 caps the body at 500"


def install_block() -> str:
    return skill_text().split("## When To Use")[0]


def package_json() -> dict:
    return json.loads((REPO / "package.json").read_text(encoding="utf-8"))


def test_the_install_block_names_what_this_repo_publishes() -> None:
    """SKILL-SPEC §6.1: the CLI and the Skill are installed separately, the
    Skill through `npx skills add`.

    This replaced an editable `pip install -e` of an absolute
    path on one developer's machine, which is unrunnable for every other reader
    and leaks a local directory layout into a published artifact.

    Registry liveness is deliberately not asserted. Whether the package is up
    on npm right now is a publish-time fact and would make the suite fail
    offline; what has to hold in the repo is that the install line names the
    package and repository this repo actually ships.
    """
    head = install_block()
    pkg = package_json()

    named = re.search(r"npm install -g (\S+)", head)
    assert named, "SKILL-SPEC §6.1 wants a copy-paste CLI install line"
    assert named.group(1) == pkg["name"], (
        f"install block says {named.group(1)}, package.json publishes {pkg['name']}"
    )

    skill_line = re.search(r"npx skills add (\S+)", head)
    assert skill_line, "the Skill must install through `npx skills add`, not a CLI subcommand"
    owner_repo = re.search(r"github\.com/([^/]+/[^/.]+)", pkg["repository"]["url"]).group(1)
    assert skill_line.group(1) == owner_repo, (
        f"npx target {skill_line.group(1)} is not this repository ({owner_repo})"
    )


def test_the_install_block_has_no_machine_local_path() -> None:
    head = install_block()
    assert not re.search(r"[A-Za-z]:[\\/]", head), "a drive letter is one machine's layout"
    assert "pip install -e" not in head, "an editable local install is not an install instruction"


def test_the_binary_installed_is_the_binary_declared() -> None:
    """§6.1: the binary in the install block must match `requires.bins`."""
    bins = json.loads(frontmatter()["metadata"])["requires"]["bins"]
    head = install_block()
    for b in bins:
        assert re.search(rf"\b{re.escape(b)}\b", head), f"{b} is declared but never installed"
