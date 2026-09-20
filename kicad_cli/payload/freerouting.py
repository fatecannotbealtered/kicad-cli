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
import subprocess
import tempfile
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


def version(java: str, jar: str, timeout: float = 120.0) -> str | None:
    """问它自己的版本号。

    在临时目录里问。Freerouting 把自己的日志写到 <工作目录>/<语言>/ 下面,
    所以就这么一次 --help 也会在调用方的当前目录里凭空长出一个 en/ 文件夹。
    doctor 每跑一次就拉一次——实测在本仓库根目录里拉出来过,还差点被提交。

    不带 -l:版本那一行是 "Freerouting v2.4.1 (build-date: ...)",产品名加版本号,
    不跟着语言走。少一个参数,少一个副作用。
    """
    try:
        with tempfile.TemporaryDirectory(prefix="kicadcli-fr-v") as scratch:
            proc = subprocess.run(
                [java, "-jar", jar, "--help"],
                capture_output=True,
                timeout=timeout,
                cwd=scratch,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or b"").decode("utf-8", "replace")
    text += (proc.stderr or b"").decode("utf-8", "replace")
    match = re.search(r"Freerouting\s+v?(\d+\.\d+[\w.]*)", text)
    return match.group(1) if match else None


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
    out["version"] = version(java, jar)
    if out["version"] is None:
        out["reason"] = "jar 和 java 都在，但问不出版本号——这一对可能配不上"
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
