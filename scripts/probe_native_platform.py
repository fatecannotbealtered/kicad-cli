"""Record native build/install facts before claiming a new platform supported.

This development probe preserves actual product failures. It edits no CAD file,
uses an owned profile, and never treats collected diagnostics as acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def invoke(argv, output, label, environ=None):
    process = subprocess.run(argv, capture_output=True, timeout=120, env=environ)
    (output / (label + ".stdout")).write_bytes(process.stdout)
    (output / (label + ".stderr")).write_bytes(process.stderr)
    return {"argv": list(map(str, argv)), "exit_code": process.returncode}


def main(binary, kicad_root, destination):
    binary, root, out = map(lambda p: Path(p).resolve(), (binary, kicad_root, destination))
    out.mkdir(parents=True, exist_ok=False)
    record = {
        "stage": "native_baseline_diagnostic_only",
        "platform_support_accepted": False,
        "platform": sys.platform,
        "machine": platform.machine(),
        "system": platform.platform(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "kicad_root": str(root),
        "calls": {},
    }
    for tool, args in (
        ("file", [str(binary)]),
        ("ldd", [str(binary)]),
        ("otool", ["-L", str(binary)]),
        ("vtool", ["-show-build", str(binary)]),
    ):
        if shutil.which(tool):
            record["calls"][tool] = invoke([tool, *args], out, tool)
    official_candidates = [root / "bin/kicad-cli", root / "Contents/MacOS/kicad-cli"]
    python_candidates = [root / "bin/python3"]
    if sys.platform == "darwin":
        python_candidates += sorted(
            root.glob("Contents/Frameworks/Python.framework/Versions/*/bin/python3*")
        )
        module_files = list(root.glob("Contents/**/pcbnew.py"))
        record["pcbnew_module_paths"] = [str(p) for p in module_files]
        record["resource_templates"] = [str(p) for p in root.glob("Contents/**/sym-lib-table")]
    else:
        module_files = []
        record["resource_templates"] = [str(root / "share/kicad/template/sym-lib-table")]
    official = next((p for p in official_candidates if p.is_file()), None)
    record["official_cli"] = str(official) if official else None
    if official:
        record["calls"]["official_version"] = invoke(
            [str(official), "--version"], out, "official-version"
        )
    probe_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if module_files:
        probe_env["PYTHONPATH"] = os.pathsep.join(str(p.parent) for p in module_files)
    record["native_python_probes"] = []
    seen = set()
    for candidate in python_candidates:
        if not candidate.is_file() or candidate.resolve() in seen:
            continue
        seen.add(candidate.resolve())
        row = invoke(
            [str(candidate), "-B", "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
            out,
            "pcbnew-" + str(len(seen)),
            probe_env,
        )
        row["version_output"] = (out / ("pcbnew-" + str(len(seen)) + ".stdout")).read_text(
            encoding="utf-8", errors="replace"
        )[-500:]
        row["error_tail"] = (out / ("pcbnew-" + str(len(seen)) + ".stderr")).read_text(
            encoding="utf-8", errors="replace"
        )[-500:]
        record["native_python_probes"].append(row)
    config = out / "runtime-config.json"
    config.write_text(
        json.dumps(
            {"format_version": 1, "kicad_root": str(root), "profile_dir": str(out / "profile")}
        )
        + "\n",
        encoding="utf-8",
    )
    product_env = dict(
        os.environ, KICAD_CLI_RUNTIME_CONFIG=str(config), PYTHONDONTWRITEBYTECODE="1"
    )
    for label, argv in (
        ("check-environment", ["--check-environment", "--compact"]),
        ("reference-index", ["reference", "--index", "--compact"]),
    ):
        row = invoke([str(binary), *argv], out, label, product_env)
        try:
            body = json.loads((out / (label + ".stdout")).read_bytes())
            row.update(ok=body.get("ok"), error_code=body.get("error", {}).get("code"))
        except (ValueError, UnicodeError):
            row["valid_json"] = False
        record["calls"][label] = row
    (out / "NATIVE-PROBE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--kicad-root", required=True)
    parser.add_argument("--destination", required=True)
    main(**vars(parser.parse_args()))
