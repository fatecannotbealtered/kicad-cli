"""Build committed module product inputs without modifying an installed environment."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(destination, python):
    out = Path(destination).resolve()
    out.mkdir(exist_ok=False)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    source = out / "source"
    source.mkdir()
    archive = out / "product-inputs.tar"
    selected = [
        "build.py",
        "entry.py",
        "LICENSE",
        "NOTICE.md",
        "package.json",
        "pyproject.toml",
        "requirements-runtime.lock",
        "requirements-build.lock",
        "scripts/audit_dependencies.py",
        "scripts/collect_runtime_notices.py",
        "CHANGELOG.md",
        "kicad_cli",
        "third_party/tool-driver/DISTRIBUTION.json",
        "third_party/tool-driver/NOTICE.md",
        "third_party/tool-driver/LICENSE",
        "third_party/ai-native-cli-spec/LICENSE",
    ]
    subprocess.run(
        ["git", "archive", "--format=tar", "--output=" + str(archive), revision, "--", *selected],
        cwd=ROOT,
        check=True,
    )
    with tarfile.open(archive) as packed:
        for item in packed:
            assert (source / item.name).resolve().is_relative_to(source)
            assert item.isfile() or item.isdir()
            packed.extract(item, source, filter="data")
    blobs = {}
    for row in subprocess.check_output(["git", "ls-tree", "-rz", revision], cwd=ROOT).split(b"\0"):
        if row:
            header, name = row.split(b"\t", 1)
            blobs[name.decode()] = header.decode().split()[2]
    files = {}
    for path in source.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            name = path.relative_to(source).as_posix()
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            assert blobs[name] == blob, name
            files[name] = {"git_blob": blob, "sha256": hashlib.sha256(raw).hexdigest()}
    (out / "SOURCE-INPUTS.json").write_text(
        json.dumps({"product_source": revision, "files": files}, indent=2) + "\n"
    )
    # POSIX venv executables are symlinks. Resolving one switches to the base
    # interpreter and loses the isolated build dependencies selected by the caller.
    python = Path(python).expanduser().absolute()
    if not python.is_file():
        raise FileNotFoundError(python)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    command = [
        str(python),
        "-B",
        str(source / "build.py"),
        "--internal-runtime",
        "--work-dir",
        str(out / "work"),
        "--output",
        str(out / "dist"),
    ]
    record = {
        "product_source": revision,
        "argv": command,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "build_python": str(python),
        "build_python_environment": json.loads(
            subprocess.check_output(
                [
                    str(python),
                    "-B",
                    "-c",
                    "import json,sys; print(json.dumps({'prefix':sys.prefix,"
                    "'base_prefix':sys.base_prefix}))",
                ]
            )
        ),
        "no_install": True,
    }
    with (out / "stdout.log").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
        process = subprocess.run(
            command, cwd=source, env=env, stdout=stdout, stderr=stderr, timeout=1200
        )
    record.update(
        exit_code=process.returncode,
        inputs_unchanged=all(
            hashlib.sha256((source / p).read_bytes()).hexdigest() == v["sha256"]
            for p, v in files.items()
        ),
    )
    binary = out / "dist" / ("kicad-cli.exe" if sys.platform == "win32" else "kicad-cli")
    record.update(
        platform=sys.platform,
        architecture=platform.machine(),
        binary_name=binary.name,
    )
    if binary.exists():
        record["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        if sys.platform == "win32":
            record["exe_sha256"] = record["binary_sha256"]
    record["binary_present"] = binary.is_file()
    (out / "BUILD-RESULT.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))
    if process.returncode:
        print(
            (out / "stderr.log").read_text(encoding="utf-8", errors="replace")[-4000:],
            file=sys.stderr,
        )
    raise SystemExit(process.returncode or (0 if binary.is_file() else 1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--python", required=True, help="Existing prepared build interpreter")
    args = parser.parse_args()
    main(args.destination, args.python)
