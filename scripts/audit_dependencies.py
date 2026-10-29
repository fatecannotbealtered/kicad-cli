"""Check the build runtime lock and strictly audit the resolved build environment."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.requirements import Requirement

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def key(name):
    return name.lower().replace("_", "-").replace(".", "-")


def check_runtime(root=ROOT):
    locked = {}
    for line in (root / "requirements-runtime.lock").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        specs = list(requirement.specifier)
        if (
            requirement.url
            or requirement.marker
            or requirement.extras
            or len(specs) != 1
            or specs[0].operator != "=="
        ):
            raise ValueError("Runtime lock must contain exact unconditional versions")
        name = key(requirement.name)
        if name in locked or "*" in specs[0].version:
            raise ValueError("Runtime lock has duplicate or wildcard versions")
        locked[name] = specs[0].version
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pending = list(project["project"]["dependencies"])
    # build.py passes .vendor before site-packages to PyInstaller when present.
    vendor = root / ".vendor"
    vendored = (
        {
            key(distribution.metadata["Name"]): distribution
            for distribution in importlib.metadata.distributions(path=[str(vendor)])
        }
        if vendor.is_dir()
        else {}
    )
    actual = {}
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = key(requirement.name)
        distribution = vendored.get(name) or importlib.metadata.distribution(requirement.name)
        if distribution.version not in requirement.specifier:
            raise ValueError(f"Installed runtime dependency violates declaration: {name}")
        if name not in actual:
            actual[name] = distribution.version
            pending.extend(distribution.requires or [])
    if actual != locked:
        raise ValueError(
            "Installed runtime dependency closure differs from requirements-runtime.lock"
        )
    return actual


def audit(runtime):
    # The editable project is our reviewed source, not a PyPI dependency. Freeze
    # every other installed distribution, including bootstrap/build tools, then
    # keep strict collection failure and vulnerability failure enabled.
    frozen = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze", "--all", "--exclude-editable"], text=True
    )
    pins = {}
    for line in frozen.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        specs = list(requirement.specifier)
        if (
            requirement.url
            or requirement.marker
            or requirement.extras
            or len(specs) != 1
            or specs[0].operator != "=="
            or "*" in specs[0].version
            or key(requirement.name) in pins
        ):
            raise ValueError("Audit input contains an unresolved distribution")
        pins[key(requirement.name)] = specs[0].version
    if not pins or any(pins.get(name) != version for name, version in runtime.items()):
        raise ValueError("Resolved audit input does not contain the verified runtime closure")
    with tempfile.TemporaryDirectory(prefix="kicad-dependency-audit-") as temporary:
        path = Path(temporary) / "resolved.txt"
        path.write_text(frozen, encoding="utf-8", newline="\n")
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--strict",
                "--no-deps",
                "--disable-pip",
                "--requirement",
                str(path),
            ]
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    runtime = check_runtime()
    print(json.dumps({"runtime": runtime, "lock_matches_installed_closure": True}), flush=True)
    raise SystemExit(audit(runtime) if args.audit else 0)
