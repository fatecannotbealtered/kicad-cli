"""PyInstaller entry used by release.yml to produce a single-file binary.

Run `scripts/smoke_binary.py dist/kicad-cli[.exe]` after this. A binary that
builds is not a binary that runs: the first version of this file handed
`kicad_cli/main.py` to PyInstaller directly, which builds cleanly and then dies
on its first relative import.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = ";" if sys.platform == "win32" else ":"
RUNTIME_COMMIT = "9607a35fe0ea33fe822e1f2ad2704eae260d4018"
RUNTIME_PIN_SHA256 = "e26dabd06dd559f70ce07535f1b589497dba0a002b5cee59049ff855671b432f"


def data(source: Path, dest: str) -> list[str]:
    return ["--add-data", f"{source}{SEP}{dest}"]


def source_files(package: Path) -> dict[str, bytes]:
    """Only source files: never include caches, state, local projects or vendor."""
    return {
        p.relative_to(package).as_posix(): p.read_bytes()
        for p in sorted(package.rglob("*.py"))
        if not {"_driver_sdk", "driver_spec"}.intersection(p.relative_to(package).parts)
    }


def specification_data(source: Path, destination: Path) -> Path:
    """Stage pinned contracts/schemas without redistributing SDK Python code."""
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(
            "Specification staging must be empty; old SDK files must not ride along"
        )
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_file() and path.suffix != ".py" and "__pycache__" not in relative.parts:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    return destination


def internal_runtime(package: Path, destination: Path, authorization: Path) -> dict:
    """Stage authenticated assets under the recorded distribution permission.

    The SDK stays excluded from PYZ: its authenticated source bytes are loaded
    after _pin() succeeds, rather than executing an unrelated compiled copy.
    """
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Internal runtime staging must be empty")
    grant_bytes = authorization.read_bytes()
    grant = json.loads(grant_bytes)
    if (
        grant.get("commit") != RUNTIME_COMMIT
        or grant.get("authorization_source") != "rights_holder_instruction"
        or grant.get("schema") != "kicad-runtime-distribution/v2"
        or grant.get("license_model") != "Apache-2.0-with-preserved-MIT-baseline"
        or grant.get("publication_policy", {}).get("npm_publication_enabled") is not False
    ):
        raise ValueError("Exact runtime distribution permission is required")
    pin_bytes = (package / "driver_spec/PIN.json").read_bytes()
    if hashlib.sha256(pin_bytes).hexdigest() != RUNTIME_PIN_SHA256:
        raise ValueError("Fixed runtime PIN bytes do not match")
    pin = json.loads(pin_bytes)
    if pin.get("commit") != RUNTIME_COMMIT:
        raise ValueError("Wrong runtime source commit")
    licensed_assets = {row["target"]: row for row in grant["assets"]}
    files, hidden = {}, set()
    for name, record in pin["files"].items():
        if name not in licensed_assets or licensed_assets[name]["sha256"] != record["sha256"]:
            raise ValueError("Runtime asset is not covered by the exact authorization")
        baseline_asset = name.startswith("driver_spec/base/ai-native-cli-spec/")
        expected_license = "MIT" if baseline_asset else "Apache-2.0"
        expected_path = (
            "third_party/ai-native-cli-spec/LICENSE"
            if baseline_asset
            else "third_party/tool-driver/LICENSE"
        )
        if (
            licensed_assets[name].get("license_basis") != expected_license
            or licensed_assets[name].get("license_path") != expected_path
        ):
            raise ValueError("Runtime asset license does not match its source scope")
        source = package / name
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError("Runtime source bytes do not match the authenticated PIN")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files[name] = {
            "sha256": record["sha256"],
            "bytes": len(content),
            "upstream_source": record["source"],
            "license_basis": licensed_assets[name]["license_basis"],
        }
        if source.suffix == ".py":
            for node in ast.walk(ast.parse(content)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split(".")[0]]
                hidden.update(
                    name
                    for name in names
                    if name in sys.stdlib_module_names and name != "__future__"
                )
    expected_schemas = {
        name.removeprefix("driver_spec/")
        for name in files
        if name.startswith("driver_spec/schemas/")
    }
    actual_schemas = {
        path.relative_to(package / "driver_spec").as_posix()
        for path in (package / "driver_spec/schemas").glob("*.schema.json")
    }
    if actual_schemas != expected_schemas:
        raise ValueError("Runtime schema inventory does not match the PIN")
    (destination / "driver_spec/PIN.json").write_bytes(pin_bytes)
    notices = destination / "runtime-notices"
    notices.mkdir()
    (notices / "DISTRIBUTION.json").write_bytes(grant_bytes)
    shutil.copyfile(ROOT / "third_party/tool-driver/NOTICE.md", notices / "DRIVER-NOTICE.md")
    baseline = ROOT / "third_party/ai-native-cli-spec/LICENSE"
    shutil.copyfile(baseline, notices / "BASELINE-MIT-LICENSE.txt")
    shutil.copyfile(ROOT / "third_party/tool-driver/LICENSE", notices / "DRIVER-APACHE-LICENSE.txt")
    shutil.copyfile(ROOT / "LICENSE", notices / "PRODUCT-LICENSE.txt")
    shutil.copyfile(ROOT / "NOTICE.md", notices / "PRODUCT-NOTICE.md")
    manifest = {
        "format": "kicad-internal-runtime-assets/v1",
        "commit": RUNTIME_COMMIT,
        "distribution": "public_repository_and_matching_kicad_packages",
        "public_distribution": True,
        "npm_publication": False,
        "license_model": "Apache-2.0-with-preserved-MIT-baseline",
        "sdk_python_in_pyz": False,
        "execution": "Authenticate all fixed source/data files before loading SDK source.",
        "authorization_sha256": hashlib.sha256(grant_bytes).hexdigest(),
        "baseline_license_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "pin_sha256": RUNTIME_PIN_SHA256,
        "files": files,
        "hidden_stdlib_imports": sorted(hidden),
    }
    manifest_path = destination / "bundled-runtime-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"root": destination, "manifest": manifest, "hidden": sorted(hidden)}


def archive_checker_sources(package: Path, archive: Path, version: str) -> dict[str, bytes]:
    files = source_files(package)
    manifest = {
        "format": 1,
        "version": version,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as packed:
        for name, content in {
            **files,
            "source-manifest.json": json.dumps(manifest, sort_keys=True).encode(),
        }.items():
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            packed.writestr(item, content)
    return files


def main(argv=None) -> int:
    from kicad_cli import __version__  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build")
    parser.add_argument(
        "--internal-runtime",
        action="store_true",
        help=(
            "Bundle exact SDK/spec under its recorded distribution permission; legacy option name."
        ),
    )
    options = parser.parse_args(argv)
    from scripts.audit_dependencies import check_runtime
    from scripts.collect_runtime_notices import collect

    check_runtime(ROOT)
    work = options.work_dir.resolve()
    output = options.output.resolve()
    archive = work / "checker-sources.zip"
    sources = archive_checker_sources(ROOT / "kicad_cli", archive, __version__)
    staging = Path(tempfile.mkdtemp(prefix="specification-data-", dir=work))
    dependency_notices = work / "dependency-notices"
    collect(ROOT, dependency_notices)
    runtime = None
    if options.internal_runtime:
        runtime = internal_runtime(
            ROOT / "kicad_cli",
            staging,
            ROOT / "third_party/tool-driver/DISTRIBUTION.json",
        )
        spec_data = staging / "driver_spec"
    else:
        spec_data = specification_data(ROOT / "kicad_cli/driver_spec", staging)
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        "kicad-cli",
        "--distpath",
        str(output),
        "--workpath",
        str(work / "pyinstaller"),
        "--specpath",
        str(work),
        # The entry script imports the package rather than being a module of
        # it, so the frozen binary keeps its package context.
        "--paths",
        str(ROOT),
        # Payloads are read from disk by path at runtime, so they must ride
        # along as data rather than being frozen into the import graph.
        *data(ROOT / "kicad_cli" / "payload", "kicad_cli/payload"),
        # The custom finding gate fingerprints shipped source, not a label or
        # an unbound hash assertion. PyInstaller otherwise omits those files.
        *data(archive, "kicad_cli"),
        # The incremental runtime verifies and loads pinned schema/checker data.
        *data(spec_data, "kicad_cli/driver_spec"),
        "--exclude-module",
        "kicad_cli._driver_sdk",
        "--exclude-module",
        "kipy",
        "--exclude-module",
        "jsonschema.tests",
        "--exclude-module",
        "jsonschema.benchmarks",
        "--exclude-module",
        "referencing.tests",
        "--collect-submodules",
        "jsonschema",
        "--collect-data",
        "jsonschema_specifications",
        "--hidden-import",
        "sqlite3",
        "--hidden-import",
        "platform",
        "--hidden-import",
        "signal",
        *data(ROOT / "kicad_cli" / "driver_contract.json", "kicad_cli"),
        *data(ROOT / "CHANGELOG.md", "."),
        *data(dependency_notices, "kicad_cli/dependency-notices"),
    ]
    if runtime is not None:
        args += [
            *data(staging / "_driver_sdk", "kicad_cli/_driver_sdk"),
            *data(staging / "runtime-notices", "kicad_cli/runtime-notices"),
            *data(staging / "bundled-runtime-manifest.json", "kicad_cli"),
        ]
        for name in runtime["hidden"]:
            args += ["--hidden-import", name]
    if sys.platform == "win32":
        # CPython 3.12.10's optional WMI worker can outlive its caller's stack
        # data and close an invalid handle after a timeout. The captured frozen
        # candidate stack reaches that worker before argument parsing through a
        # setuptools runtime hook. Use platform.py's existing ImportError fallback
        # within this binary; do not patch the SDK or installed Python/KiCad.
        args += [
            "--exclude-module",
            "_wmi",
            "--runtime-hook",
            str(ROOT / "kicad_cli/frozen_platform.py"),
        ]

    # Validator dependencies may be installed here. SDK code is excluded from
    # PYZ and authenticated source data is included only in --internal-runtime
    # mode under the exact recorded distribution permission. IPC stays optional.
    vendor = ROOT / ".vendor"
    if vendor.is_dir():
        args += ["--paths", str(vendor)]
    else:
        print("warning: .vendor is missing; use existing external dependency installations")

    args.append(str(ROOT / "entry.py"))
    result = subprocess.call(args)
    if source_files(ROOT / "kicad_cli") != sources:
        print(
            "error: package source changed while building; candidate is not accepted",
            file=sys.stderr,
        )
        return 1
    if runtime is not None:
        for name, record in runtime["manifest"]["files"].items():
            if (
                hashlib.sha256((ROOT / "kicad_cli" / name).read_bytes()).hexdigest()
                != record["sha256"]
            ):
                print("error: pinned runtime input changed during build", file=sys.stderr)
                return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
