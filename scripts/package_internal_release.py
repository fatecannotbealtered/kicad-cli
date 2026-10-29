"""Curated product distribution; private deployment configuration never ships."""

import argparse
import hashlib
import json
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kicad_cli.internal_release import SERIES, TAG, profile  # noqa: E402
from scripts.build_module_skills import copy_knowledge, generate  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def package(build, destination, sample, binding):
    build, out, sample, binding = map(Path, (build, destination, sample, binding))
    out.mkdir(exist_ok=False)
    bundle = out / "kicad-cli-internal"
    (bundle / "bin").mkdir(parents=True)
    build_record = json.loads((build / "BUILD-RESULT.json").read_bytes())
    target_os = build_record.get("platform", sys.platform)
    target_arch = {"AMD64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        build_record.get("architecture", platform.machine())
    )
    if target_os not in {"win32", "darwin", "linux"} or target_arch not in {"x64", "arm64"}:
        raise ValueError("Unsupported native build target")
    binary_name = "kicad-cli.exe" if target_os == "win32" else "kicad-cli"
    binary = bundle / "bin" / binary_name
    shutil.copy2(build / "dist" / binary_name, binary)
    if target_os != "win32":
        binary.chmod(0o755)
    version = json.loads((ROOT / "package.json").read_bytes())["version"]
    runtime_binding = json.loads(binding.read_bytes())["observation"]["binding"]
    package_meta = json.loads((ROOT / "package.json").read_bytes())
    package_meta.pop("optionalDependencies", None)
    package_meta.update(
        os=[target_os],
        cpu=[target_arch],
        files=[
            "bin",
            "scripts/run.js",
            "skills",
            "INTERNAL-BUNDLE.json",
            "README_zh.md",
            "README.md",
            "LICENSE",
            "NOTICE.md",
            "authorization",
            "release",
            "examples",
            "qualification",
            "knowledge",
            "docs",
            "third_party",
        ],
    )
    put(bundle / "package.json", package_meta)
    (bundle / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/run.js", bundle / "scripts/run.js")
    for name in ("README.md", "README_zh.md", "LICENSE", "NOTICE.md", "CHANGELOG.md"):
        shutil.copyfile(ROOT / name, bundle / name)
    (bundle / "docs/modules").mkdir(parents=True)
    shutil.copyfile(ROOT / "docs/modules/DELIVERY.md", bundle / "docs/modules/DELIVERY.md")
    for name in ("INSTALLATION.md", "COMPATIBILITY.md", "KNOWLEDGE.md"):
        shutil.copyfile(ROOT / "docs" / name, bundle / "docs" / name)
    shutil.copyfile(ROOT / "docs/modules/common.md", bundle / "docs/modules/common.md")
    copy_knowledge(bundle / "knowledge")
    for ext in ("json", "md"):
        target = bundle / "authorization" / ("DISTRIBUTION.json" if ext == "json" else "NOTICE.md")
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "third_party/tool-driver" / target.name, target)
    shutil.copytree(ROOT / "third_party", bundle / "third_party")
    shutil.copytree(build / "work/dependency-notices", bundle / "third_party/runtime")
    generate(
        bundle / "skills",
        executable=binary,
        runtime_binding=runtime_binding,
    )
    put(bundle / "release/PROFILE.json", profile())
    put(
        bundle / "INTERNAL-BUNDLE.json",
        {
            "delivery_series": SERIES,
            "tag": TAG,
            "product_version": version,
            "exe_sha256": sha(binary),
            "binary_name": binary_name,
            "platform": target_os,
            "architecture": target_arch,
            "runtime_binding": runtime_binding,
            "build_source": json.loads((build / "BUILD-RESULT.json").read_bytes())[
                "product_source"
            ],
        },
    )
    for example in ("functional-schmitt", "library", "arc-scope"):
        shutil.copytree(ROOT / "examples" / example, bundle / "examples" / example)
    # A small editable design, with all local library assets and no old control plane.
    target = bundle / "examples/wired-schmitt"
    for source in sample.rglob("*"):
        if not source.is_file():
            continue
        if (
            source.suffix
            in {".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_mod", ".kicad_sym"}
            or source.name in {"sym-lib-table", "fp-lib-table", "source-facts.json"}
            or source.name.endswith(".provenance.json")
        ):
            dest = target / source.relative_to(sample)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    q = bundle / "qualification"
    q.mkdir()
    (q / "README.md").write_text(
        "Exact executable/package acceptance is recorded alongside these artifacts. "
        "No prior executable evidence is inherited. Agent evaluations remain not_run.\n",
        encoding="utf-8",
        newline="\n",
    )
    put(
        bundle / "bin/runtime-config.example.json",
        {
            "format_version": 1,
            "kicad_root": "<existing legal KiCad10.0.6 root>",
            "profile_dir": "<new owned profile directory>",
        },
    )
    files = {
        p.relative_to(bundle).as_posix(): sha(p) for p in sorted(bundle.rglob("*")) if p.is_file()
    }
    put(bundle / "FILE-MANIFEST.json", files)
    target = f"{target_os}-{target_arch}"
    tgz = out / f"fateforge-kicad-cli-{version}-{target}-internal.tgz"
    with tarfile.open(tgz, "w:gz") as archive:
        archive.add(bundle, arcname="package")
    zipped = out / f"kicad-cli-internal-{SERIES}-{target}.zip"
    with zipfile.ZipFile(zipped, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for p in sorted(bundle.rglob("*")):
            if p.is_file():
                archive.write(p, p.relative_to(bundle.parent).as_posix())
    portable = None
    if target_os != "win32":
        portable = out / f"kicad-cli-internal-{SERIES}-{target}.tar.gz"
        with tarfile.open(portable, "w:gz") as archive:
            archive.add(bundle, arcname=bundle.name)
    put(
        out / "ARTIFACTS.json",
        {
            "series": SERIES,
            "product_version": version,
            "exe_sha256": sha(binary),
            "tgz": tgz.name,
            "tgz_sha256": sha(tgz),
            "zip": zipped.name,
            "zip_sha256": sha(zipped),
            "manifest_sha256": sha(bundle / "FILE-MANIFEST.json"),
            "platform": target_os,
            "architecture": target_arch,
            "binary_name": binary_name,
            "archive": portable.name if portable else zipped.name,
            "archive_sha256": sha(portable) if portable else sha(zipped),
        },
    )
    print(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for key in ("build", "destination", "sample", "binding"):
        parser.add_argument("--" + key, required=True)
    package(**vars(parser.parse_args()))
