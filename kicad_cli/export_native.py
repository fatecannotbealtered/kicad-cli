"""Bounded external SDK runner, with a durable child identity before waiting."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import kicad_env
from ._driver_sdk.models import DriverError

PAYLOAD = Path(__file__).resolve().parent / "payload" / "export_driver.py"


def _json(stdout):
    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("SDK subprocess did not return a JSON object")


def probe():
    python = kicad_env.find_python()
    if not python:
        raise DriverError(
            "E_BACKEND_UNAVAILABLE",
            "No existing KiCad Python SDK interpreter was found.",
            details={"next_action": "Run doctor; configure the existing KiCad interpreter."},
            dispatch_state="not_started",
        )
    try:
        result = subprocess.run(
            [python, "-I", "-B", str(PAYLOAD), "--describe"],
            capture_output=True,
            timeout=30,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        if result.returncode:
            raise ValueError("SDK identity probe failed")
        identity = _json(result.stdout)
        if not identity.get("version") or len(identity.get("components", [])) != 3:
            raise ValueError("incomplete SDK identity")
        for item in identity["components"]:
            path = Path(item["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("SDK identity changed during the probe")
        identity["payloads"] = {
            name: hashlib.sha256(PAYLOAD.with_name(name).read_bytes()).hexdigest()
            for name in [
                "export_driver.py",
                "export_identity.py",
                "plot.py",
                "plot_settings.py",
                "drill.py",
                "kicad_lib.py",
            ]
        }
        return identity
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise DriverError(
            "E_BACKEND_UNAVAILABLE",
            "The installed SDK cannot establish its loaded identity.",
            details={
                "reason": str(exc),
                "next_action": "Run doctor and inspect the existing installation.",
            },
            dispatch_state="not_started",
        ) from exc


def run(identity, exporter, argv, record_child, timeout=1800):
    command = [identity["python"], "-I", "-B", "-u", str(PAYLOAD), exporter, *argv]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    try:
        record_child(process.pid)
    except BaseException:
        # This child is owned by this invocation, never an attached user editor.
        process.kill()
        process.communicate()
        raise
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        process.kill()
        process.communicate()
        raise
    report = _json(stdout)
    if process.returncode and report.get("ok"):
        raise ValueError("SDK returned success with a nonzero process exit")
    report["process"] = {
        "pid": process.pid,
        "returncode": process.returncode,
        "argv": command,
        "stderr": stderr.decode("utf-8", "replace")[-2000:],
    }
    return report
