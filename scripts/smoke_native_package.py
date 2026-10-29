"""Basic native install and public library/SCH/PCB smoke, wholly in owned copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "bench")]
from module_trial import Trial, library_trial  # noqa: E402

from kicad_cli.installation import python_candidates  # noqa: E402
from scripts.package_internal_release import package  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def verify_files(folder):
    files = json.loads((folder / "FILE-MANIFEST.json").read_bytes())
    assert all(sha(folder / name) == digest for name, digest in files.items())
    return len(files)


def main(build, kicad_root, destination):
    build, kicad_root, out = map(lambda p: Path(p).resolve(), (build, kicad_root, destination))
    out.mkdir(parents=True, exist_ok=False)
    result = {
        "status": "running",
        "scope": "basic native package smoke",
        "cad_inputs": "new owned projects",
        "full_fcc_required": False,
    }
    try:
        built = json.loads((build / "BUILD-RESULT.json").read_bytes())
        binary = build / "dist" / built["binary_name"]
        assert built["exit_code"] == 0 and sha(binary) == built["binary_sha256"]
        direct = library_trial(out / "bare-binary-smoke", binary, kicad_root)
        observed = direct.cli("modules")
        put(out / "binding.json", observed)
        distribution = out / "distribution"
        package(build, distribution, direct.root / "project", out / "binding.json")
        artifacts = json.loads((distribution / "ARTIFACTS.json").read_bytes())
        archive = distribution / artifacts["archive"]
        assert sha(archive) == artifacts["archive_sha256"]
        unpacked = out / "direct-install"
        unpacked.mkdir()
        if archive.name.endswith(".tar.gz"):
            with tarfile.open(archive) as packed:
                packed.extractall(unpacked, filter="data")
        else:
            with zipfile.ZipFile(archive) as packed:
                packed.extractall(unpacked)
        installed = unpacked / "kicad-cli-internal"
        file_count = verify_files(installed)
        manifest = json.loads((installed / "INTERNAL-BUNDLE.json").read_bytes())
        installed_binary = installed / "bin" / manifest["binary_name"]
        assert sha(installed_binary) == built["binary_sha256"]
        assert os.access(installed_binary, os.X_OK)
        clean = Trial(out / "direct-install-startup", installed_binary, kicad_root)
        boot = json.loads((clean.logs / "0001/stdout").read_bytes())
        checks = {c["name"]: c for c in boot["data"]["checks"]}
        assert checks["pinned_runtime"]["mode"] == "embedded"
        assert checks["python_validation"]["mode"] == "frozen"
        clean.env["PATH"] = (
            str(Path(os.environ["SystemRoot"]) / "System32") if os.name == "nt" else "/usr/bin:/bin"
        )
        index = clean.cli("reference", "--index")
        assert len(index["commands"]) == 43
        assert clean.cli("context")["version"] == manifest["product_version"]
        node = shutil.which("node")
        assert node, "Node wrapper smoke needs Node; direct binary above does not"
        npm_command = shutil.which("npm")
        assert npm_command, "Node installation must include npm for wrapper install smoke"
        npm_cli = (
            Path(node).parent / "node_modules/npm/bin/npm-cli.js"
            if os.name == "nt"
            else Path(npm_command).resolve()
        )
        prefix = out / "npm-prefix"
        proc = subprocess.run(
            [
                node,
                str(npm_cli),
                "install",
                "--global",
                "--prefix",
                str(prefix),
                "--cache",
                str(out / "npm-cache"),
                "--offline",
                "--no-audit",
                "--no-fund",
                str(distribution / artifacts["tgz"]),
            ],
            capture_output=True,
            timeout=120,
        )
        (out / "npm-install.log").write_bytes(proc.stdout + proc.stderr)
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")
        node_package = (
            prefix / "node_modules/@fateforge/kicad-cli"
            if os.name == "nt"
            else prefix / "lib/node_modules/@fateforge/kicad-cli"
        )
        verify_files(node_package)
        assert sha(node_package / "bin" / manifest["binary_name"]) == built["binary_sha256"]
        trial = library_trial(
            out / "installed-wrapper-smoke", [node, node_package / "scripts/run.js"], kicad_root
        )
        live = trial.cli("modules")["observation"]["module_bindings"]
        installed_skills = []
        for folder in sorted((node_package / "skills").iterdir()):
            if not folder.is_dir():
                continue
            files = json.loads((folder / "RESOURCE-MANIFEST.json").read_bytes())
            assert all(sha(folder / file) == digest for file, digest in files.items())
            binding = json.loads((folder / "MODULE-BINDING.json").read_bytes())
            expected = live[binding["module"]]
            assert {key: binding[key] for key in expected} == expected
            assert binding["executable_sha256"] == built["binary_sha256"]
            target = out / "skill-install" / folder.name / ".agents/skills" / folder.name
            target.parent.mkdir(parents=True)
            shutil.copytree(folder, target)
            assert all(sha(target / file) == digest for file, digest in files.items())
            installed_skills.append(folder.name)
        assert len(installed_skills) == 6
        invalid = out / "invalid-config.json"
        put(
            invalid,
            {
                "format_version": 1,
                "kicad_root": str(out / "missing-install"),
                "profile_dir": str(out / "invalid-profile"),
            },
        )
        bad = subprocess.run(
            [str(installed_binary), "reference", "--index", "--compact"],
            env=dict(os.environ, KICAD_CLI_RUNTIME_CONFIG=str(invalid)),
            capture_output=True,
            timeout=60,
        )
        assert bad.returncode == 4 and json.loads(bad.stdout)["error"]["code"] == "E_CONFIG"
        wrong = out / "wrong-target"
        shutil.copytree(node_package, wrong)
        bad_manifest = dict(manifest, platform="win32" if sys.platform != "win32" else "linux")
        put(wrong / "INTERNAL-BUNDLE.json", bad_manifest)
        bad_node = subprocess.run(
            [node, str(wrong / "scripts/run.js"), "reference", "--index"],
            capture_output=True,
            timeout=30,
        )
        assert bad_node.returncode == 1 and b"targets" in bad_node.stderr
        native_python = next(p for p in python_candidates(kicad_root) if p.is_file())
        reader = out / "native-reopen.py"
        reader.write_text(
            """import json,sys,pcbnew
