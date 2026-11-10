#!/usr/bin/env python3
"""Build the native program and the platform package for this machine (DREPO-051).

    python scripts/build.py

PyInstaller builds the tool as one folder that needs no Python on the user's machine.
Everything the commands read at run time ships inside it: package.json, CHANGELOG.md,
knowledge/, skills/ and contract/driver-contract.json. The result is the platform package
directory dist/npm/<package>-<os>-<cpu>/, ready for scripts/pack.py.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
DATA = ["package.json", "CHANGELOG.md", "knowledge", "skills", "contract/driver-contract.json", "plugin"]


def target() -> tuple[str, str]:
    """The npm names of this machine's operating system and CPU architecture."""
    system = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    cpu = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return system, cpu


def main() -> int:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    tool = next(iter(package["bin"]))
    pkg = tool.replace("-", "_")
    system, cpu = target()
    name = f"{package['name']}-{system}-{cpu}"
    if name not in package.get("optionalDependencies", {}):
        raise SystemExit(f"package.json does not list the platform package {name}")
    build = ROOT / "build"
    shutil.rmtree(build / "pyinstaller", ignore_errors=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console", "--name", tool,
               "--distpath", str(build / "pyinstaller"), "--workpath", str(build / "work"), "--specpath", str(build),
               "--paths", str(ROOT), "--collect-submodules", pkg]
    for item in DATA:
        source = ROOT / item
        if not source.exists():
            continue  # plugin/ exists only with a host component
        dest = str(Path(item).parent) if source.is_file() else item
        command += ["--add-data", f"{source}{os.pathsep}{dest}"]
    command.append(str(ROOT / pkg / "__main__.py"))
    sys.stdout.flush()
    if subprocess.call(command, cwd=ROOT, creationflags=NO_WINDOW, stdout=sys.stdout, stderr=sys.stderr) != 0:
        return 1
    out = ROOT / "dist" / "npm" / name.replace("/", "__")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(build / "pyinstaller" / tool, out / "bin")
    for document in ("LICENSE", "NOTICE.md"):
        shutil.copyfile(ROOT / document, out / document)
    shutil.copytree(ROOT / "licenses", out / "licenses")  # the runtime's license and notice (DREPO-033)
    (out / "README.md").write_text(f"# {name}\n\nThe native program of `{package['name']}` for {system} {cpu}. "
                                   f"Install `{package['name']}` instead; npm picks this package.\n", encoding="utf-8", newline="\n")
    manifest = {"name": name, "version": package["version"], "description": f"The native program of {package['name']} for {system} {cpu}.",
                "license": package.get("license"), "repository": package.get("repository"), "os": [system], "cpu": [cpu],
                "files": ["bin/", "licenses/", "NOTICE.md"]}
    (out / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    program = out / "bin" / (tool + (".exe" if system == "win32" else ""))
    reply = subprocess.run([str(program), "--version"], capture_output=True, text=True, encoding="utf-8",
                           creationflags=NO_WINDOW)
    try:
        version = json.loads(reply.stdout)["data"]["version"]
    except (ValueError, KeyError):
        version = None
    if version != package["version"]:
        print(f"the built program reports {version!r}, not {package['version']}: {reply.stderr.strip()[-500:]}", file=sys.stderr)
        return 1
    print(f"Built {name} {version} in {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
