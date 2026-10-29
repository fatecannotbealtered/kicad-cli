"""Configure an owned Windows trial copy using existing external dependencies only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def absolute(value, name, *, directory=False):
    path = Path(value)
    if not path.is_absolute() or not (path.is_dir() if directory else path.is_file()):
        raise ValueError(
            name + " requires an existing absolute " + ("directory" if directory else "file")
        )
    return path.resolve()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--kicad-root", required=True)
    parser.add_argument("--driver-sdk", required=True)
    parser.add_argument("--consumer-python", required=True)
    parser.add_argument(
        "--authorize-owned-root",
        required=True,
        help="Explicit authorization only for this package's rc/execution directory",
    )
    args = parser.parse_args(argv)
    package = args.package_root.resolve()
    manifest = json.loads((package / "FILES.sha256.json").read_bytes())
    for relative, expected in manifest["files"].items():
        target = (package / relative).resolve()
        if not target.is_relative_to(package) or sha(target) != expected:
            raise ValueError("Package file missing or changed: " + relative)
    execution = package / "rc/execution"
    packet = package / "rc/agent-packet"
    authorized = absolute(args.authorize_owned_root, "--authorize-owned-root", directory=True)
    if authorized != execution.resolve():
        raise ValueError(
            "Authorize only the copied package's rc/execution directory; other roots are refused"
        )
    if any(
        (packet / name).exists() for name in ("DEPENDENCIES.json", "REVISION.json", "scope.json")
    ):
        raise FileExistsError(
            "Configuration already exists. Use public doctor; for a changed "
            "package, unpack a new copy."
        )
    candidate_record = json.loads((package / "CANDIDATE.json").read_bytes())
    candidate = package / candidate_record["executable"]
    if sha(candidate) != candidate_record["sha256"]:
        raise ValueError("Candidate hash does not match CANDIDATE.json")
    native_root = absolute(args.kicad_root, "--kicad-root", directory=True)
    sdk = absolute(args.driver_sdk, "--driver-sdk", directory=True)
    consumer = absolute(args.consumer_python, "--consumer-python")
    if not (sdk.parent / "driver_spec").is_dir():
        raise ValueError(
            "The external driver SDK requires its adjacent pinned driver_spec directory"
        )
    python = absolute(str(native_root / "bin/python.exe"), "KiCad native Python")
    official = absolute(str(native_root / "bin/kicad-cli.exe"), "Official KiCad CLI")
    if sha(official) == sha(candidate):
        raise ValueError("The official KiCad CLI and this driver EXE must be different programs")
    config = packet / "config/10.0"
    if config.exists():
        raise FileExistsError("Configuration seed already exists; use a fresh unpack directory")
    settings = json.loads((packet / "DEPENDENCIES.example.json").read_bytes())
    for item in settings["libraries"].values():
        item["path"] = item["path"].replace("<EXISTING_KICAD_ROOT>", str(native_root))
        if sha(item["path"]) != item["sha256"]:
            raise ValueError(
                "Installed library differs from this case's frozen 10.0.6 input: " + item["path"]
            )
    environment = {
        "KICAD_CLI_DRIVER_SDK_PATH": str(sdk),
        "KICAD_CLI_ROOT": str(native_root),
        "KICAD_CLI_PYTHON": str(python),
        "KICAD_CLI_OFFICIAL": str(official),
        "KICAD_CONFIG_HOME": str(execution / "configuration"),
        "KICAD_CLI_STATE_DIR": str(execution / "state"),
        "KICAD_CLI_SCOPE_FILE": str(packet / "scope.json"),
        "KICAD10_SYMBOL_DIR": str(native_root / "share/kicad/symbols"),
        "KICAD10_FOOTPRINT_DIR": str(native_root / "share/kicad/footprints"),
        "KICAD10_3DMODEL_DIR": str(native_root / "share/kicad/3dmodels"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KICAD", "PYTHON"))}
    env.update(environment)
    log = package / ("configure-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    log.mkdir()

    def call(name, command):
        write_new(
            log / (name + ".start.json"),
            {
                "program": str(command[0]),
                "program_sha256": sha(command[0]),
                "arguments": list(map(str, command[1:])),
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        try:
            process = subprocess.run(
                list(map(str, command)), cwd=package, env=env, capture_output=True, timeout=60
            )
        except subprocess.TimeoutExpired as exc:
            (log / (name + ".stdout")).write_bytes(exc.stdout or b"")
            (log / (name + ".stderr")).write_bytes(exc.stderr or b"")
            write_new(log / (name + ".finish.json"), {"status": "timeout", "exit": None})
            raise ValueError("Dependency probe timed out; inspect " + str(log)) from exc
        (log / (name + ".stdout")).write_bytes(process.stdout)
        (log / (name + ".stderr")).write_bytes(process.stderr)
        write_new(
            log / (name + ".finish.json"),
            {
                "status": "completed" if process.returncode == 0 else "failed",
                "exit": process.returncode,
                "program_sha256": sha(command[0]),
            },
        )
        if process.returncode:
            raise ValueError(name + " failed; inspect " + str(log))
        return process.stdout.decode("utf-8")

    # Validate the pinned runtime through its public entry before any SDK import by a helper.
    reference = json.loads(call("reference", [candidate, "reference", "--compact"]))
    if not reference.get("ok"):
        raise ValueError("Candidate runtime bootstrap refused the explicit SDK")
    native = json.loads(
        call(
            "native-identity",
            [
                python,
                "-B",
                "-c",
                (
                    "import json,sys,pcbnew,_pcbnew; "
                    "print(json.dumps({'version':pcbnew.GetBuildVersion(),'python':sys.ex"
                    "ecutable,'pcbnew':pcbnew.__file__,'_pcbnew':_pcbnew.__file__}))"
                ),
            ],
        )
    )
    if native["version"] != "10.0.6":
        raise ValueError(
            "This fixed package is accepted only against KiCad 10.0.6; another "
            "version needs separate validation"
        )
    official_version = call("official-version", [official, "--version"]).strip()
    if official_version != native["version"]:
        raise ValueError("Official CLI version differs from the selected native Python")
    rendering = json.loads(
        call(
            "renderer-identity",
            [
                consumer,
                "-B",
                "-c",
                (
                    "import json,sys,fitz,pymupdf,pymupdf._mupdf; "
                    "print(json.dumps({'python':sys.executable,'fitz':fitz.__file__,'pymu"
                    "pdf':pymupdf.__file__,'renderer':pymupdf._mupdf.__file__}))"
                ),
            ],
        )
    )
    native_files = {
        name: {"path": str(Path(path).resolve()), "sha256": sha(path)}
        for name, path in {
            "python": str(python),
            "official": str(official),
            "pcbnew": native["pcbnew"],
            "_pcbnew": native["_pcbnew"],
            "consumer": str(consumer),
            "fitz": rendering["fitz"],
            "pymupdf": rendering["pymupdf"],
            "renderer": rendering["renderer"],
        }.items()
    }
    config.mkdir(parents=True)
    for name in ("sym-lib-table", "fp-lib-table"):
        shutil.copyfile(native_root / "share/kicad/template" / name, config / name)
    settings.update(
        candidate={
            "path": str(candidate),
            "sha256": sha(candidate),
            "source_commit": candidate_record["source_commit"],
        },
        environment=environment,
        readonly_consumer_python=str(consumer),
        native_version=native["version"],
        native_files=native_files,
        working_directory=str(execution),
    )
    write_new(packet / "DEPENDENCIES.json", settings)
    write_new(
        packet / "REVISION.json",
        {
            "candidate": str(candidate),
            "sha256": sha(candidate),
            "source_commit": candidate_record["source_commit"],
        },
    )
    write_new(
        packet / "scope.json",
        {
            "scope_id": "owned-rc-internal-trial",
            "tool": "kicad-cli",
            "granted_by": "Caller explicitly selected --authorize-owned-root",
            "granted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "expires_at": None,
            "effects": ["observe", "change", "persist", "artifact"],
            "dangerous": False,
            "roots": [str(authorized)],
            "outputs": [str(authorized)],
            "notes": "Only this disposable package execution directory; no user original project",
        },
    )
    doctor = json.loads(call("doctor", [candidate, "doctor", "--compact"]))
    checks = {row["check"]: row["status"] for row in doctor["data"]["checks"]}
    if not all(
        checks.get(name) == "pass" for name in ("native_backend", "official_cli", "authorization")
    ):
        raise ValueError(
            "Required doctor checks failed; preserve configuration/logs and "
            "inspect the named dependency"
        )
    result = {
        "ok": True,
        "candidate_sha256": sha(candidate),
        "native_version": native["version"],
        "configured_packet": str(packet),
        "authorized_root": str(authorized),
        "external_dependencies": native_files,
        "logs": str(log),
        "release_readiness": "unpublishable",
        "next_action": (
            "Read FIRST_VERSION_SCOPE.md and WINDOWS_TRIAL.md. Choose a documented "
            "saved-project edit in HIERARCHICAL_EDIT.md or ADVANCED_SCHEMATIC_EDIT.md "
            "under a new owned execution directory. Creation/native export risks remain "
            "separate. This authorizes no user originals or GUI operations."
        ),
    }
    write_new(log / "result.json", result)
    # Escaped JSON remains machine-readable even when the caller's Windows
    # console encoding differs from the UTF-8 files written above.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "RC_CONFIGURATION_REFUSED",
                        "message": str(exc),
                        "next_action": (
                            "Use existing explicitly selected dependencies; do not bypass pins, "
                            "install implicitly, or reuse partially configured package copies."
                        ),
                    },
                }
            )
        )
        raise SystemExit(2) from exc
