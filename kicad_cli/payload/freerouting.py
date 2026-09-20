"""找到 Freerouting 和跑它需要的 Java。不打包,只发现——和 KiCad 一个待遇。

Freerouting 是 GPL-3.0,本项目是 MIT。NOTICE.md 的立场是「不重分发 KiCad 的
代码或资产,跑的是用户本机装的那一份」——KiCad 自己也是 GPL-3.0,所以这里
没有新问题,只有同一条规矩的第二次适用:字节不进我们的包,我们只负责找到它、
用命令行参数隔着进程调一次。

可以被导入而不需要 pcbnew,所以 doctor 在宿主侧就能报告它在不在。

    KICAD_CLI_FREEROUTING   freerouting 的 .jar（或可执行文件）
    KICAD_CLI_JAVA          java 可执行文件；不给就按 JAVA_HOME / PATH 找
"""

from __future__ import annotations

import os
import re
import shutil
import zipfile
from pathlib import Path

ENV_JAR = "KICAD_CLI_FREEROUTING"
ENV_JAVA = "KICAD_CLI_JAVA"

# 2.x 才有本工具依赖的那套命令行（-de/-do/-mt/-us）。1.x 的参数不一样,
# 与其猜不如让调用方看见版本号自己判断。
MIN_MAJOR = 2

# 官方安装包和「下载一个 jar 扔在某处」两种用法都照顾到。顺序即优先级。
_JAR_GLOBS = (
    "freerouting*.jar",
    "freerouting/freerouting*.jar",
    "tools/freerouting*.jar",
)


def _candidate_dirs() -> list[Path]:
    home = Path.home()
    dirs = [Path.cwd(), home / "Downloads", home / ".local" / "share", home / "freerouting"]
    for key in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if value:
            dirs.append(Path(value) / "freerouting")
    return [d for d in dirs if d.is_dir()]


def find_jar() -> str | None:
    """显式配置优先;否则在几个常见位置找一个 jar。找不到就是找不到。

    不去全盘搜索。一个在 C 盘某个角落里的 jar,和一个用户明确指过的 jar,
    是两种不同的信任级别,而这个工具没有资格替用户把前者升级成后者。
    """
    override = os.environ.get(ENV_JAR)
    if override:
        candidate = Path(override).expanduser()
        return str(candidate.resolve()) if candidate.is_file() else None
    for directory in _candidate_dirs():
        for pattern in _JAR_GLOBS:
            found = sorted(directory.glob(pattern))
            if found:
                return str(found[-1].resolve())
    return None


def find_java() -> str | None:
    """跑 jar 要 JVM。Freerouting 2.x 要 Java 21+,但版本由调用方去问。"""
    override = os.environ.get(ENV_JAVA)
    if override:
        candidate = Path(override).expanduser()
        return str(candidate.resolve()) if candidate.is_file() else None
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        for name in ("bin/java.exe", "bin/java"):
            candidate = Path(java_home) / name
            if candidate.is_file():
                return str(candidate.resolve())
    found = shutil.which("java")
    return str(Path(found).resolve()) if found else None


def version(jar: str) -> str | None:
    """从 jar 里读版本号,不启动它。

    原来是跑一次 `java -jar ... --help` 再从输出里抠。那要起一个 JVM,实测把
    doctor 从 0.79 秒拖到 3.59 秒——而 doctor 正是出问题时第一个要跑的命令,
    为了一个可选引擎的版本号让它慢四倍半不划算。

    顺带还少了一件事:不必为了问版本去执行一个我们没验证过的 jar。

    两个来源。文件名是官方 release 的命名,最便宜;改过名就去 Constants.class
    里扫——版本号以字面量编在里面。两个都读不到就返回 None,由 status 决定
    怎么办,不猜。
    """
    name = Path(jar).name
    match = re.search(r"freerouting[-_]v?(\d+\.\d+[\w.]*?)(?:\.jar)?$", name, re.I)
    if match:
        return match.group(1)
    try:
        with zipfile.ZipFile(jar) as archive:
            body = archive.read("app/freerouting/constants/Constants.class")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    found = re.search(rb"(\d+\.\d+\.\d+)", body)
    return found.group(1).decode("ascii") if found else None


def status() -> dict:
    """doctor 要的那一行:找到了吗、哪一个、什么版本、能不能用。"""
    jar, java = find_jar(), find_java()
    out = {
        "jar": jar,
        "java": java,
        "version": None,
        "usable": False,
        "reason": None,
    }
    if not jar:
        out["reason"] = (
            f"找不到 freerouting 的 jar。下载 https://github.com/freerouting/freerouting "
            f"的 release，然后设 {ENV_JAR}=<path to freerouting.jar>"
        )
        return out
    if not java:
        out["reason"] = f"找到了 jar 但没有 java。装 JDK 21+，或设 {ENV_JAVA}=<path to java>"
        return out
    out["version"] = version(jar)
    if out["version"] is None:
        # 读不出版本不等于不能用。拦下来的代价是明确的（这个引擎用不了），
        # 放过去的代价是可能撞上 1.x 的参数不兼容——而那会带着 Freerouting
        # 自己的报错回来，比我们在这里替它拒绝更说得清。
        out["usable"] = True
        out["reason"] = "读不出版本号（jar 改过名？）——按能用处理；真不兼容会在调用时报出来"
        return out
    try:
        major = int(out["version"].split(".")[0])
    except ValueError:
        major = 0
    if major < MIN_MAJOR:
        out["reason"] = f"需要 Freerouting {MIN_MAJOR}.x 及以上，找到的是 {out['version']}"
        return out
    out["usable"] = True
    return out
