"""Turn a Keep a Changelog file into the base changelog entries (CLI-SPEC §11), newest first."""

from __future__ import annotations

import re

HEADING = re.compile(r"^##\s+\[?v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\]?(?:\s*[-–—]\s*(\d{4}-\d{2}-\d{2}))?")
SECTION = re.compile(r"^###\s+(added|changed|fixed|deprecated|removed|security)\b", re.IGNORECASE)
ITEM = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")


def parse_changelog(text: str) -> list[dict]:
    """Released versions only; the Unreleased section is skipped. Unknown subsections are ignored."""
    entries: list[dict] = []
    current = None
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            match = HEADING.match(line)
            current = {"version": match.group(1), "date": match.group(2), "changes": {}} if match else None
            section = None
            if current is not None:
                entries.append(current)
            continue
        if current is None:
            continue
        found = SECTION.match(line)
        if found:
            section = found.group(1).lower()
            current["changes"].setdefault(section, [])
            continue
        if line.startswith("#"):
            section = None
            continue
        item = ITEM.match(line)
        if item and section:
            current["changes"][section].append(item.group(1))
        elif section and line.startswith("  ") and line.strip() and current["changes"][section]:
            current["changes"][section][-1] += " " + line.strip()
    return entries


def since(entries: list[dict], version: str) -> list[dict]:
    """Entries strictly newer than version (base: --since)."""
    def key(value: str):
        core, _, pre = value.split("+", 1)[0].partition("-")
        return tuple(int(part) for part in core.split(".")), (1,) if not pre else (0, pre)
    return [entry for entry in entries if key(entry["version"]) > key(version)]
