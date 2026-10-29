"""Offline drift checks for the fixed seed, inherited base and exact driver SDK."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(root=ROOT):
    lock = json.loads((root / "contract/spec-lock.json").read_bytes())
    problems = []
    if (root / ".agent/SPEC_VERSION").read_text().strip() != lock["seed"]["tag"]:
        problems.append("root seed version does not match the fixed lock")
    for name, expected in lock["seed"]["files"].items():
        if sha((root / name).read_bytes().replace(b"\r\n", b"\n")) != expected:
            problems.append(f"seed drift: {name}")
    package = root / "kicad_cli"
    pin_raw = (package / "driver_spec/PIN.json").read_bytes()
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse((package / "driver_runtime.py").read_text()).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "PIN_SHA256"
    }
    if sha(pin_raw) != constants.get("PIN_SHA256"):
        problems.append("driver PIN manifest drift")
    pin = json.loads(pin_raw)
    if pin["commit"] != lock["driver"]["commit"] or pin["version"] != lock["driver"]["version"]:
        problems.append("driver identity drift")
    for name, record in pin["files"].items():
        path = package / name
        if (
            not path.resolve().is_relative_to(package.resolve())
            or sha(path.read_bytes()) != record["sha256"]
        ):
            problems.append(f"driver drift: {name}")
    schemas = {p.name for p in (package / "driver_spec/schemas").glob("*.json")}
    expected = {Path(p).name for p in pin["files"] if p.startswith("driver_spec/schemas/")}
    if schemas != expected:
        problems.append("driver schema inventory drift")
    base = json.loads((package / "driver_spec/base/base-lock.json").read_bytes())
    if any(base[key] != lock["base"][key] for key in ("tag", "commit")):
        problems.append("inherited base identity drift")
    return problems


if __name__ == "__main__":
    findings = check()
    for finding in findings:
        print(finding)
    if not findings:
        print("Fixed seed v1.6.2, driver 1.0.2 and inherited base v1.6.3: exact files verified")
    raise SystemExit(bool(findings))