def snapshot(board):
    parts=[]
    for f in board.GetFootprints():
        pads=sorted((p.GetNumber(),int(p.GetPosition().x),int(p.GetPosition().y)) for p in f.Pads())
        parts.append((f.GetReference(),f.GetValue(),pads))
    return sorted(parts)
board=pcbnew.LoadBoard(sys.argv[1])
before=snapshot(board)
assert len(before)==1 and len(before[0][2])==5
pcbnew.SaveBoard(sys.argv[2],board)
after=snapshot(pcbnew.LoadBoard(sys.argv[2]))
assert before==after
print(json.dumps({"kicad":pcbnew.GetBuildVersion(),"footprints":len(after),"pads":len(after[0][2]),"geometry_and_fields_preserved":before==after}))
""",
            encoding="utf-8",
        )
        reopened = trial.raw(
            [
                native_python,
                "-B",
                reader,
                trial.root / "project/coupon.kicad_pcb",
                out / "native-reopened.kicad_pcb",
            ]
        )
        assert reopened.returncode == 0, reopened.stderr.decode(errors="replace")
        native = next(
            json.loads(line) for line in reopened.stdout.splitlines() if line.startswith(b"{")
        )
        result.update(
            status="passed",
            product_source=built["product_source"],
            product_version=manifest["product_version"],
            platform=manifest["platform"],
            architecture=manifest["architecture"],
            binary_sha256=built["binary_sha256"],
            artifacts=artifacts,
            installed_manifest_files_verified=file_count,
            direct_binary_without_build_python_on_path=True,
            node_install_exit_code=proc.returncode,
            six_skill_installations_and_bindings_verified=installed_skills,
            public_cli_calls=direct.index + clean.index + trial.index,
            native_save_reopen=native,
            wrong_root_refused=True,
            wrong_platform_wrapper_refused=True,
            agent_evaluation_run=False,
            limits="Basic one-symbol/pin-mapping coupon; no full CAD or hardware claim",
        )
    except BaseException as exc:
        result.update(status="failed", error_type=type(exc).__name__, reason=str(exc)[-4000:])
        traceback.print_exc()
    put(out / "SMOKE-RESULT.json", result)
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("build", "kicad-root", "destination"):
        parser.add_argument("--" + name, required=True)
    raise SystemExit(main(**vars(parser.parse_args())))
