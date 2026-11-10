#!/usr/bin/env python3
"""Pack the npm packages and check what is inside them (DREPO-051, DSEC-072, DSKL-032).

    python scripts/pack.py [--main | --platform]

The main package holds only the launcher, skills/ and the list of platform packages, besides
package.json, README.md, LICENSE and NOTICE.md. Each platform package, built by scripts/build.py,
holds the native program with the licenses and notices of what it bundles. Both are packed with npm into dist/; then every tarball is checked: only
the allowed files, no install scripts, Skill directories with only SKILL.md and references/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
ALWAYS = {"package/package.json", "package/README.md", "package/LICENSE", "package/NOTICE.md"}


def npm() -> str:
    found = shutil.which("npm")
    if not found:
        raise SystemExit("npm was not found on PATH")
    return found


def main_package(package: dict, tool: str) -> Path:
    out = ROOT / "dist" / "npm" / package["name"].replace("/", "__")
    shutil.rmtree(out, ignore_errors=True)
    (out / "bin").mkdir(parents=True)
    shutil.copyfile(ROOT / "bin" / f"{tool}.js", out / "bin" / f"{tool}.js")
    shutil.copytree(ROOT / "skills", out / "skills")
    for name in ("README.md", "LICENSE", "NOTICE.md"):
        shutil.copyfile(ROOT / name, out / name)
    shutil.copyfile(ROOT / "package.json", out / "package.json")
    return out


def check(tarball: Path, main: bool, tool: str) -> list[str]:
    problems = []
    with tarfile.open(tarball) as archive:
        names = [m.name for m in archive.getmembers() if m.isfile()]
        manifest = json.loads(archive.extractfile("package/package.json").read().decode("utf-8"))
    for name in names:
        if name in ALWAYS:
            continue
        if main and name == f"package/bin/{tool}.js":
            continue
        if main and re.fullmatch(r"package/skills/[^/]+/(SKILL\.md|references/[^/]+)", name):
            continue
        if not main and (name.startswith("package/bin/") or name.startswith("package/licenses/")):
            continue
        problems.append(f"{tarball.name}: {name} may not be in this package")
    for hook in ("preinstall", "install", "postinstall", "prepare"):
        if hook in (manifest.get("scripts") or {}):
            problems.append(f"{tarball.name}: the install script `{hook}` is not allowed")
    if main and set(manifest.get("dependencies") or {}):
        problems.append(f"{tarball.name}: the main package depends only on its platform packages")
    return problems


def main(argv: list[str]) -> int:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    tool = next(iter(package["bin"]))
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    folders = []
    if "--platform" not in argv:
        folders.append((main_package(package, tool), True))
    if "--main" not in argv:
        for name in package.get("optionalDependencies", {}):
            folder = dist / "npm" / name.replace("/", "__")
            if folder.is_dir():
                folders.append((folder, False))
    problems = []
    for folder, main in folders:
        reply = subprocess.run([npm(), "pack", "--json", "--pack-destination", str(dist)], cwd=folder, capture_output=True,
                               text=True, encoding="utf-8", creationflags=NO_WINDOW)
        if reply.returncode != 0:
            print(reply.stderr, file=sys.stderr)
            return 1
        tarball = dist / json.loads(reply.stdout)[0]["filename"].replace("@", "").replace("/", "-")
        problems += check(tarball, main, tool)
        print(f"Packed {tarball.relative_to(ROOT)}")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
