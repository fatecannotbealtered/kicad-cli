#!/usr/bin/env python3
"""The last gate of a release (DREPO-040, DREPO-042, DREPO-043).

    python scripts/release_gate.py <artifacts>           check the platform delivery records
    python scripts/release_gate.py <artifacts> --order   print the tarballs to publish, platform packages first
    python scripts/release_gate.py --notes               print the release notes of the package.json version

Every platform package listed in package.json must have a delivery record with result pass,
and the record's digests must match the tarballs about to be published. The release notes are
the version's section of CHANGELOG.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def integrity(path: Path) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()


def tarballs(folder: Path, version: str) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(folder.rglob("*.tgz")):
        with tarfile.open(path) as archive:
            manifest = json.loads(archive.extractfile("package/package.json").read().decode("utf-8"))
        if manifest.get("version") == version:
            found.setdefault(manifest["name"], path)
    return found


def notes(version: str) -> str:
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"## [{version}]")), None)
    if start is None:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end]).strip() + "\n"


def main(argv: list[str]) -> int:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    version = package["version"]
    if argv == ["--notes"]:
        sys.stdout.write(notes(version))
        return 0
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    folder = Path(argv[0])
    found = tarballs(folder, version)
    platforms = list(package.get("optionalDependencies", {}))
    problems = [f"no tarball of {name} {version}" for name in [package["name"], *platforms] if name not in found]
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.rglob("delivery-*.json"))]
    for name in platforms:
        system, cpu = name[len(package["name"]) + 1:].split("-")
        record = next((r for r in records if r.get("platform") == f"{system}-{cpu}" and r.get("version") == version), None)
        if record is None or record.get("result") != "pass":
            problems.append(f"platform delivery did not pass for {name}")
            continue
        for checked in (package["name"], name):
            if checked in found and record.get("packages", {}).get(checked) != integrity(found[checked]):
                problems.append(f"{checked} differs from the package delivered on {system}-{cpu}")
    notes(version)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    if argv[1:] == ["--order"]:
        for name in [*platforms, package["name"]]:
            print(found[name].as_posix())
    else:
        print(f"Every platform package of {package['name']} {version} passed platform delivery.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
