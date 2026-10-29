"""Assemble a new internal module candidate; never replace a frozen package."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_module_skills import generate


def put(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(build, destination):
    build, destination = Path(build).resolve(), Path(destination).resolve()
    destination.mkdir(exist_ok=False)
    (destination / "bin").mkdir()
    binary = destination / "bin/kicad-cli.exe"
    shutil.copyfile(build / "dist/kicad-cli.exe", binary)
    profile = destination.parent / (destination.name + "-packaging-profile")
    profile.mkdir(exist_ok=False)
    config = profile / "runtime-config.json"
    put(
        config,
        {
            "format_version": 1,
            "kicad_root": "D:/software/kicad/home",
            "profile_dir": str(profile / "owned"),
        },
    )
    env = dict(os.environ, KICAD_CLI_RUNTIME_CONFIG=str(config))

    def public(*args):
        process = subprocess.run(
            [str(binary), *args, "--compact"], env=env, capture_output=True, timeout=90, cwd=profile
        )
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert result["ok"], result
        return result["data"]

    public("--check-environment")
    modules = public("modules")["observation"]
    reference = public("reference")
    generate(destination / "skills", executable=binary, runtime_binding=modules["binding"])
    put(destination / "CAPABILITY-MATRIX.json", modules)
    put(destination / "REFERENCE.json", reference)
    for name in ("README_zh.md", "LICENSE", "NOTICE.md", "SECURITY.md", "CHANGELOG.md"):
        shutil.copyfile(ROOT / name, destination / name)
    shutil.copytree(ROOT / "docs/modules", destination / "docs")
    shutil.copytree(ROOT / "examples/library", destination / "examples/library")
    auth = destination / "authorization"
    auth.mkdir()
    for suffix in ("json", "md"):
        shutil.copyfile(
            ROOT
            / "third_party/tool-driver"
            / ("DISTRIBUTION.json" if suffix == "json" else "NOTICE.md"),
            auth / ("DISTRIBUTION.json" if suffix == "json" else "NOTICE.md"),
        )
    old = ROOT.parent / "kicad-first-design-20261024/candidate-05"
    shutil.copytree(old / "notices", destination / "notices")
    # License texts are unchanged dependencies; prior runtime inventories are
    # not copied as qualification for this binary.
    for path in (destination / "notices").glob("*INDEX*.json"):
        path.unlink()
    test_root = ROOT.parent / "kicad-modules-validation-20261026"
    baseline = json.loads((test_root / "BASELINE.json").read_bytes())
    phone = destination / "examples/phone"
    phone.mkdir()
    with zipfile.ZipFile(baseline["phone_zip"]) as archive:
        prefix = "PHONE-A1-ENGINEERING-REVIEW-r02-LITE/"
        for entry in archive.infolist():
            if entry.filename.startswith(prefix + "cad/") and not entry.is_dir():
                target = (phone / entry.filename[len(prefix) :]).resolve()
                assert target.is_relative_to(phone)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    result = json.loads((test_root / "phone-patch-16/PATCH-RESULT.json").read_bytes())
    put(phone / "patch.json", result["proposal"])
    acceptance = destination / "acceptance"
    acceptance.mkdir()
    put(
        acceptance / "SOURCE-PHONE-RESULT.json",
        {
            "input_sha256": baseline["phone_board_sha256"],
            "result_sha256": hashlib.sha256(
                (test_root / "phone-patch-16/cad/main/PHONE_A1_MAIN.kicad_pcb").read_bytes()
            ).hexdigest(),
            "task": result["receipt"]["receipt"]["extensions"]["task"],
            "finding_comparison": result["receipt"]["receipt"]["extensions"]["finding_comparison"],
            "not_frozen_qualification": True,
        },
    )
    binding = json.loads((build / "BUILD-RESULT.json").read_bytes())
    binding.update(
        source_binding=modules["binding"],
        exe_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        sdk_authorization="authorization/DISTRIBUTION.json",
        installation="Standalone module folders include all resources; external legal KiCad required",
        whole_product_release_readiness="unpublishable",
        full_FCC="unknown",
    )
    put(destination / "CANDIDATE-BINDING.json", binding)
    put(
        destination / "QUALIFICATION.json",
        {
            "independent_execution": "pending",
            "artifact": binding["exe_sha256"],
            "scope": "same-host Windows x64/local NTFS/KiCad10.0.6 internal candidate; no hardware approval",
        },
    )
    put(
        destination / "FILE-MANIFEST.json",
        {
            p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(destination.rglob("*"))
            if p.is_file() and p.name != "FILE-MANIFEST.json"
        },
    )
    print(destination)


if __name__ == "__main__":
    main(*sys.argv[1:3])
