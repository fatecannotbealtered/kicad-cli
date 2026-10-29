"""Assemble an internal Windows trial; licensed external dependencies stay external."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path, PureWindowsPath


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


FIRST_VERSION_ARCHIVE = Path("docs/history/windows-first-version-6960788")
FIRST_VERSION_ARCHIVE_SOURCE = "6960788540888f198cf0ba81c51b2dbcd7619f74"
FIRST_VERSION_ARCHIVE_FILES = {
    "FIRST_VERSION_SCOPE.md": (
        21257,
        "85df6e5a1ddb0fb22bad8828114a472f24940f2af9b5370c74f66d2a586f5a94",
    ),
    "FIRST_VERSION_CANDIDATE.md": (
        30040,
        "d43073a5ed1c8624a114296ebf3ab3f2a3e06d6d46884f1da489e3ced441f5e7",
    ),
    "FIRST_VERSION_HANDOFF.md": (
        21443,
        "829fe4b5d4e8d9fb372dab08b90193a51e82d2f79375af962ba3290c80f6876a",
    ),
}
CURRENT_ENTRY_ARCHIVE = Path("docs/history/windows-first-version-c913dc2")
CURRENT_ENTRY_ARCHIVE_SOURCE = "c913dc2596feb57ed88de41ffa434f436e4e64c3"
CURRENT_ENTRY_ARCHIVE_FILES = {
    "FIRST_VERSION_SCOPE.md": (
        3582,
        "c22d95c72bbfe01f53b3514f49341dbbcea6f62fbf4699f18967faa051d34f53",
    ),
    "FIRST_VERSION_CANDIDATE.md": (
        3052,
        "e182908b7c0a707bd634cc6b9cb9e84c71de2276adee3987e8b919ff4e55c1b4",
    ),
    "FIRST_VERSION_HANDOFF.md": (
        3634,
        "5509867523322998066d286e498ffd98da81ff9c68a9fc6019a837f809d708d2",
    ),
}


DRILL_ENTRY_ARCHIVE = Path("docs/history/windows-first-version-4b28db0")
DRILL_ENTRY_ARCHIVE_SOURCE = "4b28db0f316cfdf63663e21630996f70d7aba14e"
DRILL_ENTRY_ARCHIVE_FILES = {
    "FIRST_VERSION_SCOPE.md": (
        3381,
        "ed7e38b85e3d993d00dc293b4bf77521e19ef495b4b1b8500651a01ed5de094a",
    ),
    "FIRST_VERSION_CANDIDATE.md": (
        3494,
        "693a3cc1ee596078aee77bc89be4776285b1fab443ad2c899c53574bc3b29845",
    ),
    "FIRST_VERSION_HANDOFF.md": (
        4852,
        "0af4b459a05576c9196e1822436b728933d2e5f549e229ed0898edca5e8039c0",
    ),
    "DUAL_RC_ECO.md": (
        13762,
        "37753b0422bcbaa7979df83ce414932a40906d5b4ad08719b4ff5bcf83897aca",
    ),
    "dual-rc-acceptance/ASSEMBLY-LIMITS.md": (
        2589,
        "3a81fc13a7162c733cef424fca0d85e2f1ae940b8a572c273aa45974ff03306c",
    ),
}

SILK_ENTRY_ARCHIVE = Path("docs/history/windows-first-version-16ac7df")
SILK_ENTRY_ARCHIVE_SOURCE = "16ac7df86532d521220409dfce71b7fdba933cfd"
SILK_ENTRY_ARCHIVE_FILES = {
    "FIRST_VERSION_SCOPE.md": (
        3068,
        "b1e40b9c1b1307a67217b9fa6be4bbbfdd18779dafedc16098ab4b8f329912a3",
    ),
    "FIRST_VERSION_CANDIDATE.md": (
        4082,
        "efe9bb524a4c1b48f20ba9b529f2a878ab5dde6e14a6509b3a1bd1d8ca17f473",
    ),
    "FIRST_VERSION_HANDOFF.md": (
        4700,
        "7b250bd06f3e4bff8b9cfbc35de8dd4c8f01105ab16cbc232ce624e32d1c8ff5",
    ),
    "DUAL_RC_DRILL_CLEARANCE.md": (
        15594,
        "2c4f71682805ddeb30fa651a04729b1025eeded1eb0e9d0797d270c9820bf4c0",
    ),
}


def archived_document_bases(package):
    bases = {}
    for relative, source, files in (
        (
            FIRST_VERSION_ARCHIVE,
            FIRST_VERSION_ARCHIVE_SOURCE,
            FIRST_VERSION_ARCHIVE_FILES,
        ),
        (
            CURRENT_ENTRY_ARCHIVE,
            CURRENT_ENTRY_ARCHIVE_SOURCE,
            CURRENT_ENTRY_ARCHIVE_FILES,
        ),
        (DRILL_ENTRY_ARCHIVE, DRILL_ENTRY_ARCHIVE_SOURCE, DRILL_ENTRY_ARCHIVE_FILES),
    ):
        bases.update(_archived_document_bases(package, relative, source, files))
    bases.update(_silk_archived_document_bases(package))
    return bases


def _silk_archived_document_bases(package):
    """Resolve exact 16ac entry bytes against their original repository root."""
    archive = package / SILK_ENTRY_ARCHIVE
    if not archive.exists() and not archive.is_symlink():
        return {}
    manifest_path = archive / "ORIGINALS.json"
    if not manifest_path.resolve().is_relative_to(package) or not manifest_path.is_file():
        raise ValueError("Missing or unsafe targeted-silk entry archive manifest")
    manifest = json.loads(manifest_path.read_bytes())
    if (
        not isinstance(manifest, dict)
        or manifest.get("source_commit") != SILK_ENTRY_ARCHIVE_SOURCE
        or manifest.get("kind") != "exact_git_blob_bytes"
        or manifest.get("original_link_base") != "repository root at source_commit"
        or not isinstance(manifest.get("files"), list)
        or len(manifest["files"]) != len(SILK_ENTRY_ARCHIVE_FILES)
    ):
        raise ValueError("Unexpected targeted-silk entry archive identity")
    bases = {}
    seen = set()
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid targeted-silk entry archive member")
        source = item.get("path")
        if (
            not isinstance(source, str)
            or source not in SILK_ENTRY_ARCHIVE_FILES
            or source in seen
        ):
            raise ValueError("Unexpected or repeated targeted-silk archive path")
        seen.add(source)
        size, digest = SILK_ENTRY_ARCHIVE_FILES[source]
        path = archive / source
        if (
            type(item.get("bytes")) is not int
            or item["bytes"] != size
            or item.get("sha256") != digest
            or not path.resolve().is_relative_to(package)
            or not path.is_file()
            or path.stat().st_size != size
            or sha(path) != digest
            or not (package / source).is_file()
        ):
            raise ValueError("Archived targeted-silk entry bytes changed: " + source)
        bases[path.resolve()] = (package / source).parent
    return bases


def _archived_document_bases(package, relative, source_commit, archive_files):
    """Validate this exact byte archive before resolving its historical links."""
    archive = package / relative
    if not archive.exists() and not archive.is_symlink():
        return {}
    manifest_path = archive / "ARCHIVE.json"
    if not manifest_path.resolve().is_relative_to(package) or not manifest_path.is_file():
        raise ValueError("Missing or unsafe first-version archive manifest")
    manifest = json.loads(manifest_path.read_bytes())
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "kicad-cli.first-version-entry-archive.v1"
        or manifest.get("source_commit") != source_commit
        or not isinstance(manifest.get("files"), list)
        or len(manifest["files"]) != len(archive_files)
    ):
        raise ValueError("Unexpected first-version archive identity")
    bases = {}
    seen = set()
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid first-version archive entry")
        source = item.get("source_path")
        if not isinstance(source, str) or source not in archive_files or source in seen:
            raise ValueError("Unexpected or repeated archived source_path")
        seen.add(source)
        size, digest = archive_files[source]
        archived_path = relative / source
        path = package / archived_path
        if (
            item.get("archive_path") != archived_path.as_posix()
            or type(item.get("bytes")) is not int
            or item["bytes"] != size
            or item.get("physical_sha256") != digest
            or item.get("archive_sha256") != digest
            or item.get("byte_equal") is not True
            or not path.resolve().is_relative_to(package)
            or not path.is_file()
            or path.stat().st_size != size
            or sha(path) != digest
            or not (package / source).is_file()
        ):
            raise ValueError("Archived first-version bytes or mapping changed: " + source)
        # The archived file is unchanged, so its relative links retain the
        # original source document's directory rather than the archive folder.
        bases[path.resolve()] = (package / source).parent
    return bases


def copy_review_packet(source, target):
    """Copy a declared review packet without raw state, dependencies or PDFs."""
    forbidden_directories = {
        "state",
        ".vendor",
        "_driver_sdk",
        "driver_spec",
        "node_modules",
    }
    forbidden_names = {"scope.json", "CONFIRM-PLAN.json", "ORIGINAL-PLAN.json"}
    forbidden_suffixes = {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pdf",
        ".dmp",
        ".exe",
        ".dll",
        ".pyd",
    }
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Review packet contains a symlink: " + str(path))
        relative = path.relative_to(source)
        if (
            set(relative.parts) & forbidden_directories
            or path.name in forbidden_names
            or path.suffix.lower() in forbidden_suffixes
            or re.search(r"(?:^|[._-])tokens?(?:[._-]|$)", path.name, re.I)
        ):
            raise ValueError("Review packet contains excluded raw material: " + str(relative))
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def relocate_saved_netlist_sources(package, origins):
    """Project copied provenance only; never alter the archived source inputs."""
    projections = []
    for case, origin in origins["cases"].items():
        if Path(case).name != case or "\\" in case or case in {".", ".."}:
            raise ValueError("Unsafe saved input case")
        folder = package / "saved-examples" / case
        for name, original_hash in list(origin["files"].items()):
            if Path(name).name != name or "\\" in name:
                raise ValueError("Unsafe saved input file")
            if not name.endswith(".net"):
                continue
            path = folder / name
            raw = path.read_bytes()
            if sha(path) != original_hash:
                raise ValueError("Netlist differs from its frozen origin")
            text = raw.decode("utf-8")
            matches = list(re.finditer(r'(?m)^\s*\(source\s+("(?:\\.|[^"\\])*")\)', text))
            if len(matches) != 1:
                raise ValueError("Netlist source metadata must be unambiguous")
            match = matches[0]
            source = json.loads(match.group(1))
            identity = PureWindowsPath(source)
            if not identity.is_absolute():
                if source != Path(name).with_suffix(".kicad_sch").name:
                    raise ValueError("Unexpected relative netlist source metadata")
                continue
            relative = identity.name
            if (
                relative != Path(name).with_suffix(".kicad_sch").name
                or not (folder / relative).is_file()
            ):
                raise ValueError("Netlist source does not identify the copied schematic")
            projected = (
                text[: match.start(1)] + json.dumps(relative) + text[match.end(1) :]
            ).encode("utf-8")
            path.write_bytes(projected)
            projected_hash = sha(path)
            origin["files"][name] = projected_hash
            projections.append(
                {
                    "case": case,
                    "file": name,
                    "archived_file_sha256": original_hash,
                    "package_file_sha256": projected_hash,
                    "change": "Only source metadata becomes the copied schematic basename",
                    "relative_source": relative,
                    "electrical_and_other_bytes_unchanged": True,
                    "historical_acceptance_inherited": False,
                }
            )
    origins["package_projections"] = projections
    (package / "saved-examples/ORIGINS.json").write_text(
        json.dumps(origins, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return projections


def validate_docs(package):
    package = package.resolve()
    archived_bases = archived_document_bases(package)
    problems = []
    for path in sorted(package.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"[CD]:[/\\](?:Users|personalSpace|environment|software)", text, re.I):
            problems.append(str(path.relative_to(package)) + ": developer absolute path")
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
            if re.match(r"(?:https?:|mailto:|#)", target):
                continue
            target = target.split("#", 1)[0]
            base = archived_bases.get(path.resolve(), path.parent)
            resolved = (base / target).resolve()
            if not resolved.is_relative_to(package) or not resolved.is_file():
                problems.append(str(path.relative_to(package)) + ": missing link " + target)
    if problems:
        raise ValueError("Package documentation incomplete: " + "; ".join(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New package directory")
    parser.add_argument(
        "--build-source-commit",
        help="Recorded EXE build commit when only uncompiled package reviewers/docs changed",
    )
    args = parser.parse_args()
    source, package = args.source.resolve(), args.output.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, text=True
    )
    if dirty:
        raise ValueError("Build package from a committed, unchanged source checkout")
    build_commit = commit
    if args.build_source_commit:
        build_commit = subprocess.check_output(
            ["git", "rev-parse", args.build_source_commit + "^{commit}"],
            cwd=source,
            text=True,
        ).strip()
        subprocess.run(
            [
                "git",
                "diff",
                "--exit-code",
                build_commit,
                commit,
                "--",
                "kicad_cli",
                "entry.py",
                "build.py",
                "CHANGELOG.md",
                "package.json",
                "pyproject.toml",
                "requirements.txt",
            ],
            cwd=source,
            check=True,
            capture_output=True,
        )
    archive = package.with_suffix(".zip")
    if package.exists() or archive.exists():
        raise FileExistsError("Preserve earlier packages; choose a new output path")
    package.mkdir(parents=True)
    (package / "bin").mkdir()
    shutil.copyfile(args.exe, package / "bin/kicad-cli.exe")
    for name in (
        "WINDOWS_TRIAL.md",
        "WINDOWS_CANDIDATE_REPLAY.md",
        "ADVANCED_SCHEMATIC_EDIT.md",
        "HIERARCHICAL_EDIT.md",
        "SAVED_FOOTPRINT_MOVE.md",
        "SAVED_NETWORK_REPAIR.md",
        "MOVE_REPLY_RECOVERY.md",
        "FIRST_VERSION_SCOPE.md",
        "FIRST_VERSION_CANDIDATE.md",
        "FIRST_VERSION_HANDOFF.md",
        "PERSISTENCE_BOUNDARIES.md",
        "ACCEPTANCE_HARDENING.md",
        "CHANGELOG.md",
        "LICENSE",
        "NOTICE.md",
        "NOTICE_zh.md",
    ):
        shutil.copyfile(source / name, package / name)
    completion_packet = source / "saved-network-completion"
    completion_navigation = (
        "本轮成果复核从[剩余网络只读工程包](saved-network-completion/README.md)开始。"
        if completion_packet.is_dir()
        else "剩余网络只读工程包尚未加入源码目录，不能视为已有成果。"
    )
    dual_rc_guide = source / "DUAL_RC_ECO.md"
    if dual_rc_guide.is_file():
        shutil.copyfile(dual_rc_guide, package / "DUAL_RC_ECO.md")
    setup_navigation = (
        "当前依赖、隔离目录和scope登记见[双通道配置步骤]"
        "(DUAL_RC_ECO.md#准备独立环境与授权)。旧configure.py仅授权rc/execution。"
        if dual_rc_guide.is_file()
        else "首次配置见[Windows依赖与配置步骤](WINDOWS_TRIAL.md#1-准备已有依赖)。"
    )
    engineering_navigation = (
        "当前工程从[双通道RC与ECO指南](DUAL_RC_ECO.md)开始，"
        "状态与待验收项以[当前交接](FIRST_VERSION_HANDOFF.md)为准。"
        "工程、方法、EXE和最终包各自绑定；只读复核不重复确认原操作。"
        "旧R4/R5及五网成果仍保留在[原只读工程包](saved-network-repair/README.md)和"
        "[剩余网络只读包](saved-network-completion/README.md)。"
        if dual_rc_guide.is_file()
        else (
            "工程操作见[保存网络连接修复](SAVED_NETWORK_REPAIR.md)，"
            "范围和结果见[剩余网络工程账本]"
            "(docs/evidence/windows-network-completion-20261017/README.md)。"
            f"{completion_navigation}"
        )
    )
    drill_guide = source / "DUAL_RC_DRILL_CLEARANCE.md"
    drill_packet = source / "dual-rc-drill-clearance"
    drill_evidence = source / "docs/evidence/windows-dual-rc-drill-clearance-20261018"
    if drill_guide.is_file():
        if not all(
            (drill_packet / name).is_file()
            for name in ("README.md", "ORIGINS.json", "PACKET-CONTENTS.json")
        ):
            raise ValueError("Current drill-clearance guide requires its complete curated packet")
        if not all(
            (drill_evidence / name).is_file() for name in ("README.md", "acceptance-current.json")
        ):
            raise ValueError("Current drill-clearance guide requires its current evidence entry")
        shutil.copyfile(drill_guide, package / "DUAL_RC_DRILL_CLEARANCE.md")
        setup_navigation = (
            "新自有目录、合法依赖与scope登记见[钻孔避让配置步骤]"
            "(DUAL_RC_DRILL_CLEARANCE.md#准备独立环境与授权)。"
            "旧ECO的10k输入不用于当前12k任务。"
        )
        engineering_navigation = (
            "当前工程从[按指定网络避开SMD钻孔](DUAL_RC_DRILL_CLEARANCE.md)及"
            "[只读工程包](dual-rc-drill-clearance/README.md)开始。"
            "工程、候选图、正式保存与新读回分别留证；状态见"
            "[当前交接](FIRST_VERSION_HANDOFF.md)。旧ECO包与PARTIAL仍保留。"
        )
    silk_guide = source / "TARGETED_SILK_READABILITY.md"
    silk_packet = source / "targeted-silk-readability"
    silk_evidence = source / "docs/evidence/windows-targeted-silkscreen-20261018"
    if silk_guide.is_file():
        if not all(
            (silk_packet / name).is_file()
            for name in ("README.md", "ORIGINS.json", "PACKET-CONTENTS.json")
        ):
            raise ValueError("Current targeted-silk guide requires its complete curated packet")
        if not all(
            (silk_evidence / name).is_file() for name in ("README.md", "acceptance-current.json")
        ):
            raise ValueError("Current targeted-silk guide requires its current evidence entry")
        if not (source / SILK_ENTRY_ARCHIVE / "ORIGINALS.json").is_file():
            raise ValueError("Current targeted-silk guide requires its exact prior entry archive")
        shutil.copyfile(silk_guide, package / "TARGETED_SILK_READABILITY.md")
        setup_navigation = (
            "新自有目录、合法依赖、输入冻结与目标身份登记见"
            "[目标丝印使用配方](TARGETED_SILK_READABILITY.md)。"
            "保持R10=12k、R20=10k，不重放旧路由或确认。"
        )
        engineering_navigation = (
            "当前工程从[目标位号整理与阅读交付](TARGETED_SILK_READABILITY.md)及"
            "[同快照只读工程包](targeted-silk-readability/README.md)开始。"
            "实际PCB字段平移、原生图与阅读副图分别留证；准确范围见"
            "[当前交接](FIRST_VERSION_HANDOFF.md)。"
            "旧钻孔避让、ECO工程及独立PARTIAL仍保留，不作为本轮新执行。"
        )
    entry_history = (
        "docs/history/windows-first-version-16ac7df/README.md"
        if (source / SILK_ENTRY_ARCHIVE).is_dir()
        else "docs/history/windows-first-version-4b28db0/README.md"
        if (source / DRILL_ENTRY_ARCHIVE).is_dir()
        else "docs/history/windows-first-version-c913dc2/README.md"
        if (source / CURRENT_ENTRY_ARCHIVE).is_dir()
        else "docs/history/windows-first-version-6960788/README.md"
    )
    (package / "README.md").write_text(
        "# KiCad Windows内部候选\n\n"
        "从[当前候选与准确构件](FIRST_VERSION_CANDIDATE.md)开始，"
        "核对CANDIDATE.json和FILES.sha256.json，再阅读"
        "[包内Skill](skills/kicad-cli/SKILL.md)。同名npm包不识别或安装此候选。\n\n"
        f"{setup_navigation}"
        "需要合法已有的KiCad 10.0.6、固定SDK/spec及声明的读取和渲染依赖；"
        "这些依赖在包外，不隐式下载或替换。\n\n"
        f"{engineering_navigation}"
        "先核实设计意图和公开reference，预览后再确认；"
        "保存、独立物理连通、原生DRC与完整图面分别验收。"
        "不为重复演示再次改板。\n\n"
        "回复丢失时进入[原ID查询与恢复入口](MOVE_REPLY_RECOVERY.md)，"
        "保留原状态库，不重发确认。历史来源见"
        f"[原入口归档]({entry_history})。\n\n"
        "保持私有、npm关闭，release_readiness为unpublishable。"
        "本包不代表全树通过、完整连接图或制造签核。\n",
        encoding="utf-8",
        newline="\n",
    )
    shutil.copytree(source / "skills/kicad-cli", package / "skills/kicad-cli")
    (package / "docs/evidence/hierarchy-usable-20261013").mkdir(parents=True)
    for name in ("PUBLIC_COMMANDS.json", "PUBLIC_COMMANDS.md"):
        shutil.copyfile(source / "docs" / name, package / "docs" / name)
    shutil.copyfile(
        source / "docs/evidence/hierarchy-usable-20261013/summary.json",
        package / "docs/evidence/hierarchy-usable-20261013/summary.json",
    )
    shutil.copytree(source / "bench/saved_examples", package / "saved-examples")
    shutil.copytree(source / "saved-network-repair", package / "saved-network-repair")
    if completion_packet.is_dir():
        shutil.copytree(completion_packet, package / "saved-network-completion")
    dual_rc_packet = source / "dual-rc-acceptance"
    if dual_rc_packet.is_dir():
        copy_review_packet(dual_rc_packet, package / "dual-rc-acceptance")
    if drill_packet.is_dir():
        copy_review_packet(drill_packet, package / "dual-rc-drill-clearance")
    if silk_packet.is_dir():
        copy_review_packet(silk_packet, package / "targeted-silk-readability")
    shutil.copytree(
        source / "bench/persistence_acceptance",
        package / "persistence_acceptance",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (package / "persistence_acceptance/support").mkdir()
    for name in (
        "process_evidence.py",
        "owned_process.py",
        "win_debugging.py",
        "native_observation.py",
    ):
        shutil.copyfile(source / "bench" / name, package / "persistence_acceptance/support" / name)
    shutil.copytree(
        source / "docs/evidence/persistence-boundaries-20261013",
        package / "docs/evidence/persistence-boundaries-20261013",
    )
    shutil.copytree(
        source / "docs/evidence/acceptance-hardening-20261013",
        package / "docs/evidence/acceptance-hardening-20261013",
    )
    shutil.copytree(source / "docs/fcc", package / "docs/fcc")
    shutil.copytree(
        source / "docs/evidence/first-version-20261014",
        package / "docs/evidence/first-version-20261014",
    )
    shutil.copytree(
        source / "docs/evidence/execution-diagnostics-20261014",
        package / "docs/evidence/execution-diagnostics-20261014",
    )
    shutil.copytree(
        source / "docs/evidence/windows-candidate-20261014",
        package / "docs/evidence/windows-candidate-20261014",
    )
    shutil.copytree(
        source / "docs/evidence/windows-continuation-20261015",
        package / "docs/evidence/windows-continuation-20261015",
    )
    shutil.copytree(
        source / "docs/evidence/windows-method-continuation-20261016",
        package / "docs/evidence/windows-method-continuation-20261016",
    )
    move_evidence = source / "docs/evidence/windows-move-continuation-20261016"
    if move_evidence.is_dir():
        shutil.copytree(move_evidence, package / "docs/evidence/windows-move-continuation-20261016")
    safety_evidence = source / "docs/evidence/windows-move-safety-20261016"
    if safety_evidence.is_dir():
        shutil.copytree(safety_evidence, package / "docs/evidence/windows-move-safety-20261016")
    stability_evidence = source / "docs/evidence/windows-move-stability-20261016"
    if stability_evidence.is_dir():
        shutil.copytree(
            stability_evidence,
            package / "docs/evidence/windows-move-stability-20261016",
        )
    reply_evidence = source / "docs/evidence/windows-move-reply-recovery-20261017"
    if reply_evidence.is_dir():
        shutil.copytree(
            reply_evidence,
            package / "docs/evidence/windows-move-reply-recovery-20261017",
        )
    shutil.copytree(
        source / "docs/evidence/windows-saved-net-repair-20261017",
        package / "docs/evidence/windows-saved-net-repair-20261017",
    )
    completion_evidence = source / "docs/evidence/windows-network-completion-20261017"
    if completion_evidence.is_dir():
        shutil.copytree(
            completion_evidence,
            package / "docs/evidence/windows-network-completion-20261017",
        )
    dual_rc_evidence = source / "docs/evidence/windows-dual-rc-eco-20261018"
    if dual_rc_evidence.is_dir():
        copy_review_packet(dual_rc_evidence, package / "docs/evidence/windows-dual-rc-eco-20261018")
    if drill_evidence.is_dir():
        copy_review_packet(
            drill_evidence,
            package / "docs/evidence/windows-dual-rc-drill-clearance-20261018",
        )
    if silk_evidence.is_dir():
        copy_review_packet(
            silk_evidence,
            package / "docs/evidence/windows-targeted-silkscreen-20261018",
        )
    if (source / SILK_ENTRY_ARCHIVE).is_dir():
        shutil.copytree(source / SILK_ENTRY_ARCHIVE, package / SILK_ENTRY_ARCHIVE)
    if (source / DRILL_ENTRY_ARCHIVE).is_dir():
        shutil.copytree(source / DRILL_ENTRY_ARCHIVE, package / DRILL_ENTRY_ARCHIVE)
    shutil.copytree(source / FIRST_VERSION_ARCHIVE, package / FIRST_VERSION_ARCHIVE)
    if (source / CURRENT_ENTRY_ARCHIVE).is_dir():
        shutil.copytree(source / CURRENT_ENTRY_ARCHIVE, package / CURRENT_ENTRY_ARCHIVE)
    saved_inputs = json.loads((package / "saved-examples/ORIGINS.json").read_bytes())
    for name, origin in saved_inputs["cases"].items():
        actual = {
            p.relative_to(package / "saved-examples" / name).as_posix(): sha(p)
            for p in (package / "saved-examples" / name).rglob("*")
            if p.is_file()
        }
        if actual != origin["files"]:
            raise ValueError("Previously accepted saved input bytes changed: " + name)
    relocate_saved_netlist_sources(package, saved_inputs)
    method = source / "bench/rc_trial"
    shutil.copyfile(method / "configure.py", package / "configure.py")
    for directory in ("execution", "agent-packet"):
        shutil.copytree(
            method / directory,
            package / "rc" / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copytree(
        source / "bench/advanced_edit",
        package / "advanced-edit",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    dependencies_path = package / "rc/agent-packet/DEPENDENCIES.example.json"
    shutil.copytree(
        source / "bench/hierarchy_edit",
        package / "hierarchy-edit",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (package / "hierarchy-edit/support").mkdir()
    for name in ("process_evidence.py", "owned_process.py"):
        shutil.copyfile(source / "bench" / name, package / "hierarchy-edit/support" / name)
    dependencies = json.loads(dependencies_path.read_bytes())
    usable = json.loads((package / "hierarchy-edit/usable/requirements/FROZEN.json").read_bytes())
    dependencies["libraries"].update(usable["external_libraries"])
    requirements = json.loads((package / "advanced-edit/requirements/FROZEN.json").read_bytes())
    for kind, item in requirements["symbol_and_footprint"].items():
        folder = "symbols/" if kind == "symbol" else "footprints/TestPoint.pretty/"
        dependencies["libraries"][item["id"]] = {
            "path": "<EXISTING_KICAD_ROOT>/share/kicad/" + folder + item["file"],
            "sha256": item["sha256"],
        }
    dependencies_path.write_text(
        json.dumps(dependencies, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    # Freeze the copied input bytes before any engineering run. Git checkout line
    # endings may differ from the historical archived method; never carry its old
    # byte digest into a new package. This does not derive intent from outputs.
    plan_path = package / "rc/execution/plans/baseline-layout-v2.json"
    plan = json.loads(plan_path.read_bytes())
    plan["requirements_sha256"] = sha(package / "rc/agent-packet/requirements/intent-baseline.json")
    plan["requirements_binding"] = (
        "Copied package input bytes, frozen before engineering generation"
    )
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8", newline="\n")
    candidate = {
        "source_commit": build_commit,
        "package_source_commit": commit,
        "build_inputs_unchanged_since_build_commit": True,
        "build_input_comparison_basis": (
            "Git logical diff for the listed build paths only; physical build/package input "
            "identity requires a separately recorded byte comparison against the build plan"
        ),
        "executable": "bin/kicad-cli.exe",
        "sha256": sha(package / "bin/kicad-cli.exe"),
        "release_readiness": "unpublishable",
        "distribution": "internal local candidate, not an npm release or self-contained installer",
        "external_driver_pin": "9607a35fe0ea33fe822e1f2ad2704eae260d4018",
        "native_case_version": "10.0.6",
    }
    (package / "CANDIDATE.json").write_text(
        json.dumps(candidate, indent=2) + "\n", encoding="utf-8"
    )
    validate_docs(package)
    files = {
        p.relative_to(package).as_posix(): sha(p) for p in sorted(package.rglob("*")) if p.is_file()
    }
    forbidden = [
        name
        for name in files
        if any(
            part in name.split("/")
            for part in (".vendor", "_driver_sdk", "driver_spec", "kicad_cli")
        )
    ]
    if forbidden:
        raise ValueError("External dependency or product source copied: " + str(forbidden))
    (package / "FILES.sha256.json").write_text(
        json.dumps({"source_commit": commit, "files": files}, indent=2) + "\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                zip_file.write(path, path.relative_to(package).as_posix())
    print(
        json.dumps(
            {
                "package": str(package),
                "archive": str(archive),
                "archive_sha256": sha(archive),
                "candidate": candidate,
                "files": len(files),
                "local_links_checked": True,
            }
        )
    )


if __name__ == "__main__":
    main()
