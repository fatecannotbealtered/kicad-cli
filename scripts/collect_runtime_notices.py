"""Preserve licenses of the selected Python runtime and fixed bundled packages."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
import sysconfig
from pathlib import Path

from packaging.requirements import Requirement


def collect(root: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    records = []

    def copy(component, version, name, source):
        raw = source.read_bytes()
        target = destination / component / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        records.append(
            {
                "component": component,
                "version": version,
                "path": target.relative_to(destination).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    names = []
    vendor = root / ".vendor"
    vendored = (
        {
            d.metadata["Name"].lower().replace("_", "-"): d
            for d in importlib.metadata.distributions(path=[str(vendor)])
        }
        if vendor.is_dir()
        else {}
    )
    for line in (root / "requirements-runtime.lock").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            names.append(Requirement(line).name)
    # The frozen executable includes the PyInstaller bootloader, not its build tools.
    for name in [*names, "pyinstaller"]:
        distribution = (
            vendored.get(name.lower().replace("_", "-")) if name != "pyinstaller" else None
        ) or importlib.metadata.distribution(name)
        licenses = [
            item
            for item in distribution.files or []
            if item.name.lower().startswith(("license", "copying", "notice"))
        ]
        if not licenses:
            raise ValueError(f"Missing installed distribution license: {name}")
        for index, item in enumerate(licenses):
            copy(
                name,
                distribution.version,
                f"{index}-{item.name}",
                Path(distribution.locate_file(item)),
            )
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    ]
    source = next((item for item in candidates if item.is_file()), None)
    if source is None:
        raise ValueError("The selected CPython distribution has no readable LICENSE.txt")
    copy("CPython", sys.version.split()[0], "LICENSE.txt", source)
    manifest = {"format": "kicad-runtime-license-files/v1", "files": records}
    (destination / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
