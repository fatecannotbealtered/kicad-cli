"""``kicad-cli changelog`` -- what changed between versions.

Derived from CHANGELOG.md, which stays the single source of truth. We parse it
rather than maintaining a second copy, because two copies drift.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import __version__, envelope

# "Unreleased" is matched deliberately. An agent asking what changed is usually
# asking about the build in front of it, and that build is almost never the last
# tagged release -- skipping the section made this command answer a question
# nobody had asked.
_HEADING = re.compile(
    r"^##\s*\[?(?P<version>[0-9]+\.[0-9]+\.[0-9]+|[Uu]nreleased)\]?\s*-?\s*(?P<date>[0-9-]{8,10})?"
)
_SECTION = re.compile(r"^###\s*(?P<name>Added|Changed|Fixed|Deprecated|Removed|Security)\s*$", re.I)
_ITEM = re.compile(r"^[-*]\s+(?P<text>.+?)\s*$")
# A bullet wrapped over several lines is one entry. Reading only the first line
# is how every item in this changelog came out cut off mid-sentence.
_CONTINUATION = re.compile(r"^\s{2,}(?P<text>\S.*?)\s*$")


def _changelog_path() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "CHANGELOG.md"
        if cand.exists():
            return cand
    return None


def _parse(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    bucket: str | None = None
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            current = {
                "version": m.group("version"),
                "date": m.group("date"),
                "changes": {},
            }
            entries.append(current)
            bucket = None
            continue
        if current is None:
            continue
        m = _SECTION.match(line)
        if m:
            bucket = m.group("name").lower()
            current["changes"].setdefault(bucket, [])
            continue
        if bucket:
            m = _ITEM.match(line)
            if m:
                current["changes"][bucket].append(m.group("text"))
                continue
            m = _CONTINUATION.match(line)
            if m and current["changes"][bucket]:
                current["changes"][bucket][-1] += " " + m.group("text")
    return entries


def _newer(version: str, since: str) -> bool:
    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    try:
        return key(version) > key(since)
    except ValueError:
        # "Unreleased" has no number to compare, and it is newer than anything
        # tagged, so `--since` must always keep it.
        return True


def run(args: dict[str, Any]) -> None:
    path = _changelog_path()
    if not path:
        envelope.fail("E_NOT_FOUND", "CHANGELOG.md not found next to the package")
    entries = _parse(path.read_text(encoding="utf-8"))
    since = args.get("since")
    if since:
        entries = [e for e in entries if _newer(e["version"], since)]
    envelope.ok({"current_version": __version__, "since": since, "entries": entries})
