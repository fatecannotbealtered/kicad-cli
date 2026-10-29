"""Assemble self-contained functional evidence without copying scopes or operation state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_module_skills import generate  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scrub(value, key=""):
    if key.lower() in {
        "confirm_token",
        "confirmation_token",
        "secret",
        "hmac",
        "signing_key",
        "private_key",
        "signature",
        "mac",
    }:
        return "<redacted>"
    if isinstance(value, dict):
        if {"scope_id", "granted_by", "roots"} <= value.keys():
            return "<local authorization grant omitted>"
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        result = [scrub(v) for v in value]
        for index, item in enumerate(result[:-1]):
            if item == "--confirm":
                result[index + 1] = "<redacted-confirm-token>"
        return result
    if isinstance(value, str):
        value = re.sub(r"ct_[A-Za-z0-9_-]{50,}\.[A-Za-z0-9_-]+", "<redacted-confirm-token>", value)
        value = re.sub(
            r"(?i)([\"'](?:hmac|secret|signing_key|private_key|mac)[\"']\s*:\s*)[\"'][^\"']+[\"']",
            r'\1"<redacted>"',
            value,
        )
        # Redact any Windows user-profile prefix, not a developer-specific account.
        return re.sub(r"(?i)[A-Z]:[\\/]+Users[\\/]+[^\\/\s\"']+", "<USER>", value)
    return value


def copy_cad(source, destination):
    allowed = {".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_sym", ".kicad_mod"}
    for path in sorted(Path(source).rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        source_record = path.name == "source-facts.json" or path.name.startswith("library-input-")
        if (
            path.suffix not in allowed
            and path.name not in {"sym-lib-table", "fp-lib-table"}
            and not path.name.endswith(".provenance.json")
            and not source_record
        ):
            continue
        target = Path(destination) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        assert sha(path) == sha(target)


def calls(source, target):
    index = []
    for directory in sorted((source / "calls").iterdir()):
        if not (directory / "call.json").is_file():
            continue
        record = json.loads((directory / "call.json").read_bytes())
        row = {"call": record}
        raw = {}
        for name in ("stdout", "stderr"):
            path = directory / name
            raw[name] = {"sha256": sha(path), "bytes": path.stat().st_size}
            try:
                value = json.loads(path.read_bytes())
            except (ValueError, UnicodeError):
                value = path.read_bytes().decode("utf-8", "replace")
            row[name] = value
        put(target / (directory.name + ".json"), scrub(row))
        index.append({"call": directory.name, "original": raw})
    put(target / "RAW-INDEX.json", index)


def evidence(source, target):
    target.mkdir(parents=True, exist_ok=False)
    calls(source, target / "calls")
    for path in sorted(source.iterdir()):
        if path.is_file() and path.suffix == ".json" and path.name != "runtime-config.json":
            put(target / path.name, scrub(json.loads(path.read_bytes())))
        elif path.is_file() and path.suffix in {".xml", ".log"}:
            (target / path.name).write_text(
                scrub(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8"
            )


def manifest(folder):
    values = {
        p.relative_to(folder).as_posix(): sha(p)
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != "FILE-MANIFEST.json"
    }
    put(folder / "FILE-MANIFEST.json", values)
    return values


def archive(folder, path):
    values = manifest(folder)
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as packed:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                packed.write(p, p.relative_to(folder.parent).as_posix())
    with zipfile.ZipFile(path) as packed:
        assert packed.testzip() is None
        assert all(
            hashlib.sha256(packed.read(folder.name + "/" + n)).hexdigest() == h
            for n, h in values.items()
        )
    return {
        "zip": str(path),
        "zip_sha256": sha(path),
        "exe_sha256": sha(folder / "bin/kicad-cli.exe"),
        "manifest_sha256": sha(folder / "FILE-MANIFEST.json"),
        "bytes": path.stat().st_size,
        "file_count": len(values) + 1,
    }


def main(build, destination, validation, functional, phone, reproduction, suffix="01"):
    build, destination, validation = map(
        lambda p: Path(p).resolve(), (build, destination, validation)
    )
    functional, phone, reproduction = map(
        lambda p: Path(p).resolve(), (functional, phone, reproduction)
    )
    destination.mkdir(exist_ok=False)
    binary = destination / "bin/kicad-cli.exe"
    binary.parent.mkdir()
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
        p = subprocess.run(
            [str(binary), *args, "--compact"],
            cwd=profile,
            env=env,
            capture_output=True,
            timeout=120,
        )
        result = json.loads(p.stdout)
        assert p.returncode == 0 and result["ok"]
        return result["data"]

    public("--check-environment")
    modules, reference = public("modules")["observation"], public("reference")
    generate(destination / "skills", executable=binary, runtime_binding=modules["binding"])
    put(destination / "CAPABILITY-MATRIX.json", modules)
    put(destination / "REFERENCE.json", reference)
    for name in ("README_zh.md", "LICENSE", "NOTICE.md", "SECURITY.md", "CHANGELOG.md"):
        shutil.copyfile(ROOT / name, destination / name)
    shutil.copytree(
        ROOT / "docs/modules", destination / "docs", ignore=shutil.ignore_patterns("DELIVERY*")
    )
    shutil.copytree(ROOT / "examples/library", destination / "examples/library")
    shutil.copytree(
        ROOT / "examples/functional-schmitt", destination / "examples/functional-schmitt"
    )
    for auth_ext in ("json", "md"):
        path = (
            destination
            / "authorization"
            / ("DISTRIBUTION.json" if auth_ext == "json" else "NOTICE.md")
        )
        path.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "third_party/tool-driver" / path.name, path)
    old_notices = ROOT.parent / "kicad-first-design-20261024/candidate-05/notices"
    shutil.copytree(
        old_notices, destination / "notices", ignore=shutil.ignore_patterns("*INDEX*.json")
    )
    original = (
        ROOT.parent
        / "phone-design-20261025/delivery/compression-trials-r02-lite"
        / "phone-a1-review-delivery-r02-lite.zip"
    )
    assert sha(original) == "6136585088cc76ebf1f15dc86de6e1141a4e5a3f85c8dde856e2d877fcda4ab1"
    with zipfile.ZipFile(original) as packed:
        prefix = "PHONE-A1-ENGINEERING-REVIEW-r02-LITE/"
        for item in packed.infolist():
            if item.is_dir() or not item.filename.startswith(
                (prefix + "cad/", prefix + "licenses/")
            ):
                continue
            target = (destination / "examples/phone" / item.filename[len(prefix) :]).resolve()
            assert target.is_relative_to(destination / "examples/phone")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(packed.read(item))
    assert (
        sha(destination / "examples/phone/cad/main/PHONE_A1_MAIN.kicad_pcb")
        == "0bc266ab2fc24e353fe62eb590d132bcc00bf87c5d60661f764060bb5860c0e8"
    )
    copy_cad(functional / "project", destination / "engineering/functional/project")
    copy_cad(phone / "phone/cad", destination / "engineering/phone-deduplicated/cad")
    for name in (
        "INTENT-BEFORE-DESIGN.json",
        "CONTINUATION-INTENT-BEFORE.json",
        "source-facts.json",
    ):
        if (functional / name).is_file():
            shutil.copyfile(functional / name, destination / "engineering/functional" / name)
    default_exports = (
        "sch-netlist-eco",
        "sch-svg-eco",
        "sch-pdf-eco",
        "pcb-svg",
        "pcb-pdf",
        "gerber",
        "drill",
    )
    result = json.loads((functional / "FUNCTIONAL-RESULT.json").read_bytes())
    exports = result.get("current_exports", {name: name for name in default_exports})
    for name, source in exports.items():
        shutil.copytree(functional / source, destination / "engineering/functional/exports" / name)
    q = destination / "qualification"
    for source, name in (
        (functional, "functional-frozen"),
        (phone, "phone-frozen"),
        (reproduction, "reproduction/frozen"),
        (validation / "before-01", "reproduction/prior"),
    ):
        evidence(source, q / name)
    copy_cad(reproduction / "project", q / "reproduction/minimal-inputs")
    evidence(validation / ("straight-regression-" + suffix), q / "straight-regression")
    copy_cad(
        validation / ("straight-regression-" + suffix) / "before", q / "straight-regression/before"
    )
    copy_cad(
        validation / ("straight-regression-" + suffix) / "project", q / "straight-regression/after"
    )
    copy_cad(validation / "before-01/project", q / "reproduction/prior-output")
    evidence(validation / "sub-nm-before-public-01", q / "reproduction/sub-nm-prior")
    copy_cad(
        validation / "sub-nm-before-public-01/project", q / "reproduction/sub-nm-prior/outputs"
    )
    for name in ("before-routing", "before-eco", "before-continuation-eco"):
        if (functional / name).is_dir():
            copy_cad(functional / name, q / "functional-baselines" / name)
    shutil.copytree(
        functional / "review-views", destination / "engineering/functional/review-views"
    )
    for name in ("SOURCE-INPUTS.json", "BUILD-RESULT.json", "ACTUAL-PIN-DEPENDENCY-CHECK.json"):
        shutil.copyfile(build / name, q / name)
    names = [
        "regression-source-01",
        "regression-final-shared-" + suffix,
        "regression-frozen-sch-" + suffix,
    ]
    for name in names:
        if name != "regression-source-01":
            request = json.loads((validation / name / "REQUEST.json").read_bytes())
            assert request["binary_sha256"] == sha(binary), "Regression belongs to another artifact"
        folder = q / name
        folder.mkdir()
        for path in (validation / name).glob("*"):
            if path.is_file() and path.suffix in {".json", ".log", ".xml"}:
                if path.suffix == ".json":
                    put(folder / path.name, scrub(json.loads(path.read_bytes())))
                else:
                    shutil.copyfile(path, folder / path.name)
    # Keep actual development failures, not only successful final calls.
    for name in (
        "function-source-01",
        "function-source-02",
        "function-source-03",
        "function-source-04",
    ):
        evidence(validation / name, q / "development-failures" / name)
    controller_logs = q / "development-controllers"
    controller_logs.mkdir()
    for path in sorted(validation.glob("*.log")):
        if path.name.startswith(("function-", "functional-", "layout-", "straight-")):
            (controller_logs / path.name).write_text(
                scrub(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8"
            )
    for name in (
        "LINT-COMPARISON.json",
        "SKILL-EOL-REPRO.json",
        "SKILL-GIT-BLOB-VERIFICATION.json",
    ):
        put(q / name, scrub(json.loads((validation / name).read_bytes())))
    for report_ext in ("xml", "log"):
        shutil.copyfile(
            validation / ("skill-final-02." + report_ext), q / ("skill-final-02." + report_ext)
        )
    binding = json.loads((build / "BUILD-RESULT.json").read_bytes())
    binding.update(
        source_binding=modules["binding"],
        exe_sha256=sha(binary),
        full_FCC="unknown",
        release_readiness="unpublishable",
    )
    put(destination / "CANDIDATE-BINDING.json", binding)
    put(
        destination / "QUALIFICATION.json",
        {
            "artifact_sha256": sha(binary),
            "product_source": binding["product_source"],
            "source_regression": "qualification/regression-source-01",
            "native_functional": "qualification/functional-frozen",
            "native_phone": "qualification/phone-frozen",
            "independent_execution": "pending",
            "whole_product_FCC": "unknown",
            "release_readiness": "unpublishable",
            "scope": "same-host Windows x64 / local NTFS / legal KiCad10.0.6; "
            "no power-on, SI or manufacturing signoff",
        },
    )
    (destination / "READ-ME-FIRST.md").write_text(
        "# 工程模块功能候选\n\n先读README_zh.md。"
        "engineering/functional为已真实接线且完成再次修改的工程；"
        "exports包含当前图面、网表及制造文件。examples含事前意图和原始手机板。"
        "qualification含脱敏实际回执、失败、原生报告和最小复现，不能只看摘要数字。\n\n"
        "独立会话必须使用自己的profile和有限scope，通过公开CLI继续修改；"
        "不得重放旧token或selector。整份Skill目录可独立安装，不覆盖已安装环境。"
        "有限内部资格不等于完整FCC、仿真、上电或制造通过。\n",
        encoding="utf-8",
    )
    values = manifest(destination)
    for p in destination.rglob("*"):
        if p.is_file() and p.suffix in {".json", ".log", ".md"}:
            assert not re.search(rb"ct_[A-Za-z0-9_-]{50,}\.[A-Za-z0-9_-]+", p.read_bytes()), p
    print(
        json.dumps(
            {"folder": str(destination), "files": len(values) + 1, "exe_sha256": sha(binary)},
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("build", "destination", "validation", "functional", "phone", "reproduction"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--suffix", default="01")
    args = parser.parse_args()
    main(**vars(args))
